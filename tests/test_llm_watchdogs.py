"""Regression tests for streaming watchdogs and Qwen3 direct-answer recovery."""
from __future__ import annotations

import json
import io
import socket
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

from r20_backend import llm_manager as lm
from tests.test_llm_transport import ChunkResponse, Clock, reply, response, sse


class TimedChunks(io.BytesIO):
    """Return pre-split SSE frames while advancing a deterministic clock."""

    def __init__(self, chunks, clock, delay):
        super().__init__(b"")
        self.chunks = list(chunks)
        self.clock = clock
        self.delay = delay
        self.socket = Mock()
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self.socket))

    def read1(self, n=-1):
        self.clock.tick(self.delay)
        return self.chunks.pop(0) if self.chunks else b""


class LLMWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.primary = {
            "model": "qwen3.8-flash",
            "base_url": "https://gateway.example/v1",
            "api_format": "openai_chat",
            "reasoning_type": "standard_effort",
            "reasoning_effort": "high",
            "thinking_timeout": 300,
            "request_attempts": 1,
            "fallback_model_ids": ["backup-m"],
        }
        self.backup = {
            **self.primary,
            "model": "backup-m",
            "reasoning_type": "none",
            "reasoning_effort": "none",
        }
        patchers = (
            patch.object(lm, "get_active_llm_runtime", return_value=self.primary),
            patch.object(lm, "resolve_model_runtime", return_value=self.backup),
            patch.object(lm, "FAILOVER_MAX_TOTAL_WAIT", 0),
            patch.object(lm.time, "perf_counter", self.clock),
            patch.object(lm.time, "sleep", side_effect=self.clock.tick),
            patch.object(lm.analysis_capture, "emit"),
        )
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.failover_patcher = patch.object(lm, "record_failover_event")
        self.failover_events = self.failover_patcher.start()
        self.addCleanup(self.failover_patcher.stop)

    def call(self, **kwargs):
        return lm.execute_llm_request(
            [{"role": "user", "content": "fixture"}], **kwargs
        )

    def test_response_start_watchdog_moves_to_backup(self):
        calls = []

        def transport(req, timeout):
            calls.append((json.loads(req.data)["model"], timeout))
            if len(calls) == 1:
                self.clock.tick(timeout)
                raise socket.timeout("no headers")
            return response(reply("BACKUP"))

        with patch.object(lm, "RESPONSE_START_TIMEOUT_SECONDS", 5), patch.object(
            lm, "REASONING_ONLY_TIMEOUT_SECONDS", 0
        ), patch.object(lm.urllib.request, "urlopen", side_effect=transport):
            self.assertEqual(self.call(timeout=20)[0], "BACKUP")
        self.assertEqual(calls, [("qwen3.8-flash", 5), ("backup-m", 5)])
        self.assertIn(
            "响应头看门狗 5s 到期",
            self.failover_events.call_args_list[-1].args[0]["errors"][0],
        )

    def test_reasoning_only_watchdog_stops_stream_before_candidate_deadline(self):
        body = sse({"choices": [{"delta": {"reasoning_content": "thinking"}}]}, done=False)
        stream = ChunkResponse(body, self.clock, delay=6, chunk_size=len(body))
        with patch.object(lm, "RESPONSE_START_TIMEOUT_SECONDS", 0), patch.object(
            lm, "REASONING_ONLY_TIMEOUT_SECONDS", 5
        ), patch.object(lm.urllib.request, "urlopen", return_value=stream):
            with self.assertRaisesRegex(TimeoutError, "已收到思考内容"):
                self.call(timeout=30, allow_fallback=False)
        self.assertLess(self.clock.now, 30)

    def test_qwen_reasoning_timeout_gets_direct_answer_recovery(self):
        body = sse({"choices": [{"delta": {"reasoning_content": "thinking"}}]}, done=False)
        stream = ChunkResponse(body, self.clock, delay=6, chunk_size=len(body))
        seen = []

        def transport(req, timeout):
            payload = json.loads(req.data)
            seen.append((payload, timeout))
            return stream if len(seen) == 1 else response(reply('{"action":"WAIT"}'))

        with patch.object(lm, "RESPONSE_START_TIMEOUT_SECONDS", 0), patch.object(
            lm, "REASONING_ONLY_TIMEOUT_SECONDS", 5
        ), patch.object(lm, "QWEN_DIRECT_RECOVERY_TIMEOUT_SECONDS", 8), patch.object(
            lm.urllib.request, "urlopen", side_effect=transport
        ):
            result = self.call(
                timeout=30,
                allow_fallback=False,
                response_format={"type": "json_object"},
            )
        self.assertEqual(json.loads(result[0]), {"action": "WAIT"})
        recovery = seen[1][0]
        self.assertEqual(recovery["reasoning_effort"], "none")
        self.assertFalse(recovery["enable_thinking"])
        self.assertEqual(recovery["max_tokens"], lm.QWEN_DIRECT_RECOVERY_MAX_TOKENS)
        self.assertIn("立即结束内部思考", recovery["messages"][0]["content"])
        self.assertEqual(seen[1][1], 8)

    def test_recovery_removes_only_parameters_rejected_by_gateway(self):
        body = sse({"choices": [{"delta": {"reasoning_content": "thinking"}}]}, done=False)
        stream = ChunkResponse(body, self.clock, delay=6, chunk_size=len(body))
        seen = []

        def transport(req, timeout):
            payload = json.loads(req.data)
            seen.append(payload)
            if len(seen) == 1:
                return stream
            if "enable_thinking" in payload:
                raise urllib.error.HTTPError(
                    req.full_url, 400, "unsupported", {},
                    io.BytesIO(b"enable_thinking unsupported"),
                )
            if "max_tokens" in payload:
                raise urllib.error.HTTPError(
                    req.full_url, 400, "unsupported", {},
                    io.BytesIO(b"max_tokens unsupported"),
                )
            return response(reply('{"action":"WAIT"}'))

        with patch.object(lm, "RESPONSE_START_TIMEOUT_SECONDS", 0), patch.object(
            lm, "REASONING_ONLY_TIMEOUT_SECONDS", 5
        ), patch.object(lm, "QWEN_DIRECT_RECOVERY_TIMEOUT_SECONDS", 8), patch.object(
            lm.urllib.request, "urlopen", side_effect=transport
        ):
            self.assertEqual(
                json.loads(self.call(
                    timeout=30,
                    allow_fallback=False,
                    response_format={"type": "json_object"},
                )[0]),
                {"action": "WAIT"},
            )
        self.assertNotIn("enable_thinking", seen[2])
        self.assertNotIn("max_tokens", seen[3])
        self.assertEqual(seen[3]["reasoning_effort"], "none")

    def test_failed_direct_recovery_continues_to_next_model(self):
        body = sse({"choices": [{"delta": {"reasoning_content": "thinking"}}]}, done=False)
        stream = ChunkResponse(body, self.clock, delay=6, chunk_size=len(body))
        calls = []

        def transport(req, timeout):
            calls.append((json.loads(req.data)["model"], timeout))
            if len(calls) == 1:
                return stream
            if len(calls) == 2:
                self.clock.tick(timeout)
                raise socket.timeout("direct answer stalled")
            return response(reply('{"action":"BACKUP"}'))

        with patch.object(lm, "RESPONSE_START_TIMEOUT_SECONDS", 0), patch.object(
            lm, "REASONING_ONLY_TIMEOUT_SECONDS", 5
        ), patch.object(lm, "QWEN_DIRECT_RECOVERY_TIMEOUT_SECONDS", 8), patch.object(
            lm.urllib.request, "urlopen", side_effect=transport
        ):
            self.assertEqual(
                self.call(
                    timeout=30,
                    response_format={"type": "json_object"},
                )[0],
                '{"action":"BACKUP"}',
            )
        self.assertEqual([name for name, _ in calls], ["qwen3.8-flash", "qwen3.8-flash", "backup-m"])
        self.assertEqual(calls[1][1], 8)

    def test_completed_reasoning_only_response_gets_direct_recovery(self):
        seen = []

        def transport(req, timeout):
            seen.append(json.loads(req.data))
            if len(seen) == 1:
                return response({
                    "choices": [{"message": {
                        "content": "",
                        "reasoning_content": "thinking",
                    }}]
                })
            return response(reply('{"action":"WAIT"}'))

        with patch.object(lm, "RESPONSE_START_TIMEOUT_SECONDS", 0), patch.object(
            lm, "REASONING_ONLY_TIMEOUT_SECONDS", 0
        ), patch.object(lm, "QWEN_DIRECT_RECOVERY_TIMEOUT_SECONDS", 8), patch.object(
            lm.urllib.request, "urlopen", side_effect=transport
        ):
            result = self.call(
                timeout=30,
                allow_fallback=False,
                response_format={"type": "json_object"},
            )
        self.assertEqual(json.loads(result[0]), {"action": "WAIT"})
        self.assertEqual(len(seen), 2)

    def test_recovery_uses_remaining_candidate_budget(self):
        body = sse({"choices": [{"delta": {"reasoning_content": "thinking"}}]}, done=False)
        stream = ChunkResponse(body, self.clock, delay=6, chunk_size=len(body))
        seen = []

        def transport(req, timeout):
            seen.append(timeout)
            return stream if len(seen) == 1 else response(reply('{"action":"WAIT"}'))

        with patch.object(lm, "RESPONSE_START_TIMEOUT_SECONDS", 0), patch.object(
            lm, "REASONING_ONLY_TIMEOUT_SECONDS", 5
        ), patch.object(lm, "QWEN_DIRECT_RECOVERY_TIMEOUT_SECONDS", 60), patch.object(
            lm.urllib.request, "urlopen", side_effect=transport
        ):
            result = self.call(
                timeout=20,
                allow_fallback=False,
                response_format={"type": "json_object"},
            )
        self.assertEqual(json.loads(result[0]), {"action": "WAIT"})
        self.assertEqual(seen, [20, 8])

    def test_qwen_recovery_is_limited_to_openai_chat(self):
        calls = []

        def attempt(*args, **kwargs):
            calls.append(kwargs)
            raise lm._LLMTransientError(
                "reasoning only", timed_out=True, reasoning_only=True
            )

        non_chat = {**self.primary, "api_format": "openai_responses"}
        with patch.object(lm, "get_active_llm_runtime", return_value=non_chat), patch.object(
            lm, "_attempt_llm_call", side_effect=attempt
        ), patch.object(lm, "QWEN_DIRECT_RECOVERY_TIMEOUT_SECONDS", 8):
            with self.assertRaises(TimeoutError):
                self.call(
                    timeout=20,
                    allow_fallback=False,
                    response_format={"type": "json_object"},
                )
        self.assertEqual(len(calls), 1)

    def test_failed_recovery_preserves_error_and_does_not_recurse(self):
        calls = []

        def attempt(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise lm._LLMTransientError(
                    "reasoning only", timed_out=True, reasoning_only=True
                )
            raise lm._LLMHardError("recovery parameter rejected")

        with patch.object(lm, "_attempt_llm_call", side_effect=attempt), patch.object(
            lm, "QWEN_DIRECT_RECOVERY_TIMEOUT_SECONDS", 8
        ):
            with self.assertRaisesRegex(RuntimeError, "recovery parameter rejected"):
                self.call(
                    timeout=20,
                    allow_fallback=False,
                    response_format={"type": "json_object"},
                )
        self.assertEqual(len(calls), 2)

    def test_final_content_clears_reasoning_watchdog(self):
        chunks = [
            b": heartbeat\r\n\r\ndata: "
            + json.dumps({"choices": [{"delta": {"reasoning_content": "thinking"}}]}).encode()
            + b"\r\n\r\n",
            b"data: "
            + json.dumps({"choices": [{"delta": {"content": '{"action":"WAIT"}'}}]}).encode()
            + b"\r\n\r\n",
            b": ping\r\n\r\n",
            b": ping\r\n\r\n",
            b"data: "
            + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}).encode()
            + b"\r\n\r\n",
            b"data: [DONE]\r\n\r\n",
        ]
        stream = TimedChunks(chunks, self.clock, delay=2)
        with patch.object(lm, "RESPONSE_START_TIMEOUT_SECONDS", 0), patch.object(
            lm, "REASONING_ONLY_TIMEOUT_SECONDS", 5
        ), patch.object(lm.urllib.request, "urlopen", return_value=stream):
            result = self.call(
                timeout=30,
                allow_fallback=False,
                response_format={"type": "json_object"},
            )
        self.assertEqual(json.loads(result[0]), {"action": "WAIT"})
        self.assertGreater(self.clock.now, 5)

    def test_long_explicit_budget_is_not_cut_by_fixed_watchdog(self):
        body = sse({"choices": [{"delta": {"reasoning_content": "thinking"}}]}, done=False)
        stream = ChunkResponse(body, self.clock, delay=61, chunk_size=len(body))
        with patch.object(lm, "RESPONSE_START_TIMEOUT_SECONDS", 0), patch.object(
            lm, "REASONING_ONLY_TIMEOUT_SECONDS", 60
        ), patch.object(lm.urllib.request, "urlopen", return_value=stream):
            with self.assertRaisesRegex(RuntimeError, "流式响应中断"):
                self.call(timeout=150, allow_fallback=False)
        # The fixture ends after 2 x 61s; a 60s thinking watchdog would have
        # failed on the first read instead.
        self.assertGreaterEqual(self.clock.now, 122)


if __name__ == "__main__":
    unittest.main()

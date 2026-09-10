"""Replay delayed, interrupted and incompatible gateways without sending trading requests."""
from __future__ import annotations

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import socket
import threading
import time
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

from r20_backend import llm_manager as lm


def reply(content="OK", **extra):
    return {"choices": [{"message": {"content": content}, **extra}]}


def response(payload):
    return io.BytesIO(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def sse(*events, done=True):
    body = b": heartbeat\r\n\r\n"
    for event in events:
        body += b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\r\n\r\n"
    return body + (b"data: [DONE]\r\n\r\n" if done else b"")


class Clock:
    def __init__(self):
        self.now = 0.0

    def tick(self, seconds):
        self.now += seconds

    def __call__(self):
        return self.now


class ChunkResponse(io.BytesIO):
    """Small chunks split UTF-8, SSE fields and JSON across network reads."""
    def __init__(self, body, clock=None, delay=0, chunk_size=7):
        super().__init__(body)
        self.clock = clock
        self.delay = delay
        self.chunk_size = chunk_size
        self.socket = Mock()
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=self.socket))

    def read1(self, n=-1):
        if self.clock:
            self.clock.tick(self.delay)
        return super().read1(min(n, self.chunk_size))


class LLMTransportTests(unittest.TestCase):
    def setUp(self):
        self.primary = {
            "model": "qwen3.8-flash", "base_url": "https://gateway.example/v1",
            "api_format": "openai_chat", "reasoning_type": "standard_effort",
            "reasoning_effort": "high", "thinking_timeout": 300,
            "request_attempts": 2, "fallback_model_ids": ["qwen3.8-max"],
        }
        self.backup = {**self.primary, "model": "qwen3.8-max"}
        self.clock = Clock()
        for patcher in (
            patch.object(lm, "get_active_llm_runtime", return_value=self.primary),
            patch.object(lm, "resolve_model_runtime", return_value=self.backup),
            patch.object(lm, "FAILOVER_MAX_TOTAL_WAIT", 0),
            patch.object(lm.time, "perf_counter", self.clock),
            patch.object(lm.time, "sleep", side_effect=self.clock.tick),
            patch.object(lm.analysis_capture, "emit"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(lm, "record_failover_event")
        self.events = patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, **kwargs):
        return lm.execute_llm_request([{"role": "user", "content": "fixture"}], **kwargs)

    def test_stream_reassembles_unicode_reasoning_answer_and_usage(self):
        body = sse(
            {"choices": [{"delta": {"reasoning_content": "正在分析行情"}}]},
            {"choices": [{"delta": {"content": '{"action":'}}]},
            {"choices": [{"delta": {"content": '"等待"}'}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"total_tokens": 45}},
        )
        stream = ChunkResponse(body, self.clock, delay=0.01)
        with patch.object(lm.urllib.request, "urlopen", return_value=stream) as transport:
            content, reasoning, usage, latency = self.call(response_format={"type": "json_object"})
        self.assertEqual(json.loads(content), {"action": "等待"})
        self.assertEqual(reasoning, "正在分析行情")
        self.assertEqual(usage, {"total_tokens": 45})
        self.assertGreater(latency, 100)
        self.assertTrue(stream.closed)
        payload = json.loads(transport.call_args.args[0].data)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["reasoning_effort"], "high")

    def test_gateway_can_ignore_stream_and_return_json(self):
        with patch.object(lm.urllib.request, "urlopen", return_value=ChunkResponse(json.dumps(reply()).encode())):
            self.assertEqual(self.call()[0], "OK")

    def test_sse_with_split_utf8_bom_and_field_name_is_accepted(self):
        body = b"\xef\xbb\xbf" + sse({"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]})
        with patch.object(lm.urllib.request, "urlopen", return_value=ChunkResponse(body, chunk_size=1)):
            self.assertEqual(self.call()[0], "OK")

    def test_fenced_json_is_normalized_after_validation(self):
        with patch.object(lm.urllib.request, "urlopen", return_value=response(reply('```JSON\n{"action":"WAIT"}\n```'))):
            self.assertEqual(json.loads(self.call(response_format={"type": "json_object"})[0]), {"action": "WAIT"})

    def test_primary_over_150_seconds_still_completes(self):
        def transport(req, timeout):
            self.assertEqual(json.loads(req.data)["model"], "qwen3.8-flash")
            self.assertEqual(timeout, 300)
            self.clock.tick(180)
            return response(reply("COMPLETE"))
        with patch.object(lm.urllib.request, "urlopen", side_effect=transport) as mock_open:
            self.assertEqual(self.call()[0], "COMPLETE")
        self.assertEqual(mock_open.call_count, 1)

    def test_primary_timeout_leaves_full_backup_budget(self):
        timeouts = []
        def transport(req, timeout):
            timeouts.append(timeout)
            if len(timeouts) == 1:
                self.clock.tick(timeout)
                raise socket.timeout("timed out")
            self.clock.tick(180)
            return response(reply("BACKUP"))
        with patch.object(lm.urllib.request, "urlopen", side_effect=transport):
            self.assertEqual(self.call()[0], "BACKUP")
        self.assertEqual(timeouts, [300, 300])

    def test_body_timeout_closes_response_and_moves_to_backup(self):
        broken = response(reply())
        broken.read1 = Mock(side_effect=socket.timeout("body stalled"))
        with patch.object(lm.urllib.request, "urlopen", side_effect=[broken, response(reply("BACKUP"))]) as transport:
            self.assertEqual(self.call()[0], "BACKUP")
        self.assertTrue(broken.closed)
        self.assertEqual([json.loads(c.args[0].data)["model"] for c in transport.call_args_list],
                         ["qwen3.8-flash", "qwen3.8-max"])
        self.assertIn("读取响应体超时", self.events.call_args.args[0]["errors"][0])

    def test_heartbeats_cannot_extend_total_read_deadline(self):
        body = sse({"choices": [{"delta": {"reasoning_content": "thinking"}}]}, done=False)
        stream = ChunkResponse(body + b": ping\n\n" * 100, self.clock, delay=2, chunk_size=len(body))
        with patch.object(lm.urllib.request, "urlopen", return_value=stream) as transport:
            with self.assertRaisesRegex(TimeoutError, "已收到思考内容"):
                self.call(timeout=5, allow_fallback=False)
        self.assertEqual(transport.call_count, 1)
        self.assertTrue(stream.closed)
        self.assertEqual([c.args[0] for c in stream.socket.settimeout.call_args_list], [5, 3, 1])

    def test_response_headers_and_body_share_the_same_deadline(self):
        stream = ChunkResponse(json.dumps(reply()).encode(), self.clock, delay=3)
        def transport(req, timeout):
            self.clock.tick(4)
            return stream
        with patch.object(lm.urllib.request, "urlopen", side_effect=transport):
            with self.assertRaises(TimeoutError):
                self.call(timeout=5, allow_fallback=False)
        self.assertEqual(stream.socket.settimeout.call_args.args[0], 1)

    def test_parameter_adaptation_preserves_effort_and_response_format(self):
        seen = []
        def transport(req, timeout):
            payload = json.loads(req.data)
            seen.append((payload, timeout))
            if len(seen) == 1:
                self.clock.tick(4)
                raise urllib.error.HTTPError(req.full_url, 400, "unsupported", {}, io.BytesIO(b"stream_options unsupported"))
            return response(reply('{"action":"WAIT"}'))
        with patch.object(lm.urllib.request, "urlopen", side_effect=transport):
            self.call(timeout=10, response_format={"type": "json_object"})
        first, second = seen
        self.assertEqual(second[0], {k: v for k, v in first[0].items() if k != "stream_options"})
        self.assertEqual([first[1], second[1]], [10, 6])

    def test_rejected_stream_can_use_json_within_original_budget(self):
        calls = []
        def transport(req, timeout):
            payload = json.loads(req.data)
            calls.append(payload)
            if payload.get("stream"):
                raise urllib.error.HTTPError(req.full_url, 400, "unsupported", {}, io.BytesIO(b"stream is not supported"))
            return response(reply())
        with patch.object(lm.urllib.request, "urlopen", side_effect=transport):
            self.assertEqual(self.call()[0], "OK")
        self.assertEqual(len(calls), 2)
        self.assertNotIn("stream_options", calls[1])
        self.assertEqual(calls[1]["reasoning_effort"], "high")

    def test_timeout_during_parameter_adaptation_is_not_retried_on_same_model(self):
        calls = []
        def transport(req, timeout):
            calls.append(json.loads(req.data)["model"])
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 400, "unsupported", {}, io.BytesIO(b"reasoning_effort unsupported"))
            if len(calls) == 2:
                self.clock.tick(timeout)
                raise socket.timeout("stalled after adaptation")
            return response(reply("BACKUP"))
        with patch.object(lm.urllib.request, "urlopen", side_effect=transport):
            self.assertEqual(self.call()[0], "BACKUP")
        self.assertEqual(calls, ["qwen3.8-flash", "qwen3.8-flash", "qwen3.8-max"])

    def test_unusable_answers_are_retried_before_returning(self):
        bad_answers = [
            reply(None),
            {"choices": [{"message": {"content": None, "reasoning_content": "still thinking"}}]},
            reply('{"action":'),
            reply('{"action":"WAIT"}', finish_reason="length"),
            {"error": {"message": "upstream failed"}},
            {"choices": []},
        ]
        for bad in bad_answers:
            with self.subTest(bad=bad), patch.object(lm.urllib.request, "urlopen", side_effect=[
                response(bad), response(reply('{"action":"WAIT"}')),
            ]) as transport:
                self.assertEqual(json.loads(self.call(response_format={"type": "json_object"})[0]), {"action": "WAIT"})
                self.assertEqual(transport.call_count, 2)

    def test_interrupted_sse_is_retried_even_with_parseable_partial_content(self):
        body = sse({"choices": [{"delta": {"content": '{"action":"WAIT"}'}}]}, done=False)
        with patch.object(lm.urllib.request, "urlopen", side_effect=[io.BytesIO(body), response(reply("COMPLETE"))]) as transport:
            self.assertEqual(self.call()[0], "COMPLETE")
        self.assertEqual(transport.call_count, 2)

    def test_incomplete_http_body_is_retried(self):
        broken = response(reply())
        broken.read1 = Mock(side_effect=http.client.IncompleteRead(b"partial"))
        with patch.object(lm.urllib.request, "urlopen", side_effect=[broken, response(reply("COMPLETE"))]):
            self.assertEqual(self.call()[0], "COMPLETE")
        self.assertTrue(broken.closed)

    def test_exhausted_candidate_retry_budget_does_not_skip_backup(self):
        calls = []
        def transport(req, timeout):
            calls.append(json.loads(req.data)["model"])
            if len(calls) == 1:
                self.clock.tick(timeout - 1)
                raise urllib.error.URLError("connection reset")
            return response(reply("BACKUP"))
        with patch.object(lm.urllib.request, "urlopen", side_effect=transport):
            self.assertEqual(self.call(timeout=10)[0], "BACKUP")
        self.assertEqual(calls, ["qwen3.8-flash", "qwen3.8-max"])

    def test_explicit_chain_cap_reports_unattempted_fallback(self):
        def transport(req, timeout):
            self.clock.tick(timeout)
            raise socket.timeout("stalled")
        with patch.object(lm, "FAILOVER_MAX_TOTAL_WAIT", 200), patch.object(lm.urllib.request, "urlopen", side_effect=transport) as mock_open:
            with self.assertRaisesRegex(TimeoutError, "部分备用模型未执行"):
                self.call()
        self.assertEqual(mock_open.call_count, 1)
        self.assertEqual(self.events.call_args.args[0]["attempted_models"], ["qwen3.8-flash"])

    def test_http_failure_then_timeout_is_not_reported_as_all_timeouts(self):
        with patch.object(lm.urllib.request, "urlopen", side_effect=[
            urllib.error.HTTPError("https://gateway.example", 401, "denied", {}, io.BytesIO(b"invalid api key")),
            socket.timeout("stalled"),
        ]):
            with self.assertRaisesRegex(RuntimeError, "模型链全部失败"):
                self.call()

    def test_non_streaming_protocols_still_parse_complete_json_answers(self):
        for protocol, payload in (
            ("openai_responses", {"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"action":"WAIT"}'}]}]}),
            ("claude_messages", {"content": [{"type": "text", "text": '{"action":"WAIT"}'}], "stop_reason": "end_turn"}),
        ):
            with self.subTest(protocol=protocol), patch.object(lm.urllib.request, "urlopen", return_value=response(payload)):
                self.assertEqual(json.loads(self.call(api_format=protocol, response_format={"type": "json_object"})[0]), {"action": "WAIT"})


class LLMSocketDeadlineTests(unittest.TestCase):
    def test_real_chunked_stream_cannot_keep_request_alive_with_heartbeats(self):
        stop = threading.Event()

        class Gateway(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                chunk = sse({"choices": [{"delta": {"reasoning_content": "still thinking"}}]}, done=False)
                try:
                    while not stop.is_set():
                        self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                        stop.wait(0.03)
                        chunk = b": heartbeat\n\n"
                except (OSError, ConnectionError):
                    pass
                finally:
                    self.close_connection = True

        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        runtime = {
            "model": "qwen3.8-flash", "base_url": f"http://127.0.0.1:{server.server_port}/v1",
            "reasoning_type": "standard_effort", "request_attempts": 3,
        }
        # Bypass machine proxy settings for this loopback-only gateway.
        transport = lm.urllib.request.build_opener(lm.urllib.request.ProxyHandler({})).open
        try:
            with patch.object(lm, "get_active_llm_runtime", return_value=runtime), \
                    patch.object(lm.urllib.request, "urlopen", side_effect=transport) as mock_open, \
                    patch.object(lm, "record_failover_event"), patch.object(lm.analysis_capture, "emit"):
                started = time.perf_counter()
                with self.assertRaisesRegex(TimeoutError, "已收到思考内容"):
                    lm.execute_llm_request([{"role": "user", "content": "fixture"}], timeout=0.3, allow_fallback=False)
                elapsed = time.perf_counter() - started
                self.assertEqual(mock_open.call_count, 1)
                self.assertGreaterEqual(elapsed, 0.25)
                self.assertLess(elapsed, 1.5)
        finally:
            stop.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

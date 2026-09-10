"""Standalone scheduler retry and per-job timeout tests."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import subprocess
import unittest
from unittest.mock import patch

import r20_backend.scheduler as scheduler

BJ = timezone(timedelta(hours=8))


class RunIntervalJobTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 1, 18, 0, tzinfo=BJ)

    def test_failed_interval_job_retries_once_in_same_slot(self):
        last = {"trader": self.now - timedelta(seconds=900)}
        retry: dict[str, float] = {}
        with patch.object(scheduler, "run_script", return_value=False) as run:
            self.assertTrue(scheduler.run_interval_job("trader", 900, self.now, last, retry))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(retry["trader"], self.now.timestamp() + 60)

            # Retry is pending but not due yet: nothing launches.
            self.assertFalse(scheduler.run_interval_job("trader", 900, self.now + timedelta(seconds=30), last, retry))
            self.assertEqual(run.call_count, 1)

            # 60s later the retry fires once.
            self.assertTrue(scheduler.run_interval_job("trader", 900, self.now + timedelta(seconds=60), last, retry))
            self.assertEqual(run.call_count, 2)
            self.assertNotIn("trader", retry)

            # A second failure must not schedule another retry in the same slot.
            self.assertFalse(scheduler.run_interval_job("trader", 900, self.now + timedelta(seconds=61), last, retry))
            self.assertFalse(scheduler.run_interval_job("trader", 900, self.now + timedelta(seconds=300), last, retry))
            self.assertEqual(run.call_count, 2)
            self.assertNotIn("trader", retry)

    def test_successful_interval_job_is_never_retried(self):
        last = {"news": self.now - timedelta(seconds=600)}
        retry: dict[str, float] = {}
        with patch.object(scheduler, "run_script", return_value=True) as run:
            self.assertTrue(scheduler.run_interval_job("news", 600, self.now, last, retry))
            self.assertEqual(retry, {})
            self.assertFalse(scheduler.run_interval_job("news", 600, self.now + timedelta(seconds=60), last, retry))
            self.assertFalse(scheduler.run_interval_job("news", 600, self.now + timedelta(seconds=120), last, retry))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(retry, {})

    def test_nothing_launches_before_interval_without_pending_retry(self):
        last = {"trader": self.now}
        retry: dict[str, float] = {}
        with patch.object(scheduler, "run_script") as run:
            self.assertFalse(scheduler.run_interval_job("trader", 900, self.now + timedelta(seconds=30), last, retry))
            run.assert_not_called()
            self.assertEqual(retry, {})

    def test_elapsed_interval_becomes_normal_due_run_not_a_retry(self):
        last = {"trader": self.now}
        retry = {"trader": (self.now + timedelta(seconds=60)).timestamp()}
        later = self.now + timedelta(seconds=900)
        with patch.object(scheduler, "run_script", return_value=False) as run:
            self.assertTrue(scheduler.run_interval_job("trader", 900, later, last, retry))
            self.assertEqual(run.call_count, 1)
            self.assertEqual(last["trader"], later)
            # It ran as a normal due launch, so a fresh retry is scheduled for it.
            self.assertEqual(retry["trader"], later.timestamp() + 60)


class RunScriptTimeoutTests(unittest.TestCase):
    def test_run_script_returns_false_on_timeout(self):
        expired = subprocess.TimeoutExpired(cmd="ai_factor_trader.py", timeout=840)
        with patch("r20_backend.scheduler.subprocess.run", side_effect=expired):
            self.assertFalse(scheduler.run_script("trader"))

    def test_run_script_uses_per_job_timeout(self):
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        with patch("r20_backend.scheduler.subprocess.run", return_value=completed) as run:
            self.assertTrue(scheduler.run_script("trader"))
            self.assertEqual(run.call_args.kwargs["timeout"], scheduler.JOB_TIMEOUTS["trader"])
            self.assertTrue(scheduler.run_script("factor_library"))
            self.assertEqual(run.call_args.kwargs["timeout"], scheduler.JOB_TIMEOUTS["factor_library"])


if __name__ == "__main__":
    unittest.main()

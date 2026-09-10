"""Standalone process scheduler for R20 maintenance jobs.

It owns scheduling but deliberately invokes existing scripts as isolated processes,
which preserves each script's file lock and fail-closed behavior.
"""
from __future__ import annotations
import fcntl_compat as fcntl
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from r20_backend.schedule_store import load_schedule
except ModuleNotFoundError:
    from schedule_store import load_schedule

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "data"
LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)
DATA.mkdir(exist_ok=True)

# Held exclusively by r20_gateway.worker while the gateway scheduler is alive.
GATEWAY_LOCK = DATA / ".r20_gateway.lock"

logging.basicConfig(
    filename=LOGS / "r20_scheduler.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

JOBS = {
    "trader": ("ai_factor_trader.py", 15 * 60),
    "factor_library": ("factor_library.py", 60),
    "news": ("news_sentiment_harvester.py", 10 * 60),
    "daily_briefing": ("daily_summary_and_backup.py", None),
    "self_improvement": ("self_improvement_engine.py", None),
    "nightly_backup": ("nightly_backup_and_clean.py", None),
}


JOB_TIMEOUTS = {
    "trader": 840,
    "factor_library": 55,
    "news": 300,
    "daily_briefing": 600,
    "self_improvement": 1200,
    "nightly_backup": 1800,
}


def gateway_scheduler_running() -> bool:
    """Return True when the gateway worker holds its exclusive lock (i.e. is alive)."""
    with GATEWAY_LOCK.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return False


def run_script(name: str) -> bool:
    script = SCRIPTS / JOBS[name][0]
    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    child_env["PYTHONIOENCODING"] = "utf-8"
    timeout = JOB_TIMEOUTS.get(name, 600)
    try:
        result = subprocess.run([sys.executable, str(script)], cwd=ROOT, env=child_env, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        logging.error("job=%s timeout=%ss expired; killed", name, timeout)
        return False
    if result.returncode:
        logging.error("job=%s rc=%s stderr=%s", name, result.returncode, result.stderr[-1000:])
    else:
        logging.info("job=%s completed stdout=%s", name, result.stdout[-500:])
    return result.returncode == 0


def run_interval_job(name: str, interval: float, now: datetime, last: dict[str, datetime | None], retry: dict[str, float]) -> bool:
    """Launch an interval job when due, retrying a failure once 60s later in the same slot.

    ``last`` and ``retry`` are mutated in place, and ``now`` is injected so the helper
    stays deterministic. Returns True when a job was launched.
    """
    previous = last.get(name)
    interval_elapsed = previous is None or (now - previous).total_seconds() >= interval
    retry_at = retry.get(name)
    is_retry = not interval_elapsed and retry_at is not None and now.timestamp() >= retry_at
    if not interval_elapsed and not is_retry:
        return False
    retry.pop(name, None)
    if is_retry:
        logging.warning("job=%s retrying after failure", name)
    succeeded = run_script(name)
    last[name] = now
    if not succeeded and not is_retry:
        logging.warning("job=%s failed; scheduling retry in 60s", name)
        retry[name] = now.timestamp() + 60
    elif not succeeded:
        logging.warning("job=%s retry failed; waiting for next interval slot", name)
    return True


def due_daily(now: datetime, schedule_time: str, last_run: datetime | None) -> bool:
    try:
        hour, minute = [int(part) for part in schedule_time.split(":", 1)]
    except (TypeError, ValueError):
        return False
    if (now.hour, now.minute) != (hour, minute):
        return False
    return not last_run or last_run.date() != now.date() or (last_run.hour, last_run.minute) != (hour, minute)


def main() -> None:
    lock_path = DATA / ".r20_scheduler.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("R20 standalone scheduler already running")

        tz = timezone(timedelta(hours=8))
        last: dict[str, datetime | None] = {key: None for key in JOBS}
        retry: dict[str, float] = {}
        gateway_active = False
        logging.info("R20 standalone scheduler v6.6.2 started")
        while True:
            # The gateway scheduler owns these jobs; defer while it is alive
            # so jobs like ai_factor_trader.py do not fire twice.
            if gateway_scheduler_running():
                if not gateway_active:
                    logging.info("gateway scheduler holds %s; deferring all jobs to it", GATEWAY_LOCK.name)
                    gateway_active = True
                time.sleep(30)
                continue
            if gateway_active:
                logging.info("gateway scheduler lock released; resuming standalone scheduling")
                gateway_active = False
            now = datetime.now(tz).replace(second=0, microsecond=0)
            current = datetime.now(tz)
            run_interval_job("trader", 15 * 60, current, last, retry)
            run_interval_job("factor_library", 60, current, last, retry)
            run_interval_job("news", 10 * 60, current, last, retry)
            schedule = load_schedule()
            briefing_times = schedule.get("briefing_times", ["08:00", "20:00"])
            if any(due_daily(now, schedule_time, last["daily_briefing"]) for schedule_time in briefing_times):
                run_script("daily_briefing")
                last["daily_briefing"] = now
            if due_daily(now, schedule.get("self_improvement_time", "20:00"), last["self_improvement"]):
                run_script("self_improvement")
                last["self_improvement"] = now
            if due_daily(now, schedule.get("backup_time", "02:00"), last["nightly_backup"]):
                run_script("nightly_backup")
                last["nightly_backup"] = now
            time.sleep(5)


if __name__ == "__main__":
    main()

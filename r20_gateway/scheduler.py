"""Gateway-owned scheduler running existing jobs in isolated subprocesses."""
from __future__ import annotations
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from r20_backend.schedule_store import load_schedule
from r20_backend.backup_store import list_jobs as list_backup_jobs
from r20_gateway.store import GatewayStore

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
BJ_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class JobSpec:
    name: str
    script: str
    interval_seconds: int | None = None
    timeout_seconds: int = 600
    schedule_key: str = ""
    default_times: tuple[str, ...] = ()
    offset_seconds: int = 0


JOBS = (
    JobSpec("trader", "ai_factor_trader.py", 15 * 60, 840),
    JobSpec("factor_library", "factor_library.py", 60, 55),
    JobSpec("news", "news_sentiment_harvester.py", 10 * 60, 300, offset_seconds=180),
    JobSpec("daily_briefing", "daily_summary_and_backup.py", None, 600, "briefing_times", ("08:00", "20:00")),
    JobSpec("self_improvement", "self_improvement_engine.py", None, 1200, "self_improvement_times", ("02:00", "08:00", "14:00", "20:00")),
    JobSpec("disk_cleanup", "cleanup_disk.py", 3600, 300),
)


def backup_job_specs() -> tuple[JobSpec, ...]:
    specs: list[JobSpec] = []
    for index, job in enumerate(list_backup_jobs()):
        if not job.get("enabled"):
            continue
        name = "nightly_backup" if index == 0 or job.get("id") == "nightly-default" else f"backup:{job['id']}"
        specs.append(JobSpec(name, "nightly_backup_and_clean.py", None, 1800, f"backup_job:{job['id']}", tuple(job.get("schedule_times", ["02:00"]))))
    return tuple(specs)


def current_jobs() -> tuple[JobSpec, ...]:
    return (*JOBS, *backup_job_specs())


def _resolve_times(spec: JobSpec, schedule: dict[str, Any]) -> tuple[str, ...]:
    if spec.schedule_key.startswith("backup_job:"):
        return spec.default_times
    value = schedule.get(spec.schedule_key)
    # Also check fallback keys if list key not found
    if value is None and spec.schedule_key == "self_improvement_times":
        value = schedule.get("self_improvement_time")
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    if isinstance(value, str):
        return (value,)
    return spec.default_times


def _schedule_text(spec: JobSpec, schedule: dict[str, Any]) -> str:
    if spec.interval_seconds and spec.offset_seconds:
        return f"每 {spec.interval_seconds // 60} 分钟 (错峰 +{spec.offset_seconds // 60}m)"
    if spec.interval_seconds:
        return f"每 {spec.interval_seconds // 60} 分钟"
    return "、".join(_resolve_times(spec, schedule))


def scheduler_snapshot(store: GatewayStore, running: dict[str, Future[None]] | None = None) -> dict[str, Any]:
    schedule = load_schedule()
    now = datetime.now(BJ_TZ)
    jobs = []
    for spec in current_jobs():
        raw = store.get_state(f"job.last.{spec.name}")
        try:
            last = datetime.fromisoformat(raw) if raw else None
        except ValueError:
            last = None
        job: dict[str, Any] = {
            "name": spec.name,
            "script": spec.script,
            "last_scheduled_at": last.isoformat() if last else "",
            "schedule": _schedule_text(spec, schedule),
            "timezone": "Asia/Shanghai",
            "overdue": bool(spec.interval_seconds and last and (now - last).total_seconds() > spec.interval_seconds * 2),
            "offset_seconds": spec.offset_seconds,
        }
        if running is not None:
            job["running"] = spec.name in running and not running[spec.name].done()
        jobs.append(job)
    return {"jobs": jobs, "recent_runs": store.job_runs(30)}


class GatewayScheduler:
    def __init__(self, store: GatewayStore, max_workers: int = 3):
        self.store = store
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="r20-job")
        self.running: dict[str, Future[bool]] = {}
        self.retry_slots: dict[str, int] = {}
        self.retried_slots: dict[str, int] = {}
        self.retry_after: dict[str, datetime] = {}

    def _last_at(self, name: str) -> datetime | None:
        raw = self.store.get_state(f"job.last.{name}")
        try:
            return datetime.fromisoformat(raw) if raw else None
        except ValueError:
            return None

    def initialize_migration_baseline(self, now: datetime | None = None) -> None:
        now = now or datetime.now(BJ_TZ)
        for spec in current_jobs():
            if not self.store.get_state(f"job.last.{spec.name}"):
                self.store.set_state(f"job.last.{spec.name}", now.isoformat())

    def _scheduled_times(self, spec: JobSpec, schedule: dict[str, Any]) -> tuple[str, ...]:
        return _resolve_times(spec, schedule)

    def due(self, spec: JobSpec, now: datetime, schedule: dict[str, Any]) -> bool:
        last = self._last_at(spec.name)
        if spec.interval_seconds:
            if spec.name == "trader":
                slot = int(now.timestamp()) // spec.interval_seconds
                last_slot = int(last.timestamp()) // spec.interval_seconds if last else -1
                return slot > last_slot
            if spec.offset_seconds:
                # Staggered execution aligned to clock with offset to prevent resource collisions
                ts = int(now.timestamp())
                slot = (ts - spec.offset_seconds) // spec.interval_seconds
                last_slot = (int(last.timestamp()) - spec.offset_seconds) // spec.interval_seconds if last else -1
                sec_in_slot = (ts - spec.offset_seconds) % spec.interval_seconds
                return slot > last_slot and sec_in_slot < 30
            return not last or (now - last).total_seconds() >= spec.interval_seconds
        minute = now.strftime("%H:%M")
        if minute not in self._scheduled_times(spec, schedule):
            return False
        return not last or last.date() != now.date() or last.strftime("%H:%M") != minute

    def _slot(self, spec: JobSpec, timestamp: datetime) -> int | None:
        if not spec.interval_seconds:
            return None
        return (int(timestamp.timestamp()) - spec.offset_seconds) // spec.interval_seconds

    def _execute(self, spec: JobSpec) -> bool:
        run_id = self.store.begin_job(spec.name)
        try:
            command = [sys.executable, str(SCRIPTS / spec.script)]
            if spec.schedule_key.startswith("backup_job:"):
                command.extend(["--job-id", spec.schedule_key.split(":", 1)[1]])
            child_env = os.environ.copy()
            child_env["PYTHONUTF8"] = "1"
            child_env["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=child_env,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=spec.timeout_seconds,
            )
            detail = (result.stderr if result.returncode else result.stdout)[-2000:]
            self.store.finish_job(run_id, result.returncode, detail)
            return result.returncode == 0
        except subprocess.TimeoutExpired as exc:
            self.store.finish_job(run_id, 124, f"timeout after {spec.timeout_seconds}s: {exc}")
            return False
        except Exception as exc:
            self.store.finish_job(run_id, 1, f"{type(exc).__name__}: {exc}")
            return False

    def tick(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(BJ_TZ)
        specs_by_name = {spec.name: spec for spec in current_jobs()}
        finished: dict[str, Future[bool]] = {
            name: future for name, future in self.running.items() if future.done()
        }
        for name, future in finished.items():
            spec = specs_by_name.get(name)
            self.running.pop(name, None)
            if not spec or not spec.interval_seconds:
                continue
            try:
                succeeded = bool(future.result())
            except Exception:
                succeeded = False
            if succeeded:
                continue
            last_launch = self._last_at(name)
            launch_slot = self._slot(spec, last_launch) if last_launch else None
            if launch_slot is not None and self.retried_slots.get(name) != launch_slot:
                self.retry_slots[name] = launch_slot
                self.retry_after[name] = now + timedelta(seconds=60)
        schedule = load_schedule()
        launched: list[str] = []
        for spec in current_jobs():
            if spec.name in self.running:
                continue
            slot = self._slot(spec, now)
            retry_due = (
                slot is not None
                and self.retry_slots.get(spec.name) == slot
                and now >= self.retry_after.get(spec.name, now + timedelta(days=1))
            )
            if retry_due:
                self.retry_slots.pop(spec.name, None)
                self.retry_after.pop(spec.name, None)
                self.retried_slots[spec.name] = slot
            elif not self.due(spec, now, schedule):
                continue
            elif slot is not None:
                self.retried_slots.pop(spec.name, None)
            future = self.executor.submit(self._execute, spec)
            self.store.set_state(f"job.last.{spec.name}", now.isoformat())
            self.running[spec.name] = future
            launched.append(spec.name)
        return launched

    def status(self) -> dict[str, Any]:
        return scheduler_snapshot(self.store, self.running)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

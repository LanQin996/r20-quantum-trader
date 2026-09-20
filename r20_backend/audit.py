"""Append-only audit log for all authenticated admin actions."""
from __future__ import annotations
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
AUDIT_FILE = ROOT / "logs" / "r20_admin_audit.jsonl"


def _audit_file() -> Path:
    """调用时解析（审计卫生修复）：此前模块级绑定路径不可重定向，全部
    TestClient 后台测试把伪造记录直写生产 logs/r20_admin_audit.jsonl（实测
    >2200 条 testclient 污染）。R20_AUDIT_FILE 环境变量覆盖 + tests/__init__.py
    统一隔离到临时目录；生产默认路径不变。"""
    override = os.environ.get("R20_AUDIT_FILE")
    return Path(override) if override else AUDIT_FILE


def record(action: str, status: str, detail: dict[str, Any] | None = None,
           ip: str | None = None, user_agent: str | None = None) -> None:
    """写入一条审计记录。

    ip / user_agent 为新增可观测性字段：此前审计只有 timestamp/action/status/detail，
    导致「7000+ 次成功登录」这类异常完全无法归因来源。
    """
    target = _audit_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone(timedelta(hours=8)))
    payload = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "status": status,
        "detail": detail or {},
    }
    if ip:
        payload["actor_ip"] = str(ip)[:64]
    if user_agent:
        payload["user_agent"] = str(user_agent)[:200]
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def recent(limit: int = 50) -> list[dict[str, Any]]:
    target = _audit_file()
    if not target.exists():
        return []
    capped = max(1, min(limit, 200))
    # Read backwards from EOF in blocks instead of loading the whole file.
    block_size = 65536
    data = b""
    with target.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        while position > 0 and data.count(b"\n") <= capped:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            data = handle.read(read_size) + data
    lines = data.decode("utf-8", errors="replace").splitlines()[-capped:]
    records: list[dict[str, Any]] = []
    for line in reversed(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records

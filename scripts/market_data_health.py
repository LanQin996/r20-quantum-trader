"""行情取数的**失败计数 + 一次性告警**（只做可观测性，不改变任何取值行为）。

## 为什么需要它（真实事故，2026-09-15）

`scripts/brain/packages.py` 里 6 处取数各自包着**静默** `except Exception: pass`。
当抽取子模块时漏带模块级 `import json` / `import urllib.request`，`NameError` 被这些
`except` 全部吞掉：函数照常返回、现价恒为 0、`data_quality` 恒为 `invalid` ⇒ 主脑
P0 数据有效性拦截、**约 30 小时没有开新仓**，而整个过程**零日志零信号**。
（详见 `plan_local/` 台账 §136。）

本模块只做两件事：**计数**与**每种失败只吭一声**。

## 语义边界（务必保持）

- `note_failure()` **绝不抛异常**（内部整体 try/except）—— 它本身不能成为新的故障源；
- 不改变调用方的返回值/控制流：调用点仍是 `except ...: note_failure(...)`，
  **继续吞掉异常**（这是原有设计：单点取数失败不影响整包装配）；
- "一次性"= **每个 kind 每进程只打印一次**，后续只累加计数。
  主脑每 15 分钟起一个新进程 ⇒ 故障期间每轮至少一条日志，但不会刷屏；
- 计数用锁保护（同一进程多线程调用安全）；`stats()` 返回**纯副本**，调用方可安全序列化。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

_LOCK = threading.Lock()
_FAILURES: Dict[str, int] = {}
_LOGGED: set[str] = set()
_LAST_ERROR: Dict[str, str] = {}

__all__ = ["note_failure", "stats", "reset", "failure_count"]


def note_failure(kind: str, exc: Optional[BaseException] = None) -> None:
    """记一次取数失败：累加计数；该 kind **首次**失败时打印一条告警。

    绝不抛异常：本函数自身任何意外都被吞掉（它不能变成新的故障源）。
    """
    # ① 计数：**独立兜底且最先执行** —— 计数是事故取证的关键，
    #    绝不允许因为"格式化异常信息失败"连计数一起丢掉（门里的 `__str__` 会抛
    #    的异常用例逼出了这一点：原实现把格式化放在计数之前，detail 一炸就整条丢失）。
    try:
        with _LOCK:
            _FAILURES[kind] = _FAILURES.get(kind, 0) + 1
            count = _FAILURES[kind]
            first = kind not in _LOGGED
            if first:
                _LOGGED.add(kind)
    except Exception:
        return
    # ② 详情与一次性告警：同样各自兜底，失败只影响"记录得详不详细"。
    try:
        detail = f"{type(exc).__name__}: {exc}" if exc is not None else "未知错误"
        with _LOCK:
            _LAST_ERROR[kind] = detail
        if first:
            print(f"[行情取数] ⚠️ {kind} 取数失败（第 {count} 次，后续只累加不再重复打印）: {detail}",
                  flush=True)
    except Exception:
        pass


def failure_count(kind: str) -> int:
    """某个 kind 的累计失败次数（0 表示没失败过）。"""
    with _LOCK:
        return _FAILURES.get(kind, 0)


def stats() -> Dict[str, Any]:
    """失败统计快照（纯副本，可安全 JSON 化）：

    ``{"total": 3, "by_kind": {"okx_ticker": 2, ...}, "last_error": {...}}``
    """
    with _LOCK:
        by_kind = dict(_FAILURES)
        return {
            "total": sum(by_kind.values()),
            "by_kind": by_kind,
            "last_error": dict(_LAST_ERROR),
        }


def reset() -> None:
    """清空计数与"已打印"标记（供测试与进程内复用）。"""
    with _LOCK:
        _FAILURES.clear()
        _LOGGED.clear()
        _LAST_ERROR.clear()

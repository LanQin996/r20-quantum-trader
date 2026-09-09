#!/usr/bin/env python3
"""
R20 Authentic OKX Positions-History Ledger Synchronizer (sync_full_ledger.py)
Directly reads OKX official `account positions-history` & `account positions` API.
Eliminates bills heuristic split-error, accurately records real position-level trades!
"""

from okx_runtime import replace_cli_prefix as okx_private_command
import subprocess
import json
import os
import datetime
import tempfile

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
INITIAL_STATE_FILE = os.path.join(DATA_DIR, "account_initial_state.json")
POSITION_TRACKER_FILE = os.path.join(DATA_DIR, "position_trackers.json")

from instrument_pool import load_instruments

TARGET_INSTRUMENTS = load_instruments()

# 历史币种白名单缓存：交易所规格回退查询用（进程内一次即可）
_CTVAL_CACHE = {}

def _sqlite_traded_names():
    """SQLite 台账里出现过的币种名（已下架币种的历史事实源）。"""
    names = set()
    try:
        import sqlite3
        db = os.path.join(DATA_DIR, "r20_quant.db")
        if os.path.exists(db):
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for (inst,) in con.execute("SELECT DISTINCT inst FROM trades"):
                if inst:
                    names.add(str(inst))
            con.close()
    except Exception:
        pass
    return names

def allowed_inst_ids(existing_ledger_trades=None):
    """台账重建允许集合 = 当前标的池 ∪ 历史留痕币种。

    修复(2026-09-09)：此前重建仅认当前池，用户从池中删除币种后，下一次同步
    会把该币种的全部已平仓历史从 trading_ledger.json 抹掉（SQLite 仍在，但页面
    台账消失）；持仓中途删币还会让在途仓位在台账里隐身。历史是交易所事实，
    不随池配置消亡；噪声过滤（拦截 R20 从未交易过的手动单）由并集继续保证。
    """
    allowed = {item["instId"] for item in TARGET_INSTRUMENTS}
    names = _sqlite_traded_names()
    for t in (existing_ledger_trades or []):
        inst = str(t.get("inst") or t.get("name") or "")
        if inst:
            names.add(inst)
    try:
        if os.path.exists(POSITION_TRACKER_FILE):
            with open(POSITION_TRACKER_FILE, "r", encoding="utf-8") as f:
                for key in json.load(f):
                    inst = str(key).rsplit("_", 1)[0]
                    if inst:
                        names.add(inst)
    except Exception:
        pass
    for n in names:
        n = n.strip()
        if not n:
            continue
        allowed.add(n if "-USDT-SWAP" in n or "-USD-SWAP" in n else f"{n}-USDT-SWAP")
    return allowed

def get_ct_val(inst_name):
    for item in TARGET_INSTRUMENTS:
        if item["name"] == inst_name or item["instId"] == inst_name:
            return item["ctVal"]
    # 已下架币种回退：查 OKX 公共合约规格（免费、无需鉴权），进程内缓存
    inst_id = inst_name if "-SWAP" in inst_name else f"{inst_name}-USDT-SWAP"
    if inst_id in _CTVAL_CACHE:
        return _CTVAL_CACHE[inst_id]
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId={inst_id}",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        rows = payload.get("data") or []
        ct = float(rows[0].get("ctVal", 1.0) or 1.0) if rows else 1.0
    except Exception:
        ct = 1.0
    _CTVAL_CACHE[inst_id] = ct
    return ct

def build_lifecycle_ledger():
    """Publish a compatibility ledger from durable account-isolated history."""
    import sys
    from pathlib import Path
    if WORKSPACE_DIR not in sys.path:
        sys.path.insert(0, WORKSPACE_DIR)
    from r20_backend.analysis_capture import enabled, identity, recover_legacy, fault
    from r20_backend.analysis_sync import sync_archive
    from r20_backend.analysis_store import Archive, timestamp_ms
    from r20_backend.okx_trade_service import _request
    from scripts.okx_runtime import selected_environment

    if not enabled():
        return []
    env = selected_environment()
    recover_legacy()
    # A failed live-position query must not publish an empty/closed portfolio.
    positions = _request("GET", "/api/v5/account/positions", {"instType": "SWAP"}, env=env, timeout=8)
    previous_ids = set()
    if os.path.exists(LEDGER_JSON_FILE):
        with open(LEDGER_JSON_FILE, encoding="utf-8") as handle:
            previous_ids = {str(t.get("id")) for t in json.load(handle) if t.get("status") == "closed"}
    trades = sync_archive(positions)
    initial = {}
    if os.path.exists(INITIAL_STATE_FILE):
        with open(INITIAL_STATE_FILE, encoding="utf-8") as handle:
            initial = json.load(handle)
    reset_ms = timestamp_ms(initial.get("reset_time")) or 0
    output = []
    for trade in trades:
        if trade.get("status") == "closed" and (trade.get("close_ms") or 0) < reset_ms:
            continue
        t = dict(trade)
        t["lifecycle_status"] = t.get("status")
        t["side"] = {"long": "多", "short": "空"}.get(t.get("side"), t.get("side"))
        if t["status"] in ("partial", "unknown"):
            t["status"] = "holding"
        t["lever"] = f"{t.get('lever') or '--'}x"
        mins = ((t.get("close_ms") or 0) - (t.get("open_ms") or 0)) // 60000
        t["duration"] = f"{mins // 60}时{mins % 60}分" if mins >= 0 and t.get("close_ms") else "--"
        t["pnl"] = trade["net_pnl"] if trade.get("net_pnl") is not None else trade.get("known_net_pnl")
        t["risk_pnl_basis"] = trade.get("cost_basis") if trade.get("net_pnl") is not None else "legacy_gross_plus_fee"
        t["open_fee"] = None
        t["close_fee"] = None
        quantity, entry = t.get("sz"), t.get("open_px")
        spec = next((item for item in TARGET_INSTRUMENTS if item["instId"] == t.get("inst_id")), None)
        margin = None
        if spec and quantity and entry and trade.get("lever"):
            margin = abs(float(quantity)) * float(spec["ctVal"]) * float(entry) / float(trade["lever"])
        t["margin"] = float(trade["margin"]) if trade.get("margin") else margin
        t["margin_basis"] = "exchange" if trade.get("margin") else "notional_estimate" if margin else "unknown"
        t["roi_pct"] = float(trade["net_pnl"]) / t["margin"] * 100 if trade.get("net_pnl") is not None and t["margin"] else None
        if t["status"] == "holding":
            t["close_px"] = trade.get("mark_px")
            t["net_pnl"] = trade.get("unrealized_pnl")
            t["pnl_basis"] = "unrealized"
        output.append(t)
    fd, tmp_path = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, LEDGER_JSON_FILE)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
    if previous_ids:
        try:
            from qq_notifier import notify_trade_close
            for trade in output:
                if trade.get("status") == "closed" and trade["id"] not in previous_ids and trade.get("cost_complete"):
                    notify_trade_close(inst=trade.get("inst","CRYPTO"), pnl=float(trade["net_pnl"]),
                        stage=trade.get("exit_reason") or "平仓结清",
                        exit_px=float(trade.get("close_px") or 0), roi_pct=float(trade.get("roi_pct") or 0),
                        duration_str=trade.get("duration",""))
        except Exception as exc:
            print(f"[Ledger Sync Notify Warning] {exc}")
    print(f"Durable lifecycle ledger synced: {len(output)} visible / {len(trades)} archived")
    return output

if __name__ == "__main__":
    build_lifecycle_ledger()

"""Fixed post-extraction baselines for reviewed changes already merged before CI repair.

Only the functions listed here moved beyond their original extraction snapshot.
Keep the old baseline for every other function. Never read HEAD as an expectation.
Runtime behavior tests remain responsible for capture, closed candles, equity risk
budgets, Gate order normalization and decimal protection quantities.
"""
import ast
from functools import lru_cache
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
# 1940940 includes the v8.1/v8.1.1 merges and decimal OCO fix; it predates
# the export job feature and this CI repair.
REVISION = "1940940"
CHANGES = {
    ("scripts/brain/dispatch.py", "dispatch_llm_and_persist_decisions"): "analysis capture and initialized fallback content",
    ("scripts/brain/packages.py", "fetch_single_instrument_package"): "closed candles, data-quality checks and ticker latency",
    ("r20_backend/llm/store_normalize.py", "finalize_config_document"): "do not rewrite unchanged configuration",
    ("scripts/trader/circuit_guard.py", "is_circuit_breaker_active"): "captured daily-loss gate and equity-based wording",
    ("scripts/trader/cloud_protection.py", "amend_venue_stop_loss"): "Gate initial.text stop-loss identification",
    ("scripts/trader/cloud_protection.py", "_live_oco_coverage"): "decimal coverage summation",
    ("scripts/trader/cloud_protection.py", "ensure_cloud_position_protection"): "decimal missing coverage and order size",
    ("scripts/trader/cycle_stages.py", "scan_risk_gates_and_ai_brain"): "equity-based daily-loss budget",
    ("scripts/trader/cycle_stages.py", "fetch_positions_and_reconcile"): "shared account equity snapshot",
    ("scripts/trader/entry_execution.py", "execute_entry_scan"): "decision capture and lotSz/minSz sizing",
    ("scripts/trader/ledger_writer.py", "record_trade"): "trade and exit-reason capture",
    ("scripts/trader/order_lifecycle.py", "clean_stale_open_orders"): "Gate signed size and reduce-only normalization",
    ("scripts/trader/order_submit.py", "submit_protected_limit_order"): "execution capture and decimal order quantities",
    ("scripts/trader/position_exit.py", "manage_position_tp_and_trailing"): "position samples and exit attribution",
}


@lru_cache(maxsize=None)
def source(path):
    return subprocess.check_output(
        ["git", "show", f"{REVISION}:{path}"], cwd=ROOT, encoding="utf-8")


def accepted_function(path, name, original):
    if (path, name) not in CHANGES:
        return original
    return next(node for node in ast.parse(source(path)).body
                if isinstance(node, ast.FunctionDef) and node.name == name)

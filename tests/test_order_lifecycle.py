"""挂单生命周期：清扫阈值必须让 AI 的 KEEP 有意义。

背景（2026-09-10 实盘复盘）：`clean_stale_open_orders` 曾用 240s 硬阈值，而决策周期是
15 分钟，导致任何挂单在下一次清理必被机械撤销 —— 提示词授予模型的 KEEP 永远无效，
入场退化为"一个周期内不成交就撤单改价"，实测 20 张挂单 11 张未成交。
"""
from __future__ import annotations

import importlib
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

DECISION_CADENCE_SECONDS = 15 * 60


def order(ord_id: str, age_seconds: float, state: str = "live"):
    now_ms = int(time.time() * 1000)
    return {"instId": "LINK-USDT-SWAP", "ordId": ord_id, "state": state,
            "cTime": str(int(now_ms - age_seconds * 1000))}


class StaleOrderCleanupTests(unittest.TestCase):
    def setUp(self):
        self.trader = importlib.import_module("scripts.ai_factor_trader")

    def test_ttl_outlives_the_decision_cadence(self):
        # A KEEP can only mean anything if an order may survive a full cleanup cycle.
        self.assertGreater(self.trader.STALE_ORDER_TTL_SECONDS, DECISION_CADENCE_SECONDS)

    def test_cleanup_keeps_fresh_orders_and_cancels_expired_ones(self):
        orders = [order("expired", age_seconds=self.trader.STALE_ORDER_TTL_SECONDS + 60),
                  order("fresh", age_seconds=60)]
        cancelled = []

        def run(cmd, timeout=15):
            if "cancel" in cmd:
                cancelled.append(cmd)
                return {"ok": True, "returncode": 0, "stdout": "[]", "stderr": "", "data": []}
            return {"ok": True, "returncode": 0, "stdout": "[]", "stderr": "", "data": orders}

        with patch.object(self.trader, "run_cmd_result", side_effect=run):
            ok, note = self.trader.clean_stale_open_orders()
        self.assertTrue(ok, note)
        self.assertEqual(len(cancelled), 1)
        self.assertIn("expired", cancelled[0])
        self.assertNotIn("fresh", cancelled[0])

    def test_order_inside_the_new_ttl_is_left_alone(self):
        # 300s was already "stale" under the old 240s rule; it must now survive so the
        # model still gets its KEEP/CANCEL decision on it.
        orders = [order("survivor", age_seconds=300), order("idle", age_seconds=10, state="canceled")]
        cancelled = []

        def run(cmd, timeout=15):
            if "cancel" in cmd:
                cancelled.append(cmd)
                return {"ok": True, "returncode": 0, "stdout": "[]", "stderr": "", "data": []}
            return {"ok": True, "returncode": 0, "stdout": "[]", "stderr": "", "data": orders}

        with patch.object(self.trader, "run_cmd_result", side_effect=run):
            ok, _ = self.trader.clean_stale_open_orders()
        self.assertTrue(ok)
        self.assertEqual(cancelled, [])


if __name__ == "__main__":
    unittest.main()

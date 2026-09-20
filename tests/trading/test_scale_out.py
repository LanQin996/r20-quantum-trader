"""Unit tests for Scale-Out Execution Engine (scripts/trader/scale_out.py)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from scripts.trader.scale_out import execute_scale_out_if_eligible


class ScaleOutExecutionTests(unittest.TestCase):
    def setUp(self):
        self.mock_okx = MagicMock()
        self.mock_record_trade = MagicMock()
        self.mock_notify = MagicMock()
        self.mock_ensure_oco = MagicMock()
        self.mock_close_fee = MagicMock(return_value=0.5)
        self.mock_payload = MagicMock(return_value={"action": "close"})

        self.sample_f_long = {
            "instId": "BTC-USDT-SWAP",
            "name": "BTC",
            "price": 82000.0,
            "atr": 1000.0,
            "precision": 2,
            "ctVal": 1.0,
            "minSz": 0.01,
            "market_data_valid": True,
        }

        self.sample_pos_long = {
            "side": "long",
            "avgPx": 80000.0,
            "pos": 10.0,
            "venue": "okx",
        }

        self.sample_trackers = {
            "BTC-USDT-SWAP_long": {
                "initialSz": 10.0,
                "currentSz": 10.0,
                "takeProfitPx": 85000.0,
                "trailingStopPx": 78000.0,
                "scale_out_phase": 0,
                "scale_count": 0,
            }
        }

    def test_disabled_by_switch(self):
        with patch("scripts.trader.scale_out.SCALE_OUT_ENABLED", False):
            actions = []
            ok, reason = execute_scale_out_if_eligible(
                self.sample_f_long, self.sample_pos_long, self.sample_trackers,
                "2026-09-20 12:00:00", actions,
                okx_rest=self.mock_okx,
            )
            self.assertFalse(ok)
            self.assertEqual(reason, "分批止盈未启用")
            self.assertEqual(len(actions), 0)
            self.mock_okx.place_order.assert_not_called()

    def test_profit_below_trigger_threshold(self):
        # cur_px = 80500, entry = 80000 -> profit = 500 < 1.2 * 1000 (1200)
        f_low_profit = dict(self.sample_f_long, price=80500.0)
        actions = []
        ok, reason = execute_scale_out_if_eligible(
            f_low_profit, self.sample_pos_long, self.sample_trackers,
            "2026-09-20 12:00:00", actions,
            okx_rest=self.mock_okx,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "浮盈未达分批止盈门槛")
        self.mock_okx.place_order.assert_not_called()
        # 验证未达标时已为操盘手和主脑计算并存入首批止盈位 TP1
        t = self.sample_trackers["BTC-USDT-SWAP_long"]
        self.assertEqual(t.get("scale_out_tp"), 81200.0)
        self.assertIn("首批止盈目标 TP1: 81200", t.get("stage_desc", ""))

    def test_insufficient_size_graceful_degrade(self):
        # pos = 0.01, minSz = 0.01 -> 0.01 < 2 * 0.01, cannot split
        pos_tiny = dict(self.sample_pos_long, pos=0.01)
        actions = []
        ok, reason = execute_scale_out_if_eligible(
            self.sample_f_long, pos_tiny, self.sample_trackers,
            "2026-09-20 12:00:00", actions,
            okx_rest=self.mock_okx,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "张数不足以切分")
        self.assertIn("降级为全仓追踪", actions[0])
        self.mock_okx.place_order.assert_not_called()

    def test_successful_long_scale_out_and_state_lock(self):
        # profit = 82000 - 80000 = 2000 >= 1.2 * 1000
        self.mock_okx.place_order.return_value = [{"ordId": "12345"}]
        self.mock_okx.pending_algo_orders.return_value = [
            {"algoId": "algo_1", "posSide": "long", "state": "live"}
        ]

        actions = []
        ok, reason = execute_scale_out_if_eligible(
            self.sample_f_long, self.sample_pos_long, self.sample_trackers,
            "2026-09-20 12:00:00", actions,
            okx_rest=self.mock_okx,
            record_trade=self.mock_record_trade,
            notify_trade_close=self.mock_notify,
            close_fee=self.mock_close_fee,
            close_trade_payload=self.mock_payload,
            ensure_cloud_position_protection=self.mock_ensure_oco,
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "首批分批平仓成功")
        
        # 验证下达平仓市价单（reduceOnly=True，卖出 5 张）
        self.mock_okx.place_order.assert_called_once_with(
            "BTC-USDT-SWAP", "sell", "5",
            pos_side="long", td_mode="cross", ord_type="market", reduce_only=True
        )

        # 验证旧 OCO 被撤销
        self.mock_okx.cancel_algo_orders.assert_called_once_with(["algo_1"], inst_id="BTC-USDT-SWAP")

        # 验证新 OCO 重挂：剩余 5 张，保本止损价 80200 (80000 + 0.25%)
        self.mock_ensure_oco.assert_called_once_with(
            "BTC-USDT-SWAP", "long", 5.0, 85000.0, 80200.0
        )

        # 验证 tracker 状态变更与金字塔加仓互斥锁定
        t = self.sample_trackers["BTC-USDT-SWAP_long"]
        self.assertEqual(t["scale_out_phase"], 1)
        self.assertEqual(t["currentSz"], 5.0)
        self.assertEqual(t["scale_count"], 999)
        self.assertEqual(t["trailingStopPx"], 80200.0)

        # 验证台账双写与通知触发
        self.mock_record_trade.assert_called_once()
        self.mock_notify.assert_called_once()

    def test_idempotent_no_duplicate_scale_out(self):
        # scale_out_phase 已为 1 时，再次调用直接拒绝，绝不重复平仓
        self.sample_trackers["BTC-USDT-SWAP_long"]["scale_out_phase"] = 1
        actions = []
        ok, reason = execute_scale_out_if_eligible(
            self.sample_f_long, self.sample_pos_long, self.sample_trackers,
            "2026-09-20 12:00:00", actions,
            okx_rest=self.mock_okx,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "已执行过分批平仓")
        self.mock_okx.place_order.assert_not_called()

    def test_ledger_holding_row_reflects_scale_out_status(self):
        import sys
        from pathlib import Path
        scripts_dir = str(Path(__file__).resolve().parents[2] / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from scripts.sync_full_ledger import _holding_row
        import datetime

        trackers = {
            "BTC-USDT-SWAP_long": {
                "scale_out_phase": 1,
                "stage_desc": "已分批止盈50% (余5张 · 保本止损 80200)",
            }
        }
        mock_env = MagicMock(mode="live")
        tz = datetime.timezone(datetime.timedelta(hours=8))
        pos_raw = {
            "instId": "BTC-USDT-SWAP",
            "pos": "5.0",
            "posSide": "long",
            "avgPx": "80000",
            "markPx": "82000",
            "upl": "10000",
            "lever": "3",
            "cTime": "1789857503880",
        }
        row = _holding_row(
            pos_raw, "okx",
            env=mock_env,
            trackers=trackers,
            tz_bj=tz,
            allowed={"BTC-USDT-SWAP"},
            council_by_inst={},
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "holding")
        self.assertIn("已分批止盈50%", row["exit_reason"])
        self.assertEqual(row["scale_out_phase"], 1)


if __name__ == "__main__":
    unittest.main()

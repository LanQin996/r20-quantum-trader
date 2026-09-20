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
        self.mock_ensure_oco.return_value = (True, "verified")
        self.mock_okx.request.return_value = [
            {"ordId": "12345", "state": "filled", "accFillSz": "5"}]
        self.mock_okx.positions.return_value = [
            {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "5"}]
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
        self.assertEqual(self.sample_pos_long["pos"], 5.0)

        # 验证台账双写与通知触发
        self.mock_record_trade.assert_called_once()
        self.mock_notify.assert_called_once()

    def test_sol_057_split_uses_lot_size_and_updates_position(self):
        f = dict(self.sample_f_long, instId="SOL-USDT-SWAP", name="SOL",
                 price=108, atr=1, precision=2, lotSz="0.01", minSz="0.01")
        pos = {"side": "short", "avgPx": 110.65, "pos": 0.57, "venue": "okx"}
        trackers = {"SOL-USDT-SWAP_short": {
            "currentSz": 0.57, "takeProfitPx": 105, "trailingStopPx": 112}}
        self.mock_okx.place_order.return_value = [{"ordId": "sol-close"}]
        self.mock_okx.request.return_value = [
            {"ordId": "sol-close", "state": "filled", "accFillSz": "0.28"}]
        self.mock_okx.positions.return_value = [
            {"instId": f["instId"], "posSide": "short", "pos": "0.29"}]
        actions = []
        ok, _ = execute_scale_out_if_eligible(
            f, pos, trackers, "", actions, okx_rest=self.mock_okx,
            ensure_cloud_position_protection=self.mock_ensure_oco)
        self.assertTrue(ok)
        self.assertEqual(self.mock_okx.place_order.call_args.args[2], "0.28")
        self.assertEqual(pos["pos"], 0.29)
        self.mock_ensure_oco.assert_called_once_with(f["instId"], "short", 0.29, 105.0, 110.37)

    def test_quantity_precision_is_independent_of_price_precision(self):
        f = dict(self.sample_f_long, precision=1, lotSz="0.001", minSz="0.001")
        pos = dict(self.sample_pos_long, pos=0.057)
        self.mock_okx.place_order.return_value = [{"ordId": "12345"}]
        self.mock_okx.request.return_value = [
            {"ordId": "12345", "state": "filled", "accFillSz": "0.028"}]
        self.mock_okx.positions.return_value = [
            {"instId": f["instId"], "posSide": "long", "pos": "0.029"}]
        ok, _ = execute_scale_out_if_eligible(
            f, pos, self.sample_trackers, "", [], okx_rest=self.mock_okx,
            ensure_cloud_position_protection=self.mock_ensure_oco)
        self.assertTrue(ok)
        self.assertEqual(self.mock_okx.place_order.call_args.args[2], "0.028")
        self.assertEqual(pos["pos"], 0.029)

    def test_lot_size_is_not_minimum_order_size(self):
        f = dict(self.sample_f_long, lotSz="0.1", minSz="0.01")
        pos = dict(self.sample_pos_long, pos=0.7)
        self.mock_okx.place_order.return_value = [{"ordId": "12345"}]
        self.mock_okx.request.return_value = [
            {"ordId": "12345", "state": "filled", "accFillSz": "0.3"}]
        self.mock_okx.positions.return_value = [
            {"instId": f["instId"], "posSide": "long", "pos": "0.4"}]
        ok, _ = execute_scale_out_if_eligible(
            f, pos, self.sample_trackers, "", [], okx_rest=self.mock_okx,
            ensure_cloud_position_protection=self.mock_ensure_oco)
        self.assertTrue(ok)
        self.assertEqual(self.mock_okx.place_order.call_args.args[2], "0.3")
        self.assertEqual(pos["pos"], 0.4)

    def test_pending_fill_keeps_protection_and_does_not_resubmit(self):
        self.mock_okx.place_order.return_value = [{"ordId": "12345"}]
        self.mock_okx.request.return_value = [
            {"ordId": "12345", "state": "live", "accFillSz": "0"}]
        with patch("scripts.trader.scale_out.time.sleep"):
            for _ in range(2):
                ok, _ = execute_scale_out_if_eligible(
                    self.sample_f_long, self.sample_pos_long, self.sample_trackers,
                    "", [], okx_rest=self.mock_okx,
                    ensure_cloud_position_protection=self.mock_ensure_oco)
                self.assertFalse(ok)
        self.mock_okx.place_order.assert_called_once()
        self.mock_okx.cancel_algo_orders.assert_not_called()
        self.mock_ensure_oco.assert_not_called()
        self.assertEqual(self.sample_pos_long["pos"], 10.0)
        self.mock_okx.request.return_value = [
            {"ordId": "12345", "state": "filled", "accFillSz": "5"}]
        ok, _ = execute_scale_out_if_eligible(
            self.sample_f_long, self.sample_pos_long, self.sample_trackers,
            "", [], okx_rest=self.mock_okx,
            ensure_cloud_position_protection=self.mock_ensure_oco)
        self.assertTrue(ok)
        self.mock_okx.place_order.assert_called_once()
        self.assertNotIn("scale_out_pending", self.sample_trackers["BTC-USDT-SWAP_long"])

    def test_failed_protection_is_not_announced_as_verified(self):
        self.mock_okx.place_order.return_value = [{"ordId": "12345"}]
        self.mock_ensure_oco.return_value = (False, "repair rejected")
        actions = []
        ok, _ = execute_scale_out_if_eligible(
            self.sample_f_long, self.sample_pos_long, self.sample_trackers,
            "", actions, okx_rest=self.mock_okx,
            ensure_cloud_position_protection=self.mock_ensure_oco)
        self.assertTrue(ok)  # The reduction itself is confirmed.
        self.assertEqual(self.sample_pos_long["pos"], 5.0)
        self.assertTrue(any("repair rejected" in action for action in actions))
        self.assertFalse(any("推进至保本位" in action for action in actions))

    def test_filled_order_with_stale_position_keeps_original_protection(self):
        self.mock_okx.place_order.return_value = [{"ordId": "12345"}]
        self.mock_okx.positions.return_value = [
            {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "10"}]
        with patch("scripts.trader.scale_out.time.sleep"):
            ok, _ = execute_scale_out_if_eligible(
                self.sample_f_long, self.sample_pos_long, self.sample_trackers,
                "", [], okx_rest=self.mock_okx,
                ensure_cloud_position_protection=self.mock_ensure_oco)
        self.assertFalse(ok)
        self.assertEqual(self.sample_pos_long["pos"], 10.0)
        self.mock_okx.cancel_algo_orders.assert_not_called()
        self.mock_ensure_oco.assert_not_called()

    def test_following_risk_check_receives_only_confirmed_remaining_position(self):
        from scripts import ai_factor_trader as trader
        self.mock_okx.place_order.return_value = [{"ordId": "12345"}]
        with patch.object(trader, "okx_rest", self.mock_okx), \
             patch.object(trader, "ensure_cloud_position_protection", self.mock_ensure_oco), \
             patch.object(trader, "record_trade"), patch.object(trader, "notify_trade_close"), \
             patch.object(trader, "_position_exit_manage", return_value=(False, "holding")) as manage:
            trader.manage_position_tp_and_trailing(
                self.sample_f_long, self.sample_pos_long, self.sample_trackers, "", [])
        self.assertEqual(manage.call_args.args[1]["pos"], 5.0)

    def test_pending_fill_does_not_run_risk_check_against_old_size(self):
        from scripts import ai_factor_trader as trader
        self.mock_okx.place_order.return_value = [{"ordId": "12345"}]
        self.mock_okx.request.return_value = [{"ordId": "12345", "state": "live"}]
        with patch.object(trader, "okx_rest", self.mock_okx), \
             patch("scripts.trader.scale_out.time.sleep"), \
             patch.object(trader, "_position_exit_manage") as manage:
            trader.manage_position_tp_and_trailing(
                self.sample_f_long, self.sample_pos_long, self.sample_trackers, "", [])
        manage.assert_not_called()

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

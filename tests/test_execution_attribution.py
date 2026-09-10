"""执行层可归因性与风险预算基数的回归测试。

背景（2026-09-10 实盘复盘）：
- 13 笔平仓里 12 笔 exit_reason 未知，其中 5 笔正是引擎自己的 CLOSE_MARKET 平仓——
  该分支既不写交易记录也不绑定 trade_id，自进化模块因此被自己的审计缺口锁死。
- 当日熔断线以 availBal(自由保证金) 为基数：平仓释放保证金后，当日最大亏损 -1.80U
  的熔断线反而从 3.49 涨到 4.63（+33%），亏损后风险预算变大。
本测试锁定这两处的口径。
"""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

from r20_backend.analysis_capture import position_meta  # noqa: E402
from r20_backend.analysis_store import lifecycle_id, normalize_position  # noqa: E402
from r20_backend.analysis_sync import infer_exit_reason  # noqa: E402
from risk_constants import risk_base_balance  # noqa: E402

LIVE_POSITION = {"instId": "LINK-USDT-SWAP", "posId": "3905725476143583232",
                 "cTime": "1788971646086", "posSide": "short", "pos": "5.2",
                 "avgPx": "12.037", "upl": "1.2948", "markPx": "11.788"}

ENTRY_ORDER = {"instId": "LINK-USDT-SWAP", "ordId": "e1", "side": "sell", "posSide": "short",
               "ordType": "limit", "clOrdId": "", "algoId": "", "reduceOnly": "false",
               "attachAlgoOrds": [{"slTriggerPx": "12.372", "tpTriggerPx": "11.515"}]}


def close_order(ord_id="c1", cl_ord_id="", algo_id="", ord_type="market", px="11.788"):
    return {"instId": "LINK-USDT-SWAP", "ordId": ord_id, "side": "buy", "posSide": "short",
            "ordType": ord_type, "clOrdId": cl_ord_id, "algoId": algo_id,
            "reduceOnly": "true", "avgPx": px}


class PositionBindingTests(unittest.TestCase):
    def test_live_position_binds_trade_id(self):
        meta = position_meta(LIVE_POSITION)
        self.assertEqual(meta["trade_id"], lifecycle_id(LIVE_POSITION))
        self.assertEqual(meta["inst"], "LINK-USDT-SWAP")
        self.assertEqual(meta["side"], "short")

    def test_unknown_position_does_not_invent_a_binding(self):
        self.assertEqual(position_meta(None), {})
        self.assertEqual(position_meta({"instId": "LINK-USDT-SWAP"}), {"inst": "LINK-USDT-SWAP"})

    def test_judgement_exit_records_its_lifecycle(self):
        trader = importlib.import_module("scripts.ai_factor_trader")
        calls = []

        def record(kind, body=None, status="observed", **meta):
            calls.append((kind, body, meta))
            return "id"

        with patch.object(trader.analysis_capture, "emit", side_effect=record):
            trader.record_trade({"action": "平仓", "action_type": "AI裁量整仓退出",
                                 "inst": "LINK", "sz": 5.2, "price": 11.788},
                                position=LIVE_POSITION)
        exits = [c for c in calls if c[0] == "position.exit_reason"]
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0][2]["trade_id"], lifecycle_id(LIVE_POSITION))
        self.assertTrue(exits[0][1]["confirmed"])
        # The trade record must carry the same binding, otherwise reconcile drops it again.
        self.assertEqual(calls[0][2]["trade_id"], lifecycle_id(LIVE_POSITION))


class ExitReasonInferenceTests(unittest.TestCase):
    def test_engine_market_close_is_reported_as_system_exit(self):
        result = infer_exit_reason([ENTRY_ORDER, close_order()], {}, "short")
        self.assertEqual(result, {"reason": "系统主动平仓", "source": "system_market_close"})

    def test_attached_stop_leg_is_distinguished_from_take_profit(self):
        fills = {("LINK-USDT-SWAP", "c1"): [{"fillPx": "12.375", "ordId": "c1"}]}
        stopped = infer_exit_reason([ENTRY_ORDER, close_order(cl_ord_id="O3907914585885451264",
                                                             algo_id="a1")], fills, "short")
        self.assertEqual(stopped["reason"], "云端止损触发")
        self.assertEqual(stopped["source"], "attached_algo_trigger")

        fills = {("LINK-USDT-SWAP", "c1"): [{"fillPx": "11.512", "ordId": "c1"}]}
        taken = infer_exit_reason([ENTRY_ORDER, close_order(cl_ord_id="O1", algo_id="a1")], fills, "short")
        self.assertEqual(taken["reason"], "云端止盈触发")

    def test_long_side_is_mirrored(self):
        entry = {"instId": "XRP-USDT-SWAP", "ordId": "e2", "side": "buy", "posSide": "long",
                 "ordType": "limit", "clOrdId": "", "algoId": "", "reduceOnly": "false",
                 "attachAlgoOrds": [{"slTriggerPx": "1.4065", "tpTriggerPx": "1.4805"}]}
        closing = {"instId": "XRP-USDT-SWAP", "ordId": "c2", "side": "sell", "posSide": "long",
                   "ordType": "market", "clOrdId": "O2", "algoId": "a2", "reduceOnly": "true",
                   "avgPx": "1.4063"}
        result = infer_exit_reason([entry, closing], {}, "long")
        self.assertEqual(result["reason"], "云端止损触发")

    def test_without_an_identifiable_close_nothing_is_claimed(self):
        self.assertIsNone(infer_exit_reason([ENTRY_ORDER], {}, "short"))


class RiskBaseTests(unittest.TestCase):
    def test_base_is_equity_and_survives_margin_being_locked(self):
        flat = risk_base_balance({"availBal": "120.18925179109739", "frozenBal": "0"})
        invested = risk_base_balance({"availBal": "84.39085179109739", "frozenBal": "35.7984"})
        self.assertAlmostEqual(flat, invested, places=6)
        self.assertGreater(invested, 84.39)

    def test_cash_balance_is_the_fallback_base(self):
        self.assertAlmostEqual(risk_base_balance({"availBal": "0", "cashBal": "97.5"}), 97.5)

    def test_daily_loss_limit_no_longer_grows_after_a_realized_loss(self):
        trader = importlib.import_module("scripts.ai_factor_trader")
        # Same equity before and after a losing position is closed and its margin released.
        before = trader.effective_daily_loss_limit(risk_base_balance({"availBal": "69.72", "frozenBal": "22.94"}))
        after = trader.effective_daily_loss_limit(risk_base_balance({"availBal": "92.67", "frozenBal": "0"}))
        self.assertAlmostEqual(before, after, places=6)
        # The old free-margin base is what let the cap climb by 33% on a loss day.
        self.assertGreater(trader.effective_daily_loss_limit(92.67),
                           trader.effective_daily_loss_limit(69.72))


class MarginEstimateTests(unittest.TestCase):
    def test_closed_lifecycle_gets_an_estimated_margin(self):
        row = {"instId": "LINK-USDT-SWAP", "posId": "1", "cTime": "1788882581271",
               "uTime": "1788904813083", "direction": "long", "type": "2", "lever": "3",
               "openAvgPx": "12.037", "closeAvgPx": "11.788", "openMaxPos": "5.2",
               "closeTotalPos": "5.2", "pnl": "1.2948", "fee": "-0.0619", "realizedPnl": "1.2329"}
        trade = normalize_position(row)
        self.assertEqual(trade["margin_basis"], "notional_estimate")
        self.assertAlmostEqual(float(trade["margin"]), 5.2 * 1.0 * 12.037 / 3, places=4)

    def test_exchange_margin_still_wins(self):
        row = {"instId": "LINK-USDT-SWAP", "posId": "1", "cTime": "1", "uTime": "2",
               "direction": "long", "type": "2", "lever": "3", "margin": "7.5",
               "openAvgPx": "12", "closeAvgPx": "12", "closeTotalPos": "1"}
        trade = normalize_position(row)
        self.assertEqual(trade["margin_basis"], "exchange")
        self.assertEqual(trade["margin"], "7.5")


class ReconcileExitAttributionTests(unittest.TestCase):
    """End-to-end through reconcile(): an exchange-side close must still carry a reason."""

    def setUp(self):
        import os
        import tempfile
        from r20_backend.analysis_store import Archive
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "r20_quant.db"
        self.env = patch.dict(os.environ, {"R20_TESTING": "1", "R20_ANALYSIS_DB": str(path)})
        self.env.start()
        self.archive = Archive(path)
        self.account = "okx:live:attribution-test"

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def seed(self, closing):
        from r20_backend.analysis_sync import reconcile
        opened, at = 1788971646086, 1788975000000
        history = {"posId": "77", "instId": "LINK-USDT-SWAP", "direction": "short",
                   "cTime": str(opened), "uTime": str(at + 246086), "type": "2",
                   "openAvgPx": "12.037", "closeAvgPx": "11.788", "closeTotalPos": "5.2",
                   "lever": "3", "pnl": "1.2948", "fee": "-0.0619", "fundingFee": "0",
                   "liqPenalty": "0", "settledPnl": "0", "realizedPnl": "1.2329"}
        self.archive.raw(self.account, "positions-history", [history])
        self.archive.raw(self.account, "orders", [dict(ENTRY_ORDER, posId="77", uTime=str(opened + 1000)),
                                                  dict(closing, posId="77", uTime=str(at))])
        self.archive.raw(self.account, "fills", [{"billId": "b1", "ordId": closing["ordId"],
                                                  "instId": "LINK-USDT-SWAP", "ts": str(at),
                                                  "fillPx": closing["avgPx"]}])
        return reconcile(self.archive, self.account, [])

    def test_attached_stop_close_is_inferred_not_confirmed(self):
        trades = self.seed(close_order(cl_ord_id="O3907914585885451264", algo_id="a1", px="12.375"))
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade["exit_reason"], "云端止损触发")
        self.assertEqual(trade["exit_evidence"], "inferred")
        self.assertEqual(trade["exit_source"], "attached_algo_trigger")
        self.assertEqual(trade["margin_basis"], "notional_estimate")

    def test_engine_market_close_is_inferred(self):
        trades = self.seed(close_order(px="11.788"))
        self.assertEqual(trades[0]["exit_reason"], "系统主动平仓")
        self.assertEqual(trades[0]["exit_evidence"], "inferred")


if __name__ == "__main__":
    unittest.main()

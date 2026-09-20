# -*- coding: utf-8 -*-
"""跨所挂单行的云端保护腿展示（2026-09-16）。

## 这个测试在守什么

Binance/Gate 的 TP/SL **不在挂单对象里**（只有 OKX 有 `attachAlgoOrds`），
旧实现给跨所挂单一律写死 `tp_px="--" / sl_px="--"`。后果是实盘上：

    用户看到：[ETH] 限价空单 2408.5 —— 保护列全 "--"（像裸单）
    交易所真相：STOP_MARKET 2456.5 + TAKE_PROFIT_MARKET 2298.5 早已挂出（reduceOnly）

这正是本仓红线「UI 不说谎」的反面：不是编造，而是**该展示的真实数据没接上**。
修法是复用 `collect_cross_venue_positions` **上方已经取回**的 `v_algos`
（零新增交易所调用），并**只认反向（reduceOnly）腿**——避免同标的其它方向持仓的
保护腿串味；匹配不到仍然诚实留在 `"--"`。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from r20_backend.dashboard_payload.multi_venue import collect_cross_venue_positions


class _FakeAdapter:
    def __init__(self, *, open_orders, algos, positions=None):
        self._open = open_orders
        self._algos = algos
        self._pos = positions or []
        self.algo_calls = 0

    def positions(self):
        return self._pos

    def open_orders(self):
        return self._open

    def list_protective_orders(self, *a, **k):
        self.algo_calls += 1
        return self._algos


def _leg(symbol, side, order_type, trigger, raw_extra=None):
    raw = {"orderType": order_type, "triggerPrice": trigger}
    raw.update(raw_extra or {})
    return {"symbol": symbol, "side": side, "trigger_price": trigger,
            "type": order_type, "raw": raw}


class PendingOrderProtectionDisplayTest(unittest.TestCase):
    def _run(self, adapter):
        pending: list = []
        empty = _FakeAdapter(open_orders=[], algos=[])
        # 只让 binance 返回夹具：`collect_cross_venue_positions` 会遍历 binance/gate
        # 两个场所，否则同一份夹具会被记两次。
        def _pick(venue, *a, **k):
            return adapter if venue == "binance" else empty
        with patch("r20_backend.exchanges.get_adapter", _pick), \
             patch("r20_backend.dashboard_payload.multi_venue._global_env_axis",
                   lambda: "demo"):
            collect_cross_venue_positions([], pending, 0, 0, 0.0)
        return pending

    def test_reduce_only_legs_are_shown_on_the_pending_row(self):
        """ETH 空单挂单：反向（buy）的 STOP=SL、TAKE_PROFIT=TP 必须出现在行上。"""
        ad = _FakeAdapter(
            open_orders=[{"symbol": "ETHUSDT", "side": "sell", "price": 2408.5, "size": "0.58"}],
            algos=[_leg("ETHUSDT", "buy", "STOP_MARKET", 2456.5),
                   _leg("ETHUSDT", "buy", "TAKE_PROFIT_MARKET", 2298.5)],
        )
        rows = self._run(ad)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sl_px"], "2456.5")
        self.assertEqual(rows[0]["tp_px"], "2298.5")
        self.assertEqual(ad.algo_calls, 1, "不得为本展示新增交易所调用（复用 v_algos）")

    def test_same_side_legs_are_not_misattributed(self):
        """同向（sell）腿属于别的方向持仓，绝不能当成这张挂单的保护腿。"""
        ad = _FakeAdapter(
            open_orders=[{"symbol": "ETHUSDT", "side": "sell", "price": 2408.5, "size": "0.58"}],
            algos=[_leg("ETHUSDT", "sell", "TAKE_PROFIT_MARKET", 2718.0)],
        )
        rows = self._run(ad)
        self.assertEqual(rows[0]["sl_px"], "--")
        self.assertEqual(rows[0]["tp_px"], "--", "串味的保护腿比 '--' 更危险")

    def test_other_symbol_legs_are_ignored(self):
        ad = _FakeAdapter(
            open_orders=[{"symbol": "ETHUSDT", "side": "sell", "price": 2408.5, "size": "0.58"}],
            algos=[_leg("BTCUSDT", "buy", "STOP_MARKET", 70000.0)],
        )
        rows = self._run(ad)
        self.assertEqual(rows[0]["sl_px"], "--")

    def test_no_legs_stays_honest_dash(self):
        ad = _FakeAdapter(
            open_orders=[{"symbol": "SOLUSDT", "side": "buy", "price": 100.0, "size": "5"}],
            algos=[],
        )
        rows = self._run(ad)
        self.assertEqual((rows[0]["sl_px"], rows[0]["tp_px"]), ("--", "--"),
                         "没腿就是没腿，不得留空也不得编造")

    def test_long_entry_matches_sell_legs(self):
        """买多挂单的保护腿是 sell 侧——镜像方向同样要能匹配上。"""
        ad = _FakeAdapter(
            open_orders=[{"symbol": "SOLUSDT", "side": "buy", "price": 97.0, "size": "5"}],
            algos=[_leg("SOLUSDT", "sell", "STOP_MARKET", 94.0),
                   _leg("SOLUSDT", "sell", "TAKE_PROFIT_MARKET", 103.0)],
        )
        rows = self._run(ad)
        self.assertEqual(rows[0]["sl_px"], "94")
        self.assertEqual(rows[0]["tp_px"], "103")

    def test_gate_native_protective_orders_matching(self):
        """Gate 原生结构：contract 为 BTC_USDT，价格在 trigger.price，text 在 initial.text。"""
        gate_algos = [
            {
                "id": "2100868994629107712",
                "trigger": {"price": "77060.0", "rule": 2},
                "initial": {"contract": "BTC_USDT", "text": "t-r20sl21170244", "auto_size": "close_long"}
            },
            {
                "id": "2100952253513859072",
                "trigger": {"price": "81000.0", "rule": 1},
                "initial": {"contract": "BTC_USDT", "text": "t-r20tp41020503", "auto_size": "close_long"}
            }
        ]
        ad = _FakeAdapter(
            open_orders=[{"contract": "BTC_USDT", "side": "buy", "price": 77620.0, "size": "177"}],
            algos=gate_algos,
        )
        rows = self._run(ad)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "BTC", "标的名称必须是干净的 BTC，绝不能带下划线 BTC_")
        self.assertEqual(rows[0]["instId"], "BTC-USDT-SWAP")
        self.assertEqual(rows[0]["sl_px"], "77060")
        self.assertEqual(rows[0]["tp_px"], "81000")


if __name__ == "__main__":
    unittest.main()

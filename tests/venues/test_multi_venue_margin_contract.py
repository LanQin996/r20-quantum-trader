from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from r20_backend.dashboard_payload.multi_venue import collect_cross_venue_positions


class MultiVenueMarginContractTests(unittest.TestCase):
    """外所（Gate / Binance）持仓名义价值与保证金口径契约测试。

    防止 Gate 等交易所因合约张数单位（如 BTC 1 张 = 0.0001 BTC）
    直接乘单价导致名义价值被放大万倍、保证金失真爆表的回归。
    """

    def test_gate_contract_multiplier_notional_and_margin_precedence(self):
        """Gate 返回 170 张合约时，必须优先使用官方 value/margin 而非张数乘市价。"""
        gate_pos = [{
            "venue": "gate",
            "inst_id": "BTC_USDT",
            "base": "BTC",
            "side": "long",
            "size_signed": 170.0,
            "entry_price": 81020.0,
            "mark_price": 81100.0,
            "leverage": 6.0,
            "margin": 230.59,
            "notional": 1378.69,
            "unrealized_pnl": 1.35,
            "raw": {
                "size": 170,
                "value": "1378.69",
                "margin": "230.59",
                "initial_margin": "230.82",
            }
        }]

        mock_gate = MagicMock()
        mock_gate.positions.return_value = gate_pos
        mock_gate.open_orders.return_value = []
        mock_gate.list_protective_orders.return_value = []

        mock_binance = MagicMock()
        mock_binance.positions.return_value = []
        mock_binance.open_orders.return_value = []
        mock_binance.list_protective_orders.return_value = []

        def get_ad(v, **kw):
            if v == "gate":
                return mock_gate
            return mock_binance

        positions = []
        pending_orders = []
        with patch("r20_backend.exchanges.get_adapter", side_effect=get_ad):
            long_c, short_c, upl = collect_cross_venue_positions(
                positions, pending_orders, 0, 0, 0.0
            )

        self.assertEqual(len(positions), 1)
        p = positions[0]
        self.assertEqual(p["instId"], "BTC-USDT-SWAP")
        self.assertEqual(p["notional_usdt"], 1378.69, "名义价值必须使用 Gate 官方 1378.69U，严禁放大万倍")
        self.assertEqual(p["margin_usdt"], 230.59, "保证金必须使用 Gate 官方 230.59U，严禁放大万倍")
        self.assertLess(p["margin_usdt"], 1000.0)

    def test_binance_official_notional_precedence(self):
        """Binance 持仓必须优先读取官方 notional 字段。"""
        binance_pos = [{
            "venue": "binance",
            "inst_id": "ETHUSDT",
            "base": "ETH",
            "side": "long",
            "size_signed": 0.527,
            "entry_price": 2618.5,
            "mark_price": 2627.82,
            "leverage": 6.0,
            "margin": 0.0,
            "notional": 1384.86,
            "unrealized_pnl": 4.91,
            "raw": {
                "notional": "1384.86",
                "isolatedMargin": "0",
            }
        }]

        mock_gate = MagicMock()
        mock_gate.positions.return_value = []
        mock_gate.open_orders.return_value = []
        mock_gate.list_protective_orders.return_value = []

        mock_binance = MagicMock()
        mock_binance.positions.return_value = binance_pos
        mock_binance.open_orders.return_value = []
        mock_binance.list_protective_orders.return_value = []

        def get_ad(v, **kw):
            if v == "binance":
                return mock_binance
            return mock_gate

        positions = []
        pending_orders = []
        with patch("r20_backend.exchanges.get_adapter", side_effect=get_ad):
            collect_cross_venue_positions(positions, pending_orders, 0, 0, 0.0)

        self.assertEqual(len(positions), 1)
        p = positions[0]
        self.assertEqual(p["notional_usdt"], 1384.86)
        self.assertEqual(p["margin_usdt"], round(1384.86 / 6.0, 2))


if __name__ == "__main__":
    unittest.main()

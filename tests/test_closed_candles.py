"""Offline regressions for confirmed-candle ingestion through real calculators.

Only the trader's data/indicator functions are loaded from its AST, avoiding
account configuration and trading-process startup. HTTP and CLI calls are mocked.
"""
from __future__ import annotations

import ast
import copy
import io
import json
import logging
import math
import os
import sys
import types
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from scripts import backtest_engine, calculus_engine, market_data_service
from scripts.candle_data import closed_okx_candles

ROOT = Path(__file__).resolve().parents[1]
TREES = {
    name: ast.parse((ROOT / 'scripts' / name).read_text(encoding='utf-8'))
    for name in ('ai_brain_trader.py', 'ai_factor_trader.py', 'factor_library.py')
}
ITEM = {
    'instId': 'BTC-USDT-SWAP', 'name': 'BTC', 'type': 'crypto', 'ccy': 'BTC',
    'precision': 2, 'ctVal': 0.01, 'base_sz': 1, 'minSz': 0.01,
}


def candle_rows(count=60):
    rows = []
    for index in range(count):
        close = 100 + index * 0.4 + index ** 2 * 0.01
        rows.append([
            str(1_700_000_000_000 + index * 3_600_000), str(close - 0.2),
            str(close + 0.6), str(close - 0.5), str(close), str(1000 + index),
            '10', '1000', '1',
        ])
    return list(reversed(rows))


def forming_bar():
    return ['1900000000000', '100', '900000', '0.01', '800000', '999999999', '1', '1', '0']


def load_functions(filename, names, namespace):
    nodes = [node for node in TREES[filename].body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
    exec(compile(module, filename, 'exec'), namespace)
    return namespace


class OfflineTest(unittest.TestCase):
    def setUp(self):
        # Any unmocked request/process must fail, even if a production fallback runs.
        for target in ('socket.socket', 'socket.create_connection', 'subprocess.Popen'):
            patcher = patch(target, side_effect=AssertionError('Unexpected network/process'))
            patcher.start()
            self.addCleanup(patcher.stop)


class ClosedCandleServiceTests(OfflineTest):
    def test_filter_rejects_unknown_flags_and_preserves_confirmed_rows(self):
        closed = candle_rows(2)
        closed[1][8] = 1  # Also accept the integer representation.
        invalid = [None, {}, [], 'not-a-row', closed[0][:8]]
        invalid += [closed[0][:8] + [flag] for flag in ('0', '', None, True, 'unknown')]
        rows = [forming_bar(), closed[0], *invalid, closed[1], forming_bar()]
        before = copy.deepcopy(rows)
        self.assertEqual(closed_okx_candles(rows), closed)
        self.assertEqual(rows, before)
        for bad_payload in (None, {}, 'not-an-array'):
            self.assertEqual(closed_okx_candles(bad_payload), [])

    def test_rest_filters_before_window_and_keeps_newest_closed_bar(self):
        closed = candle_rows(4)
        for rows in ([forming_bar(), *closed], closed):
            with self.subTest(forming=rows[0][8] == '0'), \
                    patch.object(market_data_service, '_public_get', return_value={'data': rows}) as get, \
                    patch.object(market_data_service.subprocess, 'run') as cli:
                actual = market_data_service.fetch_candles(ITEM['instId'], limit=3)
                self.assertEqual(actual, closed[:3])
                self.assertEqual(get.call_args.kwargs['params']['limit'], 4)
                self.assertTrue(all(len(row) == 9 for row in actual))
                cli.assert_not_called()

    def test_rest_without_confirmed_bars_returns_empty_without_raw_fallback(self):
        with patch.object(market_data_service, '_public_get', return_value={'data': [forming_bar()]}), \
                patch.object(market_data_service.subprocess, 'run') as cli:
            self.assertEqual(market_data_service.fetch_candles(ITEM['instId']), [])
            cli.assert_not_called()

    def test_cli_fallback_uses_the_same_confirmation_filter(self):
        closed = candle_rows(3)
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps([forming_bar(), *closed]))
        with patch.object(market_data_service, '_public_get', return_value=None), \
                patch.object(market_data_service.subprocess, 'run', return_value=result) as cli:
            self.assertEqual(market_data_service.fetch_candles(ITEM['instId'], limit=2), closed[:2])
            self.assertIn('--limit 3', cli.call_args.args[0])
        result.stdout = json.dumps([closed[0][:8], forming_bar()])
        with patch.object(market_data_service, '_public_get', return_value=None), \
                patch.object(market_data_service.subprocess, 'run', return_value=result):
            self.assertEqual(market_data_service.fetch_candles(ITEM['instId']), [])

    def test_request_limit_never_exceeds_exchange_cap(self):
        with patch.object(market_data_service, '_public_get', return_value={'data': candle_rows(300)}) as get:
            self.assertEqual(len(market_data_service.fetch_candles(ITEM['instId'], limit=300)), 300)
            self.assertEqual(get.call_args.kwargs['params']['limit'], 300)

    def test_backtest_uses_only_closed_bars_in_chronological_order(self):
        closed = candle_rows(4)
        response = io.BytesIO(json.dumps({'code': '0', 'data': [forming_bar(), *closed]}).encode('utf-8'))
        with patch.object(backtest_engine.urllib.request, 'urlopen', return_value=response) as get:
            candles = backtest_engine.fetch_okx_candles(ITEM['instId'], limit=3)
        expected = list(reversed(closed[:3]))
        self.assertEqual([c['ts_ms'] for c in candles], [int(c[0]) for c in expected])
        self.assertEqual([c['close'] for c in candles], [float(c[4]) for c in expected])
        self.assertIn('limit=4', get.call_args.args[0].full_url)
        self.assertEqual(set(candles[0]), {'symbol', 'timestamp', 'ts_ms', 'open', 'high', 'low', 'close', 'volume'})


class CandlePipelineTests(OfflineTest):
    def setUp(self):
        super().setUp()
        self.rows = {bar: candle_rows() for bar in ('15m', '1H', '4H')}
        alias = patch.dict(sys.modules, {'calculus_engine': calculus_engine})
        alias.start()
        self.addCleanup(alias.stop)
        http = patch('urllib.request.urlopen', side_effect=self.urlopen)
        http.start()
        self.addCleanup(http.stop)
        pooled = patch.object(market_data_service, '_public_get', side_effect=self.public_get)
        pooled.start()
        self.addCleanup(pooled.stop)
        self.common = {
            'json': json, 'urllib': urllib, 'math': math,
            'time': types.SimpleNamespace(time=lambda: 1_800_000_000),
            'os': types.SimpleNamespace(path=types.SimpleNamespace(exists=lambda _: False, join=os.path.join)),
            'sys': types.SimpleNamespace(path=[]), 'WORKSPACE_DIR': str(ROOT),
            'NEWS_SENTIMENT_FILE': 'unused-news.json',
            'closed_okx_candles': closed_okx_candles,
            'logger': logging.getLogger('test_closed_candles'),
        }

    def response(self, url):
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if parsed.path.endswith('/market/candles'):
            rows = self.rows[params['bar'][0]][:int(params['limit'][0])]
            return {'code': '0', 'data': rows}
        if parsed.path.endswith('/market/ticker'):
            return {'code': '0', 'data': [{'last': '180', 'bidPx': '179.9', 'askPx': '180.1', 'open24h': '170'}]}
        return {'code': '0', 'data': []}

    def urlopen(self, request, **kwargs):
        return io.BytesIO(json.dumps(self.response(request.full_url)).encode('utf-8'))

    def public_get(self, path, params, **kwargs):
        self.assertEqual(path, '/api/v5/market/candles')
        return {'code': '0', 'data': self.rows[params['bar']][:params['limit']]}

    def brain(self):
        namespace = load_functions('ai_brain_trader.py', ['fetch_single_instrument_package'], {
            **self.common,
            '_okx_get_json': lambda url, *args, **kwargs: self.response(url),
            'fetch_single_indicator': lambda *args, **kwargs: {'adx': '28'},
        })
        return namespace['fetch_single_instrument_package'](ITEM)

    def factor_library(self):
        namespace = load_functions('factor_library.py', ['safe_float', 'compute_instrument_factors'], {
            **self.common,
            'fetch_orderbook_depth': lambda *args, **kwargs: {'bids': [['180', '20']], 'asks': [['181', '1']]},
            'fetch_indicators_batch': lambda *args, **kwargs: {'ADX': {'adx': '28'}, 'KDJ': {'j': '70'}, 'CMF': {'cmf': '0.2'}},
        })
        return namespace['compute_instrument_factors'](ITEM, {'BTC': {'longShortRatio': {'weightedLongRatio': 0.8}}})

    def factor_trader(self):
        names = [
            'fetch_single_instrument_data', 'fetch_candles_direct', 'effective_risk_per_trade',
            'quantize_size', 'load_adaptive_config', 'calc_ema', 'calc_rsi', 'calc_atr',
            'calc_macd_histogram_acceleration', 'calc_obv_trend', 'calc_bollinger_squeeze',
        ]
        namespace = load_functions('ai_factor_trader.py', names, {
            **self.common,
            'fetch_candles': market_data_service.fetch_candles,
            'RISK_PER_TRADE_EQUITY_RATIO': 0.02,
            'ASSET_CLASS_PROFILES': {'crypto': {'sl_atr_mult': 2.2}},
        })
        return namespace['fetch_single_instrument_data'](ITEM, [], 1000)

    def test_extreme_unclosed_bars_do_not_change_any_pipeline(self):
        for run in (self.brain, self.factor_library, self.factor_trader):
            with self.subTest(pipeline=run.__name__):
                self.rows = {bar: candle_rows() for bar in self.rows}
                expected = run()
                self.rows = {bar: [forming_bar(), *rows] for bar, rows in self.rows.items()}
                self.assertEqual(run(), expected)

    def test_brain_keeps_ohlcv_shape_and_uses_latest_confirmed_candle(self):
        result = self.brain()
        self.assertEqual(result['data_quality'], 'valid')
        for bar, key, count in (('15m', 'recent_15m', 12), ('1H', 'recent_1h', 12), ('4H', 'recent_4h', 8)):
            self.assertEqual(len(result[key]), count)
            self.assertTrue(all(len(row) == 5 for row in result[key]))
            self.assertEqual(result[key][0], [float(v) for v in self.rows[bar][0][1:6]])
        self.assertEqual(result['price'], 180)  # The live ticker remains separate.
        self.assertGreater(result['calculus']['timeframes']['1H']['velocity'], 0)

    def test_brain_invalidates_insufficient_confirmed_indicator_history(self):
        for bar, count in (('15m', 14), ('1H', 14), ('4H', 7)):
            with self.subTest(bar=bar):
                self.rows = {tf: candle_rows() for tf in self.rows}
                self.rows[bar] = [forming_bar(), *candle_rows(count)]
                self.assertEqual(self.brain()['data_quality'], 'invalid')

    def test_factor_library_waits_when_closed_history_is_insufficient(self):
        for bar in ('15m', '1H'):
            with self.subTest(bar=bar):
                self.rows = {tf: candle_rows() for tf in self.rows}
                self.rows[bar] = [forming_bar(), *candle_rows(14)]
                self.assertEqual(self.factor_library()['signal_recommendation'], 'WAIT')

    def test_factor_trader_uses_correct_ohlcv_columns_for_calculus(self):
        result = self.factor_trader()
        self.assertTrue(result['market_data_valid'])
        self.assertEqual(result['price'], 180)
        self.assertNotEqual(result['price'], float(self.rows['15m'][0][4]))
        for bar, key, count in (('15m', '15M', 45), ('1H', '1H', 35), ('4H', '4H', 25)):
            rows = list(reversed(self.rows[bar][:count]))
            expected = calculus_engine.calculate_calculus(
                [c[4] for c in rows], [c[2] for c in rows], [c[3] for c in rows], [c[5] for c in rows])
            self.assertEqual(result['calculus']['timeframes'][key], expected)

    def test_missing_live_ticker_does_not_turn_a_closed_price_into_a_quote(self):
        original_response = self.response
        def without_ticker(url):
            if urlparse(url).path.endswith('/market/ticker'):
                return {'code': '0', 'data': []}
            return original_response(url)
        with patch.object(self, 'response', side_effect=without_ticker):
            result = self.factor_trader()
        self.assertEqual(result['price'], 0)
        self.assertFalse(result['market_data_valid'])
        self.assertEqual(result['sz'], 0)

    def test_factor_trader_disables_size_without_enough_closed_bars(self):
        for bar, count in (('15m', 29), ('1H', 19), ('4H', 19), ('15m', 1), ('1H', 1)):
            with self.subTest(bar=bar, count=count):
                self.rows = {tf: candle_rows() for tf in self.rows}
                self.rows[bar] = [forming_bar(), *candle_rows(count)]
                result = self.factor_trader()
                self.assertFalse(result['market_data_valid'])
                self.assertEqual(result['sz'], 0)

    def test_confirmation_is_required_instead_of_guessed_from_position(self):
        self.rows = {bar: [row[:8] for row in rows] for bar, rows in self.rows.items()}
        with patch.object(market_data_service.subprocess, 'run') as cli:
            result = self.brain()
            self.assertEqual(result['data_quality'], 'invalid')
            self.assertFalse(result['calculus']['valid'])
            self.assertEqual(self.factor_library()['signal_recommendation'], 'WAIT')
            result = self.factor_trader()
            self.assertFalse(result['market_data_valid'])
            self.assertEqual(result['sz'], 0)
            cli.assert_not_called()


if __name__ == '__main__':
    unittest.main()

"""`scripts/factors/candles_15m.py`（B3 第三十二刀）回归。

## 这个测试在守什么

15M K 线 → ATR/RSI/VWAP 乖离/量比/OBV + Pillar 6（微积分/定积分/概率）。
这是**因子库最核心的数值段**：它算错不会崩，只会让 AI 看到错误的指标。

## 五处易错点（详见模块文档串）

1. **K 线必须 `reversed`**（OKX 是 newest-first）。忘了 reverse 不报错，
   只是把时间轴反过来算 —— ATR/RSI/OBV 全错。
2. 列索引 `c[2]=high, c[3]=low, c[4]=close, c[5]=vol`。
3. ATR 是**简单平均**（`sum(tr[-14:])/14`），不是 Wilder 平滑。
4. RSI 的 `avg_l == 0` → `rs = 100.0`（不是除零）。
5. `atr_pct` 与 `vwap_bias_pct` 各自有**独立的除零守卫**（`price>0` / `v_sum>0`）。

## ⚠️ 这个测试**不**覆盖门面的取数缝

`fetch_candles` 留在门面，本模块只吃已取回的 K 线 —— 故这里喂构造数据。
门面那条缝由 `tests/llm/test_quant_system_calculus.py` 的 `patch.object` 守着。
"""

from __future__ import annotations

import ast
import copy
import random
import unittest
from pathlib import Path

from scripts.factors.candles_15m import (
    compute_15m_indicators,
    derive_candle_series,
)

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "factors" / "candles_15m.py"
FACADE = ROOT / "scripts" / "factor_library.py"


def _sf(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _factors(price=100.0):
    return {
        "price": price,
        "volatility_channel": {},
        "trend_momentum": {},
        "volume_money_flow": {},
        "calculus_dynamics": {},
        "definite_integrals": {},
        "probability_theory": {},
    }


def _candle(ts, o, h, l, c, v):
    return [str(ts), str(o), str(h), str(l), str(c), str(v), "0", "0", "0"]


def _series(closes, vols=None, spread=1.0, start_ts=1_700_000_000_000, step=900_000):
    """按**时间正序**造 K 线，再**反序**返回（模拟 OKX newest-first）。

    ⚠️ `spread` 默认为 1.0 会让 high/low 噪声很大（`h_i - c_{i-1}` 可能超过
    `h_i - l_i`），于是相邻 high 关系不再单调 —— 我原本用 `spread=1.0` 的
    20 根递增 close 去测 RSI，得到 99.0 而不是 100.0，误以为实现有问题。
    只关心 close 序列的用例请传很小的 spread（如 1e-6）。
    """
    vols = vols or [10.0] * len(closes)
    out = []
    for i, c in enumerate(closes):
        out.append(_candle(start_ts + i * step, c, c + spread, c - spread, c, vols[i]))
    return list(reversed(out))


#: 只关心 close 时用 —— wick 极小，`h_i - l_i` 恒为 2e-6，不干扰 close 单调性
_TIGHT = 1e-6


# ------------------------------------------------------------------ legacy


def _legacy(raw_candles, factors, safe_float, calculate_calculus=None):
    """搬走前 factor_library.py 里的内联实现（逐字原样）。"""
    closes = [safe_float(c[4]) for c in reversed(raw_candles)]
    highs = [safe_float(c[2]) for c in reversed(raw_candles)]
    lows = [safe_float(c[3]) for c in reversed(raw_candles)]
    vols = [safe_float(c[5]) for c in reversed(raw_candles)]

    tr_list = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)
    if len(tr_list) >= 14:
        atr = sum(tr_list[-14:]) / 14
        factors["volatility_channel"]["atr_14"] = round(atr, 4)
        if factors["price"] > 0:
            factors["volatility_channel"]["atr_pct"] = round(atr / factors["price"] * 100, 2)

    diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in diffs]
    losses = [-d if d < 0 else 0 for d in diffs]
    if len(gains) >= 14:
        avg_g = sum(gains[-14:]) / 14
        avg_l = sum(losses[-14:]) / 14
        rs = (avg_g / avg_l) if avg_l > 0 else 100.0
        factors["trend_momentum"]["rsi_14"] = round(100.0 - (100.0 / (1.0 + rs)), 1)

    pv_sum = sum(closes[i] * vols[i] for i in range(len(closes)))
    v_sum = sum(vols)
    if v_sum > 0:
        vwap = pv_sum / v_sum
        factors["trend_momentum"]["vwap_bias_pct"] = round((factors["price"] - vwap) / vwap * 100, 2)

    if len(vols) >= 6:
        avg_v5 = sum(vols[-6:-1]) / 5
        if avg_v5 > 0:
            factors["volume_money_flow"]["vol_ratio_15m"] = round(vols[-1] / avg_v5, 2)

    obv = 0
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]: obv += vols[i]
        elif closes[i] < closes[i-1]: obv -= vols[i]
    factors["volume_money_flow"]["obv_flow"] = "BULL_FLOW" if obv > 0 else ("BEAR_FLOW" if obv < 0 else "NEUTRAL")

    if calculate_calculus is not None:
        try:
            c_res = calculate_calculus(closes, highs, lows, vols)
            if c_res.get("valid"):
                factors["calculus_dynamics"]["velocity"] = c_res.get("velocity", 0.0)
                factors["calculus_dynamics"]["acceleration"] = c_res.get("acceleration", 0.0)
                factors["calculus_dynamics"]["impulse"] = c_res.get("impulse", 0.0)
                factors["calculus_dynamics"]["jerk"] = c_res.get("jerk", 0.0)
                factors["calculus_dynamics"]["curvature"] = c_res.get("curvature", 0.0)
                factors["calculus_dynamics"]["power"] = c_res.get("power", 0.0)
                factors["calculus_dynamics"]["power_regime"] = c_res.get("power_regime", "STEADY_FLUX")
                factors["calculus_dynamics"]["regime"] = c_res.get("regime", "RANGE_LOW_VELOCITY")
                factors["calculus_dynamics"]["quality"] = c_res.get("quality", 0.0)
                factors["calculus_dynamics"]["direction"] = c_res.get("direction", 0)
                d_int = c_res.get("definite_integrals", {})
                factors["definite_integrals"]["energy_integral"] = d_int.get("energy_integral", 0.0)
                factors["definite_integrals"]["deviation_area_integral"] = d_int.get("deviation_area_integral", 0.0)
                factors["definite_integrals"]["volume_action_integral"] = d_int.get("volume_action_integral", 0.0)
                factors["definite_integrals"]["integral_regime"] = d_int.get("integral_regime", "BALANCED_ENERGY")
                p_th = c_res.get("probability_theory", {})
                factors["probability_theory"]["skewness"] = p_th.get("skewness", 0.0)
                factors["probability_theory"]["kurtosis"] = p_th.get("kurtosis", 0.0)
                factors["probability_theory"]["continuation_prob_pct"] = p_th.get("continuation_prob_pct", 50.0)
                factors["probability_theory"]["breakdown_prob_pct"] = p_th.get("breakdown_prob_pct", 50.0)
                factors["probability_theory"]["var_95_pct"] = p_th.get("var_95_pct", 1.5)
                factors["probability_theory"]["cvar_95_pct"] = p_th.get("cvar_95_pct", 2.2)
                factors["probability_theory"]["prob_regime"] = p_th.get("prob_regime", "GAUSSIAN_BALANCED")
                factors["probability_theory"]["is_fat_tail"] = p_th.get("is_fat_tail", False)
        except Exception:
            pass

    return closes, highs, lows, vols


# ------------------------------------------------------------ 序列派生


class DeriveSeriesTest(unittest.TestCase):
    def test_reversed_to_chronological(self):
        """**核心**：OKX newest-first → 必须按时间正序返回。"""
        candles = _series([1.0, 2.0, 3.0])
        closes, highs, lows, vols = derive_candle_series(candles, safe_float=_sf)
        self.assertEqual(closes, [1.0, 2.0, 3.0],
                         "忘了 reversed 会得到 [3,2,1]，且不会报错")

    def test_column_indices(self):
        candles = [[0, "9", "11", "8", "10", "7", 0, 0, 0]]
        closes, highs, lows, vols = derive_candle_series(candles, safe_float=_sf)
        self.assertEqual((closes, highs, lows, vols), ([10.0], [11.0], [8.0], [7.0]))

    def test_bad_values_become_zero(self):
        candles = [[0, "x", "x", "x", "x", "x", 0, 0, 0]]
        closes, highs, lows, vols = derive_candle_series(candles, safe_float=_sf)
        self.assertEqual((closes, highs, lows, vols), ([0.0], [0.0], [0.0], [0.0]))


class AtrTest(unittest.TestCase):
    def test_simple_average_of_last_14_tr(self):
        # 15 根、spread=1 → high-low=2、且 close 递增使 |h_i - c_{i-1}| 可能更大
        closes = [100.0 + i for i in range(20)]
        f = _factors()
        compute_15m_indicators(_series(closes), f, safe_float=_sf)
        # TR 的期望值独立算一遍
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        tr = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
              for i in range(1, len(closes))]
        self.assertEqual(f["volatility_channel"]["atr_14"], round(sum(tr[-14:]) / 14, 4))

    def test_atr_pct_uses_price(self):
        closes = [100.0 + i for i in range(20)]
        f = _factors(price=200.0)
        compute_15m_indicators(_series(closes), f, safe_float=_sf)
        atr = f["volatility_channel"]["atr_14"]
        self.assertEqual(f["volatility_channel"]["atr_pct"], round(atr / 200.0 * 100, 2))

    def test_atr_pct_skipped_when_price_zero(self):
        closes = [100.0 + i for i in range(20)]
        f = _factors(price=0.0)
        compute_15m_indicators(_series(closes), f, safe_float=_sf)
        self.assertIn("atr_14", f["volatility_channel"])
        self.assertNotIn("atr_pct", f["volatility_channel"], "price=0 → 不写 atr_pct")

    def test_fewer_than_14_tr_skips_atr(self):
        f = _factors()
        compute_15m_indicators(_series([100.0] * 14), f, safe_float=_sf)
        self.assertEqual(f["volatility_channel"], {}, "TR 只有 13 条 → 不写")


class RsiTest(unittest.TestCase):
    def test_all_gains_gives_99_by_convention(self):
        """**⚠️ 反直觉但正确**：`avg_l == 0` 时 `rs = 100.0`，于是

            RSI = 100 - 100/(1+100) = 100 - 0.990 = **99.0**，不是 100.0。

        我第一版断言 100.0 → 失败，然后**手算**才确认实现是对的：
        `rs=100` 只是"损失为 0"的一个**有限替身**，不是无穷大。
        若要真给 100 得写 `if avg_l == 0: return 100.0` —— 那是**行为变更**，不做。

        对称地，`avg_g == 0`（全跌）时 `rs = 0` → RSI = **0.0**（这个刚好是 0）。
        """
        f = _factors()
        compute_15m_indicators(_series([100.0 + i for i in range(20)], spread=_TIGHT),
                               f, safe_float=_sf)
        self.assertEqual(f["trend_momentum"]["rsi_14"], 99.0)

    def test_all_losses_gives_zero(self):
        f = _factors()
        compute_15m_indicators(_series([100.0 - i for i in range(20)], spread=_TIGHT),
                               f, safe_float=_sf)
        self.assertEqual(f["trend_momentum"]["rsi_14"], 0.0)

    def test_flat_gives_99_by_convention(self):
        """全平：`avg_g == avg_l == 0` → `avg_l > 0` 不成立 → `rs = 100` → RSI = **99.0**。

        **这是既有约定，既不是"正确"也不是 50** —— 记下来免得后人以为该是 50
        或 100。（我第一版正是这么以为的，两条都写错。）
        """
        f = _factors()
        compute_15m_indicators(_series([100.0] * 20, spread=_TIGHT), f, safe_float=_sf)
        self.assertEqual(f["trend_momentum"]["rsi_14"], 99.0)

    def test_rsi_uses_last_14_diffs(self):
        closes = [100.0] * 6 + [100.0 + i for i in range(1, 15)]
        f = _factors()
        compute_15m_indicators(_series(closes, spread=_TIGHT), f, safe_float=_sf)
        diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        g = [d if d > 0 else 0 for d in diffs]
        l = [-d if d < 0 else 0 for d in diffs]
        avg_g, avg_l = sum(g[-14:]) / 14, sum(l[-14:]) / 14
        rs = (avg_g / avg_l) if avg_l > 0 else 100.0
        self.assertEqual(f["trend_momentum"]["rsi_14"], round(100.0 - 100.0 / (1.0 + rs), 1))


class VwapTest(unittest.TestCase):
    def test_vwap_bias(self):
        closes = [100.0, 200.0]
        vols = [1.0, 3.0]
        f = _factors(price=200.0)
        compute_15m_indicators(_series(closes, vols), f, safe_float=_sf)
        vwap = (100.0 * 1.0 + 200.0 * 3.0) / 4.0
        self.assertEqual(f["trend_momentum"]["vwap_bias_pct"],
                         round((200.0 - vwap) / vwap * 100, 2))

    def test_zero_volume_skips_vwap(self):
        f = _factors()
        compute_15m_indicators(_series([100.0] * 20, [0.0] * 20), f, safe_float=_sf)
        self.assertNotIn("vwap_bias_pct", f["trend_momentum"], "v_sum=0 → 不写")


class VolRatioTest(unittest.TestCase):
    def test_ratio_uses_last_against_previous_five(self):
        vols = [10.0] * 5 + [20.0]
        f = _factors()
        compute_15m_indicators(_series([100.0] * 6, vols), f, safe_float=_sf)
        # avg_v5 = vols[-6:-1] = 前 5 个 = 10 → ratio = 20/10 = 2.0
        self.assertEqual(f["volume_money_flow"]["vol_ratio_15m"], 2.0)

    def test_needs_six_bars(self):
        f = _factors()
        compute_15m_indicators(_series([100.0] * 5), f, safe_float=_sf)
        self.assertNotIn("vol_ratio_15m", f["volume_money_flow"])

    def test_zero_avg_skips(self):
        f = _factors()
        compute_15m_indicators(_series([100.0] * 6, [0.0] * 6), f, safe_float=_sf)
        self.assertNotIn("vol_ratio_15m", f["volume_money_flow"])


class ObvTest(unittest.TestCase):
    def _obv(self, closes, vols):
        f = _factors()
        compute_15m_indicators(_series(closes, vols), f, safe_float=_sf)
        return f["volume_money_flow"]["obv_flow"]

    def test_bull(self):
        self.assertEqual(self._obv([100.0, 101.0, 102.0], [1.0, 2.0, 3.0]), "BULL_FLOW")

    def test_bear(self):
        self.assertEqual(self._obv([102.0, 101.0, 100.0], [1.0, 2.0, 3.0]), "BEAR_FLOW")

    def test_flat_is_neutral(self):
        self.assertEqual(self._obv([100.0] * 3, [1.0] * 3), "NEUTRAL")

    def test_equal_closes_contribute_nothing(self):
        self.assertEqual(self._obv([100.0, 100.0, 101.0], [5.0, 5.0, 1.0]), "BULL_FLOW")


class Pillar6Test(unittest.TestCase):
    def _ccc(self, result):
        return lambda c, h, l, v: result

    def test_valid_result_written(self):
        f = _factors()
        compute_15m_indicators(
            _series([100.0 + i for i in range(20)]), f, safe_float=_sf,
            calculate_calculus=self._ccc({
                "valid": True, "velocity": 1.5, "acceleration": 0.5,
                "impulse": 3.0, "jerk": 0.2, "curvature": 0.1, "power": 2.0,
                "power_regime": "ACCEL", "regime": "BULL_ACCELERATING",
                "quality": 0.9, "direction": 1,
                "definite_integrals": {"energy_integral": 4.0,
                                       "deviation_area_integral": 1.1,
                                       "volume_action_integral": 2.2,
                                       "integral_regime": "X"},
                "probability_theory": {"skewness": 0.3, "kurtosis": 3.9,
                                       "continuation_prob_pct": 80.0,
                                       "breakdown_prob_pct": 20.0,
                                       "var_95_pct": 1.1, "cvar_95_pct": 1.9,
                                       "prob_regime": "Y", "is_fat_tail": True},
            }))
        self.assertEqual(f["calculus_dynamics"]["velocity"], 1.5)
        self.assertEqual(f["calculus_dynamics"]["regime"], "BULL_ACCELERATING")
        self.assertEqual(f["definite_integrals"]["energy_integral"], 4.0)
        self.assertEqual(f["probability_theory"]["continuation_prob_pct"], 80.0)
        self.assertTrue(f["probability_theory"]["is_fat_tail"])

    def test_invalid_result_leaves_defaults(self):
        f = _factors()
        compute_15m_indicators(_series([100.0 + i for i in range(20)]), f,
                               safe_float=_sf,
                               calculate_calculus=self._ccc({"valid": False}))
        self.assertEqual(f["calculus_dynamics"], {}, "valid=False → 整段不写")
        self.assertEqual(f["probability_theory"], {})

    def test_engine_raising_keeps_indicator_results(self):
        """**核心边界**：Pillar 6 引擎炸了，前面算好的 ATR/RSI **必须保留**。"""
        def boom(c, h, l, v):
            raise RuntimeError("engine down")
        f = _factors()
        compute_15m_indicators(_series([100.0 + i for i in range(20)]), f,
                               safe_float=_sf, calculate_calculus=boom)
        self.assertIn("atr_14", f["volatility_channel"], "ATR 必须保留")
        self.assertIn("rsi_14", f["trend_momentum"], "RSI 必须保留")
        self.assertIn("obv_flow", f["volume_money_flow"], "OBV 必须保留")
        self.assertEqual(f["calculus_dynamics"], {})

    def test_none_engine_skips_silently(self):
        f = _factors()
        compute_15m_indicators(_series([100.0 + i for i in range(20)]), f,
                               safe_float=_sf, calculate_calculus=None)
        self.assertEqual(f["calculus_dynamics"], {})
        self.assertIn("atr_14", f["volatility_channel"])

    def test_missing_keys_use_documented_defaults(self):
        f = _factors()
        compute_15m_indicators(_series([100.0 + i for i in range(20)]), f,
                               safe_float=_sf,
                               calculate_calculus=self._ccc({"valid": True}))
        self.assertEqual(f["calculus_dynamics"]["regime"], "RANGE_LOW_VELOCITY")
        self.assertEqual(f["calculus_dynamics"]["power_regime"], "STEADY_FLUX")
        self.assertEqual(f["definite_integrals"]["integral_regime"], "BALANCED_ENERGY")
        self.assertEqual(f["probability_theory"]["continuation_prob_pct"], 50.0)
        self.assertEqual(f["probability_theory"]["var_95_pct"], 1.5)
        self.assertEqual(f["probability_theory"]["cvar_95_pct"], 2.2)
        self.assertFalse(f["probability_theory"]["is_fat_tail"])

    def test_engine_receives_the_derived_series(self):
        seen = {}
        def ccc(c, h, l, v):
            seen["c"], seen["h"], seen["l"], seen["v"] = c, h, l, v
            return {"valid": False}
        closes = [100.0 + i for i in range(20)]
        compute_15m_indicators(_series(closes), _factors(), safe_float=_sf,
                               calculate_calculus=ccc)
        self.assertEqual(seen["c"], closes, "引擎必须收到**正序** closes")
        self.assertEqual(len(seen["h"]), len(closes))


class RandomParityTest(unittest.TestCase):
    def test_parity_24000(self):
        rng = random.Random(32001)
        for i in range(24000):
            n = rng.randint(1, 30)
            # ⚠️ close 必须 > 0：真实 VWAP = pv_sum/v_sum，若 close 全 0 而 vols 非 0
            # 则 vwap=0 → 除零。我第一版喂了 0.0，得到 ZeroDivisionError ——
            # 那是**我先声明契约之外的输入**（门面保证 K 线来自交易所）。
            closes = [rng.choice([100.0, 100.5, 99.5, 1e6]) for _ in range(n)]
            vols = [rng.choice([0.0, 1.0, 1000.0]) for _ in range(n)]
            spread = rng.choice([0.1, 1.0, 0.0])
            candles = _series(closes, vols, spread=spread)
            price = rng.choice([0.0, 100.0, 1e9])
            res = rng.choice([None, {"valid": False}, {"valid": True}])
            ccc = (None if res is None else (lambda c, h, l, v, _r=res: _r))

            fa, fb = _factors(price), _factors(price)
            ra = _legacy(copy.deepcopy(candles), fa, _sf, ccc)
            rb = compute_15m_indicators(copy.deepcopy(candles), fb, safe_float=_sf,
                                        calculate_calculus=ccc)
            self.assertEqual(fa, fb, f"第{i}组 factors 分叉")
            self.assertEqual(ra, rb, f"第{i}组返回序列分叉")

    def test_parity_real_shaped_candles(self):
        """用真实返回的 K 线形状（9 列、数字为字符串）再跑一轮。"""
        rng = random.Random(32002)
        for i in range(4000):
            n = rng.randint(14, 26)
            closes = [1000.0 + rng.uniform(-50, 50) for _ in range(n)]
            candles = _series(closes, [rng.uniform(1, 5000) for _ in range(n)],
                              spread=rng.uniform(0.5, 5))
            fa, fb = _factors(1000.0), _factors(1000.0)
            _legacy(copy.deepcopy(candles), fa, _sf)
            compute_15m_indicators(copy.deepcopy(candles), fb, safe_float=_sf)
            self.assertEqual(fa, fb, f"第{i}组分叉")


class WiringTest(unittest.TestCase):
    def test_impl_in_submodule_not_facade(self):
        facade_src = FACADE.read_text(encoding="utf-8")
        mod_src = MODULE.read_text(encoding="utf-8")
        self.assertIn("def compute_15m_indicators(", mod_src)
        self.assertNotIn("def compute_15m_indicators(", facade_src)
        self.assertIn("compute_15m_indicators(", facade_src)

    def test_fetch_candles_stays_in_facade(self):
        """**核心**：取数必须留在门面 —— 否则 patch.object 缝断掉。"""
        facade_src = FACADE.read_text(encoding="utf-8")
        mod_src = MODULE.read_text(encoding="utf-8")
        self.assertIn('fetch_candles(inst_id, bar="15m", limit=24)', facade_src)
        # ⚠️ 用**AST 导入**判定，不用文本：本模块的 docstring **故意**提到
        # `fetch_candles(...)` 作为"这个调用留在门面"的说明。文本匹配会误报
        # （这是本阶段第四次栽在同一处 —— 见 §40.7 的教训）。
        tree = ast.parse(mod_src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.asname or a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported |= {a.asname or a.name for a in node.names}
        self.assertNotIn("fetch_candles", imported,
                         "子模块不得 import 取数函数（那会让门面的 patch 缝失效）")
        self.assertNotIn("market_data_service", {n.module for n in ast.walk(tree)
                                                 if isinstance(n, ast.ImportFrom)})

    def test_facade_no_longer_contains_indicator_math(self):
        facade_src = FACADE.read_text(encoding="utf-8")
        for gone in ("tr_list = []", 'factors["volume_money_flow"]["obv_flow"] =',
                     "vwap_bias_pct", "vol_ratio_15m"):
            self.assertNotIn(gone, facade_src, f"门面仍残留 {gone!r}")

    def test_sys_path_append_no_longer_inside_the_loop(self):
        """原实现在循环体内反复 `sys.path.append` —— 现已收口为 memo 解析。"""
        tree = ast.parse(FACADE.read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "compute_instrument_factors")
        appends = [n.lineno for n in ast.walk(fn)
                   if isinstance(n, ast.Call)
                   and getattr(n.func, "attr", None) == "append"
                   and getattr(getattr(n.func, "value", None), "attr", None) == "path"]
        self.assertEqual(appends, [], f"循环体内仍有 sys.path.append: {appends}")

    def test_resolver_is_memoized(self):
        import scripts.factor_library as fl
        first = fl._resolve_calculate_calculus()
        second = fl._resolve_calculate_calculus()
        self.assertIs(first, second, "解析结果应 memo（同一函数对象）")

    def test_module_does_not_import_fetching(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top |= {a.asname or a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                top |= {a.asname or a.name for a in node.names}
        for forbidden in ("market_data_service", "requests", "urllib", "os", "sys"):
            self.assertNotIn(forbidden, top, f"纯计算模块不应 import {forbidden}")


if __name__ == "__main__":
    unittest.main()

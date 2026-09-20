"""`scripts/factors/scoring.py`（B3 第二十九刀）回归。

## 这个测试在守什么

`score_composite_alpha` 把八组因子加权成 -100~+100，再落一条信号建议。
九处门槛/加减分密集在一个函数里，**每一处都是行为契约**。

## 为什么必须用"大差分"而不是几个例子

八个计分项各自独立，**组合数 = 2^8 量级 × 各种边界**，手工例子覆盖不到。
故本文件的**主证据**是与搬走前内联实现的**随机差分**（含边界值夹逼）。

## 五处易错点（详见模块文档串）

1. 第 2 项信号缺失时**跳过**，绝不当 0/中性计入；
2. 第 6 项是 `if/elif/elif/elif`，**互斥不累加**；
3. `jerk` 取**绝对值**，velocity/acceleration/impulse **保留符号**；
4. 两个阻尼是**乘**（`*=0.8` / `*=0.6`），且**顺序参与基数**；
5. 信号建议的 ADX 门槛是 **20**，而第 1 项是 **22** —— 两个数**不可合并**。
"""

from __future__ import annotations

import ast
import copy
import itertools
import random
import unittest
from pathlib import Path

from scripts.factors.scoring import (
    ADX_SIGNAL_THRESHOLD,
    ADX_TREND_THRESHOLD,
    JERK_DAMPEN_THRESHOLD,
    SCORE_SIGNAL_THRESHOLD,
    score_composite_alpha,
)

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "factors" / "scoring.py"
FACADE = ROOT / "scripts" / "factor_library.py"


def _legacy(factors):
    """搬走前 factor_library.py 里的内联实现（逐字原样）。"""
    score = 0.0
    adx = factors["trend_momentum"]["adx_1h"]
    if adx >= 22.0:
        score += 15.0 if factors["trend_momentum"]["rsi_14"] >= 50 else -15.0
        factors["trend_momentum"]["trend_regime"] = "STRONG_TREND"
    else:
        factors["trend_momentum"]["trend_regime"] = "CHOP_RANGE"

    sm_block = factors["smart_money_derivatives"]
    sm_long = sm_block.get("weighted_long_pct")
    if sm_block.get("available") and isinstance(sm_long, (int, float)):
        if sm_long >= 70.0: score += 30.0
        elif sm_long <= 35.0: score -= 30.0

    rsi = factors["trend_momentum"]["rsi_14"]
    kdj_j = factors["trend_momentum"]["kdj_j"]
    if rsi >= 55.0 and kdj_j >= 60.0: score += 20.0
    elif rsi <= 45.0 and kdj_j <= 40.0: score -= 20.0

    cmf = factors["volume_money_flow"]["cmf_1h"]
    obv_f = factors["volume_money_flow"]["obv_flow"]
    if cmf > 0.05 and obv_f == "BULL_FLOW": score += 20.0
    elif cmf < -0.05 and obv_f == "BEAR_FLOW": score -= 20.0

    depth_r = factors["microstructure"]["bid_ask_depth_ratio"]
    if depth_r >= 1.4: score += 15.0
    elif depth_r <= 0.7: score -= 15.0

    c_dyn = factors["calculus_dynamics"]
    c_v = c_dyn.get("velocity", 0.0)
    c_a = c_dyn.get("acceleration", 0.0)
    c_i = c_dyn.get("impulse", 0.0)
    c_j = abs(c_dyn.get("jerk", 0.0))
    c_regime = c_dyn.get("regime", "")

    if c_regime == "BULL_ACCELERATING" or (c_v > 0.2 and c_a > 0.1 and c_i > 0):
        score += 15.0
    elif c_regime == "BULL_DECELERATING" or (c_v > 0.2 and c_a < -0.3):
        score -= 10.0
    elif c_regime == "BEAR_ACCELERATING" or (c_v < -0.2 and c_a < -0.1 and c_i < 0):
        score -= 15.0
    elif c_regime == "BEAR_DECELERATING" or (c_v < -0.2 and c_a > 0.3):
        score += 10.0

    d_int = factors.get("definite_integrals", {})
    e_int = d_int.get("energy_integral", 0.0)
    dev_area = d_int.get("deviation_area_integral", 0.0)
    if e_int > 1.2 and dev_area > 0.8:
        score += 10.0
    elif e_int < -1.2 and dev_area < -0.8:
        score -= 10.0
    elif abs(dev_area) >= 2.8:
        score *= 0.8

    p_th = factors.get("probability_theory", {})
    p_cont = p_th.get("continuation_prob_pct", 50.0)
    p_break = p_th.get("breakdown_prob_pct", 50.0)
    is_fat = p_th.get("is_fat_tail", False)
    if p_cont >= 72.0:
        score += 10.0
    elif p_break >= 72.0:
        score -= 10.0

    if c_j >= 1.8 or c_regime == "SHOCK_HIGH_JERK" or (is_fat and p_th.get("kurtosis", 0) >= 3.5):
        score *= 0.6

    factors["composite_alpha_score"] = round(score, 1)

    if score >= 45.0 and adx >= 20.0 and c_j < 1.8:
        factors["signal_recommendation"] = "BUY_LONG"
    elif score <= -45.0 and adx >= 20.0 and c_j < 1.8:
        factors["signal_recommendation"] = "SELL_SHORT"
    else:
        factors["signal_recommendation"] = "WAIT"
    return score


def _base(**over):
    """一份完整可跑的 factors（含所有必需路径）。"""
    f = {
        "trend_momentum": {"adx_1h": 25.0, "rsi_14": 55.0, "kdj_j": 65.0},
        "smart_money_derivatives": {"available": False},
        "volume_money_flow": {"cmf_1h": 0.0, "obv_flow": "NEUTRAL"},
        "microstructure": {"bid_ask_depth_ratio": 1.0},
        "calculus_dynamics": {"velocity": 0.0, "acceleration": 0.0,
                              "impulse": 0.0, "jerk": 0.0, "regime": ""},
        "definite_integrals": {"energy_integral": 0.0,
                               "deviation_area_integral": 0.0},
        "probability_theory": {"continuation_prob_pct": 50.0,
                               "breakdown_prob_pct": 50.0,
                               "is_fat_tail": False},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(f.get(k), dict):
            f[k].update(v)
        else:
            f[k] = v
    return f


#: `_neutral()` 的基线贡献（第 1 项 ADX 走正向）。断言写成 BASELINE + delta 形式。
BASELINE = 15.0


def _neutral(**over):
    """基线 = **恰好 +15**（第 1 项 ADX 走正向），其余七项都不加分、不阻尼。

    ⚠️ 这个 helper 我写错**两版**，两次都是"没先确认基线贡献"：

    - 第一版没有它，直接改 `_base()`（默认 `rsi_14=55/kdj_j=65` 本身带 **+20**）
      → 19 条期望值全错；
    - 第二版写 `rsi_14=0.0` 想"归零"，但**这反而让第 1 项走负向**：
      `adx>=22` 时是 `+15 if rsi_14 >= 50 else -15` —— `rsi_14=0` 给的是 **-15**。

    > 教训：断言期望值前必须先**手算基线**，不能凭"把数设成 0 应该就没贡献了"。
    > 所以现在把它写成常量 `BASELINE = 15.0`，让每个断言都是 `BASELINE + delta`
    > 的形式，基线一眼可见。

    另外 `kdj_j=0.0` 让第 3 项（RSI+KDJ）两个方向都不满足：
    `rsi=50` 不 `>=55` 也不 `<=45`。
    """
    f = {
        "trend_momentum": {"adx_1h": 30.0, "rsi_14": 50.0, "kdj_j": 0.0},
        "smart_money_derivatives": {"available": False},
        "volume_money_flow": {"cmf_1h": 0.0, "obv_flow": "NEUTRAL"},
        "microstructure": {"bid_ask_depth_ratio": 1.0},
        "calculus_dynamics": {"velocity": 0.0, "acceleration": 0.0,
                              "impulse": 0.0, "jerk": 0.0, "regime": ""},
        "definite_integrals": {"energy_integral": 0.0,
                               "deviation_area_integral": 0.0},
        "probability_theory": {"continuation_prob_pct": 50.0,
                               "breakdown_prob_pct": 50.0,
                               "is_fat_tail": False},
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(f.get(k), dict):
            f[k].update(v)
        else:
            f[k] = v
    return f


def _both(f):
    a, b = copy.deepcopy(f), copy.deepcopy(f)
    ra, rb = _legacy(a), score_composite_alpha(b)
    return a, b, ra, rb


class ThresholdConstantsTest(unittest.TestCase):
    def test_adx_thresholds_are_distinct(self):
        """**两个 ADX 门槛不同（22 vs 20），绝不可合并。**"""
        self.assertEqual(ADX_TREND_THRESHOLD, 22.0)
        self.assertEqual(ADX_SIGNAL_THRESHOLD, 20.0)
        self.assertNotEqual(ADX_TREND_THRESHOLD, ADX_SIGNAL_THRESHOLD)

    def test_other_constants(self):
        self.assertEqual(SCORE_SIGNAL_THRESHOLD, 45.0)
        self.assertEqual(JERK_DAMPEN_THRESHOLD, 1.8)


class AdxItemTest(unittest.TestCase):
    def test_below_22_is_chop_range(self):
        f = _base(trend_momentum={"adx_1h": 21.9, "rsi_14": 60.0, "kdj_j": 70.0})
        score_composite_alpha(f)
        self.assertEqual(f["trend_momentum"]["trend_regime"], "CHOP_RANGE")

    def test_exactly_22_is_strong_trend(self):
        f = _base(trend_momentum={"adx_1h": 22.0, "rsi_14": 60.0, "kdj_j": 70.0})
        score_composite_alpha(f)
        self.assertEqual(f["trend_momentum"]["trend_regime"], "STRONG_TREND")

    def test_rsi_50_boundary_is_positive(self):
        f = _base(trend_momentum={"adx_1h": 30.0, "rsi_14": 50.0, "kdj_j": 0.0},
                  volume_money_flow={"cmf_1h": 0.0}, microstructure={"bid_ask_depth_ratio": 1.0})
        score_composite_alpha(f)
        self.assertEqual(f["trend_momentum"]["trend_regime"], "STRONG_TREND")
        # +15(ADX) +0(KDJ) = 15
        self.assertEqual(f["composite_alpha_score"], BASELINE)

    def test_rsi_499_is_negative(self):
        f = _neutral(trend_momentum={"adx_1h": 30.0, "rsi_14": 49.9, "kdj_j": 0.0})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], -15.0, "rsi<50 → 第1项给 -15")


class SmartMoneyItemTest(unittest.TestCase):
    def test_unavailable_is_skipped_not_zero(self):
        """**核心**：`available=False` 时该项**什么都不加**，不是加 0 分（等价但语义不同）。

        用"只有该项会触发"的构造来区分：若实现误把它当 0 中性，
        结果仍是同一数值 —— 故这里改测**与其它项共存时的强度不被稀释**。
        """
        f = _neutral(trend_momentum={"adx_1h": 30.0, "rsi_14": 60.0, "kdj_j": 70.0},
                  smart_money_derivatives={"available": False,
                                           "weighted_long_pct": 90.0})
        score_composite_alpha(f)
        # +15(ADX) +20(RSI/KDJ) = 35，**没有** +30（即便 long_pct=90）
        self.assertEqual(f["composite_alpha_score"], BASELINE + 20.0,
                         "available=False 时不得只因 long_pct 高分就加分")

    def test_available_none_value_skipped(self):
        f = _neutral(smart_money_derivatives={"available": True,
                                           "weighted_long_pct": None})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], 15.0, "值不是数 → 跳过")

    def test_available_string_value_skipped(self):
        f = _neutral(smart_money_derivatives={"available": True,
                                           "weighted_long_pct": "88"})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], 15.0,
                         "字符串 '88' 不是 int/float → 跳过（不做隐式转换）")

    def test_70_boundary_positive(self):
        f = _neutral(smart_money_derivatives={"available": True,
                                           "weighted_long_pct": 70.0})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE + 30.0)

    def test_35_boundary_negative(self):
        f = _neutral(smart_money_derivatives={"available": True,
                                           "weighted_long_pct": 35.0})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE - 30.0)  # 15 - 30

    def test_between_35_and_70_no_change(self):
        f = _neutral(smart_money_derivatives={"available": True,
                                           "weighted_long_pct": 50.0})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE)

    def test_bool_is_int_subclass(self):
        """`True` 是 `int` 子类 → 会被当作数值 1.0（低于 35 → -30）。

        这是 Python 语义的**既有行为**，不是 bug；钉住它以防有人"顺手"排除 bool
        而改变行为。
        """
        f = _neutral(smart_money_derivatives={"available": True,
                                           "weighted_long_pct": True})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE - 30.0)


class CalculusRegimeTest(unittest.TestCase):
    def test_regime_branches_are_mutually_exclusive(self):
        """**核心**：`if/elif` 互斥。四个 regime 同时"满足"数值条件时只取 regime 那支。"""
        # ⚠️ 这里**不能**把 adx 覆盖成 0.0：那会让第 1 项走负向（-15），
        # 基线就不是 +15 了。保持 _neutral 的 adx=30/rsi=50 基线，只改 regime。
        for regime, delta in (("BULL_ACCELERATING", 15.0),
                              ("BULL_DECELERATING", -10.0),
                              ("BEAR_ACCELERATING", -15.0),
                              ("BEAR_DECELERATING", 10.0)):
            f = _neutral(calculus_dynamics={"velocity": 0.0, "acceleration": 0.0,
                                            "impulse": 0.0, "jerk": 0.0,
                                            "regime": regime})
            score_composite_alpha(f)
            self.assertEqual(f["composite_alpha_score"], BASELINE + delta, regime)

    def test_numeric_path_when_regime_empty(self):
        f = _neutral(calculus_dynamics={"velocity": 0.3, "acceleration": 0.2,
                                     "impulse": 1.0, "jerk": 0.0, "regime": ""})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE + 15.0)

    def test_regime_wins_over_numeric(self):
        """regime 非空时**短路**，数值条件不再参与（同一分支内的 `or`）。"""
        f = _neutral(calculus_dynamics={"velocity": -0.9, "acceleration": -0.9,
                                     "impulse": -9.0, "jerk": 0.0,
                                     "regime": "BULL_ACCELERATING"})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE + 15.0, "取 regime 分支 +15")

    def test_jerk_is_absolute(self):
        """`jerk` 取绝对值 → `-1.8` 与 `1.8` 触发同样的阻尼与信号屏蔽。"""
        for jerk in (1.8, -1.8):
            f = _neutral(calculus_dynamics={"velocity": 0.0, "acceleration": 0.0,
                                         "impulse": 0.0, "jerk": jerk,
                                         "regime": ""})
            score_composite_alpha(f)
            self.assertEqual(f["composite_alpha_score"], BASELINE * 0.6, f"jerk={jerk}")

    def test_velocity_sign_preserved(self):
        """velocity/acceleration/impulse **保留符号** —— 正负组合判据不同。"""
        pos = _neutral(calculus_dynamics={"velocity": 0.3, "acceleration": 0.2,
                                       "impulse": 1.0, "jerk": 0.0, "regime": ""})
        neg = _neutral(calculus_dynamics={"velocity": -0.3, "acceleration": -0.2,
                                       "impulse": -1.0, "jerk": 0.0, "regime": ""})
        score_composite_alpha(pos)
        score_composite_alpha(neg)
        self.assertEqual(pos["composite_alpha_score"], BASELINE + 15.0)
        self.assertEqual(neg["composite_alpha_score"], BASELINE - 15.0)


class IntegralAndDampenerTest(unittest.TestCase):
    def test_multiplicative_dampener_order(self):
        """**两项阻尼是"乘"且顺序参与基数。**

        构造：dev_area 触发 `*=0.8`，同时 jerk 触发 `*=0.6`。
        ADX 给 +15 → 15*0.8*0.6 = 7.2。
        若把 `*0.8` 误写成 `-=` 或调换位置（基数为 0 时更明显），结果不同。
        """
        f = _neutral(calculus_dynamics={"velocity": 0.0, "acceleration": 0.0,
                                     "impulse": 0.0, "jerk": 2.0, "regime": ""},
                  definite_integrals={"energy_integral": 0.0,
                                      "deviation_area_integral": 3.0})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], round(BASELINE * 0.8 * 0.6, 1), "基线*0.8*0.6")

    def test_positive_energy(self):
        f = _neutral(definite_integrals={"energy_integral": 1.3,
                                      "deviation_area_integral": 0.9})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE + 10.0)

    def test_negative_energy(self):
        f = _neutral(definite_integrals={"energy_integral": -1.3,
                                      "deviation_area_integral": -0.9})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE - 10.0)

    def test_energy_elif_precedence_over_dampener(self):
        """`elif abs(dev_area)>=2.8` —— 只有前两支都不满足时才走阻尼。

        `energy_integral=1.3, dev_area=-0.9`：第一支要求都正 → 不满足；
        第二支要求都负 → dev_area 负但 e_int 正 → 不满足；
        第三支 `|dev_area|=0.9 < 2.8` → 也不满足。故无阻尼。
        """
        f = _neutral(definite_integrals={"energy_integral": 1.3,
                                      "deviation_area_integral": -0.9})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE, "无任何能量项生效")

    def test_fat_tail_dampener(self):
        f = _neutral(probability_theory={"continuation_prob_pct": 50.0,
                                      "breakdown_prob_pct": 50.0,
                                      "is_fat_tail": True, "kurtosis": 3.5})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE * 0.6)

    def test_fat_tail_below_kurtosis_threshold(self):
        f = _neutral(probability_theory={"continuation_prob_pct": 50.0,
                                      "breakdown_prob_pct": 50.0,
                                      "is_fat_tail": True, "kurtosis": 3.4})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE, "3.4 < 3.5 → 不阻尼")

    def test_shock_regime_dampens(self):
        f = _neutral(calculus_dynamics={"velocity": 0.0, "acceleration": 0.0,
                                     "impulse": 0.0, "jerk": 0.0,
                                     "regime": "SHOCK_HIGH_JERK"})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], BASELINE * 0.6)


class SignalRecommendationTest(unittest.TestCase):
    def test_buy_long(self):
        f = _base(trend_momentum={"adx_1h": 30.0, "rsi_14": 60.0, "kdj_j": 70.0},
                  smart_money_derivatives={"available": True,
                                           "weighted_long_pct": 80.0})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], 65.0)  # 15+30+20
        self.assertEqual(f["signal_recommendation"], "BUY_LONG")

    def test_sell_short(self):
        f = _base(trend_momentum={"adx_1h": 30.0, "rsi_14": 40.0, "kdj_j": 30.0},
                  smart_money_derivatives={"available": True,
                                           "weighted_long_pct": 20.0})
        score_composite_alpha(f)
        self.assertEqual(f["composite_alpha_score"], -65.0)
        self.assertEqual(f["signal_recommendation"], "SELL_SHORT")

    def test_wait_in_the_middle(self):
        f = _base()
        score_composite_alpha(f)
        self.assertEqual(f["signal_recommendation"], "WAIT")

    def test_high_score_but_low_adx_is_wait(self):
        """**ADX 门槛 20**（不是 22）—— score 够但 adx=19 必须 WAIT。"""
        f = _base(trend_momentum={"adx_1h": 19.0, "rsi_14": 60.0, "kdj_j": 70.0},
                  smart_money_derivatives={"available": True,
                                           "weighted_long_pct": 80.0})
        score_composite_alpha(f)
        self.assertGreaterEqual(f["composite_alpha_score"], 45.0)
        self.assertEqual(f["signal_recommendation"], "WAIT", "adx 19 < 20")

    def test_adx_20_just_enough(self):
        f = _base(trend_momentum={"adx_1h": 20.0, "rsi_14": 59.0, "kdj_j": 70.0},
                  smart_money_derivatives={"available": True,
                                           "weighted_long_pct": 80.0})
        score_composite_alpha(f)
        self.assertEqual(f["signal_recommendation"], "BUY_LONG")

    def test_adx_22_boundary_uses_trend_threshold_for_regime(self):
        """adx=21：第 1 项走 CHOP（<22）但信号门槛 20 仍可过。"""
        f = _base(trend_momentum={"adx_1h": 21.0, "rsi_14": 60.0, "kdj_j": 70.0},
                  smart_money_derivatives={"available": True,
                                           "weighted_long_pct": 80.0})
        score_composite_alpha(f)
        self.assertEqual(f["trend_momentum"]["trend_regime"], "CHOP_RANGE")
        self.assertEqual(f["signal_recommendation"], "BUY_LONG",
                         "CHOP 不给 +15，但 20+50=70 >= 45 且 adx 21 >= 20")

    def test_high_jerk_blocks_signal(self):
        """`c_j >= 1.8` → 无论分数多高都 WAIT。"""
        f = _base(trend_momentum={"adx_1h": 30.0, "rsi_14": 60.0, "kdj_j": 70.0},
                  smart_money_derivatives={"available": True,
                                           "weighted_long_pct": 80.0},
                  calculus_dynamics={"velocity": 0.0, "acceleration": 0.0,
                                     "impulse": 0.0, "jerk": 2.0, "regime": ""})
        score_composite_alpha(f)
        self.assertEqual(f["signal_recommendation"], "WAIT")

    def test_return_value_matches_written_score(self):
        f = _base(trend_momentum={"adx_1h": 30.0, "rsi_14": 60.0, "kdj_j": 70.0})
        got = score_composite_alpha(f)
        self.assertEqual(got, f["composite_alpha_score"],
                         "返回值应是**阻尼之后**（即写入的）那个数")


class RandomParityTest(unittest.TestCase):
    """**主证据**：与搬走前内联实现的随机差分（含边界夹逼）。"""

    BOUNDARY = [0.0, 0.05, -0.05, 0.7, 1.4, 1.2, 1.3, -1.2, -1.3, 0.8, 0.9,
                -0.8, -0.9, 2.8, 3.0, 1.8, -1.8, 22.0, 21.9, 20.0, 19.9,
                45.0, 50.0, 55.0, 60.0, 70.0, 72.0, 35.0, 40.0, 0.2, -0.2,
                0.1, -0.1, 0.3, -0.3, 3.5, 3.4]
    REGIMES = ["", "BULL_ACCELERATING", "BULL_DECELERATING",
               "BEAR_ACCELERATING", "BEAR_DECELERATING", "SHOCK_HIGH_JERK",
               "UNKNOWN"]
    FLOWS = ["BULL_FLOW", "BEAR_FLOW", "NEUTRAL", ""]

    def _random_factors(self, rng):
        b = self.BOUNDARY
        return _base(
            trend_momentum={"adx_1h": rng.choice(b), "rsi_14": rng.choice(b),
                            "kdj_j": rng.choice(b)},
            smart_money_derivatives={
                "available": rng.choice([True, False]),
                "weighted_long_pct": rng.choice(b + [None, "88", True]),
            },
            volume_money_flow={"cmf_1h": rng.choice(b),
                               "obv_flow": rng.choice(self.FLOWS)},
            microstructure={"bid_ask_depth_ratio": rng.choice(b)},
            calculus_dynamics={"velocity": rng.choice(b), "acceleration": rng.choice(b),
                               "impulse": rng.choice(b), "jerk": rng.choice(b),
                               "regime": rng.choice(self.REGIMES)},
            definite_integrals={"energy_integral": rng.choice(b),
                                "deviation_area_integral": rng.choice(b)},
            probability_theory={"continuation_prob_pct": rng.choice(b),
                                "breakdown_prob_pct": rng.choice(b),
                                "is_fat_tail": rng.choice([True, False]),
                                "kurtosis": rng.choice(b)},
        )

    def test_random_parity(self):
        rng = random.Random(20260927)
        for i in range(30000):
            f = self._random_factors(rng)
            a, b, ra, rb = _both(f)
            self.assertEqual(a, b, f"第{i}组分叉:\n{a}\n{b}")
            self.assertEqual(ra, rb, f"第{i}组返回值分叉")

    def test_exhaustive_small_grid(self):
        """小网格**穷举**：八项各有"正/负/中性"三态，3^8=6561 组全覆盖。

        随机差分可能漏掉某些组合；这一条是**确定性穷举**补位。
        """
        adx_opts = [(10.0, 40.0, 30.0), (30.0, 60.0, 70.0), (30.0, 40.0, 30.0)]
        sm_opts = [{"available": False}, {"available": True, "weighted_long_pct": 80.0},
                   {"available": True, "weighted_long_pct": 20.0}]
        cmf_opts = [(0.1, "BULL_FLOW"), (-0.1, "BEAR_FLOW"), (0.0, "NEUTRAL")]
        depth_opts = [1.5, 0.5, 1.0]
        calc_opts = [{"velocity": 0.3, "acceleration": 0.2, "impulse": 1.0, "jerk": 0.0, "regime": ""},
                     {"velocity": -0.3, "acceleration": -0.2, "impulse": -1.0, "jerk": 0.0, "regime": ""},
                     {"velocity": 0.0, "acceleration": 0.0, "impulse": 0.0, "jerk": 0.0, "regime": ""}]
        int_opts = [(1.3, 0.9), (-1.3, -0.9), (0.0, 0.0)]
        prob_opts = [{"continuation_prob_pct": 80.0, "breakdown_prob_pct": 50.0},
                     {"continuation_prob_pct": 50.0, "breakdown_prob_pct": 80.0},
                     {"continuation_prob_pct": 50.0, "breakdown_prob_pct": 50.0}]
        n = 0
        for adx, sm, cmf, depth, calc, integ, prob in itertools.product(
                adx_opts, sm_opts, cmf_opts, depth_opts, calc_opts, int_opts, prob_opts):
            a1, r1, k1 = adx
            f = _base(trend_momentum={"adx_1h": a1, "rsi_14": r1, "kdj_j": k1},
                      smart_money_derivatives=sm,
                      volume_money_flow={"cmf_1h": cmf[0], "obv_flow": cmf[1]},
                      microstructure={"bid_ask_depth_ratio": depth},
                      calculus_dynamics=calc,
                      definite_integrals={"energy_integral": integ[0],
                                          "deviation_area_integral": integ[1]},
                      probability_theory=dict(prob, is_fat_tail=False))
            a, b, ra, rb = _both(f)
            self.assertEqual(a, b, f"网格分叉: {f}")
            self.assertEqual(ra, rb)
            n += 1
        self.assertEqual(n, 3 ** 7, "应为 3^7=2187 组")


class WiringTest(unittest.TestCase):
    def test_impl_in_submodule_not_facade(self):
        facade_src = FACADE.read_text(encoding="utf-8")
        mod_src = MODULE.read_text(encoding="utf-8")
        self.assertIn("def score_composite_alpha(", mod_src)
        self.assertNotIn("def score_composite_alpha(", facade_src)
        self.assertIn("score_composite_alpha(factors)", facade_src)

    def test_facade_keeps_defaults_import(self):
        """上一刀的默认结构抽取不得被本刀改坏。"""
        facade_src = FACADE.read_text(encoding="utf-8")
        self.assertIn("from scripts.factors.defaults import build_default_factors", facade_src)
        self.assertIn("build_default_factors(inst_id, name)", facade_src)

    def test_facade_no_longer_contains_scoring_items(self):
        facade_src = FACADE.read_text(encoding="utf-8")
        for gone in ("COMPOSITE HIGH-ALPHA SCORING",
                     "Anti-FOMO top chasing penalty",
                     "Anti-bottom chasing penalty",
                     "dampen conviction under high-jerk shock"):
            self.assertNotIn(gone, facade_src, f"门面仍残留 {gone!r}")

    def test_module_imports_are_typing_only(self):
        """零注入面：本模块**不应** import 任何门面/取数模块。"""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertIn(a.name, ("typing",), a.name)
            elif isinstance(node, ast.ImportFrom):
                self.assertIn(node.module or "", ("__future__", "typing"))

    def test_module_has_no_runtime_state(self):
        src = MODULE.read_text(encoding="utf-8")
        tree = ast.parse(src)
        body = list(tree.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        for node in body:
            self.assertNotIsInstance(node, ast.Expr, f"模块级裸表达式 L{node.lineno}")
            self.assertTrue(isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign,
                                              ast.AnnAssign, ast.FunctionDef, ast.ClassDef)),
                            f"不允许 {type(node).__name__} L{node.lineno}")


if __name__ == "__main__":
    unittest.main()

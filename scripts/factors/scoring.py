"""复合 alpha 打分与信号建议（结构优化阶段 4·B3 第二十九刀）。

原样搬自 `scripts/factor_library.py::compute_instrument_factors` 的打分段
（`COMPOSITE HIGH-ALPHA SCORING (-100 to +100)`，90 行）。

## 这段在做什么

把已经算好的八组因子**加权成一个 -100~+100 的分数**，再落一条信号建议。
八项各自有"门槛 + 正负对称加减分"：

| # | 项 | 门槛 | 加减 |
|---|---|---|---|
| 1 | ADX 趋势过滤 | `adx_1h >= 22` | `rsi_14 >= 50` → `+15` 否则 `-15`；并写 `trend_regime` |
| 2 | 主力资金方向 | `available` 且 `weighted_long_pct` 是数 | `>=70` `+30` / `<=35` `-30` |
| 3 | RSI + KDJ 动量 | `rsi>=55 且 kdj_j>=60` / `rsi<=45 且 kdj_j<=40` | `+20` / `-20` |
| 4 | 资金流 OBV + CMF | `cmf>0.05 且 BULL_FLOW` / 反向 | `+20` / `-20` |
| 5 | 盘口微观失衡 | `bid_ask_depth_ratio>=1.4` / `<=0.7` | `+15` / `-15` |
| 6 | 微积分动态 | `regime` 或 `velocity/acceleration/impulse` 组合 | `+15/-10/-15/+10` |
| 7 | 定积分能量 | `energy_integral>1.2 且 deviation_area>0.8` / 反向 / `|dev_area|>=2.8` | `+10` / `-10` / **`*0.8`** |
| 8 | 概率论 | `continuation_prob_pct>=72` / `breakdown_prob_pct>=72` | `+10` / `-10` |

最后一项**阻尼**：`|jerk|>=1.8` 或 `SHOCK_HIGH_JERK` 或（肥尾且 `kurtosis>=3.5`）
→ **`score *= 0.6`**。

## 五处易错点（均原样保留）

1. **第 2 项在信号缺失时"跳过"，绝不当 0/中性计入** —— `available` 为假或
   `weighted_long_pct` 不是数时**什么都不加**。若误当成 0 分，等于给"没有数据"
   投了中性票，会稀释其他项的强度。
2. **第 6 项是 `if/elif/elif/elif`**：`regime` 字符串与"数值组合"两条路径**互斥**，
   按顺序取第一个满足的。**不是累加**。
3. **`jerk` 取绝对值**（`c_j = abs(...)`），而 `velocity/acceleration/impulse`
   **保留符号**（正负组合是有意义的判据）。
4. **两个阻尼是"乘"不是"减"**：第 7 项的 `*= 0.8`、最后的 `*= 0.6`。
   顺序不可换（乘法虽可交换，但第 7 项在**循环外只可能触发一次**，
   而阻尼项在最后 —— 若把第 7 项的 `*=0.8` 移到阻尼之后，参与乘的基数就变了）。
5. **信号建议的阈值与打分不同**：`|score| >= 45` **且** `adx >= 20`
   （注意不是第 1 项的 22）**且** `c_j < 1.8` 三个条件**与**关系；
   `c_j` 用的是**已被阻尼影响过的 score**，但 `c_j` 本身是独立读的原始值。

## 与门面的分工

本模块**只依赖入参 `factors`**（读 + 原地写 `trend_regime` /
`composite_alpha_score` / `signal_recommendation`），无任何外部调用 ——
故**注入面为零**，不需要门面传函数进来。
"""

from __future__ import annotations

from typing import Any, Dict

__all__ = ["score_composite_alpha", "ADX_TREND_THRESHOLD", "ADX_SIGNAL_THRESHOLD",
           "SCORE_SIGNAL_THRESHOLD", "JERK_DAMPEN_THRESHOLD"]

#: 第 1 项趋势过滤门槛（写 trend_regime）
ADX_TREND_THRESHOLD = 22.0
#: 信号建议的 ADX 门槛 —— **与上者不同**（20 vs 22），不要合并
ADX_SIGNAL_THRESHOLD = 20.0
#: 信号建议的 |score| 门槛
SCORE_SIGNAL_THRESHOLD = 45.0
#: jerk 阻尼门槛（>= 则不推信号；同时参与最后的 *= 0.6 判定）
JERK_DAMPEN_THRESHOLD = 1.8


def score_composite_alpha(factors: Dict[str, Any]) -> float:
    """就地写入 `trend_regime` / `composite_alpha_score` / `signal_recommendation`。

    返回最终分数（**阻尼之后**的值，即写入的那个数）。
    """
    # =========================================================================
    # COMPOSITE HIGH-ALPHA SCORING (-100 to +100)
    # =========================================================================
    score = 0.0

    # 1. ADX Trend Filter (Threshold = 22)
    adx = factors["trend_momentum"]["adx_1h"]
    if adx >= ADX_TREND_THRESHOLD:
        score += 15.0 if factors["trend_momentum"]["rsi_14"] >= 50 else -15.0
        factors["trend_momentum"]["trend_regime"] = "STRONG_TREND"
    else:
        factors["trend_momentum"]["trend_regime"] = "CHOP_RANGE"

    # 2. Smart Money Direction（信号缺失时跳过本项计分，绝不当 0/中性计入）
    sm_block = factors["smart_money_derivatives"]
    sm_long = sm_block.get("weighted_long_pct")
    if sm_block.get("available") and isinstance(sm_long, (int, float)):
        if sm_long >= 70.0: score += 30.0
        elif sm_long <= 35.0: score -= 30.0

    # 3. RSI & KDJ Dynamic Momentum
    rsi = factors["trend_momentum"]["rsi_14"]
    kdj_j = factors["trend_momentum"]["kdj_j"]
    if rsi >= 55.0 and kdj_j >= 60.0: score += 20.0
    elif rsi <= 45.0 and kdj_j <= 40.0: score -= 20.0

    # 4. Money Flow (OBV & CMF)
    cmf = factors["volume_money_flow"]["cmf_1h"]
    obv_f = factors["volume_money_flow"]["obv_flow"]
    if cmf > 0.05 and obv_f == "BULL_FLOW": score += 20.0
    elif cmf < -0.05 and obv_f == "BEAR_FLOW": score -= 20.0

    # 5. Orderbook Microstructure Imbalance
    depth_r = factors["microstructure"]["bid_ask_depth_ratio"]
    if depth_r >= 1.4: score += 15.0
    elif depth_r <= 0.7: score -= 15.0

    # 6. Calculus, Definite Integrals & Probability Theory Modulation
    c_dyn = factors["calculus_dynamics"]
    c_v = c_dyn.get("velocity", 0.0)
    c_a = c_dyn.get("acceleration", 0.0)
    c_i = c_dyn.get("impulse", 0.0)
    c_j = abs(c_dyn.get("jerk", 0.0))
    c_regime = c_dyn.get("regime", "")

    # Bullish acceleration vs deceleration
    if c_regime == "BULL_ACCELERATING" or (c_v > 0.2 and c_a > 0.1 and c_i > 0):
        score += 15.0
    elif c_regime == "BULL_DECELERATING" or (c_v > 0.2 and c_a < -0.3):
        score -= 10.0  # Anti-FOMO top chasing penalty
    elif c_regime == "BEAR_ACCELERATING" or (c_v < -0.2 and c_a < -0.1 and c_i < 0):
        score -= 15.0
    elif c_regime == "BEAR_DECELERATING" or (c_v < -0.2 and c_a > 0.3):
        score += 10.0  # Anti-bottom chasing penalty

    # 7. Definite Integrals & Energy Modulation
    d_int = factors.get("definite_integrals", {})
    e_int = d_int.get("energy_integral", 0.0)
    dev_area = d_int.get("deviation_area_integral", 0.0)
    if e_int > 1.2 and dev_area > 0.8:
        score += 10.0  # Multi-period net positive displacement energy
    elif e_int < -1.2 and dev_area < -0.8:
        score -= 10.0  # Multi-period net negative depletion
    elif abs(dev_area) >= 2.8:
        # Overstretched deviation integral penalty: trigger mean-reversion caution
        score *= 0.8

    # 8. Probability Theory & Stochastic Risk
    p_th = factors.get("probability_theory", {})
    p_cont = p_th.get("continuation_prob_pct", 50.0)
    p_break = p_th.get("breakdown_prob_pct", 50.0)
    is_fat = p_th.get("is_fat_tail", False)
    if p_cont >= 72.0:
        score += 10.0  # High conditional continuation probability
    elif p_break >= 72.0:
        score -= 10.0  # High conditional breakdown probability

    # High Jerk or Extreme Fat Tail Shock Dampener
    if c_j >= JERK_DAMPEN_THRESHOLD or c_regime == "SHOCK_HIGH_JERK" or (is_fat and p_th.get("kurtosis", 0) >= 3.5):
        score *= 0.6  # dampen conviction under high-jerk shock / extreme fat tails

    factors["composite_alpha_score"] = round(score, 1)

    # Decision Recommendation
    if score >= SCORE_SIGNAL_THRESHOLD and adx >= ADX_SIGNAL_THRESHOLD and c_j < JERK_DAMPEN_THRESHOLD:
        factors["signal_recommendation"] = "BUY_LONG"
    elif score <= -SCORE_SIGNAL_THRESHOLD and adx >= ADX_SIGNAL_THRESHOLD and c_j < JERK_DAMPEN_THRESHOLD:
        factors["signal_recommendation"] = "SELL_SHORT"
    else:
        factors["signal_recommendation"] = "WAIT"

    return score

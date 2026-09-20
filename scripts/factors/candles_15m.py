"""15M K 线 → 技术指标 + 微积分/概率因子（结构优化阶段 4·B3 第三十二刀）。

原样搬自 `scripts/factor_library.py::compute_instrument_factors` 的
「15M Candles → ATR, RSI, VWAP Bias, Vol Ratio, OBV」与紧随其后的
「Pillar 6: Calculus, Definite Integrals & Probability Theory」两段（88 行）。

## 为什么这两段能合在一起抽

它们**共用同一组派生序列** `closes / highs / lows / vols`
（由 `raw_candles` 反序 + `safe_float` 得到），且都写回同一个 `factors`。
拆成两个函数就得把四个列表来回传 —— 合起来才是**一个内聚单元**。

## ⚠️ 本模块**没有**取数依赖（这是刻意的）

`fetch_candles(...)` 留在门面，本模块只吃**已经取回的** `raw_candles`。
于是：

- **零新增注入面** —— 门面对 `fetch_candles` 的 `patch.object` 缝不受影响；
- 本模块可以纯函数式地测（喂构造 K 线即可），不需要任何 mock。

## 五处易错点（均原样保留）

1. **K 线是"倒序"的**：`reversed(raw_candles)` 转成时间正序之后再算。
   OKX 返回 newest-first，若忘了 reverse，ATR/RSI 全部反向计算 —— 而且
   **不会报错**，只会给出错误的数值。
2. **索引含义**：`c[2]=high, c[3]=low, c[4]=close, c[5]=volume`。
   把 2/3 写反（high/low 互换）在 `max(high-low, ...)` 里**看不出来**
   （绝对值对称），但会让 RSI 的 close 序列错位。
3. **ATR 是"简单移动平均"不是 Wilder 平滑**：`sum(tr_list[-14:]) / 14`。
4. **RSI 的 `avg_l == 0` 分支给 `rs = 100.0`**（不是除零）。
5. **`atr_pct` / `vwap_bias_pct` 都会再除以 `price`**：
   `price > 0` 与 `v_sum > 0` 是**两道独立的除零守卫**，不能合并。

## 边界与失败语义

`calculate_calculus` 的调用被内层 `try/except: pass` 包住（原样保留）——
**积分/概率引擎不可用时，前面算好的 ATR/RSI/VWAP/OBV 仍然保留**。
注意这与"整个 15M 段失败"是两层不同的失败：外层 `try` 在门面（覆盖取数）。

> 另外：原实现在**循环体内**做 `sys.path.append(...)` 再 `from calculus_engine import ...`。
> 本刀把该依赖改为**调用期注入**（门面在模块层解析一次），
> 于是不再每标的反复改 `sys.path`。这是**行为等价**的收口，不是逻辑变更。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

__all__ = ["derive_candle_series", "compute_15m_indicators"]

#: K 线列索引（OKX: [ts, open, high, low, close, vol, ...]）
_COL_HIGH = 2
_COL_LOW = 3
_COL_CLOSE = 4
_COL_VOL = 5


def derive_candle_series(raw_candles: List[Any], *, safe_float
                         ) -> Tuple[List[float], List[float], List[float], List[float]]:
    """`raw_candles` → `(closes, highs, lows, vols)`，**时间正序**。

    ⚠️ `reversed(...)` 是必须的：OKX 返回 newest-first。忘了 reverse 不会报错，
    只会让 ATR/RSI/OBV 全部按反向时间计算。
    """
    closes = [safe_float(c[_COL_CLOSE]) for c in reversed(raw_candles)]
    highs = [safe_float(c[_COL_HIGH]) for c in reversed(raw_candles)]
    lows = [safe_float(c[_COL_LOW]) for c in reversed(raw_candles)]
    vols = [safe_float(c[_COL_VOL]) for c in reversed(raw_candles)]
    return closes, highs, lows, vols


def compute_15m_indicators(raw_candles: List[Any], factors: Dict[str, Any], *,
                           safe_float,
                           calculate_calculus=None) -> Tuple[
                               List[float], List[float], List[float], List[float]]:
    """按原顺序写入 15M 指标与 Pillar 6 因子，返回派生序列。

    调用方保证 `raw_candles` 非空且 `len >= 15`（门面里的判断是 `>= 15`）。
    """
    closes, highs, lows, vols = derive_candle_series(raw_candles, safe_float=safe_float)

    # ATR 14
    tr_list = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)
    if len(tr_list) >= 14:
        atr = sum(tr_list[-14:]) / 14
        factors["volatility_channel"]["atr_14"] = round(atr, 4)
        if factors["price"] > 0:
            factors["volatility_channel"]["atr_pct"] = round(atr / factors["price"] * 100, 2)

    # RSI 14
    diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in diffs]
    losses = [-d if d < 0 else 0 for d in diffs]
    if len(gains) >= 14:
        avg_g = sum(gains[-14:]) / 14
        avg_l = sum(losses[-14:]) / 14
        rs = (avg_g / avg_l) if avg_l > 0 else 100.0
        factors["trend_momentum"]["rsi_14"] = round(100.0 - (100.0 / (1.0 + rs)), 1)

    # VWAP Bias
    pv_sum = sum(closes[i] * vols[i] for i in range(len(closes)))
    v_sum = sum(vols)
    if v_sum > 0:
        vwap = pv_sum / v_sum
        factors["trend_momentum"]["vwap_bias_pct"] = round((factors["price"] - vwap) / vwap * 100, 2)

    # Vol Ratio
    if len(vols) >= 6:
        avg_v5 = sum(vols[-6:-1]) / 5
        if avg_v5 > 0:
            factors["volume_money_flow"]["vol_ratio_15m"] = round(vols[-1] / avg_v5, 2)

    # OBV
    obv = 0
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]: obv += vols[i]
        elif closes[i] < closes[i-1]: obv -= vols[i]
    factors["volume_money_flow"]["obv_flow"] = "BULL_FLOW" if obv > 0 else ("BEAR_FLOW" if obv < 0 else "NEUTRAL")

    # Pillar 6: Calculus, Definite Integrals & Probability Theory (15M High-Resolution)
    if calculate_calculus is not None:
        try:
            c_res = calculate_calculus(closes, highs, lows, vols)
            if c_res.get("valid"):
                # Calculus Dynamics
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

                # Definite Integrals
                d_int = c_res.get("definite_integrals", {})
                factors["definite_integrals"]["energy_integral"] = d_int.get("energy_integral", 0.0)
                factors["definite_integrals"]["deviation_area_integral"] = d_int.get("deviation_area_integral", 0.0)
                factors["definite_integrals"]["volume_action_integral"] = d_int.get("volume_action_integral", 0.0)
                factors["definite_integrals"]["integral_regime"] = d_int.get("integral_regime", "BALANCED_ENERGY")

                # Probability Theory & Stochastic Modeling
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

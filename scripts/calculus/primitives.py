"""微积分/积分/概率引擎的数学**原语**与**状态分级**。

结构优化阶段 4·B3 第四十五刀从 `scripts/calculus_engine.py` 抽出
（原 L15–113，逐字搬运，无任何改动）。本模块只含**纯函数**：
不读任何模块级路径常量、不做 IO —— 已由 `plan_local/tools/purity_scan.py`
验证（模块级路径常量 0 个、不可外提函数 0 个）。

## 导入方式（本仓约定）

`scripts/` **不是** Python 包（无 `__init__.py`），真实调用方一律以
`sys.path` 含 `scripts/` 为前提、用**裸名**导入，例如
`scripts/trader/factors.py` 的 `from calculus_engine import calculate_multi_timeframe`。

故本模块被门面 `scripts/calculus_engine.py` 以**双模导入**引用：:

    try:
        from scripts.calculus.primitives import _finite   # repo 根在 sys.path
    except ImportError:
        from calculus.primitives import _finite           # scripts/ 在 sys.path

已实测三种路径布局（`scripts/` 在 sys.path / repo 根在 sys.path /
仅 `scripts/`）**均能解析**。

## ⚠️ 这些阈值是**业务语义**，不是可调参数

`0.08`（`_sign` 阈值）、`1.8`/`0.8`（jerk/velocity 冲击门限）、
`0.12`/`0.15`/`0.10`（分级门限）、`3.0`（fat-tail 峰度）、
`70.0`（概率百分比门限）、`2.5`（偏离面积）—— 全部原样保留。
按用户约束，**不抽常量、不改数值**。
"""
from __future__ import annotations

import math
from typing import Iterable, List, Sequence

__all__ = [
    "_finite", "_ema", "_diff", "_normalise", "_sign", "_normal_cdf",
    "classify_regime", "classify_power_regime",
    "classify_integral_regime", "classify_probability_regime",
]


def _finite(values: Iterable[float]) -> List[float]:
    return [float(v) for v in values if v is not None and math.isfinite(float(v)) and float(v) > 0]


def _ema(values: Sequence[float], span: int = 3) -> List[float]:
    if not values:
        return []
    alpha = 2.0 / (max(1, span) + 1.0)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1.0 - alpha) * out[-1])
    return out


def _diff(values: Sequence[float], lag: int = 1) -> List[float]:
    lag = max(1, int(lag))
    return [values[i] - values[i - lag] for i in range(lag, len(values))]


def _normalise(value: float, scale: float, bound: float = 3.0) -> float:
    if scale <= 1e-12:
        return 0.0
    return max(-bound, min(bound, value / scale))


def _sign(value: float, threshold: float = 0.08) -> int:
    return 1 if value > threshold else (-1 if value < -threshold else 0)


def _normal_cdf(z: float) -> float:
    """Standard normal cumulative distribution function (CDF) via erf."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def classify_regime(velocity: float, acceleration: float, impulse: float, jerk: float) -> str:
    """Classify physical kinematic regime based on derivatives."""
    if abs(jerk) >= 1.8 and abs(velocity) >= 0.8:
        return "SHOCK_HIGH_JERK"
    if abs(velocity) < 0.12 and abs(acceleration) < 0.12:
        return "RANGE_LOW_VELOCITY"
    direction = 1 if impulse >= 0 else -1
    if direction > 0:
        if velocity > 0.15 and acceleration > 0.10:
            return "BULL_ACCELERATING"
        if velocity > 0.08 and acceleration < -0.10:
            return "BULL_DECELERATING"
        if velocity < -0.08:
            return "BULL_REVERSING"
        return "BULL_STABLE"
    if velocity < -0.15 and acceleration < -0.10:
        return "BEAR_ACCELERATING"
    if velocity < -0.08 and acceleration > 0.10:
        return "BEAR_DECELERATING"
    if velocity > 0.08:
        return "BEAR_REVERSING"
    return "BEAR_STABLE"


def classify_power_regime(power: float, curvature: float, velocity: float, acceleration: float) -> str:
    """Classify physical kinetic flux and power state based on power and curvature."""
    if curvature >= 1.5:
        return "HIGH_CURVATURE_INFLECTION"
    if power > 0.12 and abs(velocity) > 0.20:
        return "KINETIC_ACCELERATING"
    if power < -0.12:
        return "KINETIC_EXHAUSTION"
    return "STEADY_FLUX"


def classify_integral_regime(energy: float, deviation_area: float) -> str:
    """Classify aggregate path-energy state from aggregate integral values."""
    if energy > 0.8 and deviation_area > 0.5:
        return "POSITIVE_ENERGY_EXPANSION"
    if energy < -0.8 and deviation_area < -0.5:
        return "NEGATIVE_ENERGY_DEPLETION"
    if abs(deviation_area) >= 2.5:
        return "OVERSTRETCHED_MEAN_REVERSION"
    return "BALANCED_ENERGY"


def classify_probability_regime(
    skewness: float,
    kurtosis: float,
    continuation_prob: float,
    breakdown_prob: float,
    is_fat_tail: bool,
) -> str:
    """Classify aggregate stochastic state, prioritising tail/asymmetry risk over direction."""
    if is_fat_tail and kurtosis >= 3.0:
        return "EXTREME_FAT_TAIL_RISK"
    if skewness > 0.6:
        return "POSITIVE_SKEW_UPSIDE"
    if skewness < -0.6:
        return "NEGATIVE_SKEW_DOWNSIDE"
    if continuation_prob >= 70.0:
        return "HIGH_PROB_BULL_CONTINUATION"
    if breakdown_prob >= 70.0:
        return "HIGH_PROB_BEAR_BREAKDOWN"
    return "GAUSSIAN_BALANCED"

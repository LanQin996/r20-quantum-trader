"""Position sizing, risk budgeting, and margin constraints."""
from __future__ import annotations
import math
from typing import Optional

from scripts import risk_constants as rc
from scripts.risk_constants import (
    # 审计 P1-1(2026-09-13)：这两条 min() 口径曾在本文件与 ai_factor_trader 各存一份拷贝，
    # 而提示词构建器根本没做 min()（模型看到 1496.82U/日亏 −249.47U，引擎实际 600U/−150U）。
    # 现统一从 risk_constants 复用同一函数对象，任何改动全链路同步。
    effective_daily_loss_limit,
    effective_single_asset_margin,
)


def effective_risk_per_trade(pool_risk_usd: float, usdt_available: Optional[float] = None) -> float:
    """单笔基准风险额 = min(池内配置绝对值, 可用余额 × 2%)，避免小资金账户超额承担风险。"""
    cap = float(pool_risk_usd or 0.0)
    if usdt_available and usdt_available > 0:
        # 批6：比例常量在**调用时**从单一事实源读取——import 期绑定会在
        # `importlib.reload(risk_constants)`（测试/热改 .env）之后变成过期值。
        cap = min(cap, max(round(float(usdt_available) * rc.RISK_PER_TRADE_EQUITY_RATIO, 4), 0.05))
    return cap


def quantize_size(raw_sz: float, lot_sz: float, min_sz: float | None = None) -> float:
    """按交易所 lotSz 向下量化张数，再检查 minSz。

    关键修正：历史实现把数量强制取整并抬到「至少 1 张」，而 OKX 多数永续的 minSz 实为 0.01 张，
    导致小资金账户仓位被向上放大最多 100 倍(如 BTC 0.01 张=7.92U 名义被抬成 1 张=792U)。
    现在低于最小步长时返回 0.0 由上层跳过该标的，而不是放大成 1 张。
    """
    from decimal import Decimal, InvalidOperation, ROUND_DOWN
    try:
        raw = Decimal(str(raw_sz or 0))
        step = Decimal(str(lot_sz or 0))
        minimum = Decimal(str(min_sz)) if min_sz not in (None, "") else step
    except (InvalidOperation, TypeError, ValueError):
        return 0.0
    if raw <= 0:
        return 0.0
    if step <= 0:
        return float(raw)
    sized = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
    if sized < minimum:
        return 0.0
    return float(sized)


def max_size_within_margin(usdt_available: float, leverage: float, price: float, ct_val: float, lot_sz: float, min_sz: float | None = None) -> float:
    """可用余额硬顶：单笔保证金不得超过可用余额的 MAX_MARGIN_EQUITY_RATIO，超出部分直接砍掉。"""
    if not usdt_available or usdt_available <= 0 or price <= 0 or ct_val <= 0:
        return float("inf")
    max_margin = float(usdt_available) * rc.MAX_MARGIN_EQUITY_RATIO
    raw = (max_margin * max(1.0, float(leverage or 1.0))) / (float(price) * float(ct_val))
    return quantize_size(raw, lot_sz, min_sz)

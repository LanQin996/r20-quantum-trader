"""聪明钱与大户多空明细数据采集模块（Pillar 5）。

为解决 OKX 内部 CLI 移除后聪明钱数据缺失（US-014 遗留）的问题，本模块提供单源采集：
1. 数据源：OKX Rubik 官方公开统计端点（合约多空账户数/持仓比 + 深度成交量）；
2. 容灾：数据源不可用时优雅返回 None，保留显式缺失语义，绝不伪造虚假中性信号。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from . import okx_quant_factors as qf


def fetch_smart_money_for_symbol(
    ccy: str,
    price: float = 0.0,
    *,
    timeout: float = 3.5,
) -> Optional[Dict[str, Any]]:
    """采集单标的顶级大户持仓多空比与主动资金流（OKX Rubik 公开统计端点）。"""
    base_ccy = str(ccy or "").upper().strip()
    if not base_ccy:
        return None

    res_okx = _fetch_from_okx_rubik(base_ccy, price=price, timeout=timeout)
    if res_okx and res_okx.get("longShortRatio"):
        return res_okx

    return None


def _fetch_from_okx_rubik(ccy: str, price: float = 0.0, timeout: float = 3.5) -> Optional[Dict[str, Any]]:
    ratio_rows = qf.fetch_long_short_account_ratio(ccy, timeout=timeout)
    if not ratio_rows or not isinstance(ratio_rows[0], (list, tuple)) or len(ratio_rows[0]) < 2:
        return None
    try:
        ls_ratio = float(ratio_rows[0][1])
    except (TypeError, ValueError):
        return None
    w_long = round(ls_ratio / (1.0 + ls_ratio), 4)

    taker_rows = qf.fetch_taker_volume(ccy, "5m", timeout=timeout)
    net_notional_usd = 0.0
    taker_str = "--"
    if taker_rows and isinstance(taker_rows[0], (list, tuple)) and len(taker_rows[0]) > 2:
        try:
            sell_vol, buy_vol = float(taker_rows[0][1]), float(taker_rows[0][2])
            net_notional_usd = buy_vol - sell_vol
            taker_str = (f"{round(net_notional_usd / 1e4, 1)}万 U"
                         if abs(net_notional_usd) >= 1e4
                         else f"{round(net_notional_usd, 0)} U")
        except (TypeError, ValueError):
            pass

    return {
        "longShortRatio": {
            "weightedLongRatio": w_long,
            "longShortRatio": ls_ratio,
        },
        "notional": {
            "netNotionalUsdt": net_notional_usd,
        },
        "winRate": {},
        "takerNetUsd": taker_str,
        "lsRatio": ls_ratio,
        "weighted_long_pct": round(w_long * 100, 1),
    }


def fetch_smart_money_pool(
    instruments: List[Dict[str, Any]],
    *,
    max_workers: int = 6,
    timeout: float = 3.5,
) -> Dict[str, Any]:
    """并发采集标的池内全部币种的聪明钱与大户数据，返回标准 pool 字典。"""
    pool: Dict[str, Any] = {}
    if not instruments:
        return pool

    def _fetch_one(item: Dict[str, Any]) -> tuple[str, Optional[Dict[str, Any]]]:
        ccy = item.get("ccy") or item.get("name") or (item.get("instId", "").split("-")[0] if "-" in item.get("instId", "") else "")
        price = float(item.get("price") or 0.0)
        res = fetch_smart_money_for_symbol(ccy, price=price, timeout=timeout)
        return ccy, res

    with ThreadPoolExecutor(max_workers=min(max_workers, len(instruments))) as executor:
        for ccy, sm in executor.map(_fetch_one, instruments):
            if ccy and sm:
                pool[ccy] = sm

    return pool

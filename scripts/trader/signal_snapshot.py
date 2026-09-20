"""开仓时刻信号快照组装（B3 抽取·trader 瘦身第二刀，第八十二刀）。

从 `scripts/ai_factor_trader.py` **纯搬家** `build_signal_snapshot`（85 行，
零交易动作：只组装 dict 并读一次因子库快照文件）。

## 域归属

这是**自进化复盘**的观测面：开仓瞬间把因果动力学/数理/微观/舆情字段
钉进 journal，供 `self_improvement_engine` 做真实因果归因（2026-09-09
的 schema 错配事故让 22/24 字段恒 None —— 修复的兼容逻辑在函数体内，
本次搬家一字未动，专测 `tests/trading/test_signal_snapshot_schema.py`）。

## 注入形状

唯一外部依赖 `DATA_DIR`（因子库快照文件路径的根）由门面壳**调用期**
注入 —— 专测的 `patch.object(aft, "DATA_DIR", tmp)` patch 面照常生效。
对拍门：`tests/extraction/test_trader_signal_snapshot_extraction.py`。
"""
from __future__ import annotations

import json
import os


def build_signal_snapshot(f: dict, *, data_dir: str) -> dict:
    """抽取开仓时刻的因果动力学与数理快照，供自进化复盘做真实因果归因（而非事后倒推）。

    兼容两套数据源 schema（2026-09-09 修复）：
    1. factor_library 快照块结构（calculus_dynamics / probability_theory / definite_integrals）
    2. 执行层 f["calculus"] = calculate_multi_timeframe 聚合结构
       （velocity / max_abs_jerk / 嵌套 probability_theory / definite_integrals）
    旧版只认结构 1，而开仓路径传入的是结构 2，导致 journal 里 22/24 字段恒为
    None → 复盘全量「数理快照不可观测」、逐单归因失效。
    """
    calc = f.get("calculus_dynamics") or {}
    prob = f.get("probability_theory") or {}
    integ = f.get("definite_integrals") or {}
    multi = f.get("calculus") or {}
    if not calc and multi:
        calc = {
            "velocity": multi.get("velocity"),
            "acceleration": multi.get("acceleration"),
            "jerk": multi.get("max_abs_jerk"),
            "impulse": multi.get("impulse"),
            "curvature": multi.get("curvature"),
            "power": multi.get("power"),
            "power_regime": multi.get("power_regime"),
            "regime": multi.get("regime"),
            "quality": multi.get("quality"),
        }
        prob = multi.get("probability_theory") or {}
        integ = multi.get("definite_integrals") or {}
    micro = f.get("microstructure") or {}
    money = f.get("smart_money_derivatives") or {}
    trend = f.get("trend_momentum") or {}
    snap = {
        "price": f.get("price"),
        "atr": f.get("atr"),
        "velocity": calc.get("velocity"),
        "acceleration": calc.get("acceleration"),
        "jerk": calc.get("jerk"),
        "impulse": calc.get("impulse"),
        "curvature": calc.get("curvature"),
        "power": calc.get("power"),
        "power_regime": calc.get("power_regime"),
        "regime": calc.get("regime"),
        "dynamics_quality": calc.get("quality"),
        "continuation_prob_pct": prob.get("continuation_prob_pct"),
        "breakdown_prob_pct": prob.get("breakdown_prob_pct"),
        "var_95_pct": prob.get("var_95_pct"),
        "cvar_95_pct": prob.get("cvar_95_pct"),
        "prob_regime": prob.get("prob_regime"),
        "is_fat_tail": prob.get("is_fat_tail"),
        "energy_integral": integ.get("energy_integral"),
        "deviation_area_integral": integ.get("deviation_area_integral"),
        "adx": trend.get("adx") or f.get("adx") or f.get("adx_1h"),
        "rsi": trend.get("rsi") or f.get("rsi") or f.get("rsi_14"),
        "funding_rate": micro.get("funding_rate") or f.get("funding_rate"),
        "composite_alpha_score": f.get("composite_alpha_score") or f.get("alpha_score"),
        "smart_money_net": money.get("net_flow") or money.get("taker_net") or money.get("smart_money_flow_usd"),
    }

    # 因子库快照（60s 频，开仓时刻即最新）二次补齐执行层 f 没有的四个外部观测字段
    if any(snap.get(k) is None for k in ("adx", "funding_rate", "composite_alpha_score", "smart_money_net")):
        try:
            lib_file = os.path.join(data_dir, "factor_library_snapshot.json")
            if os.path.exists(lib_file):
                with open(lib_file, "r", encoding="utf-8") as handle:
                    lib = json.load(handle)
                entries = lib.get("instruments") or []
                if isinstance(entries, dict):
                    entries = list(entries.values())
                libf = next(
                    (x for x in entries if isinstance(x, dict) and x.get("instId") == f.get("instId")),
                    None,
                )
                if libf:
                    tm = libf.get("trend_momentum") or {}
                    sm = libf.get("smart_money_derivatives") or {}
                    if snap["adx"] is None:
                        snap["adx"] = tm.get("adx_1h")
                    if snap["funding_rate"] is None:
                        snap["funding_rate"] = sm.get("funding_rate_pct") or tm.get("funding_rate")
                    if snap["composite_alpha_score"] is None:
                        snap["composite_alpha_score"] = libf.get("composite_alpha_score")
                    if snap["smart_money_net"] is None:
                        snap["smart_money_net"] = sm.get("smart_money_flow_usd")
        except Exception:
            pass
    return snap


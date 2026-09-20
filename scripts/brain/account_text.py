"""在途持仓 / 挂单的提示词文本渲染（结构优化阶段 4·B3 第三十刀）。

原样搬自 `scripts/brain/prompt.py::construct_full_market_prompt` 的两段文本装配
（`pos_lines` 28 行 + `pending_lines` 33 行）。

## 这两段在做什么

把账户的**在途持仓**与**在途未成交挂单**渲染成给模型看的文本块。
两段都有一条**"缺失 vs 空"的三态语义**，这是本刀最要紧的不变量：

| 入参 | 渲染结果 |
|---|---|
| `None` | `[MISSING_CONTEXT:...]` —— **上下文没给**（调用方漏传） |
| `[]`（空列表） | 「当前无任何在途持仓敞口 (100% 现金空仓状态)」—— **确实是空仓** |
| 非空 | 逐条列举 |

**两者绝不能合并**：`None` 是"我不知道"，`[]` 是"确定没有"。
把它们渲染成同一句话，模型就会把"上下文缺失"误读成"空仓"，进而放大仓位。

## 四处易错点（均原样保留）

1. **持仓的利润描述是"正反两个分支"**：
   多头看 `hwm`（历史最高）、空头看 `lwm`（历史最低），
   且**各有三重合条件**（方向、极值相对开仓价的方位、开仓价 > 0）。
   两个分支的"回撤"分母**不同**：多头是 `(hwm - entry_px)`，
   空头是 `(entry_px - lwm)`。
2. **挂单方向串有 `reduce_only` × 买卖 × 限价/市价 共 8 种组合**，
   且**非 reduce_only 分支的"买"判据不含 ordType**（`side_raw == "buy"` 即算买多），
   而"卖"判据含（`ord_type != "market"` 才是限价卖空）。
   这正是容易写错的地方：`reduce_only` 与普通单的**判定不对称**。
3. **价格展示有"0 视同未填"**：`px` 为空串**或**字面 `"0"` → 市价单显示「市价」，
   否则 `--`。
4. **`int(o.get("cTime", 0) or 0) / 1000.0`**：毫秒转秒，且**先 `or 0`**
   （`None` 与 `""` 都要能兜住），`<= 0` 时显示 `--`。

## 与门面的分工

`build_position_lines` 需要 `safe_float`（浮点兜底）与 `tz_bj`（挂单时间格式化），
两者**由调用方传入** —— 不 import 期绑定，见 `r20_backend/README.md` §5。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["build_position_lines", "build_pending_order_lines"]


def build_position_lines(active_positions_detail: Optional[List[Dict[str, Any]]],
                         *, safe_float) -> str:
    """在途持仓文本块。

    `None` → `[MISSING_CONTEXT:account_positions]`；
    `[]` → 「当前无任何在途持仓敞口 (100% 现金空仓状态)」。
    """
    pos_lines = []
    if active_positions_detail and len(active_positions_detail) > 0:
        for p in active_positions_detail:
            inst_name = p.get('name') or p.get('instId')
            side = p.get('side') or p.get('posSide', 'long')
            is_long = "long" in str(side).lower()
            entry_px = safe_float(p.get('avgPx', 0))
            cur_px = safe_float(p.get('markPx') or p.get('lastPx') or entry_px)
            hwm = safe_float(p.get('highWaterMark', 0))
            lwm = safe_float(p.get('lowWaterMark', 0))
            tp_px = p.get('takeProfitPx', '--')
            stage_desc = p.get('stage_desc', '持有监控中')

            profit_desc = ""
            if is_long and hwm > entry_px and entry_px > 0:
                peak_gain_pct = round((hwm - entry_px) / entry_px * 100, 2)
                dd_from_peak = round((hwm - cur_px) / (hwm - entry_px) * 100, 1) if hwm > entry_px else 0.0
                profit_desc = f" | 曾最高到: {hwm} (极值浮盈 +{peak_gain_pct}%, 现已从极值回撤 {dd_from_peak}%)"
            elif not is_long and lwm > 0 and lwm < entry_px and entry_px > 0:
                peak_gain_pct = round((entry_px - lwm) / entry_px * 100, 2)
                dd_from_peak = round((cur_px - lwm) / (entry_px - lwm) * 100, 1) if lwm < entry_px else 0.0
                profit_desc = f" | 曾最低到: {lwm} (极值浮盈 +{peak_gain_pct}%, 现已从极值回撤 {dd_from_peak}%)"

            v_badge = f"[{str(p.get('venue', 'OKX')).upper()}] "
            pos_lines.append(
                f"- {v_badge}标的: {inst_name} | 方向: {side} {p.get('lever', p.get('leverage', '3'))}x | 开仓均价: {p.get('avgPx')} | 当前价: {cur_px} | 浮盈: {p.get('upl')} U (ROI: {round(safe_float(p.get('uplRatio')) * 100, 2)}%){profit_desc} | 动态止损线: {p.get('trailingStopPx', p.get('trailingSl', '--'))} | 目标止盈: {tp_px} | 状态: {stage_desc}"
            )
    else:
        pos_lines.append("[MISSING_CONTEXT:account_positions]" if active_positions_detail is None else "当前无任何在途持仓敞口 (100% 现金空仓状态)")

    return "\n".join(pos_lines)


def build_pending_order_lines(pending_orders_detail: Optional[List[Dict[str, Any]]],
                              *, tz_bj, datetime) -> str:
    """在途未成交挂单文本块。

    `None` → `[MISSING_CONTEXT:pending_orders]`；
    `[]` → 「当前无任何在途未成交限价挂单 (挂单池为空)」。
    """
    pending_lines = []
    if pending_orders_detail and len(pending_orders_detail) > 0:
        for o in pending_orders_detail:
            c_ts = int(o.get("cTime", 0) or 0) / 1000.0
            c_time_str = datetime.datetime.fromtimestamp(c_ts, tz=tz_bj).strftime("%Y-%m-%d %H:%M:%S") if c_ts > 0 else "--"
            inst_id = o.get("instId", "")
            side_raw = str(o.get("side", "")).lower()
            # 审计D(2026-09-13)：死局部清除——pos_side 从未参与下方展示/判定
            reduce_only = str(o.get("reduceOnly", "false")).lower() == "true"
            ord_type = str(o.get("ordType", "limit")).lower()

            if reduce_only:
                side_str = "市价平多" if (side_raw == "sell" and ord_type == "market") else ("限价平多" if side_raw == "sell" else ("市价平空" if ord_type == "market" else "限价平空"))
            else:
                side_str = "限价买多" if (side_raw == "buy" and ord_type != "market") else ("市价买多" if side_raw == "buy" else ("限价卖空" if ord_type != "market" else "市价卖空"))

            raw_px = str(o.get("px") or "").strip()
            px_val = raw_px if raw_px and raw_px != "0" else ("市价" if ord_type == "market" else "--")
            sz_val = str(o.get("sz", "--"))
            ord_id = str(o.get("ordId", ""))

            attach_list = o.get("attachAlgoOrds", [])
            tp_sl_info = ""
            if attach_list and len(attach_list) > 0:
                att = attach_list[0]
                tp_p = att.get("tpTriggerPx", "--")
                sl_p = att.get("slTriggerPx", "--")
                tp_sl_info = f" | 附带云端止盈: {tp_p} / 止损: {sl_p}"

            pending_lines.append(
                f"- [挂单ID: {ord_id}] {inst_id} | {side_str} {sz_val}张 @ {px_val} | 挂单时间: {c_time_str}{tp_sl_info}"
            )
    else:
        pending_lines.append("[MISSING_CONTEXT:pending_orders]" if pending_orders_detail is None else "当前无任何在途未成交限价挂单 (挂单池为空)")

    return "\n".join(pending_lines)

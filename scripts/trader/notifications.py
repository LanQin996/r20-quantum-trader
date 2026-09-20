"""开仓结果的**方向相关内容**构造（B3 抽取第十六块）。

从 `scripts/ai_factor_trader.py::execute_portfolio` 的 `if accepted:` 块里，
把"随方向翻转的文案与参数"抽成两个纯函数。

## 这块在解决什么

原实现在长/空两侧各写一遍（共 4 处通知 + 4 条动作文案），方向只影响：

| 载体 | 做多 | 做空 |
|---|---|---|
| 加仓动作文案 | `🚀 AI顺势浮盈金字塔加多挂单已提交` | `🌪️ AI顺势浮盈金字塔加空挂单已提交` |
| 开仓动作文案 | `AI限价多单已提交待成交` | `AI限价空单已提交待成交` |
| 失败文案 | `AI限价多单提交失败` | `AI限价空单提交失败` |
| 加仓通知 `side` | `多 (顺势加多)` | `空 (顺势加空)` |
| 开仓通知 `side` | `多` | `空` |
| 加仓通知 `strategy` | `🚀 顺势金字塔加多` | `🌪️ 顺势金字塔加空` |

**六处文案 × 两个方向 = 12 个必须保持一致的字符串常量**，散落在 74 行里。
文案错了不会让程序崩，会让**交易通知说错方向** —— 而通知是用户判断
"这单是多是空"的唯一来源。这是本次抽取真正的动机：把文案收成单一来源。

规模上也要说清楚：这 74 行里只有约 12 行是"随方向变化"的内容，**其余全是
两边逐字相同的样板**（`pending_inst_ids.add` / `reserved_slot_count += 1` /
`if notify_trade_open:` / `reason=str(ai_reason)` / `tp_px=tp_px` / `sl_px=sl_px`）。
因此本块**只抽方向相关部分**，不把样板也包进来 —— 后者会把副作用
（`save_trackers`、`pending_inst_ids`、`reserved_*` 计数）藏进子模块，
既不减行数，又让门面的状态变更不再一目了然。这是有意的取舍，不是没抽完。

## 刻意留在门面的

- `leverage=int(ai_lever),` —— 门面有计数锚点（`test_audit_batch5_d_tails.py`）
  数它的出现次数（4 处），用来保证"四处开仓通知都携带钳制后的真实杠杆"。
  注释记录的历史缺陷是曾恒写 `3`，导致 5x 仓也通知「3x 杠杆」（票圈谎报）。
  故这 4 行必须以字面量留在门面，本模块只提供 `**kwargs` 的其余部分。
- 全部状态变更（`save_trackers` / `pending_inst_ids` / `reserved_slot_count` /
  `reserved_long_count` / `reserved_short_count`）—— 见上。
"""


def entry_action_message(*, is_long, is_scale_in, name, sz, px, order_ref, tp_px, sl_px):
    """开仓/加仓成功后的 `executed_actions` 文案（含方向与加仓标记）。"""
    if is_scale_in:
        arrow = "🚀" if is_long else "🌪️"
        what = "加多" if is_long else "加空"
        return (f"[{name}] {arrow} AI顺势浮盈金字塔{what}挂单已提交 "
                f"{sz}张@{px} (order={order_ref}, TP={tp_px}, SL={sl_px})")
    what = "多" if is_long else "空"
    return (f"[{name}] AI限价{what}单已提交待成交 "
            f"{sz}张@{px} (order={order_ref}, TP={tp_px}, SL={sl_px})")


def entry_failure_message(*, is_long, name, order_ref):
    """下单被拒后的 `executed_actions` 文案。"""
    return f"[{name}] AI限价{'多' if is_long else '空'}单提交失败: {order_ref}"


def trade_open_kwargs(*, is_long, is_scale_in, name, sz, px, strat_tag, ai_reason,
                      tp_px, sl_px):
    """`notify_trade_open(...)` 的按关键字实参（**不含** `leverage`）。

    `leverage` 由调用点以字面量传入，以保留门面的计数锚点 —— 见模块 docstring。
    """
    if is_scale_in:
        arrow = "🚀" if is_long else "🌪️"
        what = "加多" if is_long else "加空"
        return dict(inst=name, side=f"{'多' if is_long else '空'} (顺势{what})",
                    sz=sz, px=px, strategy=f"{arrow} 顺势金字塔{what}",
                    reason=str(ai_reason), tp_px=tp_px, sl_px=sl_px)
    return dict(inst=name, side="多" if is_long else "空",
                sz=sz, px=px, strategy=strat_tag,
                reason=str(ai_reason), tp_px=tp_px, sl_px=sl_px)

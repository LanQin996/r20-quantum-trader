"""限价单的三价顺序钳制（B3 抽取第十三块）。

## 这块在解决什么

开仓挂单要同时给出 `limit_px` / `tp_px` / `sl_px`，而 OKX（以及各所）对三者的
**大小顺序有硬性要求**，顺序错了直接拒单：

| 方向 | 必须满足 |
|---|---|
| 做多 | `sl_px < limit_px < tp_px` |
| 做空 | `tp_px < limit_px < sl_px` |

AI 可以自己给这三个价（`entry_price` / `take_profit_price` / `stop_loss_price`），
它给出**越界或相等**的组合是常事（例如把止损填在入场价上方做多）。原实现在
长/空两个分支里各写一段"越界就用兜底距离掰回来"的修正 —— 两段逻辑同构、
方向相反，且**各自独立演化**。抽成一处后：

- 顺序不变量只有一份权威实现，不会出现"多头修了、空头忘了"；
- 兜底距离（`max(实际距离, 现价×比例)`）也只有一个来源。

## 兜底比例为什么是 1.2% / 2.4%

原实现：止损兜底 `现价 × 0.012`，止盈兜底 `现价 × 0.024`。止盈的系数更大，
因为止盈被摆错时通常需要比止损更宽的空间才拉得开盈亏比。这两个数字是
**原样搬来的字面量**，本次搬运不改其值（重构不得改业务阈值）。

## 为什么"相等"也算越界

原实现用的是 `>=` / `<=`（不是 `>` / `<`）：`sl_px == limit_px` 同样会被掰开。
`tests/` 里对拍用旧实现副本逐点覆盖等值情形，避免把 `>=` 顺手改成 `>`。
"""


def normalize_bracket_prices(*, is_long, limit_px, tp_px, sl_px, sl_dist, tp_dist,
                             price, prec):
    """把三价掰成交易所可接受的顺序，返回 `(sl_px, tp_px)`。

    语义与搬走前的两段内联实现逐字一致：

    - 做多：`sl_px >= limit_px` → `limit_px - max(sl_dist, price*0.012)`；
            `tp_px <= limit_px` → `limit_px + max(tp_dist, price*0.024)`；
    - 做空：`sl_px <= limit_px` → `limit_px + max(sl_dist, price*0.012)`；
            `tp_px >= limit_px` → `limit_px - max(tp_dist, price*0.024)`。

    两次修正都是 `round(..., prec)`，`prec` 由调用方按标的精度传入。
    """
    if is_long:
        if sl_px >= limit_px:
            sl_px = round(limit_px - max(sl_dist, price * 0.012), prec)
        if tp_px <= limit_px:
            tp_px = round(limit_px + max(tp_dist, price * 0.024), prec)
    else:
        if sl_px <= limit_px:
            sl_px = round(limit_px + max(sl_dist, price * 0.012), prec)
        if tp_px >= limit_px:
            tp_px = round(limit_px - max(tp_dist, price * 0.024), prec)
    return sl_px, tp_px


def clamp_take_profit_width(*, is_long: bool, limit_px: float, sl_px: float, tp_px: float,
                            atr: float = 0.0, prec: int = 2,
                            max_tp_atr: float | None = None,
                            max_rr: float | None = None,
                            min_rr: float | None = None) -> float:
    """平滑钳制止盈宽度，防止 AI 给出过远无法触及的天际线止盈单。

    核心逻辑：
    1. 止损风险距离 risk = abs(limit_px - sl_px)。若 risk <= 0 或参数非法，原样返回 tp_px。
    2. 计算当前止盈距离 curr_reward = (tp_px - limit_px) if is_long else (limit_px - tp_px)。
       若 curr_reward <= 0，原样返回 tp_px。
    3. 允许的最大止盈距离：
       - 基于 ATR 的上限：atr * max_tp_atr（当 atr > 0 且 max_tp_atr > 0 时生效）
       - 基于最大盈亏比的上限：risk * max_rr（当 max_rr > 0 时生效）
       两者的较严者（最小值）为 allowed_max。
    4. 保证底线：allowed_max 不得低于 risk * min_rr（默认 2.0），绝不破坏最小盈亏比。
    5. 若 curr_reward > allowed_max，则将 tp_px 平滑收窄钳制到该上限并按精度取整。
    """
    try:
        limit_px = float(limit_px)
        sl_px = float(sl_px)
        tp_px = float(tp_px)
    except (TypeError, ValueError):
        return tp_px

    if limit_px <= 0 or sl_px <= 0 or tp_px <= 0:
        return tp_px

    # 动态加载风控常量（支持热重载）
    if max_tp_atr is None or max_rr is None or min_rr is None:
        try:
            from scripts.risk_constants import (
                MAX_TAKE_PROFIT_ATR as _def_tp_atr,
                MAX_RISK_REWARD_RATIO as _def_max_rr,
                MIN_RISK_REWARD_RATIO as _def_min_rr,
            )
        except ImportError:
            from risk_constants import (
                MAX_TAKE_PROFIT_ATR as _def_tp_atr,
                MAX_RISK_REWARD_RATIO as _def_max_rr,
                MIN_RISK_REWARD_RATIO as _def_min_rr,
            )
        if max_tp_atr is None:
            max_tp_atr = _def_tp_atr
        if max_rr is None:
            max_rr = _def_max_rr
        if min_rr is None:
            min_rr = _def_min_rr

    risk = abs(limit_px - sl_px)
    if risk <= 0:
        return tp_px

    curr_reward = (tp_px - limit_px) if is_long else (limit_px - tp_px)
    if curr_reward <= 0:
        return tp_px

    # 计算各上限
    candidates = []
    try:
        atr_val = float(atr or 0.0)
        tp_atr_cap = float(max_tp_atr or 0.0)
        if atr_val > 0 and tp_atr_cap > 0:
            candidates.append(atr_val * tp_atr_cap)
    except (TypeError, ValueError):
        pass

    try:
        max_rr_val = float(max_rr or 0.0)
        if max_rr_val > 0:
            candidates.append(risk * max_rr_val)
    except (TypeError, ValueError):
        pass

    if not candidates:
        return tp_px

    allowed_max = min(candidates)

    # 绝不破坏最小盈亏比底线
    try:
        min_rr_val = float(min_rr or 2.0)
        floor_reward = risk * min_rr_val
        if allowed_max < floor_reward:
            allowed_max = floor_reward
    except (TypeError, ValueError):
        pass

    if curr_reward > allowed_max:
        if is_long:
            tp_px = round(limit_px + allowed_max, prec)
        else:
            tp_px = round(limit_px - allowed_max, prec)

    return tp_px

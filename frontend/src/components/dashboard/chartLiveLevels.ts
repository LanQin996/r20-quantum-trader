/**
 * K 线工位：从「活动持仓 / 活动挂单」推导展示用的**入场价 / 方向 / 止损 / 止盈**
 * （结构优化阶段 4·F4 抽离）。
 *
 * 这四段原先内嵌在 `ChartWorkstation.vue` 的 computed 里，但它们**不需要组件上下文**：
 * 只吃入参、只返回结果。读 ref 的动作留在调用点的 computed 内，响应式追踪语义不变。
 * 抽到模块后可用 `node --test` 直接钉住**优先级链** —— 这些链条决定图表上把
 * 止损/止盈线画在哪里，属交易安全相关逻辑，最怕被"顺手优化"。
 *
 * ## 两条容易看错、**必须原样保留**的既有语义
 *
 * 1. `??` 是**空值合并**，不是"取第一个大于 0 的值"：持仓里 `displayStop: 0`
 *    会**截断持仓层**的回退链（`0 ?? x` 得 `0`），于是不再看 `exchangeSl` /
 *    `slTriggerPx` / `trailingSl`。但 `if (s > 0)` 不成立时仍会**落到挂单层**
 *    （`order.sl_px`）—— 即"截断"只作用于持仓层，不等于直接返回 0。
 * 2. 入场价用 `||` 而非 `??`：`0` / `""` / `null` / `undefined` 回退到当前价；
 *    但**字符串 `"0"` 是真值** ⇒ `Number("0") === 0`，此时显示 0 而**不**回退。
 *    （既有行为，原样保留；本是"看起来该改"的地方，故在此记明。）
 */

/** 展示用入场价：持仓成本 → 挂单价 → 当前价（`||` 语义，假值即回退）。 */
export function deriveLiveEntry(input: { position: any; order: any; price: number }): number {
  const { position, order, price } = input
  if (position) return Number(position.avgPx || price)
  if (order) return Number(order.px || price)
  return price
}

/** 展示用方向：持仓方向优先；挂单看 `sell`/`空` 关键字；都没有则按多头。 */
export function deriveLiveSide(input: { position: any; order: any }): 'long' | 'short' {
  const { position, order } = input
  if (position) return position.side === 'short' ? 'short' : 'long'
  if (order) {
    const s = String(order.side || order.side_raw || '').toLowerCase()
    return s.includes('sell') || s.includes('空') ? 'short' : 'long'
  }
  return 'long'
}

/** 展示用止损价：持仓四级回退 → 挂单价；`> 0` 才算数，否则 0（表示"未设"）。 */
export function deriveLiveStopLoss(input: { position: any; order: any }): number {
  const { position, order } = input
  if (position) {
    const s = Number(
      position.displayStop ??
      position.exchangeSl ??
      position.slTriggerPx ??
      position.trailingSl ??
      0
    )
    if (s > 0) return s
  }
  if (order) {
    const s = Number(order.sl_px ?? 0)
    if (s > 0) return s
  }
  return 0
}

/** 展示用止盈价：持仓三级回退 → 挂单价；`> 0` 才算数，否则 0（表示"未设"）。 */
export function deriveLiveTakeProfit(input: { position: any; order: any }): number {
  const { position, order } = input
  if (position) {
    const tp = Number(
      position.displayTakeProfit ??
      position.exchangeTp ??
      position.tpTriggerPx ??
      0
    )
    if (tp > 0) return tp
  }
  if (order) {
    const tp = Number(order.tp_px ?? 0)
    if (tp > 0) return tp
  }
  return 0
}

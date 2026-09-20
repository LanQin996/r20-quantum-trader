/**
 * `src/components/dashboard/chartLiveLevels.ts` 行为契约（结构优化阶段 4·F4）。
 *
 * ## 守什么
 *
 * `ChartWorkstation.vue` 的 `liveEntry` / `liveSide` / `liveStopLoss` / `liveTakeProfit`
 * 四个 computed 决定图表上**止损/止盈线画在哪、方向箭头指向哪、入场价标多少**。
 * 它们原先只能靠肉眼看组件源码来"确认"，现在被抽成纯函数并由本文件钉住。
 *
 * 重点守两条**容易被"顺手优化"改坏**的既有语义：
 *   1. `??` 是空值合并：持仓 `displayStop: 0` 会**截断**回退链 ⇒ 返回 0，
 *      **不会**落到 `exchangeSl`，也**不会**落到挂单 `sl_px`；
 *   2. 入场价用 `||`：`avgPx` 为 `0` / `""` / `"0"` 时都回退到当前价。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  deriveLiveEntry,
  deriveLiveSide,
  deriveLiveStopLoss,
  deriveLiveTakeProfit,
} from '../src/components/dashboard/chartLiveLevels.ts';

// ---------- 入场价 ----------

test('入场价：持仓成本优先，其次挂单价，最后当前价', () => {
  assert.equal(deriveLiveEntry({ position: { avgPx: '60123.5' }, order: { px: '60000' }, price: 59000 }), 60123.5);
  assert.equal(deriveLiveEntry({ position: null, order: { px: 60000 }, price: 59000 }), 60000);
  assert.equal(deriveLiveEntry({ position: null, order: null, price: 59000 }), 59000);
});

test('入场价用 || 而非 ??:假值与缺值一样回退到当前价', () => {
  for (const bad of [0, '', null, undefined]) {
    assert.equal(deriveLiveEntry({ position: { avgPx: bad }, order: null, price: 12345 }), 12345, String(bad));
  }
});

test('入场价：字符串 "0" 是**真值** ⇒ 显示 0，不回退（既有行为，勿"优化"）', () => {
  assert.equal(deriveLiveEntry({ position: { avgPx: '0' }, order: null, price: 12345 }), 0);
  assert.equal(deriveLiveEntry({ position: { avgPx: '0.0' }, order: null, price: 12345 }), 0);
});

test('入场价：持仓在但 avgPx 假值时**不会**退到挂单价（改走当前价）', () => {
  assert.equal(deriveLiveEntry({ position: { avgPx: 0 }, order: { px: 60000 }, price: 59000 }), 59000);
});

// ---------- 方向 ----------

test('方向：持仓 side 只认 short，其余一律 long', () => {
  assert.equal(deriveLiveSide({ position: { side: 'short' }, order: null }), 'short');
  assert.equal(deriveLiveSide({ position: { side: 'SHORT' }, order: null }), 'long', '大写不识别，与既有实现一致');
  assert.equal(deriveLiveSide({ position: { side: 'long' }, order: null }), 'long');
  assert.equal(deriveLiveSide({ position: {}, order: null }), 'long');
});

test('方向：无持仓时看挂单 sell/空 关键字，side 优先于 side_raw', () => {
  assert.equal(deriveLiveSide({ position: null, order: { side: 'SELL' } }), 'short');
  assert.equal(deriveLiveSide({ position: null, order: { side: '开空' } }), 'short');
  assert.equal(deriveLiveSide({ position: null, order: { side_raw: 'sell_limit' } }), 'short');
  assert.equal(deriveLiveSide({ position: null, order: { side: 'buy' } }), 'long');
  assert.equal(deriveLiveSide({ position: null, order: {} }), 'long');
  assert.equal(deriveLiveSide({ position: null, order: null }), 'long');
});

// ---------- 止损 ----------

test('止损：持仓四级回退按顺序命中', () => {
  assert.equal(deriveLiveStopLoss({ position: { displayStop: 58000, exchangeSl: 57000 }, order: null }), 58000);
  assert.equal(deriveLiveStopLoss({ position: { exchangeSl: 57000, slTriggerPx: 56000 }, order: null }), 57000);
  assert.equal(deriveLiveStopLoss({ position: { slTriggerPx: 56000, trailingSl: 55000 }, order: null }), 56000);
  assert.equal(deriveLiveStopLoss({ position: { trailingSl: '55000' }, order: null }), 55000);
});

test('止损：displayStop 为 0 截断**持仓层**回退链（勿"优化"）', () => {
  assert.equal(
    deriveLiveStopLoss({ position: { displayStop: 0, exchangeSl: 57000 }, order: null }),
    0,
    '0 非空 ⇒ 持仓层在此截断，不再取 exchangeSl',
  );
  assert.equal(
    deriveLiveStopLoss({ position: { displayStop: 0, exchangeSl: 57000 }, order: { sl_px: 56000 } }),
    56000,
    '截断只作用于持仓层：该值不 > 0，仍会落到挂单层',
  );
});

test('止损：持仓给不出正数时才看挂单', () => {
  assert.equal(deriveLiveStopLoss({ position: {}, order: { sl_px: 55555 } }), 55555);
  assert.equal(deriveLiveStopLoss({ position: { displayStop: -1 }, order: { sl_px: 55555 } }), 55555,
    '负数不算数（>0 才算），继续回退');
  assert.equal(deriveLiveStopLoss({ position: { displayStop: 0 }, order: null }), 0);
  assert.equal(deriveLiveStopLoss({ position: null, order: { sl_px: null } }), 0);
  assert.equal(deriveLiveStopLoss({ position: null, order: null }), 0);
});

// ---------- 止盈 ----------

test('止盈：持仓三级回退 + 挂单兜底', () => {
  assert.equal(deriveLiveTakeProfit({ position: { displayTakeProfit: 65000, exchangeTp: 64000 }, order: null }), 65000);
  assert.equal(deriveLiveTakeProfit({ position: { exchangeTp: 64000, tpTriggerPx: 63000 }, order: null }), 64000);
  assert.equal(deriveLiveTakeProfit({ position: { tpTriggerPx: '63000' }, order: null }), 63000);
  assert.equal(deriveLiveTakeProfit({ position: {}, order: { tp_px: 61000 } }), 61000);
  assert.equal(deriveLiveTakeProfit({ position: null, order: null }), 0);
});

test('止盈：displayTakeProfit 为 0 同样只截断持仓层', () => {
  assert.equal(
    deriveLiveTakeProfit({ position: { displayTakeProfit: 0, exchangeTp: 64000 }, order: null }),
    0,
    '不再取 exchangeTp',
  );
  assert.equal(
    deriveLiveTakeProfit({ position: { displayTakeProfit: 0, exchangeTp: 64000 }, order: { tp_px: 63000 } }),
    63000,
    '仍会落到挂单层',
  );
});

test('止损/止盈互不影响：只读各自字段', () => {
  const position = { displayStop: 58000, displayTakeProfit: 65000 };
  assert.equal(deriveLiveStopLoss({ position, order: null }), 58000);
  assert.equal(deriveLiveTakeProfit({ position, order: null }), 65000);
});

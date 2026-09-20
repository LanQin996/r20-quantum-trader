/**
 * 数据表格（table）可访问性名称准确性与杜绝错位复制守卫闸（批 124）。
 *
 * ## 实测发现的 3 处表格 aria-label 复制错位缺陷：
 *
 * 1. `RadarDrawer.vue` 跨所基差与价差表格被误标为"持仓管理"：
 *    - 表格内容：展示 OKX/Binance/Gate 三所行情价差、资金费与多空比（Cross-Venue Evidence）；
 *    - 修复前：`:aria-label="t('dash.radar.posMgmt')"`（"持仓管理" / "Position management"）；
 *    - 修复后：`:aria-label="t('dash.radar.detail.xvenue')"`（"跨所证据与基差" / "Cross-Venue Evidence"）。
 *
 * 2. `FactorDrawer.vue` 淘汰候选表格跨页面借用台账词条：
 *    - 表格内容：当前决策周期被算法淘汰的场所与原因（Rejected Candidates）；
 *    - 修复前：`:aria-label="t('dash.ledger.venue')"`（"场所"，从台账页抄过来的裸列名）；
 *    - 修复后：`:aria-label="t('dash.matrix.venue.rejectedTitle', undefined, { n: vdRejected.length })"`（"被淘汰候选 · 3"）。
 *
 * 3. `CouncilPage.vue` 六标的点位矩阵表格复制了上一张席位表的标题：
 *    - 表格内容：BTC/ETH/SOL/DOGE/XRP/ADA 六标的推演点位与止盈止损矩阵；
 *    - 修复前：`:aria-label="t('admin.council.seatsTitle')"`（"投委会席位编排"）；
 *    - 修复后：`:aria-label="t('admin.council.matrixTitle')"`（"六标的点位矩阵"）。
 *
 * 运行：`node --test tests/*.test.mjs`
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const SRC = path.resolve(import.meta.dirname, '..', 'src');

test('RadarDrawer 跨所证据表格必须准确绑定 detail.xvenue（严禁误标为 posMgmt）', () => {
  const vue = readFileSync(path.join(SRC, 'components/dashboard/RadarDrawer.vue'), 'utf8');
  assert.doesNotMatch(
    vue,
    /<table[^>]*:aria-label="t\('dash\.radar\.posMgmt'\)"/,
    'RadarDrawer 跨所证据表格错误使用了持仓管理 posMgmt 的 aria-label',
  );
  assert.match(
    vue,
    /<table[^>]*:aria-label="t\('dash\.radar\.detail\.xvenue'\)"/,
    'RadarDrawer 跨所证据表格缺少 :aria-label="t(\'dash.radar.detail.xvenue\')"',
  );
});

test('FactorDrawer 淘汰候选表格必须准确绑定 rejectedTitle（严禁跨模块借用 ledger.venue）', () => {
  const vue = readFileSync(path.join(SRC, 'components/dashboard/FactorDrawer.vue'), 'utf8');
  assert.doesNotMatch(
    vue,
    /<table[^>]*:aria-label="t\('dash\.ledger\.venue'\)"/,
    'FactorDrawer 淘汰候选表格错误借用了台账模块的 dash.ledger.venue 词条',
  );
  assert.match(
    vue,
    /<table[^>]*:aria-label="t\('dash\.matrix\.venue\.rejectedTitle'/,
    'FactorDrawer 淘汰候选表格缺少准确的 rejectedTitle aria-label',
  );
});

test('CouncilPage 六标的点位矩阵表格必须绑定 matrixTitle（严禁复制 seatsTitle）', () => {
  const vue = readFileSync(path.join(SRC, 'views/admin/CouncilPage.vue'), 'utf8');
  const matrixBlockMatch = vue.match(/<!-- 六标的点位矩阵 -->[\s\S]*?<table\b([^>]*)>/);
  assert.ok(matrixBlockMatch, '找不到六标的点位矩阵表格');
  const tableAttrs = matrixBlockMatch[1];
  assert.doesNotMatch(
    tableAttrs,
    /:aria-label="t\('admin\.council\.seatsTitle'\)"/,
    '六标的点位矩阵表格错误复制了席位表 seatsTitle 的 aria-label',
  );
  assert.match(
    tableAttrs,
    /:aria-label="t\('admin\.council\.matrixTitle'\)"/,
    '六标的点位矩阵表格缺少 matrixTitle 的 aria-label',
  );
});

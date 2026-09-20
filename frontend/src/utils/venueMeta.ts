/**
 * US-004 · 选所决策证据渲染的单一事实源（决策抽屉 + 账户区组合风险行共用）。
 *
 * 后端契约（scripts/ai_factor_trader.py `_decision_payload`，随
 * data/ai_brain_decisions.json 的 venue_decision 键落盘）：
 *   { preferred_venue, venue, reason_code, reasons[],
 *     rejected: [{venue, stage, reason}], hysteresis_applied, allocation,
 *     decided_utc, outcome?, skip_reason?, budget? }
 * stage 取值对齐 venue_router._stage_of：executable/listing/precision/
 * freshness/budget/unknown。
 *
 * 组合风险行契约（/api/all 顶层 portfolio_risk，US-001 预留层口径）：
 *   { environment?, total_budget_usdt, reserved_usdt, available_usdt, updated_utc? }
 *
 * 铁律（与三所账户卡同源）：
 *   - 缺值/null 一律归一为 null，渲染层显「—」，绝不以 0 冒充未知；
 *   - 徽章主识别是文字（场所名/阶段词/原因码），色点与色调仅作辅助。
 */

/** 有限数值才是已知；null/undefined/''/NaN/Infinity → null（未知） */
export function numOrNull(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

const VENUE_NAMES: Record<string, string> = {
  okx: 'OKX',
  binance: 'Binance',
  gate: 'Gate.io',
};

/* 场所色只做辅助识别（色盲安全：文字永远在场）；复用既有语义色板，不新造品牌色 */
const VENUE_COLORS: Record<string, string> = {
  okx: 'var(--up)',
  binance: 'var(--warn)',
  gate: 'var(--info)',
};

/** 场所展示名：已知所给统一品牌名，未知场所原样透传（不吞数据） */
export function venueLabel(v: unknown): string {
  const raw = String(v ?? '').trim();
  if (!raw) return '--';
  return VENUE_NAMES[raw.toLowerCase()] || raw;
}

export function venueColor(v: unknown): string {
  return VENUE_COLORS[String(v ?? '').trim().toLowerCase()] || 'var(--ink-3)';
}

/**
 * 场所徽章的三元色调类（文字 / 描边 / 底色）。
 *
 * 与 `venueColor()` 同源、同语义，只是把裸色值扩成「文字 + 描边 + 底色」这一组，
 * 供徽章类元素直接绑到 `:class`。
 *
 * ⚠️ 推倒重来期修正（批 13）：行情侧多处曾把场所色**内联硬编码**为具体十六进制值
 * 或 Tailwind 色相类（`bg-blue-500` / `text-amber-500` / `bg-emerald-500`），
 * 既绕过色板又与 `venueColor()` 口径不一致 —— 同一个「Binance」在两处会是两种黄。
 * 现统一收口到本函数，全站场所配色单一事实源。
 */
const VENUE_TONE_CLS: Record<string, string> = {
  okx: 'text-[var(--up)] border-[var(--up-line)] bg-[var(--up-bg)]',
  binance: 'text-[var(--warn)] border-[var(--warn-line)] bg-[var(--warn-bg)]',
  gate: 'text-[var(--info)] border-[var(--info-line)] bg-[var(--info-bg)]',
};

export function venueToneCls(v: unknown): string {
  return (
    VENUE_TONE_CLS[String(v ?? '').trim().toLowerCase()] ||
    'text-[var(--ink-3)] border-[var(--line-1)] bg-[var(--surface-1)]'
  );
}

/**
 * 淘汰阶段 → 阶段名（对齐 `venue_router._stage_of`；未知阶段原样透传）。
 *
 * 批 76：此前表里是硬编码中文，英文界面下会直接显示中文。
 * 沿用本项目纯工具模块的既有约定（见 `llmLogic.effortOptions(labelOf)`）：
 * **文案由调用方注入**，本模块不 import i18n。
 * 阶段 → i18n 键的映射固定在这里，避免调用方各写一份。
 */
const STAGE_KEY: Record<string, string> = {
  executable: 'dash.venueAccounts.stage.executable',
  listing: 'dash.venueAccounts.stage.listing',
  precision: 'dash.venueAccounts.stage.precision',
  freshness: 'dash.venueAccounts.stage.freshness',
  budget: 'dash.venueAccounts.stage.budget',
  unknown: 'dash.venueAccounts.stage.unknown',
};

export function stageLabel(stage: unknown, labelOf?: (key: string) => string): string {
  const s = String(stage ?? '').trim();
  if (!s) return '--';
  const key = STAGE_KEY[s.toLowerCase()];
  if (!key || !labelOf) return s;
  return labelOf(key);
}

/**
 * 选所结果徽章色调：绿=中选 / 红=全拒 / 黄=预算闸拒 / 灰=其余。
 * 文字徽章（reason_code + 中选所名）始终是主识别，这里只定辅助色。
 */
export function decisionBadgeCls(raw: { reason_code?: unknown; outcome?: unknown } | null | undefined): string {
  const code = String(raw?.reason_code ?? '').trim().toUpperCase();
  const outcome = String(raw?.outcome ?? '').trim().toLowerCase();
  if (outcome === 'selected' && (code === 'OK' || code === 'OK_HYSTERESIS')) return 'badge badge-up';
  if (outcome.startsWith('budget')) return 'badge badge-warn';
  if (code === 'ALL_REJECTED' || code === 'NO_CANDIDATES' || outcome === 'rejected') return 'badge badge-down';
  return 'badge';
}

/** 对象判定：非 null、非数组的真对象（后端字段缺失/畸形时优雅降级用） */
export function isPlainObj(v: unknown): v is Record<string, any> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

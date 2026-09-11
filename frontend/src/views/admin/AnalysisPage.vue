<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { Download, RefreshCw, GitCompareArrows } from 'lucide-vue-next';
import { get, download } from '../../api/http';
import { useI18n } from '../../composables/useI18n';
import { useToast } from '../../composables/useToast';
import PageHeader from '../../components/admin/PageHeader.vue';
import BaseStat from '../../components/base/BaseStat.vue';
import BaseTabs from '../../components/base/BaseTabs.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import BasePager from '../../components/base/BasePager.vue';
import BaseDrawer from '../../components/base/BaseDrawer.vue';
import BaseCodeBlock from '../../components/base/BaseCodeBlock.vue';

type RecordData = Record<string, any>;
interface Trade extends RecordData { id: string; inst: string; side: string; cost_complete: boolean; net_pnl: number | string | null }
interface Event extends RecordData { id: string; kind: string; status: string; occurred_ms: number; inst: string }
interface Config extends RecordData { id: string; source: string; process: string; captured_ms: number }
const { t, locale } = useI18n();
const tr = (key: string) => t('analysis.' + key);
const toast = useToast();
const api = '/api/v1/admin/analysis';
const summary = ref<RecordData | null>(null);
const loading = ref(false), exporting = ref(false), error = ref('');
const tab = ref('overview');
const bjToday = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
const startDate = ref(new Date(Date.parse(bjToday) - 29 * 86400000).toISOString().slice(0, 10));
const endDate = ref(bjToday), account = ref(''), inst = ref(''), side = ref(''), configuration = ref(''), outcome = ref('');
const groupBy = ref('inst'), groups = ref<RecordData[]>([]);
const trades = ref<Trade[]>([]), tradeTotal = ref(0), tradePage = ref(1);
const events = ref<Event[]>([]), eventTotal = ref(0), eventPage = ref(1), applied = ref('');
const tradeDetail = ref<{ trade: Trade; events: Event[]; config_ids: string[] } | null>(null);
const evidence = ref<Event | null>(null), drawerOpen = ref(false), detailBusy = ref(false);
const leftId = ref(''), rightId = ref('current'), leftConfig = ref<Config | null>(null), rightConfig = ref<Config | null>(null);
let sequence = 0, detailSequence = 0, configSequence = 0;
const tabs = computed(() => ['overview', 'attribution', 'configurations', 'execution'].map(key => ({ key, label: tr(key) })));
const dimensions = ['inst', 'side', 'config_id', 'confidence', 'duration', 'exit_reason'];
const eventLabel = (kind: string) => {
  const key = ({ 'llm.request': 'modelRequest', 'llm.response': 'modelResponse', 'decision.proposed': 'proposed', 'decision.filtered': 'riskDecision', 'order.submitted': 'submitted', 'order.fill': 'filled', 'order.adjusted': 'orderAdjustment', 'position.exit_reason': 'exitReason', 'position.sample': 'positionSample', 'risk.rule': 'riskRule' } as Record<string,string>)[kind];
  return key ? tr(key) : kind;
};
const dimensionLabel = (key: string) => tr(({ config_id: 'config', exit_reason: 'exitReason' } as Record<string,string>)[key] || key);
const stats = computed(() => summary.value?.statistics || {});
const execution = computed(() => summary.value?.execution || {});
const health = computed(() => summary.value?.health || {});
const configOptions = computed<Config[]>(() => summary.value?.configurations || []);
const accounts = computed<string[]>(() => Array.from(new Set([account.value, summary.value?.account, ...(summary.value?.accounts || [])])).filter(Boolean) as string[]);
const format = (v: unknown, decimals = 2) => v == null || v === '' || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString(locale.value, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
const pct = (v: unknown) => v == null ? '—' : format(v, 1) + '%';
const time = (v: number | null | undefined) => v ? new Date(v).toLocaleString(locale.value, { timeZone: 'Asia/Shanghai', hour12: false }) : '—';
const json = (v: unknown) => JSON.stringify(v, null, 2) ?? '—';
const color = (v: unknown) => v == null ? '' : Number(v) > 0 ? 'up' : Number(v) < 0 ? 'down' : '';
const metrics = computed(() => [
  ['net', stats.value.net_pnl], ['pf', stats.value.profit_factor], ['payoff', stats.value.payoff_ratio],
  ['avgWin', stats.value.avg_win], ['avgLoss', stats.value.avg_loss], ['expectancy', stats.value.expectancy],
  ['streak', stats.value.longest_loss_streak], ['drawdown', stats.value.max_realized_drawdown],
  ['fees', stats.value.fee], ['funding', stats.value.funding_fee],
] as const);
const fault = computed(() => Boolean(health.value.capture_fault && Object.keys(health.value.capture_fault).length) || health.value.sync?.some((x: RecordData) => x.error));
const pending = computed(() => !health.value.sync?.length || health.value.sync.some((x: RecordData) => !x.complete));
const syncProblems = computed(() => (health.value.sync || []).filter((x: RecordData) => x.error).map((x: RecordData) => `${x.source}: ${x.error}`));

function params() {
  if (!startDate.value || !endDate.value || endDate.value < startDate.value) throw new Error(tr('start') + ' / ' + tr('end'));
  const exclusive = new Date(Date.parse(endDate.value) + 86400000).toISOString().slice(0, 10);
  return new URLSearchParams({ account: account.value, start: startDate.value, end: exclusive, inst: inst.value, side: side.value, config_id: configuration.value, result: outcome.value }).toString();
}
async function apply(reset = true) {
  const id = ++sequence;
  loading.value = true; error.value = '';
  try {
    const query = params();
    if (reset) { tradePage.value = 1; eventPage.value = 1; }
    const [s, rows, grouped, history] = await Promise.all([
      get<RecordData>(api + '/summary?' + query),
      get<{ items: Trade[]; total: number }>(api + '/trades?' + query + '&page=' + tradePage.value),
      tab.value === 'attribution' ? get<{ items: RecordData[] }>(api + '/breakdown?' + query + '&by=' + groupBy.value) : Promise.resolve({ items: [] }),
      tab.value === 'execution' ? get<{ items: Event[]; total: number }>(api + '/events?' + query + '&page=' + eventPage.value) : Promise.resolve({ items: [], total: 0 }),
    ]);
    if (id !== sequence) return;
    summary.value = s; account.value = s.account;
    const fixed = new URLSearchParams(query); fixed.set('account', s.account); applied.value = fixed.toString();
    trades.value = rows.items; tradeTotal.value = rows.total; groups.value = grouped.items;
    events.value = history.items; eventTotal.value = history.total;
    if (!configOptions.value.some(c => c.id === leftId.value)) leftId.value = configOptions.value[0]?.id || '';
  } catch (e) { if (id === sequence) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (id === sequence) loading.value = false; }
}
async function changePage(kind: 'trade' | 'event', page: number) {
  if (!applied.value || loading.value) return;
  const query = applied.value;
  try {
    if (kind === 'trade') {
      const r = await get<{ items: Trade[]; total: number }>(api + '/trades?' + query + '&page=' + page);
      if (query !== applied.value) return;
      trades.value = r.items; tradeTotal.value = r.total; tradePage.value = page;
    } else {
      const r = await get<{ items: Event[]; total: number }>(api + '/events?' + query + '&page=' + page);
      if (query !== applied.value) return;
      events.value = r.items; eventTotal.value = r.total; eventPage.value = page;
    }
  } catch (e) { toast.err(String(e)); }
}
async function loadGroups() {
  if (!applied.value) return;
  const query = applied.value, by = groupBy.value;
  try {
    const r = await get<{ items: RecordData[] }>(api + '/breakdown?' + query + '&by=' + by);
    if (query === applied.value && by === groupBy.value) groups.value = r.items;
  } catch (e) { toast.err(String(e)); }
}
async function inspectTrade(id: string) {
  const seq = ++detailSequence;
  drawerOpen.value = true; detailBusy.value = true; tradeDetail.value = null; evidence.value = null;
  try {
    const d = await get<{ trade: Trade; events: Event[]; config_ids: string[] }>(api + '/trades/' + encodeURIComponent(id) + '?account=' + encodeURIComponent(summary.value?.account || ''));
    if (seq === detailSequence) tradeDetail.value = d;
  } catch (e) { toast.err(String(e)); } finally { if (seq === detailSequence) detailBusy.value = false; }
}
async function inspectEvent(id: string, keepTrade = false) {
  const seq = ++detailSequence;
  drawerOpen.value = true; detailBusy.value = true; evidence.value = null;
  if (!keepTrade) tradeDetail.value = null;
  try {
    const d = await get<Event>(api + '/events/' + encodeURIComponent(id) + '?account=' + encodeURIComponent(summary.value?.account || ''));
    if (seq === detailSequence) evidence.value = d;
  } catch (e) { toast.err(String(e)); } finally { if (seq === detailSequence) detailBusy.value = false; }
}
async function compare() {
  const seq = ++configSequence;
  leftConfig.value = null; rightConfig.value = null;
  if (!leftId.value) return;
  try {
    const url = (id: string) => api + '/configurations/' + encodeURIComponent(id) + '?account=' + encodeURIComponent(summary.value?.account || '');
    const [left, right] = await Promise.all([get<Config>(url(leftId.value)), get<Config>(url(rightId.value))]);
    if (seq === configSequence) { leftConfig.value = left; rightConfig.value = right; }
  } catch (e) { toast.err(String(e)); }
}
function flatten(v: unknown, prefix = '', out: Record<string,string> = {}) {
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    for (const [k, x] of Object.entries(v)) flatten(x, prefix ? prefix + '.' + k : k, out);
  } else out[prefix] = json(v);
  return out;
}
const differences = computed(() => {
  if (!leftConfig.value || !rightConfig.value) return [];
  const a = flatten(leftConfig.value.body), b = flatten(rightConfig.value.body);
  return Array.from(new Set([...Object.keys(a), ...Object.keys(b)])).sort().filter(k => a[k] !== b[k]).map(key => ({ key, before: a[key] ?? '—', after: b[key] ?? '—' }));
});
async function exportAll() {
  if (!applied.value) return;
  exporting.value = true;
  try { await download(api + '/export?' + applied.value, 'r20-analysis-' + bjToday + '.zip'); toast.ok(tr('exported')); }
  catch (e) { toast.err(String(e)); } finally { exporting.value = false; }
}
type PlotPoint = { time_ms: number; net: number; drawdown: number; trade_id: string; x: number; y: number; dy: number };
const hoveredPoint = ref<PlotPoint | null>(null);
const plot = computed(() => {
  const source: { time_ms: number; net: number; drawdown: number; trade_id: string }[] = stats.value.curve || [];
  const limit = 300;
  const rows = source.length <= limit ? source : Array.from({ length: limit }, (_, i) => source[Math.round(i * (source.length - 1) / (limit - 1))]);
  const values = rows.flatMap(p => [p.net, -p.drawdown]);
  const min = values.reduce((a, b) => Math.min(a, b), 0), max = values.reduce((a, b) => Math.max(a, b), 0), span = max - min || 1;
  const y = (n: number) => 150 - (n - min) / span * 132;
  const points = rows.map((p, i) => ({ ...p, x: 10 + i / Math.max(1, rows.length - 1) * 780, y: y(p.net), dy: y(-p.drawdown) }));
  return { points, zero: y(0), min, max, current: rows.at(-1)?.net ?? null, trades: source.length, line: points.map(p => p.x + ',' + p.y).join(' '), drawdown: points.map(p => p.x + ',' + p.dy).join(' ') };
});
watch(groupBy, loadGroups);
watch([leftId, rightId], compare);
watch(tab, value => { if (value === 'configurations') compare(); });
onMounted(() => apply());
</script>

<template>
  <div class="analysis-page space-y-4">
    <PageHeader :title="tr('title')" :description="tr('subtitle')">
      <template #actions>
        <button class="btn btn-ghost btn-sm" :disabled="loading" @click="apply(false)"><RefreshCw :class="{ 'animate-spin': loading }" />{{ tr('refresh') }}</button>
        <button class="btn btn-primary btn-sm" :disabled="exporting || loading || !applied" @click="exportAll"><Download />{{ exporting ? tr('exporting') : tr('export') }}</button>
      </template>
    </PageHeader>
    <form class="card p-3 flex flex-wrap gap-3 items-end" @submit.prevent="apply()">
      <label>{{ tr('account') }}<select v-model="account" class="field"><option v-if="!account" value="">{{ tr('current') }}</option><option v-for="a in accounts" :key="a" :value="a">{{ a }}</option></select></label>
      <label>{{ tr('start') }}<input v-model="startDate" type="date" required class="field" /></label>
      <label>{{ tr('end') }}<input v-model="endDate" type="date" required class="field" /></label>
      <label>{{ tr('inst') }}<select v-model="inst" class="field"><option value="">{{ tr('all') }}</option><option v-for="i in summary?.instruments" :key="i" :value="i">{{ i }}</option></select></label>
      <label>{{ tr('side') }}<select v-model="side" class="field"><option value="">{{ tr('all') }}</option><option value="long">{{ tr('long') }}</option><option value="short">{{ tr('short') }}</option></select></label>
      <label>{{ tr('outcome') }}<select v-model="outcome" class="field"><option value="">{{ tr('all') }}</option><option value="win">{{ tr('win') }}</option><option value="loss">{{ tr('loss') }}</option><option value="breakeven">{{ tr('breakeven') }}</option></select></label>
      <label class="max-w-64">{{ tr('config') }}<select v-model="configuration" class="field"><option value="">{{ tr('all') }}</option><option v-for="c in configOptions" :key="c.id" :value="c.id">{{ c.process }} · {{ c.id.slice(0, 10) }}</option></select></label>
      <button class="btn btn-secondary" type="submit" :disabled="loading">{{ tr('apply') }}</button>
    </form>
    <p v-if="error" role="alert" class="card p-3 down">{{ error }}</p>
    <div class="card p-3 space-y-2">
      <div class="flex flex-wrap items-center gap-3 text-xs">
        <strong>{{ tr('health') }}</strong>
        <span>{{ tr('coverage') }}: {{ time(health.coverage_start_ms) }}</span>
        <span>{{ tr('lastSeen') }}: {{ time(health.last_observed_ms) }}</span>
        <span>{{ tr('disk') }}: {{ format((health.bytes || 0) / 1048576) }} MB</span>
        <span>{{ tr('linkRate') }}: {{ pct(stats.decision_link_rate) }}</span>
        <span>{{ tr('costRate') }}: {{ pct(stats.cost_complete_rate) }}</span>
      </div>
      <p v-if="fault" role="status" class="text-xs down">{{ tr('syncError') }}<span v-if="syncProblems.length"> · {{ syncProblems.join('；') }}</span></p>
      <p v-else-if="pending" class="text-xs t-faint">{{ tr('syncPending') }}</p>
      <details v-if="fault || pending" class="text-xs t-faint"><summary>{{ tr('coverageNote') }}</summary><BaseCodeBlock :code="json(health)" /></details>
    </div>
    <BaseTabs v-model="tab" :items="tabs" />
    <p v-if="loading" role="status" class="text-sm t-faint">{{ tr('refresh') }}…</p>
    <section v-if="tab === 'overview'" class="space-y-4">
      <div class="card grid grid-cols-2 lg:grid-cols-4">
        <BaseStat :label="tr('closed')" :value="format(stats.closed_count, 0)" :delta="tr('incomplete') + ': ' + format(stats.incomplete_count, 0)" />
        <BaseStat :label="tr('complete')" :value="format(stats.sample_count, 0)" :delta="[tr('win') + ' ' + format(stats.wins, 0), tr('loss') + ' ' + format(stats.losses, 0), tr('breakeven') + ' ' + format(stats.breakeven, 0)].join(' / ')" />
        <BaseStat :label="tr('winRate')" :value="pct(stats.win_rate)" />
        <BaseStat :label="tr('confidenceInterval')" :value="stats.win_rate_ci95 ? stats.win_rate_ci95.map(pct).join(' ~ ') : '—'" />
      </div>
      <p class="text-xs t-faint">{{ tr('sampleNote') }}</p>
      <div class="card grid grid-cols-2 md:grid-cols-3 xl:grid-cols-5"><BaseStat v-for="[key, value] in metrics" :key="key" :label="tr(key)" :value="format(value, key === 'streak' ? 0 : 2)" /></div>
      <div class="card p-4">
        <h2 class="font-semibold text-sm">{{ tr('curve') }}</h2><p class="text-xs t-faint mt-1">{{ tr('curveNote') }}</p>
        <BaseEmpty v-if="!plot.points.length" :text="tr('noData')" />
        <div v-if="plot.points.length" class="curve-summary grid grid-cols-2 md:grid-cols-4 gap-2 mt-3 text-xs"><span><b>{{ tr('minimum') }}</b><strong>{{ format(plot.min) }}</strong></span><span><b>{{ tr('maximum') }}</b><strong>{{ format(plot.max) }}</strong></span><span><b>{{ tr('current') }}</b><strong :class="color(plot.current)">{{ format(plot.current) }}</strong></span><span><b>{{ tr('trades') }}</b><strong>{{ format(plot.trades, 0) }}</strong></span></div>
        <div v-if="plot.points.length" class="curve-wrap mt-3"><svg viewBox="0 0 800 190" class="w-full" role="img" :aria-label="tr('curve')">
          <line x1="34" x2="790" :y1="plot.zero + 10" :y2="plot.zero + 10" stroke="var(--line-2)" stroke-dasharray="4 4" />
          <text x="2" y="18" class="curve-label">{{ format(plot.max) }}</text><text x="2" y="158" class="curve-label">{{ format(plot.min) }}</text><text x="2" :y="plot.zero + 7" class="curve-label">0</text>
          <polyline :points="plot.drawdown" fill="none" stroke="var(--down)" stroke-width="2" opacity=".7" transform="translate(0 10)" />
          <polyline :points="plot.line" fill="none" stroke="var(--accent)" stroke-width="2.5" transform="translate(0 10)" />
          <circle v-for="p in plot.points" :key="p.trade_id" :cx="p.x" :cy="p.y + 10" r="4.5" fill="var(--accent)" tabindex="0" role="button" @mouseenter="hoveredPoint = p" @mouseleave="hoveredPoint = null" @focus="hoveredPoint = p" @blur="hoveredPoint = null"><title>{{ time(p.time_ms) }} · {{ tr('net') }} {{ format(p.net) }}</title></circle>
        </svg><div v-if="hoveredPoint" class="curve-tooltip" role="status">{{ time(hoveredPoint.time_ms) }} · {{ tr('net') }} {{ format(hoveredPoint.net) }} · {{ tr('drawdown') }} {{ format(hoveredPoint.drawdown) }}</div></div>
      </div>
    </section>
    <section v-if="tab === 'attribution'" class="space-y-4">
      <div class="card p-3">
        <div class="flex flex-wrap items-center gap-3 mb-3"><label>{{ tr('group') }}<select v-model="groupBy" class="field"><option v-for="d in dimensions" :key="d" :value="d">{{ dimensionLabel(d) }}</option></select></label><p class="text-xs t-faint">{{ tr('modelNote') }}</p></div>
        <div class="overflow-x-auto"><table class="table"><thead><tr><th>{{ tr('group') }}</th><th>{{ tr('samples') }}</th><th>{{ tr('incomplete') }}</th><th>{{ tr('winRate') }}</th><th>{{ tr('net') }}</th><th>{{ tr('pf') }}</th><th>{{ tr('expectancy') }}</th></tr></thead>
          <tbody><tr v-for="g in groups" :key="g.key"><td class="max-w-60 break-all">{{ g.key }}</td><td>{{ g.sample_count }}</td><td>{{ g.incomplete_count }}</td><td>{{ pct(g.win_rate) }}</td><td :class="color(g.net_pnl)">{{ format(g.net_pnl) }}</td><td>{{ format(g.profit_factor) }}</td><td>{{ format(g.expectancy) }}</td></tr></tbody></table></div>
      </div>
      <div class="card overflow-hidden">
        <BaseEmpty v-if="!trades.length" :text="tr('noData')" />
        <div v-else class="overflow-x-auto"><table class="table"><thead><tr><th>{{ tr('inst') }}</th><th>{{ tr('side') }}</th><th>{{ tr('closeTime') }}</th><th>{{ tr('net') }}</th><th>{{ tr('exitReason') }}</th><th>{{ tr('evidence') }}</th><th>{{ tr('details') }}</th></tr></thead>
          <tbody><tr v-for="row in trades" :key="row.id"><td>{{ row.inst }}</td><td>{{ tr(row.side) }}</td><td class="num whitespace-nowrap">{{ row.close_time }}</td><td class="num" :class="color(row.net_pnl)">{{ format(row.net_pnl) }}</td><td>{{ row.exit_reason || tr('unknown') }}</td><td>{{ tr(row.exit_evidence || 'unknown') }}<span v-if="!row.cost_complete" class="block text-xs t-faint">{{ tr('incomplete') }}</span></td><td><button class="btn btn-ghost btn-sm" @click="inspectTrade(row.id)">{{ tr('details') }}</button></td></tr></tbody></table></div>
        <BasePager :page="tradePage" :page-count="Math.max(1, Math.ceil(tradeTotal / 20))" :total="tradeTotal" @update:page="changePage('trade', $event)" />
      </div>
    </section>

    <section v-if="tab === 'configurations'" class="space-y-4">
      <p class="text-xs t-faint">{{ tr('configurationNote') }}</p>
      <div class="card p-3 flex flex-wrap gap-3 items-end">
        <label class="flex-1 min-w-48">{{ tr('historical') }}<select v-model="leftId" class="field"><option value="">{{ tr('selectConfig') }}</option><option v-for="c in configOptions" :key="c.id" :value="c.id">{{ c.process }} · {{ c.id.slice(0, 10) }} · {{ time(c.captured_ms) }}</option></select></label>
        <label class="flex-1 min-w-48">{{ tr('compare') }}<select v-model="rightId" class="field"><option value="current">{{ tr('current') }}</option><option v-for="c in configOptions" :key="c.id" :value="c.id">{{ c.process }} · {{ c.id.slice(0, 10) }}</option></select></label>
        <button class="btn btn-secondary" :disabled="!leftId" @click="compare"><GitCompareArrows />{{ tr('compare') }}</button>
      </div>
      <BaseEmpty v-if="!leftId" :text="tr('selectConfig')" />
      <template v-if="leftConfig && rightConfig">
        <div class="card p-3 overflow-x-auto"><h2 class="font-semibold text-sm mb-2">{{ tr('differences') }} · {{ differences.length }}</h2><BaseEmpty v-if="!differences.length" :text="tr('noDifferences')" />
          <table v-else class="table config-diff"><thead><tr><th>{{ tr('field') }}</th><th>{{ tr('before') }}</th><th>{{ tr('after') }}</th></tr></thead>
            <tbody><tr v-for="d in differences" :key="d.key"><td>{{ d.key }}</td><td><pre>{{ d.before }}</pre></td><td><pre>{{ d.after }}</pre></td></tr></tbody></table>
        </div>
        <div class="grid lg:grid-cols-2 gap-3"><details class="card p-3"><summary>{{ tr('historical') }} · {{ time(leftConfig.captured_ms) }}</summary><BaseCodeBlock :code="json(leftConfig)" max-height="600px" /></details><details class="card p-3"><summary>{{ tr('compare') }} · {{ time(rightConfig.captured_ms) }}</summary><BaseCodeBlock :code="json(rightConfig)" max-height="600px" /></details></div>
      </template>
    </section>
    <section v-if="tab === 'execution'" class="space-y-4">
      <div class="card grid grid-cols-2 md:grid-cols-4">
        <BaseStat :label="tr('proposed')" :value="format(execution.proposed_decisions, 0)" />
        <BaseStat :label="tr('passed')" :value="format(execution.passed_decisions, 0)" />
        <BaseStat :label="tr('submitted')" :value="format(execution.submitted_orders, 0)" />
        <BaseStat :label="tr('filled')" :value="format(execution.filled_orders, 0)" />
      </div>
      <p class="text-xs t-faint">{{ tr('executionNote') }}</p>
      <div v-if="execution.rejections?.length" class="card p-3"><h2 class="font-semibold text-sm mb-2">{{ tr('rejection') }}</h2><div v-for="r in execution.rejections" :key="r.reason" class="flex justify-between gap-4 py-1 text-xs"><span>{{ r.reason }}</span><strong class="num">{{ r.count }}</strong></div></div>
      <div class="card overflow-hidden"><BaseEmpty v-if="!events.length" :text="tr('emptyEvents')" />
        <div v-else class="overflow-x-auto"><table class="table"><thead><tr><th>{{ tr('time') }}</th><th>{{ tr('kind') }}</th><th>{{ tr('inst') }}</th><th>{{ tr('status') }}</th><th>{{ tr('evidence') }}</th></tr></thead><tbody><tr v-for="e in events" :key="e.id"><td class="whitespace-nowrap">{{ time(e.occurred_ms) }}</td><td>{{ eventLabel(e.kind) }}<span class="block text-xs t-faint">{{ e.kind }}</span></td><td>{{ e.inst || '—' }}</td><td :class="e.status === 'failed' || e.status === 'rejected' ? 'down' : ''">{{ e.status }}</td><td><button class="btn btn-ghost btn-sm" @click="inspectEvent(e.id)">{{ tr('raw') }}</button></td></tr></tbody></table></div>
        <BasePager :page="eventPage" :page-count="Math.max(1, Math.ceil(eventTotal / 30))" :total="eventTotal" @update:page="changePage('event', $event)" />
      </div>
    </section>
    <BaseDrawer :open="drawerOpen" width="920px" :title="tradeDetail ? tr('details') + ' · ' + tradeDetail.trade.inst : tr('preview')" @close="drawerOpen = false; detailSequence++">
      <p v-if="detailBusy" role="status" class="t-faint">{{ tr('refresh') }}…</p>
      <div v-if="tradeDetail" class="space-y-3">
        <p v-if="!tradeDetail.trade.decision_id" class="text-xs t-faint">{{ tr('detailsUnknown') }}</p>
        <div class="grid grid-cols-2 md:grid-cols-3 gap-2">
          <BaseStat :label="tr('net')" :value="format(tradeDetail.trade.net_pnl)" />
          <BaseStat :label="tr('fees')" :value="format(tradeDetail.trade.fee)" />
          <BaseStat :label="tr('funding')" :value="format(tradeDetail.trade.funding_fee)" />
        </div>
        <dl class="grid grid-cols-2 gap-3 text-xs card p-3">
          <div><dt class="t-faint">{{ tr('openTime') }}</dt><dd class="mt-1">{{ tradeDetail.trade.open_time }}</dd></div>
          <div><dt class="t-faint">{{ tr('closeTime') }}</dt><dd class="mt-1">{{ tradeDetail.trade.close_time }}</dd></div>
          <div><dt class="t-faint">{{ t('dash.ledger.col.entry') }}</dt><dd class="mt-1 num">{{ format(tradeDetail.trade.open_px, 4) }}</dd></div>
          <div><dt class="t-faint">{{ t('dash.ledger.col.exit') }}</dt><dd class="mt-1 num">{{ format(tradeDetail.trade.close_px, 4) }}</dd></div>
          <div><dt class="t-faint">{{ tr('exitReason') }}</dt><dd class="mt-1">{{ tradeDetail.trade.exit_reason || tr('unknown') }} · {{ tr(tradeDetail.trade.exit_evidence || 'unknown') }}</dd></div>
          <div><dt class="t-faint">{{ tr('delta') }}</dt><dd class="mt-1 num">{{ format(tradeDetail.trade.entry_deviation_bps) }}</dd></div>
        </dl>
        <details class="card p-3"><summary>{{ tr('body') }}</summary><BaseCodeBlock :code="json(tradeDetail.trade)" /></details>
        <h3 class="text-sm font-semibold">{{ tr('timeline') }}</h3>
        <div v-for="e in tradeDetail.events" :key="e.id" class="flex items-center gap-2 text-xs border-b py-2" style="border-color: var(--line-1)"><span class="num">{{ time(e.occurred_ms) }}</span><span class="flex-1">{{ eventLabel(e.kind) }} · {{ e.status }}</span><button class="btn btn-ghost btn-sm" @click="inspectEvent(e.id, true)">{{ tr('raw') }}</button></div>
      </div>
      <div v-if="evidence" class="space-y-2 mt-4"><h3 class="text-sm font-semibold">{{ evidence.kind }}</h3><BaseCodeBlock :code="json(evidence)" max-height="70vh" /></div>
    </BaseDrawer>
  </div>
</template>
<style scoped>
.analysis-page { color: var(--ink-1); }
.analysis-page label { display: flex; flex-direction: column; gap: 5px; font-size: 11px; color: var(--ink-2); }
.analysis-page summary { cursor: pointer; font-size: 12px; padding: 6px 0; }
.config-diff { table-layout: fixed; min-width: 660px; }
.config-diff td { overflow-wrap: anywhere; vertical-align: top; }
.config-diff pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 160px; overflow: auto; font-size: 11px; }
.curve-wrap { position: relative; min-height: 190px; }
.curve-summary span { display: flex; flex-direction: column; gap: 2px; padding: 6px 8px; border: 1px solid var(--line-1); border-radius: 6px; }
.curve-summary b, .curve-label { color: var(--ink-3); font-size: 10px; }
.curve-summary strong { color: var(--ink-1); font-variant-numeric: tabular-nums; }
.curve-label { fill: var(--ink-3); }
.curve-wrap circle { cursor: crosshair; outline: none; }
.curve-wrap circle:hover, .curve-wrap circle:focus { r: 7px; stroke: var(--ink-1); stroke-width: 2px; }
.curve-tooltip { position: absolute; top: 4px; right: 4px; padding: 6px 8px; border: 1px solid var(--line-1); border-radius: 6px; background: var(--surface-2); color: var(--ink-1); font-size: 11px; pointer-events: none; }
</style>

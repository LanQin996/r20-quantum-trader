<script setup lang="ts">
/**
 * KpiRibbon.vue · DeepSeek Harness 风格核心指标仪表盘
 * 纯净低饱和黑白/深灰主题，分层卡片结构，呈现多所总权益、走势、浮亏与防线
 */
import { computed, onMounted, ref } from 'vue';
import { ShieldCheck, Layers } from 'lucide-vue-next';
import { useDashboardStore } from '../../stores/dashboard';
import { useVenueAccountsStore } from '../../stores/venueAccounts';
import { useI18n } from '../../composables/useI18n';
import { fmtNum, fmtSigned, fmtPct, arrow } from '../../utils/format';
import { venueColor } from '../../utils/venueMeta';
import BaseStat from '../base/BaseStat.vue';
import BaseSparkline from '../base/BaseSparkline.vue';

const store = useDashboardStore();
const venueStore = useVenueAccountsStore();
const { t } = useI18n();

const account = computed(() => store.data?.account || ({} as any));
const today = computed(() => (store.data as any)?.today_stats || {});

const isLiveEnv = computed(() => venueStore.environment === 'live');
const envBadgeText = computed(() => (isLiveEnv.value ? t('dash.venueAccounts.envLive') : t('dash.venueAccounts.envDemo')));

const portfolioSummary = computed(() => venueStore.portfolioSummary || (store.data as any)?.multi_venue_portfolio || null);
const hasMultiVenue = computed(() => {
  const sum = portfolioSummary.value;
  return !!sum && Number(sum.total_equity || 0) > 0;
});

const totalEquityNum = computed(() => {
  const sum = portfolioSummary.value;
  if (sum && Number(sum.total_equity || 0) > 0) return Number(sum.total_equity);
  return Number(account.value.total_eq || 0);
});

const totalAggregatedEquity = computed(() => {
  return fmtNum(totalEquityNum.value, 2);
});

const distOkx = computed(() => Number(portfolioSummary.value?.asset_distribution?.okx?.share_pct || 0));
const distBinance = computed(() => Number(portfolioSummary.value?.asset_distribution?.binance?.share_pct || 0));
const distGate = computed(() => Number(portfolioSummary.value?.asset_distribution?.gate?.share_pct || 0));

const todayNet = computed(() => Number(today.value.net_realized ?? today.value.total_pnl ?? 0));
const todayTrades = computed(() => Number(today.value.closed_trades ?? (Number(today.value.win_trades ?? 0) + Number(today.value.loss_trades ?? 0) + Number(today.value.breakeven_trades ?? 0))));
const todayWinRate = computed(() => today.value.win_rate == null ? null : Number(today.value.win_rate));

const floatPnl = computed(() => Number(account.value.pos_upl_total ?? account.value.upl ?? 0));
const posMargin = computed(() =>
  store.positions.reduce((s, p: any) => s + (Number(p.margin_usdt ?? p.margin ?? 0) || 0), 0),
);
const floatRoi = computed(() =>
  posMargin.value > 0 ? (floatPnl.value / posMargin.value) * 100 : 0,
);

const longCount = computed(() => store.positions.filter((p) => p.side === 'long').length);
const shortCount = computed(() => store.positions.filter((p) => p.side === 'short').length);

const actualMarginUsed = computed(() => {
  if (posMargin.value > 0) return posMargin.value;
  const sum = portfolioSummary.value;
  if (sum && typeof sum.margin_used === 'number') return Number(sum.margin_used);
  return Number(account.value.total_pos_margin || 0);
});

const marginUsage = computed(() => {
  if (totalEquityNum.value > 0) {
    return Math.round((actualMarginUsed.value / totalEquityNum.value) * 1000) / 10;
  }
  return 0;
});

const ocoCoverage = computed(() => {
  const total = store.positions.length;
  if (!total) return { pct: 100, missing: 0 };
  const ok = store.positions.filter((p: any) => p.cloud_oco_verified !== false && p.protectionStatus !== 'unprotected').length;
  return { pct: Math.round((ok / total) * 100), missing: total - ok };
});

/* 14 日净值走势 */
const eqSeries = ref<number[]>([]);
onMounted(async () => {
  try {
    const r = await fetch('/api/v1/equity_history?days=14');
    const d = await r.json();
    eqSeries.value = (d.days || []).map((x: any) => Number(x.equity)).filter((n: number) => Number.isFinite(n));
  } catch {
    /* sparkline optional */
  }
});
</script>

<template>
  <div class="dsh-card">
    <!-- 头部：多所组合分布与状态 -->
    <header
      v-if="hasMultiVenue"
      class="dsh-card-header text-3xs font-medium"
    >
      <div class="flex items-center gap-2">
        <span class="flex items-center gap-1.5 font-bold" style="color: var(--ink-strong)">
          <Layers class="h-3.5 w-3.5 text-[var(--accent)]" />
          {{ t('dash.matrix.kpi.multiEquity') }}
        </span>
        <span class="hidden md:inline font-mono font-semibold" style="color: var(--ink-1)">{{ totalAggregatedEquity }} U</span>
        <span
          class="rounded px-1.5 py-0.5 border text-3xs font-mono"
          style="background-color: var(--surface-2); border-color: var(--line-1); color: var(--ink-2)"
        >
          {{ t('dash.matrix.kpi.venuesConnected', undefined, { n: portfolioSummary?.active_venues_count }) }}
        </span>
      </div>

      <!-- 资产份额条 -->
      <div class="hidden sm:flex items-center gap-3 font-mono">
        <span class="flex items-center gap-1">
          <span class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: venueColor('okx') }" />
          <span style="color: var(--ink-2)">OKX</span>
          <span style="color: var(--ink-1)">{{ distOkx }}%</span>
        </span>
        <span class="flex items-center gap-1">
          <span class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: venueColor('binance') }" />
          <span style="color: var(--ink-2)">Binance</span>
          <span style="color: var(--ink-1)">{{ distBinance }}%</span>
        </span>
        <span class="flex items-center gap-1">
          <span class="h-1.5 w-1.5 rounded-full" :style="{ backgroundColor: venueColor('gate') }" />
          <span style="color: var(--ink-2)">Gate</span>
          <span style="color: var(--ink-1)">{{ distGate }}%</span>
        </span>
      </div>
    </header>

    <!-- 6 个核心指标单元格 -->
    <div class="grid grid-cols-2 gap-px bg-[var(--line-1)] sm:grid-cols-3 xl:grid-cols-6">
      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.comboEquity')"
          :value="totalAggregatedEquity"
          :hint="hasMultiVenue ? `${envBadgeText} ${t('dash.matrix.kpi.comboEquityTip')}` : t('dash.matrix.kpi.equityTip')"
        >
          <template #extra>
            <div class="flex items-center gap-2 mt-1">
              <span class="num text-xs font-semibold" :class="todayNet >= 0 ? 'up' : 'down'">
                {{ arrow(todayNet) }} {{ fmtSigned(todayNet) }}
              </span>
              <BaseSparkline :values="eqSeries" :width="48" :height="18" />
            </div>
          </template>
        </BaseStat>
      </div>

      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.todayPnl')"
          :value="fmtSigned(todayNet)"
          :delta="todayTrades ? `${todayTrades} ${t('common.unitCount')} · ${todayWinRate == null ? '—' : fmtNum(todayWinRate, 1) + '%'}` : undefined"
          :delta-tone="todayWinRate === null ? 'muted' : todayWinRate >= 50 ? 'up' : 'down'"
          :hint="t('dash.matrix.kpi.todayTip')"
        />
      </div>

      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.floatPnl')"
          :value="fmtSigned(floatPnl)"
          :delta="store.positions.length ? `(${fmtPct(floatRoi)})` : '--'"
          :delta-tone="floatPnl >= 0 ? 'up' : 'down'"
          :hint="t('dash.matrix.kpi.floatTip')"
        />
      </div>

      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.ls')"
          :value="`${longCount} / ${shortCount}`"
          hint="L / S"
        />
      </div>

      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.margin')"
          :value="`${fmtNum(marginUsage, 1)}%`"
          :delta="actualMarginUsed > 0 ? `${fmtNum(actualMarginUsed, 2)} U` : '0.00 U'"
          :delta-tone="marginUsage > 70 ? 'down' : marginUsage > 30 ? 'warn' : 'muted'"
          :hint="t('dash.matrix.kpi.marginTip')"
        />
      </div>

      <div class="bg-[var(--surface-1)] hover:bg-[var(--surface-2)] transition-colors flex flex-col justify-between">
        <BaseStat
          :label="t('dash.matrix.kpi.oco')"
          :value="`${ocoCoverage.pct}%`"
          :delta="ocoCoverage.missing ? t('dash.matrix.kpi.missN', undefined, { n: ocoCoverage.missing }) : t('dash.matrix.kpi.allCovered')"
          :delta-tone="ocoCoverage.pct === 100 ? 'up' : 'warn'"
          :hint="t('dash.matrix.kpi.ocoTip')"
        >
          <template #extra>
            <ShieldCheck class="h-4 w-4 shrink-0 mt-1" :style="{ color: ocoCoverage.pct === 100 ? 'var(--up)' : 'var(--warn)' }" />
          </template>
        </BaseStat>
      </div>
    </div>
  </div>
</template>

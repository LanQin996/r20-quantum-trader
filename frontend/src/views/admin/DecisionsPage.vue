<script setup lang="ts">
/**
 * DecisionsPage.vue · 决策审计与三路日志控制台
 * ---------------------------------------------------------------------------
 * 骨架（推倒重来）：
 *   旧 = 页头 + 一张卡（seg 切源 + `<pre>` 原样倾倒纯文本）
 *        —— 日志没有行结构、没有级别语义、没有筛选，纯粹是文件打印
 *   新 = 页头 + 全高日志控制台
 *        ① 卡头：标题 + 「最新在前」标记 + 三路源切换
 *        ② 工具条：内容筛选 + 级别筛选 + 命中计数 + 一键复制
 *        ③ 结构化日志体：时间列 / 级别列 / 正文列，续行缩进对齐
 *        —— 视口高度填满工作台，而不是写死 480px
 *
 * 解析在**客户端**完成（后端仍返回纯文本，接口与参数零改动）。
 * 分组语义沿用原有 ENTRY_START 正则：命中 `[时间戳]` 或 `LEVEL:` 视为新条目起始，
 * 其余行归入上一条目的续行；条目数组反转 → 最新在前（与旧版字符串反转等价的观感）。
 *
 * 后端契约（逐字未改）：
 *   GET /api/v1/admin/logs?source={trader|backend|scheduler}&lines=100
 *   → 读取 res.content，回退 res.lines.join('\n')；异常时取 e.message
 */
import { computed, onMounted, ref } from 'vue';
import { useI18n } from '../../composables/useI18n';
import { useRovingTabs } from '../../composables/useRovingTabs';
import { useApi } from '../../composables/useApi';
import { Terminal, RefreshCw, AlertCircle } from 'lucide-vue-next';
import PageHeader from '../../components/admin/PageHeader.vue';
import CopyButton from '../../components/base/CopyButton.vue';
import BaseLoadingAnnounce from '../../components/base/BaseLoadingAnnounce.vue';

const { t } = useI18n();
const { api } = useApi();

type LogSource = 'trader' | 'backend' | 'scheduler';

interface LogEntry {
  key: number;
  time: string;
  level: string;
  msg: string;
  extra: string[];
  raw: string;
}

const activeLogTab = ref<LogSource>('trader');
const entries = ref<LogEntry[]>([]);
const logLoading = ref(false);
const logError = ref('');

/** 条目起始判定：与重构前完全一致 */
const ENTRY_START = /^(\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]|(INFO|WARNING|ERROR|CRITICAL|DEBUG)[:\s])/;
const TIME_RE = /^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*/;
const LEVEL_RE = /^(INFO|WARNING|ERROR|CRITICAL|DEBUG)\b[:\s]*/;

const LEVEL_RANK: Record<string, number> = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };

/** 按 ENTRY_START 分组 → 拆出时间/级别/正文/续行 */
function parseEntries(raw: string): LogEntry[] {
  const groups: string[][] = [];
  for (const line of raw.split('\n')) {
    if (groups.length === 0 || ENTRY_START.test(line)) groups.push([line]);
    else groups[groups.length - 1].push(line);
  }
  return groups.map((g, i) => {
    const head = g[0] ?? '';
    let rest = head;
    let time = '';
    let level = '';
    const tm = rest.match(TIME_RE);
    if (tm) {
      time = tm[1];
      rest = rest.slice(tm[0].length);
    }
    const lm = rest.match(LEVEL_RE);
    if (lm) {
      level = lm[1];
      rest = rest.slice(lm[0].length);
    }
    return {
      key: i,
      time,
      level,
      msg: rest.trim() || head.trim(),
      extra: g.slice(1).map((s) => s.trim()).filter(Boolean),
      raw: g.join('\n'),
    };
  });
}

async function fetchLogStream(type: LogSource) {
  activeLogTab.value = type;
  logLoading.value = true;
  logError.value = '';
  try {
    const res = await api(`/api/v1/admin/logs?source=${type}&lines=100`);
    const raw: string = res.content || res.lines?.join('\n') || '';
    entries.value = raw ? parseEntries(raw).reverse() : [];
  } catch (e: any) {
    entries.value = [];
    logError.value = t('admin.decisions.fetchLogFailed', undefined, { message: e.message });
  } finally {
    logLoading.value = false;
  }
}

onMounted(() => {
  fetchLogStream('trader');
});

/* ── 客户端筛选（不触发任何新请求） ── */
const query = ref('');
const levelFilter = ref<'all' | 'warn' | 'error'>('all');

/* 批 66：日志来源与日志级别两条分段条此前都没有漫游 tabindex，方向键也无响应。 */
const LOG_TABS = ['trader', 'backend', 'scheduler'] as const;
const { setRef: setLogTabRef, onKeydown: onLogTabKey, roving: logTabRoving } = useRovingTabs(
  () => LOG_TABS.length,
  (i) => { fetchLogStream(LOG_TABS[i]); },
);

const LEVEL_TABS = ['all', 'warn', 'error'] as const;
const { setRef: setLevelRef, onKeydown: onLevelKey, roving: levelRoving } = useRovingTabs(
  () => LEVEL_TABS.length,
  (i) => { levelFilter.value = LEVEL_TABS[i]; },
);

const warnPlusCount = computed(
  () => entries.value.filter((e) => (LEVEL_RANK[e.level] ?? 1) >= 2).length,
);
const errorCount = computed(
  () => entries.value.filter((e) => (LEVEL_RANK[e.level] ?? 1) >= 3).length,
);

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  const min = levelFilter.value === 'error' ? 3 : levelFilter.value === 'warn' ? 2 : -1;
  return entries.value.filter((e) => {
    if ((LEVEL_RANK[e.level] ?? 1) < min) return false;
    if (q && !e.raw.toLowerCase().includes(q)) return false;
    return true;
  });
});

const copyText = computed(() => filtered.value.map((e) => e.raw).join('\n'));

function tone(level: string): string {
  if (level === 'ERROR' || level === 'CRITICAL') return 'is-error';
  if (level === 'WARNING') return 'is-warn';
  if (level === 'DEBUG') return 'is-debug';
  return '';
}
</script>

<template>
  <div class="dc">
    <PageHeader :title="t('nav.admin.decisions')" :description="t('admin.decisions.desc')">
      <template #actions>
        <span class="dsh-pill">
          <span class="dsh-status-dot active" aria-hidden="true" />
          {{ t('admin.decisions.normalRun') }}
        </span>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="logLoading" @click="fetchLogStream(activeLogTab)">
          <RefreshCw :size="14" :class="logLoading && 'animate-spin shrink-0'" />
          <span>{{ t('common.refresh') }}</span>
        </button>
      </template>
    </PageHeader>

    <!-- ══ 日志控制台 ══ -->
    <section class="card dc-console">
      <header class="card-head dc-head">
        <div class="dc-head-left">
          <h2 class="card-title"><Terminal :size="14" />{{ t('nav.admin.decisions') }}</h2>
          <span class="badge">{{ t('admin.decisions.latestFirst') }}</span>
        </div>

        <div class="seg" role="tablist" :aria-label="t('admin.decisions.logSourceAria')">
          <button
            type="button"
            role="tab"
            :ref="setLogTabRef(0)"
            :aria-selected="activeLogTab === 'trader'"
            :tabindex="logTabRoving(activeLogTab === 'trader')"
            :class="{ 'seg-on': activeLogTab === 'trader' }"
            @click="fetchLogStream('trader')"
            @keydown="onLogTabKey($event, 0)"
          >
            {{ t('admin.decisions.tabTrader') }}
          </button>
          <button
            type="button"
            role="tab"
            :ref="setLogTabRef(1)"
            :aria-selected="activeLogTab === 'backend'"
            :tabindex="logTabRoving(activeLogTab === 'backend')"
            :class="{ 'seg-on': activeLogTab === 'backend' }"
            @click="fetchLogStream('backend')"
            @keydown="onLogTabKey($event, 1)"
          >
            {{ t('admin.decisions.tabBackend') }}
          </button>
          <button
            type="button"
            role="tab"
            :ref="setLogTabRef(2)"
            :aria-selected="activeLogTab === 'scheduler'"
            :tabindex="logTabRoving(activeLogTab === 'scheduler')"
            :class="{ 'seg-on': activeLogTab === 'scheduler' }"
            @click="fetchLogStream('scheduler')"
            @keydown="onLogTabKey($event, 2)"
          >
            {{ t('admin.decisions.tabScheduler') }}
          </button>
        </div>
      </header>

      <!-- 工具条：内容 + 级别 + 计数 + 复制 -->
      <div class="dc-tools">
        <input
          v-model="query"
          type="search"
          autocomplete="off"
          spellcheck="false"
          class="field dc-search"
          :aria-label="t('admin.decisions.searchPlaceholder')"
          :placeholder="t('admin.decisions.searchPlaceholder')"
        />

        <div class="seg" role="tablist" :aria-label="t('admin.decisions.logLevelAria')">
          <button
            type="button"
            role="tab"
            :ref="setLevelRef(0)"
            :aria-selected="levelFilter === 'all'"
            :tabindex="levelRoving(levelFilter === 'all')"
            :class="{ 'seg-on': levelFilter === 'all' }"
            @click="levelFilter = 'all'"
            @keydown="onLevelKey($event, 0)"
          >
            {{ t('admin.decisions.filterAll') }}
            <span class="dc-seg-n num">{{ entries.length }}</span>
          </button>
          <button
            type="button"
            role="tab"
            :ref="setLevelRef(1)"
            :aria-selected="levelFilter === 'warn'"
            :tabindex="levelRoving(levelFilter === 'warn')"
            :class="{ 'seg-on': levelFilter === 'warn' }"
            @click="levelFilter = 'warn'"
            @keydown="onLevelKey($event, 1)"
          >
            {{ t('admin.decisions.filterWarn') }}
            <span class="dc-seg-n num">{{ warnPlusCount }}</span>
          </button>
          <button
            type="button"
            role="tab"
            :ref="setLevelRef(2)"
            :aria-selected="levelFilter === 'error'"
            :tabindex="levelRoving(levelFilter === 'error')"
            :class="{ 'seg-on': levelFilter === 'error' }"
            @click="levelFilter = 'error'"
            @keydown="onLevelKey($event, 2)"
          >
            {{ t('admin.decisions.filterError') }}
            <span class="dc-seg-n num">{{ errorCount }}</span>
          </button>
        </div>

        <div class="dc-tools-right">
          <span class="dc-count num">
            {{ t('admin.decisions.entriesCount', undefined, { n: filtered.length }) }}
          </span>
          <CopyButton :text="copyText" />
        </div>
      </div>

      <!-- 日志体 -->
      <div class="dc-body">
        <!-- 首屏加载 -->
        <div v-if="logLoading && !entries.length" class="dc-skel">
          <BaseLoadingAnnounce />
          <div v-for="i in 14" :key="i" class="skeleton skeleton-text" :style="{ width: 40 + ((i * 37) % 55) + '%' }" />
        </div>

        <!-- 拉取失败 -->
        <div v-else-if="logError" role="alert" class="state-block is-error">
          <span class="state-icon"><AlertCircle :size="17" /></span>
          <p class="state-title">{{ t('common.loadFailed') }}</p>
          <p class="state-desc">{{ logError }}</p>
          <button type="button"
            class="btn btn-ghost btn-sm mt-1"
            :disabled="logLoading"
            @click="fetchLogStream(activeLogTab)"
          >
            <RefreshCw :size="14" :class="logLoading && 'animate-spin shrink-0'" />
            <span>{{ t('common.retry') }}</span>
          </button>
        </div>

        <!-- 无日志 -->
        <div v-else-if="!entries.length" class="state-block">
          <span class="state-icon"><Terminal :size="17" /></span>
          <p class="state-title">{{ t('admin.decisions.noLiveLogs') }}</p>
          <p class="state-desc">{{ t('admin.decisions.pullingLogs') }}</p>
        </div>

        <!-- 筛选无命中 -->
        <div v-else-if="!filtered.length" class="state-block">
          <span class="state-icon"><Terminal :size="17" /></span>
          <p class="state-title">{{ t('admin.decisions.filterNoMatch') }}</p>
        </div>

        <!-- 结构化日志行 -->
        <div v-else class="dc-lines">
          <article
            v-for="e in filtered"
            :key="e.key"
            class="dc-entry"
            :class="tone(e.level)"
          >
            <time class="dc-time">{{ e.time ? e.time.slice(11) : '--:--:--' }}</time>
            <span class="dc-level">{{ e.level || 'LOG' }}</span>
            <div class="dc-msg">
              <p class="dc-msg-head">{{ e.msg }}</p>
              <p v-for="(x, i) in e.extra" :key="i" class="dc-msg-cont">{{ x }}</p>
            </div>
          </article>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.dc {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-4);
  flex: 1;
  min-height: 0;
}

/* ══ 控制台容器：填满工作台高度 ══ */
.dc-console {
  flex: 1;
  min-height: 360px;
  overflow: hidden;
}

/* 卡头 */
.dc-head {
  flex-wrap: wrap;
}
.dc-head-left {
  display: flex;
  align-items: center;
  gap: var(--ds-space-3);
  min-width: 0;
}

/* 工具条 */
.dc-tools {
  display: flex;
  align-items: center;
  gap: var(--ds-space-3);
  flex-wrap: wrap;
  padding: var(--ds-space-3) var(--ds-space-4);
  border-bottom: 1px solid var(--ds-color-border-default);
  background-color: var(--ds-color-bg-surface-inset);
}
.dc-search {
  width: 220px;
  flex: 0 1 220px;
}
.dc-seg-n {
  margin-left: 4px;
  color: var(--ds-color-text-placeholder);
  font-size: var(--text-4xs);
}
.dc-tools-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: var(--ds-space-2);
}
.dc-count {
  font-size: var(--text-3xs);
  color: var(--ds-color-text-placeholder);
  white-space: nowrap;
}

/* ══ 日志体 ══ */
.dc-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}
.dc-skel {
  padding: var(--ds-space-3) var(--ds-space-4);
  display: flex;
  flex-direction: column;
  gap:8px;
}
.dc-lines {
  display: flex;
  flex-direction: column;
  padding: var(--ds-space-2) 0;
}

/* 每条：时间 / 级别 / 正文 三列，续行在正文列内缩进 */
.dc-entry {
  display: grid;
  grid-template-columns: 62px 52px minmax(0, 1fr);
  gap: var(--ds-space-3);
  align-items: baseline;
  padding:4px var(--ds-space-4);
  border-left: 2px solid transparent;
  font-family: var(--ds-font-mono);
  font-size: var(--text-2xs);
  line-height: var(--leading-dense);
}
.dc-entry:hover {
  background-color: var(--ds-color-bg-hover);
}
.dc-entry.is-warn {
  border-left-color: var(--warn);
}
.dc-entry.is-error {
  border-left-color: var(--down);
  background-color: var(--down-bg);
}
.dc-entry.is-error:hover {
  background-color: var(--down-bg);
}

.dc-time {
  color: var(--ds-color-text-placeholder);
  font-variant-numeric: tabular-nums;
}
.dc-level {
  font-size: var(--text-4xs);
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--ds-color-text-placeholder);
}
.dc-entry.is-warn .dc-level {
  color: var(--warn);
}
.dc-entry.is-error .dc-level {
  color: var(--down);
}

.dc-msg {
  min-width: 0;
}
.dc-msg-head {
  color: var(--ds-color-text-secondary);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.dc-entry.is-warn .dc-msg-head {
  color: var(--warn);
}
.dc-entry.is-error .dc-msg-head {
  color: var(--down);
}
.dc-msg-cont {
  padding-left: var(--ds-space-3);
  color: var(--ds-color-text-placeholder);
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.dc-entry.is-debug .dc-msg-head {
  color: var(--ds-color-text-placeholder);
}

@media (max-width: 720px) {
  .dc-search {
    flex: 1 1 100%;
    width: 100%;
  }
  .dc-tools-right {
    margin-left: 0;
  }
  .dc-entry {
    grid-template-columns: 56px minmax(0, 1fr);
  }
  .dc-level {
    grid-column: 2;
    grid-row: 1;
  }
  .dc-msg {
    grid-column: 2;
  }
}
</style>

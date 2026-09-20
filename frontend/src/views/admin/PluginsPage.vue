<script setup lang="ts">
/**
 * PluginsPage.vue · 内置插件清单工位
 * ---------------------------------------------------------------------------
 * 骨架（推倒重来）：
 *   旧 = 一行简介 + 徽章 + 一张表 + 一张策略提示卡（无状态汇总、无骨架、无空态）
 *   新 = 共享 PageHeader（策略徽章 + 刷新）
 *        → **插件状态带**（在册 / 健康 / 已停用 / 异常，发丝分隔单卡）
 *        → **插件清单行式清单**（图标 + 名称·类型·版本 / plugin_id / 权限声明标签 + 健康徽章·启用键）
 *        → 安装策略提示条（语义警示）
 *
 * ⚠️ 修复：`useResource` 的文档声明 `immediate` 默认 true，但实现只在传入真值时取数，
 *    本页此前**从不自动加载**。现显式传 `immediate: true`（与网关页同一处理）。
 *
 * 后端契约（逐字未改）：GET /api/v1/admin/plugins
 *   → { plugins:[{plugin_id,name,plugin_type,version,permissions[],enabled_key,health}],
 *       installation_policy, reason }
 */
import { computed } from 'vue';
import { useI18n } from '../../composables/useI18n';
const { t } = useI18n();
import PageHeader from '../../components/admin/PageHeader.vue';
import BaseEmpty from '../../components/base/BaseEmpty.vue';
import { useResource } from '../../composables/useResource';
import { Blocks, ShieldAlert, RefreshCw, Loader2, AlertTriangle, PackageCheck, PackageX } from 'lucide-vue-next';
import BaseLoadingAnnounce from '../../components/base/BaseLoadingAnnounce.vue';

const { data, loading, error, loaded, reload: load } = useResource<any>('/api/v1/admin/plugins', {
  immediate: true,
});

const plugins = computed<any[]>(() => data.value?.plugins || []);
const showSkeleton = computed(() => loading.value && !loaded.value);

const healthyCount = computed(() => plugins.value.filter((p) => p.health === 'healthy').length);
const disabledCount = computed(() => plugins.value.filter((p) => p.health === 'disabled').length);
const issueCount = computed(
  () => plugins.value.length - healthyCount.value - disabledCount.value,
);

/** 健康状态 → 徽章色调（healthy 绿 / disabled 中性 / 其余琥珀） */
function healthTone(h: string): string {
  if (h === 'healthy') return 'badge-up';
  if (h === 'disabled') return '';
  return 'badge-warn';
}
function healthLabel(h: string): string {
  if (h === 'healthy') return t('admin.plugins.healthNormal');
  if (h === 'disabled') return t('admin.plugins.healthDisabled');
  return h;
}
/** 类型文案：查表本地化，未登记类型原样回退（批 27）。 */
function typeLabel(v: string): string {
  return t(`admin.plugins.type.${v}`, v);
}
</script>

<template>
  <div class="pl">
    <PageHeader :title="t('nav.admin.plugins')" :description="t('admin.plugins.intro')">
      <template #actions>
        <span class="badge badge-accent mono">{{ t('admin.plugins.badge') }}</span>
        <button type="button" class="btn btn-ghost btn-sm" :disabled="loading" @click="load">
          <Loader2 v-if="loading && loaded" :size="14" class="animate-spin shrink-0" />
          <RefreshCw v-else :size="14" />
          <span>{{ t('admin.plugins.refresh') }}</span>
        </button>
      </template>
    </PageHeader>

    <!-- 拉取失败 -->
    <div v-if="error && !data" role="alert" class="state-block is-error">
      <span class="state-icon"><AlertTriangle :size="17" /></span>
      <p class="state-title">{{ t('common.loadFailed') }}</p>
      <p class="state-desc">{{ error }}</p>
      <button type="button" class="btn btn-ghost btn-sm mt-1" :disabled="loading" @click="load">
        <RefreshCw :size="14" />
        <span>{{ t('common.retry') }}</span>
      </button>
    </div>

    <template v-else>
      <!-- ══ 插件状态带 ══ -->
      <section class="card band">
        <template v-if="showSkeleton">
          <BaseLoadingAnnounce />
          <div v-for="i in 4" :key="i" class="fact">
            <div class="skeleton skeleton-text" style="width: 48%" />
            <div class="skeleton skeleton-text skeleton-value" style="width: 62%" />
            <div class="skeleton skeleton-text" style="width: 36%" />
          </div>
        </template>

        <template v-else>
          <div class="fact">
            <span class="fact-label"><Blocks :size="12" />{{ t('admin.plugins.bandPlugins') }}</span>
            <span class="fact-value num">{{ plugins.length }}</span>
            <span class="fact-foot mono">{{ t('admin.plugins.policyTitle') }}</span>
          </div>

          <div class="fact">
            <span class="fact-label"><PackageCheck :size="12" />{{ t('admin.plugins.bandHealthy') }}</span>
            <span class="fact-value num" :class="healthyCount === plugins.length && plugins.length ? 'is-up' : ''">
              {{ healthyCount }}
            </span>
            <span class="fact-foot">{{ t('admin.plugins.thHealth') }}</span>
          </div>

          <div class="fact">
            <span class="fact-label"><PackageX :size="12" />{{ t('admin.plugins.bandDisabled') }}</span>
            <span class="fact-value num">{{ disabledCount }}</span>
            <span class="fact-foot">{{ t('admin.plugins.thEnableSwitch') }}</span>
          </div>

          <div class="fact">
            <span class="fact-label"><ShieldAlert :size="12" />{{ t('admin.plugins.bandIssues') }}</span>
            <span class="fact-value num" :class="issueCount ? 'is-warn' : 'is-up'">{{ issueCount }}</span>
            <span class="fact-foot">{{ t('admin.plugins.healthOutside', undefined, { a: t('admin.plugins.healthNormal'), b: t('admin.plugins.healthDisabled') }) }}</span>
          </div>
        </template>
      </section>

      <!-- ══ 插件清单 ══ -->
      <section class="card">
        <header class="card-head">
          <div>
            <h2 class="card-title"><Blocks :size="14" />{{ t('admin.plugins.registryTitle') }}</h2>
            <p class="card-sub">{{ t('admin.plugins.registryDesc') }}</p>
          </div>
          <span v-if="!showSkeleton" class="badge mono">{{ plugins.length }}</span>
        </header>

        <div v-if="showSkeleton" class="pl-skel">
          <BaseLoadingAnnounce />
          <div v-for="i in 4" :key="i" class="skeleton skeleton-row" />
        </div>

        <BaseEmpty v-else-if="!plugins.length" :text="t('common.noRecords')" />

        <div v-else class="pl-rows">
          <article
            v-for="p in plugins"
            :key="p.plugin_id"
            class="pl-row"
            :class="{ 'is-off': p.health === 'disabled' }"
          >
            <span class="icon-box"><Blocks :size="14" /></span>

            <div class="pl-main">
              <div class="pl-title">
                <span class="pl-name">{{ p.name }}</span>
                <span class="badge" :title="p.plugin_type">{{ typeLabel(p.plugin_type) }}</span>
                <span class="badge mono">v{{ p.version }}</span>
              </div>
              <span class="pl-id mono">{{ p.plugin_id }}</span>
              <div class="pl-perms">
                <span class="label-caps">{{ t('admin.plugins.thPermissions') }}</span>
                <template v-if="(p.permissions || []).length">
                  <span v-for="perm in p.permissions" :key="perm" class="pl-tag">{{ perm }}</span>
                </template>
                <span v-else class="pl-none">--</span>
              </div>
            </div>

            <div class="pl-right">
              <span class="badge" :class="healthTone(p.health)">{{ healthLabel(p.health) }}</span>
              <span class="pl-key mono" :title="t('admin.plugins.thEnableSwitch')">
                {{ p.enabled_key || t('admin.plugins.defaultEnabled') }}
              </span>
            </div>
          </article>
        </div>
      </section>

      <!-- ══ 安装策略 ══ -->
      <aside class="card pl-policy">
        <span class="pl-policy-icon"><ShieldAlert :size="15" /></span>
        <div class="pl-policy-text">
          <h3 class="pl-policy-title">
            {{ t('admin.plugins.policyTitle') }}{{ t('common.punct.colon') }}
            {{ data?.installation_policy === 'builtin-only'
              ? t('admin.plugins.policyBuiltinOnly')
              : (data?.installation_policy || '--') }}
          </h3>
          <p class="panel-desc">{{ data?.reason }}</p>
        </div>
      </aside>
    </template>
  </div>
</template>

<style scoped>
.pl {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-4);
}

/* ══ 状态带 ══ */









/* ══ 清单 ══ */
.pl-skel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: var(--ds-space-4);
}
.pl-rows {
  display: flex;
  flex-direction: column;
}
.pl-row {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr) auto;
  align-items: start;
  gap: var(--ds-space-3);
  padding: var(--ds-space-3) var(--ds-space-4);
  border-bottom: 1px solid var(--ds-color-border-default);
  transition: background-color var(--dur-fast);
}
.pl-row:last-child {
  border-bottom: 0;
}
.pl-row:hover {
  background-color: var(--ds-color-bg-hover);
}
.pl-row.is-off {
  opacity: 0.6;
}
.pl-main {
  display: flex;
  flex-direction: column;
  gap:4px;
  min-width: 0;
}
.pl-title {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.pl-name {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ds-color-text-primary);
}
.pl-id {
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
}
.pl-perms {
  display: flex;
  align-items: center;
  gap:6px;
  flex-wrap: wrap;
  margin-top: 2px;
}
.pl-tag {
  /* 批 35：与 .ip-tag 同一处偏离 —— 22px/11px → 全站规范 20px/--text-3xs */
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 6px;
  border-radius: var(--r-xs);
  border: 1px solid var(--ds-color-border-default);
  background-color: var(--ds-color-bg-surface-1);
  font-family: var(--ds-font-mono);
  font-size: var(--text-3xs);
  color: var(--ds-color-text-secondary);
}
.pl-none {
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
}
.pl-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap:6px;
  flex-shrink: 0;
}
.pl-key {
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
  white-space: nowrap;
}

/* ══ 安装策略 ══ */
.pl-policy {
  display: flex;
  align-items: flex-start;
  gap: var(--ds-space-3);
  padding: var(--ds-space-4);
  border-left: 2px solid var(--warn);
}
.pl-policy-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border-radius: var(--r-ctl);
  background-color: var(--warn-bg);
  color: var(--warn);
  flex-shrink: 0;
}
.pl-policy-text {
  min-width: 0;
}
.pl-policy-title {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ds-color-text-primary);
}

@media (max-width: 720px) {
  .pl-row {
    grid-template-columns: 26px minmax(0, 1fr);
  }
  .pl-right {
    grid-column: 2;
    align-items: flex-start;
    flex-direction: row;
    flex-wrap: wrap;
  }
}
</style>

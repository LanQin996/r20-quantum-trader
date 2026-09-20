<script setup lang="ts">
/**
 * VenueCredentialCard · 三所凭证卡外壳（**仅供 SecurityPage 使用**）
 * ---------------------------------------------------------------------------
 * 批 10 重构：内联 `TONES` 样式三元 → 语义徽章类；内嵌 `surface-1` 方块 →
 * 面板内分区（发丝分隔）；页脚动作位改用共享按钮语汇。
 * 对外接口（props / slots）保持兼容。
 */
import { computed } from 'vue'
import { useI18n } from '../../../composables/useI18n'

const { t } = useI18n()

const props = withDefaults(defineProps<{
  name: string
  apiLabel: string
  statusText: string
  tone?: 'up' | 'warn' | 'down'
  envText: string
  envLabel?: string
}>(), { tone: 'warn', envLabel: '' })

/**
 * 档位标签（批 41）。**不能**写成 props 默认值里的 `t(...)`：
 * SFC 编译器会把默认值提到独立作用域（`checkInvalidScopeReference`），
 * 引用 setup 里的 `t` 会在 `npm run build` 阶段直接失败 ——
 * 而这一步 `vue-tsc` **查不出来**（它只看类型）。故改为渲染期回落。
 */
const envLabelText = computed(() => props.envLabel || t('dash.venueAccounts.envLabel'))

const toneClass = computed(
  () => ({ up: 'badge-up', warn: 'badge-warn', down: 'badge-down' })[props.tone] ?? 'badge-warn',
)
</script>

<template>
  <div class="vc">
    <!-- 卡头：所名 + 接口档 + 状态徽章 -->
    <header class="vc-head">
      <div class="vc-id">
        <!-- 批 45：h4 → h3。本卡是「设置分区」内的一张卡（分区标题是 h2），
             h2→h4 是跳级；`.vc-name` 类控制外观，改层级不影响观感。 -->
        <h3 class="vc-name">{{ name }}</h3>
        <span class="vc-api">{{ apiLabel }}</span>
      </div>
      <span class="badge" :class="toneClass">{{ statusText }}</span>
    </header>

    <!-- 资金档位 -->
    <div class="kv-row vc-env">
      <span class="vc-env-label">{{ envLabelText }}</span>
      <span class="vc-env-value mono">{{ envText }}</span>
    </div>

    <div class="vc-body">
      <slot name="env" />
      <slot />
      <slot name="extra" />
    </div>

    <!-- 页脚动作 -->
    <footer class="vc-foot">
      <slot name="footer-left" />
      <div class="vc-foot-actions">
        <slot name="probe" />
        <slot name="save" />
      </div>
    </footer>
  </div>
</template>

<style scoped>
.vc {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--ds-color-border-default);
  border-radius: var(--r-ctl);
  background-color: var(--ds-color-bg-surface-inset);
  overflow: hidden;
}
.vc-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--ds-space-3);
  padding: 10px var(--ds-space-3);
  border-bottom: 1px solid var(--ds-color-border-default);
}
.vc-id {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.vc-name {
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--ds-color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.vc-api {
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
}
.vc-head .badge {
  flex-shrink: 0;
}
/* 批 90：凭证卡内更紧凑（8/12，行高 36px）—— 形态 delta，其余交给 .kv-row */
.vc-env {
  padding: 8px var(--ds-space-3);
}
.vc-env-label {
  font-size: var(--text-4xs);
  color: var(--ds-color-text-placeholder);
}
.vc-env-value {
  font-size: var(--text-3xs);
  font-weight: 600;
  color: var(--ds-color-text-primary);
}
.vc-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1;
  padding: var(--ds-space-3);
}
.vc-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--ds-space-2);
  flex-wrap: wrap;
  padding: 10px var(--ds-space-3);
  border-top: 1px solid var(--ds-color-border-default);
  background-color: var(--ds-color-bg-surface-1);
}
.vc-foot-actions {
  display: flex;
  align-items: center;
  gap: var(--ds-space-2);
  margin-left: auto;
}
</style>

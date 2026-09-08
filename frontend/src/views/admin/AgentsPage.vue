<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { Package, Cpu, KeyRound, RefreshCw, Play } from 'lucide-vue-next'

const { api } = useApi()
const data = ref<any>(null)
const loading = ref(true)
const errText = ref('')
const runningJob = ref('')
const runMsg = ref<{ text: string; type: 'ok' | 'err' } | null>(null)

// 运行单元 → 后端手动任务（POST /api/v1/admin/gateway/jobs/{job}/run）
// backup_agent 的 nightly_backup 不在白名单内，不提供手动入口
const runnableJobs: Record<string, { job: string; label: string; dangerous: boolean }> = {
  trading_brain: { job: 'trader', label: 'AI量化主脑决策', dangerous: true },
  self_improvement: { job: 'self_improvement', label: '自进化复盘', dangerous: false },
  factor_engine: { job: 'factor_library', label: '多因子矩阵计算', dangerous: false },
  news_engine: { job: 'news', label: '全网情绪抓取', dangerous: false },
}

async function load() {
  loading.value = true
  try {
    data.value = await api('/api/v1/admin/agents')
    errText.value = ''
  } catch (e: any) {
    errText.value = e.message
  } finally {
    loading.value = false
  }
}

async function runNow(agent: any) {
  const cfg = runnableJobs[agent.id]
  if (!cfg || runningJob.value) return
  if (cfg.dangerous && !confirm(`立即执行「${cfg.label}」将触发一次真实的全市场决策与持仓裁决（可能产生真实交易）。确认继续？`)) return
  runningJob.value = cfg.job
  runMsg.value = null
  try {
    const res = await api(`/api/v1/admin/gateway/jobs/${cfg.job}/run`, {
      method: 'POST',
      body: JSON.stringify({ confirmation: 'RUN JOB' }),
    })
    runMsg.value = { text: `✅ ${res.detail || `${cfg.label}已顺利完成`}`, type: 'ok' }
  } catch (e: any) {
    runMsg.value = { text: `❌ ${cfg.label}执行失败: ${e.message}`, type: 'err' }
  } finally {
    runningJob.value = ''
    await load()
  }
}

function statusColor(s: string) {
  if (['success', 'running', 'online', 'idle'].includes(s)) return 'text-emerald-400'
  if (['failed', 'error', 'offline'].includes(s)) return 'text-rose-400'
  return 'text-amber-400'
}

onMounted(load)
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between">
      <p class="text-xs text-[#707E94] font-mono">受管 Worker 存活、密文库状态与大模型调用遥测。</p>
      <span class="text-[10px] font-mono text-blue-400 bg-blue-500/10 px-2 py-1 rounded border border-blue-500/20">策略配置 · 3/3</span>
    </div>

    <div v-if="errText" class="p-3 rounded-lg text-xs font-mono bg-rose-500/10 border border-rose-500/20 text-rose-400">{{ errText }}</div>
    <div v-if="runMsg" class="p-3 rounded-lg text-xs font-mono border" :class="runMsg.type === 'ok' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-rose-500/10 border-rose-500/20 text-rose-400'">{{ runMsg.text }}</div>
    <div v-if="loading" class="py-12 text-center text-xs font-mono text-[#707E94]"><RefreshCw class="w-5 h-5 animate-spin inline mr-1.5 text-blue-400" />正在加载运行单元...</div>

    <template v-else-if="data">
      <!-- Agents -->
      <div class="rounded-xl border overflow-hidden shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
        <div class="px-4 py-3 border-b flex items-center justify-between" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle);">
          <div class="flex items-center space-x-2">
            <Package class="w-4 h-4 text-blue-400" />
            <h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">受管 Worker 单元清单</h2>
          </div>
          <button @click="load" class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-[11px] font-mono cursor-pointer transition-all shadow-xs" style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-main);">
            <RefreshCw class="w-3 h-3" />
            <span>刷新</span>
          </button>
        </div>
        <div class="table-scroll-container">
          <table class="w-full text-left text-xs font-mono whitespace-nowrap">
            <thead>
              <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle); color: var(--text-muted);">
                <th class="py-2.5 px-4">Worker 单元</th>
                <th class="py-2.5 px-3">核心职责</th>
                <th class="py-2.5 px-3">健康状态</th>
                <th class="py-2.5 px-3">最近执行时间</th>
                <th class="py-2.5 px-3">运行结果</th>
                <th class="py-2.5 px-3">产物时效</th>
                <th class="py-2.5 px-4 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="a in data.agents" :key="a.id" class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors" style="border-color: var(--border-subtle);">
                <td class="py-2.5 px-4 font-bold" style="color: var(--text-main);">{{ a.name }}</td>
                <td class="py-2.5 px-3" style="color: var(--text-muted);">{{ a.role }}</td>
                <td class="py-2.5 px-3 font-bold" :class="statusColor(a.health)">{{ a.health }}</td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-faint);">{{ a.last_run_at || '尚未调度' }}</td>
                <td class="py-2.5 px-3 font-bold" :class="statusColor(a.last_run_status)">{{ a.last_run_status }}<div v-if="a.last_run_status === 'failed' && a.last_run_detail" class="font-normal max-w-sm truncate" style="color: var(--text-faint);" :title="a.last_run_detail">{{ a.last_run_detail }}</div></td>
                <td class="py-2.5 px-3" style="color: var(--text-muted);">{{ a.output_age_seconds != null ? Math.round(a.output_age_seconds / 60) + ' 分钟前' : (a.output ? '冷启动' : '无产物') }}</td>
                <td class="py-2.5 px-4 text-right">
                  <button v-if="runnableJobs[a.id]" @click="runNow(a)" :disabled="!!runningJob" class="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-[11px] font-mono cursor-pointer transition-all shadow-xs disabled:opacity-50 disabled:cursor-not-allowed" style="background-color: var(--bg-card); border-color: var(--border-medium); color: var(--text-main);">
                    <RefreshCw v-if="runningJob === runnableJobs[a.id].job" class="w-3 h-3 animate-spin" />
                    <Play v-else class="w-3 h-3 text-emerald-400" />
                    <span>{{ runningJob === runnableJobs[a.id].job ? '执行中' : '立即执行' }}</span>
                  </button>
                  <span v-else class="text-[11px]" style="color: var(--text-faint);">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <!-- Model Telemetry -->
        <div class="rounded-xl border overflow-hidden shadow-xs p-4" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2 mb-3"><Cpu class="w-4 h-4 text-purple-400" /><h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">模型调用遥测 (最近 50 次)</h2></div>
          <div class="text-[11px] font-mono mb-3 p-2.5 rounded-lg border leading-relaxed" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle); color: var(--text-muted);">{{ data.prompt_policy }}</div>
          <div class="grid grid-cols-3 gap-2.5 mb-3 text-center">
            <div class="rounded-lg border p-2" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"><div class="text-[10px] font-mono" style="color: var(--text-faint);">总调用量</div><div class="text-sm font-bold font-mono num-tabular mt-0.5" style="color: var(--text-main);">{{ data.model_stats?.total_calls ?? '--' }}</div></div>
            <div class="rounded-lg border p-2" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"><div class="text-[10px] font-mono" style="color: var(--text-faint);">调用成功率</div><div class="text-sm font-bold num-tabular mt-0.5" :class="(data.model_stats?.total_calls ?? 0) > 0 && (data.model_stats?.successful_calls ?? 0) < (data.model_stats?.total_calls ?? 0) ? 'text-amber-500' : 'text-emerald-500'">{{ (data.model_stats?.total_calls ?? 0) > 0 ? Math.round(100 * (data.model_stats?.successful_calls ?? 0) / data.model_stats.total_calls) + '%' : '--' }}</div></div>
            <div class="rounded-lg border p-2" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);"><div class="text-[10px] font-mono" style="color: var(--text-faint);">平均时延</div><div class="text-sm font-bold font-mono num-tabular mt-0.5" style="color: var(--text-main);">{{ data.model_stats?.avg_duration_ms ? Math.round(data.model_stats.avg_duration_ms) + 'ms' : '--' }}</div></div>
          </div>
          <div class="table-scroll-container max-h-60 overflow-y-auto rounded-lg border" style="border-color: var(--border-subtle);">
            <table class="w-full text-left text-xs font-mono whitespace-nowrap">
              <thead class="sticky top-0 z-10">
                <tr class="border-b text-[11px] uppercase tracking-wider font-bold" style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle); color: var(--text-muted);">
                  <th class="py-2 px-3">调用方</th>
                  <th class="py-2 px-2">模型</th>
                  <th class="py-2 px-2">状态</th>
                  <th class="py-2 px-2">错误</th>
                  <th class="py-2 px-2">Tokens</th>
                  <th class="py-2 px-3 text-right">耗时</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="c in (data.model_calls || []).slice(0, 30)" :key="c.id" class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors" style="border-color: var(--border-subtle);">
                  <td class="py-1.5 px-3" style="color: var(--text-muted);">{{ c.caller || '--' }}</td>
                  <td class="py-1.5 px-2 num-tabular" style="color: var(--text-faint);">{{ c.model || '--' }}</td>
                  <td class="py-1.5 px-2 font-bold" :class="statusColor(c.status)">{{ c.status }}</td>
                  <td class="py-1.5 px-2 max-w-64 truncate" :class="c.status === 'success' ? '' : 'text-rose-400'" :title="c.error_detail || c.error_type || ''">{{ c.status === 'success' ? '--' : (c.error_detail || c.error_type || '--') }}</td>
                  <td class="py-1.5 px-2 num-tabular" style="color: var(--text-muted);">{{ c.total_tokens ?? '--' }}</td>
                  <td class="py-1.5 px-3 text-right num-tabular" style="color: var(--text-muted);">{{ c.duration_ms ? Math.round(c.duration_ms) + 'ms' : '--' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Secret Store -->
        <div class="rounded-xl border p-4 shadow-xs transition-colors" style="background-color: var(--bg-card); border-color: var(--border-subtle);">
          <div class="flex items-center space-x-2 mb-3"><KeyRound class="w-4 h-4 text-amber-500" /><h2 class="text-xs font-black font-mono uppercase tracking-wide" style="color: var(--text-main);">本机加密密文库</h2></div>
          <div class="space-y-1.5 text-xs font-mono">
            <div class="flex items-center justify-between border rounded-lg px-3 py-2" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
              <span style="color: var(--text-muted);">加密库状态</span>
              <span :class="data.secret_store?.initialized ? 'text-emerald-500 font-bold' : 'text-rose-500 font-bold'">{{ data.secret_store?.initialized ? '已初始化 ✓' : '未初始化' }} · {{ data.secret_store?.count ?? 0 }} 项密文 · 文件权限 {{ data.secret_store?.store_mode || '--' }}</span>
            </div>
            <div class="flex items-center justify-between border rounded-lg px-3 py-2" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
              <span style="color: var(--text-muted);">读取优先级</span><span style="color: var(--text-main);">{{ data.secret_store?.source_priority || 'encrypted-store-over-env' }}</span>
            </div>
            <div v-for="k in (data.secret_store?.keys || [])" :key="k" class="flex items-center justify-between border rounded-lg px-3 py-2" style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle);">
              <span style="color: var(--text-muted);">{{ k }}</span>
              <span class="text-emerald-500 font-bold">已配置 ✓</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

/** 插件清单页文案 */
export const zhAdminPlugins = {
  intro: "插件清单：内置插件健康状态；实盘控制面仅允许随仓库审计过的内置插件。",
  badge: "策略配置 · 2/3",
  loading: "正在加载插件状态...",
  refresh: "刷新",
  thPlugin: "插件",
  thType: "类型",
  thVersion: "版本",
  thPermissions: "权限声明",
  thEnableSwitch: "启用开关",
  thHealth: "健康状态",
  defaultEnabled: "默认启用",
  healthNormal: "正常",
  healthDisabled: "已禁用",
  healthOutside: '{a} / {b} 之外',
  installPolicy: "安装策略：",
  policyBuiltinOnly: "仅内置插件",

  // ── 推倒重来新增（批 7）──
  bandPlugins: '在册插件',
  bandHealthy: '健康',
  bandDisabled: '已停用',
  bandIssues: '异常',
  registryTitle: '插件清单',
  registryDesc: '内置插件及其健康状态；实盘控制面仅允许随仓库审计过的内置插件',
  policyTitle: '安装策略',

  // 批 27：类型列的表头是中文「类型」，值却是后端枚举（channel / scheduler / …）。
  // 查表本地化，未登记的类型原样回退（新插件类型不会因此变成空白）。
  type: {
    channel: '渠道',
    scheduler: '调度器',
    runtime: '运行时',
    telemetry: '遥测',
    security: '安全',
    exchange: '交易所',
    notify: '通知',
    storage: '存储',
    strategy: '策略',
  },
};

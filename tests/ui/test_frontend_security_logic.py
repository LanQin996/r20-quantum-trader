"""`frontend/src/views/admin/securityLogic.ts`（阶段 4·B3 第三十四刀）回归。

## 抽了什么

`SecurityPage.vue` 的 script setup 里有六段**纯派生**（给定输入与 `t` 就有确定输出），
与 Vue 响应式无关，原件与组件状态交织在 432 行里：

| 函数 | 决定什么 |
|---|---|
| `deriveOkxLinked` | OKX 是否已连通（**两个条件都要**：READY 且 mode_configured） |
| `deriveMxHealthChips` | 多所健康度 chip（缺数据 → **`null`** 而不是 `[]`） |
| `deriveGateExecDirty` | 执行闸开关是否有未保存改动 |
| `venueStatus` | 三所状态徽章（**未知一律 warn**） |
| `envTextOf` / `okxEnvText` | 资金档位文字（**未知不乐观**） |
| `envBadge` | 环境徽章（空值兜底 `DEMO`） |

## 为什么这些值得单测

三条状态派生直接决定运维看到的是**绿/黄/红**。它们若朝**乐观**方向错
（把"未知"当"已就绪"），会让人以为交易所已连通 —— 这类"UI 说谎"是本仓红线。
`SecurityPage` 此前**没有任何单测**。

## ⚠️ 为什么用 node 当 oracle，而不是写死期望值

写死期望值 = 我把"我以为的语义"抄一遍，实现改了期望值不动 → 测试仍绿。
本测试改用 **node 直接执行那个 `.ts`**（`--experimental-strip-types`），
拿它的输出与 **Python 侧的独立规则**比对：

- 源码里的实现被改动 → node 输出变 → 与规则/表格不一致 → **翻红**。

另外把从源码**解析出来的**关键常量（`'warn'`、`'demo'`、`'READY'` 等）
与表格互证，于是"表格"与"源码"也不能各自静态地为真。

## ⚠️ 离线闸下必须跳过（`tests/offline_suite.py`）

该闸的 `subprocess.Popen` 白名单**不含 node**，任何 node 子进程在离线套件里都会
抛 `RuntimeError`。故本测试：

- 拿不到 node（或探测失败）→ **`skipTest` 并说明原因**，不假装验证过；
- 离线套件用 `OFFLINE_SUITE=1` 标记自身 → 直接跳过，避免把闸打红。

> `skip` 不是"通过"。本文件的规则断言（不依赖 node）会**始终**执行，
> 所以即使 node 缺席，红线规则仍被守住。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "frontend" / "src" / "views" / "admin" / "securityLogic.ts"
PAGE = ROOT / "frontend" / "src" / "views" / "admin" / "SecurityPage.vue"

NODE = shutil.which("node")

#: 用 node 跑一次导出函数，返回 JSON。缺失 node / 失败 → (None, 原因)。
_PROBE_JS = """
const t = (p) => p;
const out = {};
try {
  out.okxLinked = [
    m.deriveOkxLinked({status:'READY', mode_configured:true}),
    m.deriveOkxLinked({status:'READY', mode_configured:false}),
    m.deriveOkxLinked({status:'DEGRADED', mode_configured:true}),
    m.deriveOkxLinked(null),
    m.deriveOkxLinked(undefined),
  ];
  out.chips = [
    m.deriveMxHealthChips(null),
    m.deriveMxHealthChips({}),
    m.deriveMxHealthChips({health:{}}),
    m.deriveMxHealthChips({health:{venues:{}}}),
    m.deriveMxHealthChips({health:{venues:{binance:{ok:[1,2],failed:{a:1,b:2},avg_ms:30,testnet:true}}}}),
    m.deriveMxHealthChips({health:{venues:{gate:{}}}}),
  ];
  out.dirty = [
    m.deriveGateExecDirty(false, {venues:{gate:{execution_open:false}}}),
    m.deriveGateExecDirty(true,  {venues:{gate:{execution_open:false}}}),
    m.deriveGateExecDirty(false, {venues:{gate:{execution_open:true}}}),
    m.deriveGateExecDirty(false, {}),
    m.deriveGateExecDirty(false, null),
  ];
  out.venue = [
    m.venueStatus('gate', null, t),
    m.venueStatus('gate', {}, t),
    m.venueStatus('gate', {venues:{}}, t),
    m.venueStatus('gate', {venues:{binance:{has_api_key:true}}}, t),
    m.venueStatus('gate', {venues:{gate:{has_api_key:false}}}, t),
    m.venueStatus('gate', {venues:{gate:{has_api_key:true, execution_open:true}}}, t),
    m.venueStatus('gate', {venues:{gate:{has_api_key:true, execution_open:false}}}, t),
  ];
  out.envtxt = [
    m.envTextOf('gate', 'SBX', null, {binance:false, gate:false}, t),
    m.envTextOf('gate', 'SBX', {}, {binance:false, gate:false}, t),
    m.envTextOf('gate', 'SBX', {venues:{gate:{}}}, {binance:false, gate:true}, t),
    m.envTextOf('gate', 'SBX', {venues:{gate:{}}}, {binance:false, gate:false}, t),
  ];
  out.okxenv = [
    m.okxEnvText('live', t), m.okxEnvText('demo', t),
    m.okxEnvText('', t), m.okxEnvText(null, t), m.okxEnvText('LIVE', t),
  ];
  out.badge = [
    m.envBadge('live'), m.envBadge('demo'), m.envBadge(''),
    m.envBadge(null), m.envBadge(undefined), m.envBadge('Live'),
  ];
  process.stdout.write(JSON.stringify(out));
} catch (e) {
  process.stderr.write('PROBE_ERR ' + e.message);
  process.exit(3);
}
"""


def _run_probe():
    """返回 (结果 dict | None, 失败原因 | None)。"""
    if not NODE:
        return None, "找不到 node"
    src = MODULE.read_text(encoding="utf-8")
    script = (
        "import('" + MODULE.as_uri() + "').then(m => {" + _PROBE_JS + "})."
        "catch(e => { process.stderr.write('IMPORT_ERR ' + e.message); process.exit(4); });"
    )
    try:
        proc = subprocess.run(
            [NODE, "--experimental-strip-types", "--input-type=module", "-e", script],
            capture_output=True, text=True, timeout=90, cwd=str(ROOT))
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"node 执行失败: {exc}"
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        return None, f"node 退出码 {proc.returncode}: {err[-1] if err else '无 stderr'}"
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"node 输出不是 JSON: {exc}"


_PROBE_CACHE: dict = {}


def _probe():
    if not _PROBE_CACHE:
        data, why = _run_probe()
        _PROBE_CACHE["data"] = data
        _PROBE_CACHE["why"] = why
    return _PROBE_CACHE["data"], _PROBE_CACHE["why"]


def _fn_body(src: str, name: str) -> str:
    """取出 `export function <name>(...) { ... }` 的函数体。

    ⚠️ 这里必须**带捕获组**（把 `\n\}` 再包一层括号）。

    我第一版写了 `r"export function X\(.*?\n\}"` —— **没有括号** —— 却调用
    `group(1)`，六处全部 `IndexError: no such group`。更值得记的是：
    我随后把这条**写进了本函数的 docstring** 作为教训，**却仍然忘了加括号**，
    于是同一条错误在 helper 里原地复现了一次（7 个 ERROR）。

    > 教训：把教训写进注释**不等于**把教训写进代码。
    > 注释描述的是"我打算怎么做"，只有代码本身才强制"我真的这么做了"。
    """
    m = re.search(r"export function " + re.escape(name) + r"\((.*?)\n\}", src, re.S)
    if not m:
        raise AssertionError(f"源码里找不到 export function {name}(")
    return m.group(1)


def _guard_offline(case: unittest.TestCase) -> None:
    """离线套件下跳过（**在 spawn 之前**）。

    判据来自 `tests/offline_suite.py::main` 显式设置的环境变量 —— 它在装 audit hook
    **之前**就设好，所以这里一定来得及。

    ⚠️ 我第一版假定"套件会设某个环境变量"，写了个想象出来的名字（`OFFLINE_SUITE`），
    于是**永远不触发**；随后又想靠 `sys.addaudithook` 是否存在来近似判断 ——
    那是 **CPython 内部实现**（3.8 起恒为真），判不出任何东西。
    最终做法是**给套件加一行显式标记**，而不是猜它有什么。

    宁可**多跳**也不要在离线套件里真的拉起 node：跳过只是"未验证"，
    真的拉起来却会让整个套件变脏（实测：`NETWORK_ATTEMPTS` 里多出
    `external child process: node`，套件 exit 1）。
    """
    if os.environ.get("OFFLINE_SUITE_RUNNING"):
        # ⚠️ 必须 `raise`（理由同 prompt_studio 那份）：`setUpClass` 里
        # `cls.skipTest(reason)` 会把 reason 绑到 self → TypeError。
        raise unittest.SkipTest("离线套件禁用外部子进程（node 不在白名单）—— 见 tests/offline_suite.py")


def _require_node(case: unittest.TestCase):
    _guard_offline(case)
    data, why = _probe()
    if data is None:
        raise unittest.SkipTest(f"拿不到 node oracle：{why}（规则断言仍会执行，但本项未验证）")
    return data


# ------------------------------------------------------------- 源码事实


class SourceFactsTest(unittest.TestCase):
    """与源码互证的常量：让"表格"和"源码"不能各自静态地为真。"""

    @classmethod
    def setUpClass(cls):
        cls.src = MODULE.read_text(encoding="utf-8")

    def test_module_exports_all_six(self):
        for name in ("deriveOkxLinked", "deriveMxHealthChips", "deriveGateExecDirty",
                     "venueStatus", "envTextOf", "okxEnvText", "envBadge"):
            self.assertIn(f"export function {name}(", self.src, name)

    def test_okx_linked_requires_both_conditions(self):
        body = _fn_body(self.src, "deriveOkxLinked")
        self.assertIn("'READY'", body, "必须判 status === 'READY'")
        self.assertIn("mode_configured", body, "必须同时判 mode_configured")
        self.assertIn("&&", body, "两个条件必须是与关系")

    def test_unknown_tone_is_warn_in_source(self):
        """`venueStatus` 的未知分支必须是 warn —— 这不是表格说了算，是源码说了算。"""
        body = _fn_body(self.src, "venueStatus")
        self.assertIn("statusUnknown", body)
        self.assertIn("tone: 'warn'", body)
        self.assertNotIn("tone: 'up'", body.split("if (!v)")[1].split("return")[1][:40],
                         "未知分支不得给 up")

    def test_env_badge_defaults_to_demo_not_empty(self):
        body = _fn_body(self.src, "envBadge")
        self.assertIn("'demo'", body)
        self.assertIn("toUpperCase", body)

    def test_chips_total_adds_failed_key_count(self):
        body = _fn_body(self.src, "deriveMxHealthChips")
        self.assertIn("Object.keys(v.failed || {}).length", body, "total 必须加 failed 的键数")
        self.assertIn("if (!venues) return null", body, "缺 venues 必须返回 null")

    def test_env_text_of_guards_missing_venue(self):
        body = _fn_body(self.src, "envTextOf")
        self.assertIn("envUnknown", body, "缺数据必须是 unknown，不是 LIVE")


class WiringTest(unittest.TestCase):
    """组件必须**真的**改用抽出的模块（否则抽离只是搬了个备份）。"""

    @classmethod
    def setUpClass(cls):
        cls.page = PAGE.read_text(encoding="utf-8")

    def test_page_imports_the_module(self):
        self.assertIn("from './securityLogic'", self.page)

    def test_page_uses_derived_helpers(self):
        for call in ("deriveOkxLinked(", "deriveMxHealthChips(", "deriveGateExecDirty(",
                     "venueStatus('binance'", "venueStatus('gate'",
                     "envTextOf('binance'", "envTextOf('gate'", "okxEnvTextOf("):
            self.assertIn(call, self.page, f"组件未使用 {call}")

    def test_page_no_longer_inlines_the_logic(self):
        """旧的本地定义必须消失 —— 否则会出现两份语义不同的实现。"""
        for gone in ("function venueStatus(", "function envTextOf(", "function envBadge(",
                     "const mxHealthChips = computed(() => {\n  const venues"):
            self.assertNotIn(gone, self.page, f"组件仍保留本地定义 {gone!r}")

    def test_module_has_no_vue_or_network_dependency(self):
        src = MODULE.read_text(encoding="utf-8")
        self.assertNotIn("from 'vue'", src, "纯逻辑模块不得依赖 vue")
        self.assertNotIn("useApi", src)
        self.assertNotIn("fetch(", src)


# ------------------------------------------------ 规则断言（不依赖 node）


class InvariantRulesTest(unittest.TestCase):
    """"未知不乐观"等红线规则 —— **始终执行**，即使 node 缺席。

    这些是**手工写死的规则**（不是从实现生成），故它们与实现是独立的两个来源。
    """

    def test_source_forbids_optimistic_unknown(self):
        src = MODULE.read_text(encoding="utf-8")
        # venueStatus：!v → warn
        seg = _fn_body(src, "venueStatus")
        self.assertRegex(seg, r"if \(!v\)\s*return \{[^}]*tone: 'warn'",
                         "缺数据必须直接返回 warn")
        # envTextOf：!mx?.venues?.[venue] → envUnknown
        seg2 = _fn_body(src, "envTextOf")
        self.assertRegex(seg2, r"if \(!mx\?\.venues\?\.\[venue\]\)\s*return t\('admin\.security\.envUnknown'\)")

    def test_live_keyword_requires_explicit_live(self):
        """`okxEnvText` 只认小写 `live` / `demo`；`LIVE` 必须落到 unknown。

        （原实现是 `env === 'live' ? ... : env === 'demo' ? ... : unknown`，
        大小写敏感 —— 大写的 `LIVE` 会显示"未知"而不是"实盘"。）
        """
        src = MODULE.read_text(encoding="utf-8")
        seg = _fn_body(src, "okxEnvText")
        self.assertIn("env === 'live'", seg)
        self.assertIn("env === 'demo'", seg)
        self.assertNotIn(".toLowerCase()", seg, "原实现大小写敏感，不得悄悄加归一化")


# --------------------------------------------------- node oracle 对表


class NodeOracleTest(unittest.TestCase):
    """用 node 执行真实 `.ts`，与**手写规则**比对。

    ⚠️ `setUpClass` 里**必须**先决定跳不跳，再决定要不要 spawn ——
    我第一版把 `_probe()`（会 spawn node）直接放在 `setUpClass`，而跳过判定在
    `setUp` 里，于是**跳过判定根本来不及生效**：
    离线套件的 audit hook 在 node 被拉起时就记了一笔
    `external child process: node`，并让 `subprocess.run` 抛 RuntimeError →
    `setUpClass` 报 ERROR。**"会写 skip 的测试"仍然真的 spawn 了子进程。**
    """

    @classmethod
    def setUpClass(cls):
        _guard_offline(cls)          # 先判：离线则这里直接 SkipTest，不 spawn
        cls.data, cls.why = _probe()
        if cls.data is None:
            raise unittest.SkipTest(
                f"拿不到 node oracle：{cls.why}（规则断言仍会执行，但本项未验证）")

    def test_okx_linked(self):
        got = self.data["okxLinked"]
        self.assertEqual(got, [True, False, False, False, False],
                         "两个条件缺一不可；null/undefined 必须 False")

    def test_chips_null_vs_empty(self):
        got = self.data["chips"]
        self.assertIsNone(got[0], "null 输入 → None")
        self.assertIsNone(got[1], "{} → None（没有 health）")
        self.assertIsNone(got[2], "health={} → None（没有 venues）")
        self.assertEqual(got[3], [], "venues={} → **空数组**（不是 None）")

    def test_chips_total_counts_failed_keys(self):
        row = self.data["chips"][4][0]
        self.assertEqual(row["name"], "binance")
        self.assertEqual(row["ok"], 2)
        self.assertEqual(row["total"], 2 + 2, "total = ok 条数 + failed 的键数")
        self.assertEqual(row["avg_ms"], 30)
        self.assertIs(row["testnet"], True)

    def test_chips_defaults(self):
        row = self.data["chips"][5][0]
        self.assertEqual(row["ok"], 0)
        self.assertEqual(row["total"], 0)
        self.assertEqual(row["avg_ms"], 0, "avg_ms 缺省 0（不是 None）")
        self.assertIs(row["testnet"], False)

    def test_gate_exec_dirty(self):
        got = self.data["dirty"]
        self.assertEqual(got, [False, True, True, False, False],
                         "undefined→!!→false，故同值不脏；null 输入不脏")

    def test_venue_status(self):
        got = self.data["venue"]
        names = [g["text"] for g in got]
        tones = [g["tone"] for g in got]
        self.assertEqual(names[0], "admin.security.statusUnknown")
        self.assertEqual(names[1], "admin.security.statusUnknown")
        self.assertEqual(names[2], "admin.security.statusUnknown")
        self.assertEqual(names[3], "admin.security.statusUnknown", "缺 gate → unknown")
        self.assertEqual(names[4], "admin.security.publicMarket", "无密钥 → 公共行情")
        self.assertEqual(names[5], "admin.security.keyExecOpen")
        self.assertEqual(names[6], "admin.security.keyExecClosed")
        self.assertEqual(tones, ["warn", "warn", "warn", "warn", "warn", "up", "up"],
                         "未知一律 warn；有密钥一律 up（含执行闸关闭）")

    def test_env_text_of(self):
        got = self.data["envtxt"]
        self.assertEqual(got, ["admin.security.envUnknown",
                               "admin.security.envUnknown",
                               "SBX",
                               "admin.security.envLive"])

    def test_okx_env_text_case_sensitive(self):
        got = self.data["okxenv"]
        self.assertEqual(got, ["admin.security.envLive",
                               "admin.security.envDemoOkx",
                               "admin.security.envUnknown",
                               "admin.security.envUnknown",
                               "admin.security.envUnknown"],
                         "大写 LIVE 落 unknown —— 原实现大小写敏感")

    def test_env_badge(self):
        got = self.data["badge"]
        self.assertEqual(got, ["LIVE", "DEMO", "DEMO", "DEMO", "DEMO", "LIVE"])

    def test_all_probe_keys_covered(self):
        """防"探针扩了字段但测试没跟上"。"""
        expected = {"okxLinked", "chips", "dirty", "venue", "envtxt", "okxenv", "badge"}
        self.assertEqual(set(self.data), expected)


if __name__ == "__main__":
    unittest.main()

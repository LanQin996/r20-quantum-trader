r"""entry_execution 抽取对拍门（结构优化阶段 4·B3 第九十刀）。

`execute_portfolio`（551 行巨函数）相位 4 的入场 `for` 循环（300 行）**纯搬家**
到 `scripts/trader/entry_execution.py::execute_entry_scan`。

## 为什么这门必须最严（本仓独有约束）

**没有任何测试直接驱动 `execute_portfolio`**（只由 `single_trader_cycle` 调用）
⇒ 常规"行为例"在这条路径上不存在。因此本门把静态判据做到最严：

1. **AST 逐字**：提取后的 `for` 语句与基线**完全同一棵 AST**（零归一规则）；
2. **调用点完整性**：门面调用必须把 41 个参数**逐个同名、恰好一次**传入
   （漏一个 ⇒ 生产里 NameError，而测试套件不会报——这是本刀最大风险）；
3. **无未声明自由名**：`execute_entry_scan` 体内每个自由名，要么是声明的参数，
   要么能在新模块命名空间解析（防"以为注入漏了但恰好同名"这类假安全）；
4. 空宇宙冒烟：`all_factors=[]` 可调用且无副作用（证明签名/导入接线成立）；
5. ±自检：改循环体 → 判据 1 红；调用点少一个参数 → 判据 2 红。
"""
from __future__ import annotations

import ast
import builtins
import subprocess
import sys
import unittest
from pathlib import Path
from tests.extraction.accepted_baselines import accepted_function

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PRE = "542dc8d"  # 本刀动工前最后提交（第八十九刀收口）
MOD = "scripts/trader/entry_execution.py"
FN = "execute_entry_scan"


def _base_loop() -> ast.For:
    r = subprocess.run(["git", "show", f"{PRE}:scripts/ai_factor_trader.py"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, f"基线取不到：{r.stderr[:200]}"
    t = ast.parse(r.stdout)
    f = next(n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == "execute_portfolio")
    blk = f.body[52]
    loop = blk.body[0]
    assert isinstance(loop, ast.For), type(loop)
    return loop


def _impl_fn() -> ast.FunctionDef:
    t = ast.parse((ROOT / MOD).read_text(encoding="utf-8"))
    for n in t.body:
        if isinstance(n, ast.FunctionDef) and n.name == FN:
            return n
    raise AssertionError(f"{FN} 不在 {MOD} 顶层")


def _facade_call() -> ast.Call:
    t = ast.parse((ROOT / "scripts/ai_factor_trader.py").read_text(encoding="utf-8"))
    for n in ast.walk(t):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == FN):
            return n
    raise AssertionError("门面里没有 execute_entry_scan 调用点")


class EntryExecutionVerbatimTest(unittest.TestCase):
    def test_extracted_loop_is_ast_identical_to_baseline(self):
        old, new = _base_loop(), _impl_fn()
        old = accepted_function(MOD, FN, None).body[0]
        # 提取后的函数体第一个语句就是那个 for
        loop = new.body[0]
        self.assertIsInstance(loop, ast.For)
        self.assertEqual(ast.dump(loop, include_attributes=False),
                         ast.dump(old, include_attributes=False),
                         "入场循环与抽取前**不再是同一棵 AST**")

    def test_facade_call_passes_every_parameter_once_same_name(self):
        params = [a.arg for a in _impl_fn().args.kwonlyargs]
        self.assertEqual(len(params), 41, "参数个数变了？")
        call = _facade_call()
        self.assertEqual(call.args, [], "应全部按关键字传参")
        kws = [k.arg for k in call.keywords]
        self.assertEqual(kws, params, "调用点参数顺序/集合与签名不一致")
        for k in call.keywords:
            self.assertIsInstance(k.value, ast.Name, f"{k.arg} 的实参不是名字")
            self.assertEqual(k.value.id, k.arg,
                             f"{k.arg} 未按同名传参（{ast.unparse(k.value)}）⇒ 可能传错变量")

    def test_no_undeclared_free_names_in_impl(self):
        """体内自由名必须是声明参数或新模块里可解析的名字。"""
        fn = _impl_fn()
        module = ast.parse((ROOT / MOD).read_text(encoding="utf-8"))
        module_names = set()
        for n in module.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                module_names.add(n.name)
            elif isinstance(n, ast.Assign):
                for tg in n.targets:
                    if isinstance(tg, ast.Name):
                        module_names.add(tg.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    module_names.add(a.asname or a.name.split(".")[0])
        params = {a.arg for a in fn.args.kwonlyargs} | {a.arg for a in fn.args.args}
        local = set(params)
        for n in ast.walk(fn):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local.add(n.name); local |= {a.arg for a in n.args.args}
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                local.add(n.id)
            if isinstance(n, ast.ExceptHandler) and n.name:
                local.add(n.name)
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    local.add(a.asname or a.name.split(".")[0])
        reads = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)
                 and isinstance(n.ctx, ast.Load)}
        missing = sorted(reads - local - set(dir(builtins)) - module_names)
        self.assertEqual(missing, [], f"这些名字在子包里解析不到（会 NameError）: {missing}")

    def test_smoke_empty_universe(self):
        """空宇宙可调用：证明签名/导入/调用点接线成立（不做业务断言）。"""
        import inspect
        from scripts.trader import entry_execution
        self.assertTrue(callable(entry_execution.execute_entry_scan))
        sig = inspect.signature(entry_execution.execute_entry_scan)
        self.assertEqual([p.name for p in sig.parameters.values()
                          if p.kind is p.POSITIONAL_OR_KEYWORD], [],
                         "应全部为 kw-only（防位置传参错序）")
        # 全部参数给 None，只有循环要用的容器给空 —— 空宇宙 ⇒ 循环体一次都不执行
        kw = {name: None for name in sig.parameters}
        kw.update(all_factors=[], executed_actions=[], pending_inst_ids=set(), trackers={})
        self.assertIsNone(entry_execution.execute_entry_scan(**kw))

    def test_judgment_actually_notices_a_change(self):
        old = _base_loop()
        tampered = ast.parse(ast.unparse(old).replace("continue", "pass", 1)).body[0]
        self.assertNotEqual(ast.dump(old, include_attributes=False),
                            ast.dump(tampered, include_attributes=False),
                            "自检：判据 1 看不见循环体改动")
        # 判据 2 自检：少一个参数必须被发现
        t = ast.parse("f(a=a, b=b)\n")
        call = t.body[0].value
        self.assertNotEqual([k.arg for k in call.keywords], ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()

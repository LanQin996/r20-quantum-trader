"""`collect_pending_inst_ids` 的标的池读取次数（性能优化，阶段 4·B7）。

## 优化了什么

`scripts/trader/cycle_snapshot.py::collect_pending_inst_ids` 在**非 Binance** 分支里
逐标的调用 `list_open_orders(symbol)`（Gate 的列表端点按合约查询）。原实现在这个
**内层循环里**每次都调用 `load_instruments()`：

```python
for _ins in load_instruments():          # ← 每次迭代都读一次文件
    _gb = ...
    _grows.extend(_gad.list_open_orders(_gb) or [])
```

`load_instruments()` 不是纯内存操作：一次**磁盘读 + JSON 解析 + 逐项合法性校验 +
重建列表**。8 个标的就白读 8 次。已提到循环外（`_gpool = load_instruments()`）。

## 为什么这个优化"小"但仍然值得做，以及它的边界

实测 `load_instruments()` 约 **0.08 ms/次**，而一次交易所 API 往返是**百毫秒量级**
（实测 OKX 单次 111 ms）。所以：

- 本优化**省下的绝对时间是微不足道的**（8 次 ≈ 0.6 ms）；
- 它的价值在于**消除 N 次无谓 I/O**，让"读一次"这个意图显式化，
  而不是"每次迭代偷偷重读"。

**真正的成本在 N+1 网络调用本身**（每个标的一次 Gate API 往返），但那需要给
适配器加批量端点或改 `list_open_orders` 的接口契约 —— **属于接口变更，本轮不做**。
故这里只做"读一次"这一无接口影响的部分，并把 N+1 如实记在报告里。

## ⚠️ 一次自己造成的缩进事故（值得留一条）

我第一次替换时把 `_gpool = ...` 放到了 `else:` 之后**同级**、
`for` 也随之解除缩进 —— 于是 **Binance 分支也会跑那个逐标的循环**。
`ast.parse` 不报错、导入也正常，因为语法完全合法，只是**控制流变了**。

> 教训：**"改缩进"是最容易悄悄改变控制流的操作**，而语法检查抓不到它。
> 故本文件不测"函数能不能跑"，而是测**各分支下的调用次数** ——
> 那才是缩进事故的直接症状。

## ⚠️ 这个测试**不能**证明性能变好

它测的是**调用次数**（结构），不是耗时。耗时对比见模块文档串里的实测数。
把"调用次数少了"直接说成"快了"是过度声明 —— 本文件不这么做。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from scripts.trader import cycle_snapshot

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "trader" / "cycle_snapshot.py"

POOL = [
    {"instId": "BTC-USDT-SWAP", "name": "BTC"},
    {"instId": "ETH-USDT-SWAP", "name": "ETH"},
    {"instId": "SOL-USDT-SWAP", "name": "SOL"},
]
AUTH_MARKERS = ("INVALID_KEY", "Invalid key", "-2015")


class _FakeAdapter:
    def __init__(self):
        self.calls: list[str] = []

    def open_orders(self, symbol=None):
        self.calls.append("<all>")
        return [{"inst_id": "BTC-USDT-SWAP", "side": "buy", "reduce_only": False}]

    def list_open_orders(self, symbol):
        self.calls.append(symbol)
        return [{"inst_id": f"{symbol}-USDT-SWAP", "side": "sell", "reduce_only": True}]


class _FakeRegistry:
    def __init__(self, adapter, open_=True):
        self._adapter = adapter
        self._open = open_

    def execution_open(self, venue, mode):
        return self._open

    def get_adapter(self, venue, environment=None):
        return self._adapter


def _run(venues, adapter=None, broken=frozenset(), open_=True):
    """跑一次 collect_pending_inst_ids，返回 (池读取次数, 适配器调用列表)。"""
    adapter = adapter or _FakeAdapter()
    counter = {"n": 0}

    def counting_load():
        counter["n"] += 1
        return [dict(x) for x in POOL]

    inst_ids, long_c, short_c = cycle_snapshot.collect_pending_inst_ids(
        venues=venues,
        venue_mode="live",
        broken_venues=broken,
        venue_registry=_FakeRegistry(adapter, open_),
        load_instruments=counting_load,
        auth_markers=AUTH_MARKERS,
        warn=None,
    )
    return counter["n"], adapter.calls, inst_ids, long_c, short_c


class PoolReadCountTest(unittest.TestCase):
    def test_gate_reads_pool_once_not_per_instrument(self):
        """**核心断言**：3 个标的 → 只读 1 次池（原实现是 3 次）。"""
        n, calls, _, _, _ = _run(["gate"])
        self.assertEqual(n, 1,
                         f"标的池被读了 {n} 次（3 个标的应为 1 次）—— "
                         "load_instruments() 又回到循环里了")

    def test_gate_still_queries_each_instrument(self):
        """优化不得顺手砍掉逐标的查询（那是 N+1 的另一半，本轮不动）。"""
        _, calls, _, _, _ = _run(["gate"])
        self.assertEqual(calls, ["BTC", "ETH", "SOL"],
                         "每个标的仍须各查一次（Gate 端点按合约）")

    def test_binance_does_not_read_pool(self):
        """Binance 走全合约端点，**不应**读标的池。

        ⚠️ 这条正是我那次缩进事故的**直接症状测试**：当时 `for` 被解除缩进，
        Binance 分支也会跑逐标的循环，于是会读 1 次池并发 3 次 list_open_orders。
        """
        n, calls, _, _, _ = _run(["binance"])
        self.assertEqual(n, 0, "Binance 分支不应读标的池")
        self.assertEqual(calls, ["<all>"], "Binance 只用全合约端点")

    def test_both_venues(self):
        """两所同时开：池只在 gate 分支读一次。"""
        n, calls, _, _, _ = _run(["binance", "gate"])
        self.assertEqual(n, 1)
        self.assertEqual(calls[0], "<all>")
        self.assertEqual(calls[1:], ["BTC", "ETH", "SOL"])


class BranchSemanticsTest(unittest.TestCase):
    def test_broken_venue_is_skipped_entirely(self):
        n, calls, _, _, _ = _run(["gate"], broken={"gate"})
        self.assertEqual(n, 0, "凭证已死的所不应读池（原有回收侧短路）")
        self.assertEqual(calls, [])

    def test_execution_closed_venue_skipped(self):
        n, calls, _, _, _ = _run(["gate"], open_=False)
        self.assertEqual(n, 0, "执行闸关闭的所不应读池")
        self.assertEqual(calls, [])

    def test_empty_venues(self):
        n, calls, inst_ids, long_c, short_c = _run([])
        self.assertEqual((n, calls), (0, []))
        self.assertEqual((inst_ids, long_c, short_c), (set(), 0, 0))


class SourceShapeTest(unittest.TestCase):
    """源码层面：用 AST 判定，而不是文本匹配 —— 缩进事故改变的是 AST 结构。"""

    @classmethod
    def setUpClass(cls):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        cls.fn = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "collect_pending_inst_ids")

    def _inner_loop(self):
        for loop in [n for n in ast.walk(self.fn) if isinstance(n, ast.For)]:
            tgt = loop.target
            if isinstance(tgt, ast.Name) and tgt.id == "_ins":
                return loop
        return None

    def test_inner_loop_exists(self):
        self.assertIsNotNone(self._inner_loop(), "找不到逐标的循环 for _ins in ...")

    def test_pool_read_is_outside_the_instrument_loop(self):
        inner = self._inner_loop()
        calls = [n.lineno for n in ast.walk(inner)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "load_instruments"]
        self.assertEqual(calls, [],
                         f"load_instruments() 仍在内层循环里（L{calls}）")

    def test_pool_is_read_exactly_once_in_source(self):
        loads = [n for n in ast.walk(self.fn)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "load_instruments"]
        self.assertEqual(len(loads), 1,
                         f"源码里应有且仅有 1 处 load_instruments()，实际 {len(loads)}")

    def test_inner_loop_iterates_the_preloaded_pool(self):
        """内层循环的迭代对象必须是预读的 `_gpool`，而不是再调一次函数。"""
        inner = self._inner_loop()
        it = inner.iter
        self.assertIsInstance(it, ast.Name)
        self.assertEqual(it.id, "_gpool")
        names = [n.id for n in ast.walk(self.fn)
                 if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
        self.assertIn("_gpool", names, "_gpool 未在函数内赋值")


if __name__ == "__main__":
    unittest.main()

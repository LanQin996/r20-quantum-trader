"""`scripts/trader/position_universe.py`（B3 第三十一刀）回归。

## 这个测试在守什么

这两段决定**送给 AI 的"持仓全景"长什么样**。AI 看到的持仓与真实持仓不一致，
就会基于不存在的仓位做决策 —— 这类错误**不会报错**，只会让模型持续误判。

## 四处易错点（详见模块文档串）

| # | 细节 | 错了会怎样 |
|---|---|---|
| 1 | OKX 侧 `venue` 用 **`setdefault`** | 无条件覆盖会抹掉因子快照里已有的真实场所 |
| 2 | 追踪器键 `f"{instId}_{position.get('side','')}"` | 键拼错 → 静默拿不到追踪器 → 止损/水位全 `None` → 提示词显示"无止损线" |
| 3 | 外所 `instId` 是合成串 `f"{VENUE_UPPER}:{inst}"` | 用原始 id 会让 AI 把两所同一标的混为一谈 |
| 4 | `base` 回退 `inst_id`，匹配因子用 **`name`** | 匹配错 → ATR 恒为默认值 |

## 为什么必须覆盖"追踪器缺失"

第 2 条是**静默失效**：键拼错时 `trackers.get(...)` 不抛错，只返回 `{}`，
于是 `trailingStopPx` 等字段变成 `None` —— 模型看到的持仓"没有止损线"。
断言必须**直接检查这些字段的值**，而不是"函数跑通了"。
"""

from __future__ import annotations

import ast
import copy
import random
import unittest
from pathlib import Path

from scripts.trader.position_universe import (
    collect_okx_position_payloads,
    merge_cross_venue_positions,
)

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "trader" / "position_universe.py"
FACADE = ROOT / "scripts" / "ai_factor_trader.py"


# ------------------------------------------------------------ legacy 实现


def _legacy_collect(all_factors, trackers):
    active_pos_list = []
    for f in all_factors:
        position = f.get("position")
        if not position:
            continue
        position_payload = dict(position)
        position_payload.setdefault("venue", "okx")
        tracker = trackers.get(f"{f['instId']}_{position.get('side', '')}", {})
        position_payload["trailingStopPx"] = tracker.get("trailingStopPx")
        position_payload["highWaterMark"] = tracker.get("highWaterMark")
        position_payload["lowWaterMark"] = tracker.get("lowWaterMark")
        position_payload["takeProfitPx"] = tracker.get("takeProfitPx")
        position_payload["stage_desc"] = tracker.get("stage_desc", "")
        position_payload["atr"] = f.get("atr", 0.0)
        active_pos_list.append(position_payload)
    return active_pos_list


def _legacy_merge(active_pos_list, xv_positions_by_venue, all_factors):
    try:
        _xv_snap = xv_positions_by_venue
        if _xv_snap:
            for v_name, v_rows in _xv_snap.items():
                for p in v_rows:
                    base = str(p.get("base") or "").upper()
                    inst = str(p.get("inst_id") or base)
                    side = str(p.get("side") or "net").lower()
                    match_f = next((x for x in all_factors if x.get("name") == base), {})
                    active_pos_list.append({
                        "venue": v_name,
                        "instId": f"{v_name.upper()}:{inst}",
                        "name": base,
                        "side": side,
                        "pos": abs(float(p.get("size_signed") or 0)),
                        "avgPx": float(p.get("entry_price") or 0),
                        "markPx": float(p.get("mark_price") or 0),
                        "margin": float(p.get("margin") or 0),
                        "upl": float(p.get("unrealized_pnl") or 0),
                        "atr": match_f.get("atr", 0.0),
                        "leverage": float(p.get("leverage") or 0),
                    })
    except Exception as _xv_e:
        print(f"[三所持仓全景] 外所持仓汇入异常: {_xv_e}")


# ------------------------------------------------------------ OKX 侧


class CollectOkxTest(unittest.TestCase):
    def _f(self, **over):
        """⚠️ helper 我第一版写错了：`dict.update(over)` 后又 `f.update(f)`，
        后者是 no-op —— 于是 `over` **根本没生效**（我传 `instId=...` 被静默忽略，
        报错的却是 legacy 那侧）。现在正确合并 `over`。"""
        f = {"instId": "BTC-USDT-SWAP", "name": "BTC", "atr": 123.4,
             "position": {"side": "long", "pos": "1", "avgPx": "100"}}
        f.update(over)
        return f

    def test_no_position_is_skipped(self):
        out = collect_okx_position_payloads([{"instId": "X"}, {"instId": "Y", "position": None}], {})
        self.assertEqual(out, [])

    def test_empty_position_dict_is_skipped(self):
        """`{}` 是假值 —— 与 `None` 一样跳过。"""
        out = collect_okx_position_payloads([{"instId": "X", "position": {}}], {})
        self.assertEqual(out, [])

    def test_venue_defaults_to_okx(self):
        out = collect_okx_position_payloads([self._f()], {})
        self.assertEqual(out[0]["venue"], "okx")

    def test_existing_venue_is_respected(self):
        """**核心**：`setdefault` 不是无条件赋值。"""
        out = collect_okx_position_payloads([self._f(position={"side": "long", "venue": "gate"})], {})
        self.assertEqual(out[0]["venue"], "gate",
                         "因子快照已有的 venue 必须保留")

    def test_tracker_lookup_key_is_instid_underscore_side(self):
        trackers = {"BTC-USDT-SWAP_long": {"trailingStopPx": "95",
                                           "highWaterMark": "150",
                                           "lowWaterMark": "90",
                                           "takeProfitPx": "130",
                                           "stage_desc": "移动止盈中"}}
        out = collect_okx_position_payloads([self._f()], trackers)
        p = out[0]
        self.assertEqual(p["trailingStopPx"], "95")
        self.assertEqual(p["highWaterMark"], "150")
        self.assertEqual(p["lowWaterMark"], "90")
        self.assertEqual(p["takeProfitPx"], "130")
        self.assertEqual(p["stage_desc"], "移动止盈中")

    def test_wrong_key_shape_yields_none_fields(self):
        """键形态不对（如用 `instId` 而不是 `instId_side`）→ 字段全 None。

        这条把"键拼错就静默失效"的症状钉住：**不抛错，只是字段变 None**。
        """
        trackers = {"BTC-USDT-SWAP": {"trailingStopPx": "95"}}
        out = collect_okx_position_payloads([self._f()], trackers)
        self.assertIsNone(out[0]["trailingStopPx"], "键不含 _side 时取不到")
        self.assertEqual(out[0]["stage_desc"], "")
        self.assertIsNone(out[0]["highWaterMark"])

    def test_missing_tracker_gives_none_but_empty_stage_desc(self):
        out = collect_okx_position_payloads([self._f()], {})
        p = out[0]
        self.assertIsNone(p["trailingStopPx"])
        self.assertIsNone(p["highWaterMark"])
        self.assertIsNone(p["lowWaterMark"])
        self.assertIsNone(p["takeProfitPx"])
        self.assertEqual(p["stage_desc"], "", "stage_desc 默认是空串而不是 None")

    def test_side_used_verbatim_in_key(self):
        """`side` 不做规范化 —— 大写 `LONG` 的键必须是 `..._LONG`。"""
        trackers = {"BTC-USDT-SWAP_LONG": {"trailingStopPx": "77"}}
        out = collect_okx_position_payloads(
            [self._f(position={"side": "LONG"})], trackers)
        self.assertEqual(out[0]["trailingStopPx"], "77")

    def test_side_absent_yields_trailing_underscore(self):
        trackers = {"BTC-USDT-SWAP_": {"trailingStopPx": "88"}}
        out = collect_okx_position_payloads(
            [self._f(position={"pos": "1"})], trackers)
        self.assertEqual(out[0]["trailingStopPx"], "88", "缺 side → 键以 _ 结尾")

    def test_atr_from_factor_level(self):
        out = collect_okx_position_payloads([self._f(atr=9.9)], {})
        self.assertEqual(out[0]["atr"], 9.9)

    def test_atr_default_zero(self):
        out = collect_okx_position_payloads([self._f(atr=None)], {})
        self.assertIsNone(out[0]["atr"], "显式 None 保留 None（get 只在缺键时用默认）")

    def test_atr_missing_key_gives_zero(self):
        f = self._f()
        del f["atr"]
        out = collect_okx_position_payloads([f], {})
        self.assertEqual(out[0]["atr"], 0.0)

    def test_position_dict_is_copied_not_aliased(self):
        src = {"side": "long", "pos": "1"}
        out = collect_okx_position_payloads([self._f(position=src)], {})
        out[0]["injected"] = True
        self.assertNotIn("injected", src, "不得改到原 position dict")

    def test_multiple_positions_order_preserved(self):
        """顺序必须与 `all_factors` 一致。

        ⚠️ 注意 `instId` **不在**载荷里 —— 载荷是 `dict(position)` 加若干追踪器字段，
        而 `instId` 在**因子项**那一层。我第一版断言 `p["instId"]` → KeyError。
        这里改用一个真正被搬进来的字段（`atr`）来验顺序。
        """
        fs = [{"instId": "A-USDT-SWAP", "name": "A", "atr": 1.0,
               "position": {"side": "long"}},
              {"instId": "B-USDT-SWAP", "name": "B", "atr": 2.0,
               "position": {"side": "short"}}]
        out = collect_okx_position_payloads(fs, {})
        self.assertEqual([p["atr"] for p in out], [1.0, 2.0])
        self.assertEqual([p["side"] for p in out], ["long", "short"])
        self.assertNotIn("instId", out[0],
                         "既有行为：载荷不含 instId（它在因子项层）")

    def test_payload_does_not_carry_instid(self):
        """钉住上面那条既有行为，防止后人"顺手"补上 instId 而改变载荷形状。"""
        out = collect_okx_position_payloads([self._f()], {})
        self.assertNotIn("instId", out[0])
        self.assertIn("side", out[0])

    def test_empty_input(self):
        self.assertEqual(collect_okx_position_payloads([], {}), [])


# ------------------------------------------------------------ 外所汇入


class MergeCrossVenueTest(unittest.TestCase):
    FACTORS = [{"name": "BTC", "atr": 111.0}, {"name": "ETH", "atr": 222.0}]

    def _row(self, **over):
        r = {"base": "btc", "inst_id": "BTCUSDT", "side": "LONG",
             "size_signed": -2, "entry_price": 100, "mark_price": 110,
             "margin": 50, "unrealized_pnl": 20, "leverage": 5}
        r.update(over)
        return r

    def test_inst_id_is_synthetic_with_venue_prefix(self):
        """**核心**：外所 instId 带场所前缀，避免与 OKX 侧同名标的混淆。"""
        out = []
        merge_cross_venue_positions(out, {"binance": [self._row()]}, self.FACTORS)
        self.assertEqual(out[0]["instId"], "BINANCE:BTCUSDT")
        self.assertNotEqual(out[0]["instId"], "BTC-USDT-SWAP")

    def test_venue_is_lowercase_key(self):
        out = []
        merge_cross_venue_positions(out, {"gate": [self._row()]}, self.FACTORS)
        self.assertEqual(out[0]["venue"], "gate")

    def test_base_uppercased(self):
        out = []
        merge_cross_venue_positions(out, {"gate": [self._row()]}, self.FACTORS)
        self.assertEqual(out[0]["name"], "BTC")

    def test_base_empty_string_does_not_fall_back(self):
        """**故意反直觉**：`base=""`（或 `None`）**不会**回退到 `inst_id`。

        `p.get("base") or ""` —— `"" or ""` 仍是 `""`。回退只发生在
        **键不存在**（`p["base"]` 抛 KeyError 才会走 `inst_id`）。
        我第一版以为"空值会回退"，写错了。这里把既有行为钉住。
        """
        out = []
        merge_cross_venue_positions(out, {"gate": [self._row(base=None, inst_id="xyz")]},
                                    self.FACTORS)
        self.assertEqual(out[0]["name"], "")

    def test_base_key_absent_falls_back_to_inst_id(self):
        """键**不存在**时才回退（`p.get("base")` 得 None → `or ""`… 仍不回退）。

        实际上 `p.get("base")` 与 `p["base"]` 在这里等价（都走 `.get`），
        故"缺键"同样得到 `""`。**回退分支实际不可达** —— 与 §29.4 同类，
        如实记录：不要为了"让回退生效"去改实现。
        """
        out = []
        merge_cross_venue_positions(out, {"gate": [{"inst_id": "abc"}]}, self.FACTORS)
        self.assertEqual(out[0]["name"], "", "缺 base 键也得到空串（回退不可达）")

    def test_inst_falls_back_to_base(self):
        out = []
        merge_cross_venue_positions(out, {"gate": [self._row(inst_id=None)]}, self.FACTORS)
        self.assertEqual(out[0]["instId"], "GATE:BTC")

    def test_side_lowercased(self):
        out = []
        merge_cross_venue_positions(out, {"gate": [self._row(side="SHORT")]}, self.FACTORS)
        self.assertEqual(out[0]["side"], "short")

    def test_side_defaults_to_net(self):
        out = []
        merge_cross_venue_positions(out, {"gate": [self._row(side=None)]}, self.FACTORS)
        self.assertEqual(out[0]["side"], "net")

    def test_size_is_absolute_value(self):
        """`size_signed` 取绝对值 —— 方向由 `side` 表达。"""
        out = []
        merge_cross_venue_positions(out, {"gate": [self._row(size_signed=-3.5)]}, self.FACTORS)
        self.assertEqual(out[0]["pos"], 3.5)
        out2 = []
        merge_cross_venue_positions(out2, {"gate": [self._row(size_signed=3.5)]}, self.FACTORS)
        self.assertEqual(out2[0]["pos"], 3.5)

    def test_atr_matched_by_name(self):
        out = []
        merge_cross_venue_positions(out, {"gate": [self._row(base="ETH")]}, self.FACTORS)
        self.assertEqual(out[0]["atr"], 222.0)

    def test_atr_default_when_name_not_in_factors(self):
        out = []
        merge_cross_venue_positions(out, {"gate": [self._row(base="DOGE")]}, self.FACTORS)
        self.assertEqual(out[0]["atr"], 0.0)

    def test_numeric_coercion(self):
        out = []
        merge_cross_venue_positions(out, {"gate": [self._row(
            entry_price="0", mark_price=None, margin=None,
            unrealized_pnl=None, leverage=None)]}, self.FACTORS)
        p = out[0]
        self.assertEqual(p["avgPx"], 0.0)
        self.assertEqual(p["markPx"], 0.0)
        self.assertEqual(p["margin"], 0.0)
        self.assertEqual(p["upl"], 0.0)
        self.assertEqual(p["leverage"], 0.0)

    def test_appends_and_preserves_existing(self):
        out = [{"venue": "okx", "instId": "BTC-USDT-SWAP"}]
        merge_cross_venue_positions(out, {"gate": [self._row()]}, self.FACTORS)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["venue"], "okx", "已有条目应原样保留在**前**")

    def test_none_snapshot_is_noop(self):
        out = []
        merge_cross_venue_positions(out, None, self.FACTORS)
        self.assertEqual(out, [])

    def test_empty_snapshot_is_noop(self):
        out = []
        merge_cross_venue_positions(out, {}, self.FACTORS)
        self.assertEqual(out, [])

    def test_multiple_venues_and_rows(self):
        """⚠️ 顺序由 **`xv_positions_by_venue` 的插入序**决定（Python 3.7+ 保序）。

        我第一版按"字母序 binance 在前"写期望 —— 错了：字面量里 gate 先写，
        于是 gate 先被遍历。
        """
        out = []
        # `_row` 的 inst_id 默认是 "BTCUSDT"，改 base 不会自动改 inst_id ——
        # 故这里显式给出 inst_id，否则两个 gate 行会得到同一个合成 id。
        merge_cross_venue_positions(out, {
            "binance": [self._row(base="BTC")],
            "gate": [self._row(base="ETH", inst_id="ETHUSDT"),
                     self._row(base="BTC", inst_id="BTCUSD")],
        }, self.FACTORS)
        self.assertEqual(len(out), 3)
        self.assertEqual([p["instId"] for p in out],
                         ["BINANCE:BTCUSDT", "GATE:ETHUSDT", "GATE:BTCUSD"])

    def test_venue_iteration_order_follows_dict(self):
        """gate 写在前面 → gate 的行先进入列表。"""
        out = []
        merge_cross_venue_positions(out, {
            "gate": [self._row(base="ethereum", inst_id="ETHUSDT")],
            "binance": [self._row(base="btc", inst_id="BTCUSDT")],
        }, self.FACTORS)
        self.assertEqual([p["instId"] for p in out],
                         ["GATE:ETHUSDT", "BINANCE:BTCUSDT"])

    def test_venue_with_empty_row_list(self):
        out = []
        merge_cross_venue_positions(out, {"gate": []}, self.FACTORS)
        self.assertEqual(out, [])


class MergeFailureIsolationTest(unittest.TestCase):
    """**核心**：外所汇入失败绝不能影响 OKX 主路径。"""

    def test_bad_row_does_not_raise(self):
        out = [{"venue": "okx", "instId": "BTC-USDT-SWAP"}]
        merge_cross_venue_positions(out, {"gate": ["not-a-dict"]}, [])
        self.assertEqual(out, [{"venue": "okx", "instId": "BTC-USDT-SWAP"}],
                         "OKX 主路径必须原样保留")

    def test_non_numeric_size_is_swallowed(self):
        out = []
        merge_cross_venue_positions(out, {"gate": [{"base": "BTC",
                                                    "size_signed": "abc"}]}, [])
        self.assertEqual(out, [], "转换失败 → 整段放弃（既有行为）")

    def test_snapshot_not_a_dict_of_lists(self):
        out = []
        merge_cross_venue_positions(out, {"gate": 123}, [])
        self.assertEqual(out, [])

    def test_prints_warning_on_failure(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            merge_cross_venue_positions([], {"gate": [{"size_signed": "abc"}]}, [])
        self.assertIn("外所持仓汇入异常", buf.getvalue(),
                      "失败必须留痕（原实现 print 告警）")


class RandomParityTest(unittest.TestCase):
    def test_collect_parity(self):
        rng = random.Random(31001)
        for i in range(12000):
            factors = []
            for _ in range(rng.randint(0, 3)):
                pos = rng.choice([None, {}, {"side": rng.choice(["long", "short", "LONG", ""]),
                                             "venue": rng.choice(["okx", "gate", None])},
                                  {"side": "long"}])
                f = {"instId": rng.choice(["B-USDT-SWAP", "E-USDT-SWAP"]),
                     "name": rng.choice(["BTC", "ETH"]),
                     "atr": rng.choice([1.5, None, "x"])}
                if pos is not None or rng.random() < 0.5:
                    f["position"] = pos
                factors.append(f)
            trackers = {}
            for f in factors:
                if isinstance(f.get("position"), dict):
                    trackers[rng.choice([f"{f['instId']}_long", f["instId"],
                                         f"{f['instId']}_"])] = rng.choice(
                        [{}, {"trailingStopPx": "9"}, {"stage_desc": "s"}])
            got = collect_okx_position_payloads(copy.deepcopy(factors), copy.deepcopy(trackers))
            want = _legacy_collect(copy.deepcopy(factors), copy.deepcopy(trackers))
            self.assertEqual(got, want, f"第{i}组分叉")

    def test_merge_parity(self):
        rng = random.Random(31002)
        for i in range(12000):
            factors = [{"name": n, "atr": a} for n, a in
                       (("BTC", 1.0), ("ETH", 2.0), ("SOL", 3.0))]
            snap = {}
            for v in ("binance", "gate"):
                if rng.random() < 0.3:
                    continue
                rows = []
                for _ in range(rng.randint(0, 2)):
                    rows.append({
                        "base": rng.choice(["btc", "ETH", None, ""]),
                        "inst_id": rng.choice(["X1", None]),
                        "side": rng.choice(["LONG", "short", None, ""]),
                        "size_signed": rng.choice([-2, 0, 3.5, None, "1.5"]),
                        "entry_price": rng.choice([100, None, "50"]),
                        "mark_price": rng.choice([110, None]),
                        "margin": rng.choice([50, None]),
                        "unrealized_pnl": rng.choice([20, None]),
                        "leverage": rng.choice([5, None]),
                    })
                snap[v] = rows
            arg = snap if rng.random() < 0.9 else (None if rng.random() < 0.5 else {})
            a, b = [{"venue": "okx"}], [{"venue": "okx"}]
            got = copy.deepcopy(a)
            want = copy.deepcopy(b)
            merge_cross_venue_positions(got, copy.deepcopy(arg), factors)
            _legacy_merge(want, copy.deepcopy(arg), factors)
            self.assertEqual(got, want, f"第{i}组分叉")


class WiringTest(unittest.TestCase):
    def test_impl_in_submodule_not_facade(self):
        facade_src = FACADE.read_text(encoding="utf-8")
        mod_src = MODULE.read_text(encoding="utf-8")
        for fn in ("collect_okx_position_payloads", "merge_cross_venue_positions"):
            self.assertIn(f"def {fn}(", mod_src)
            self.assertNotIn(f"def {fn}(", facade_src)

    def test_facade_calls_both(self):
        """两处装配调用必须都在**主执行路径**上（第九十三刀后住 cycle_stages）。"""
        facade_src = FACADE.read_text(encoding="utf-8")
        stages_src = (ROOT / "scripts" / "trader" / "cycle_stages.py").read_text(encoding="utf-8")
        self.assertIn("_collect_okx_position_payloads(all_factors, trackers)", stages_src,
                      "持仓载荷装配的调用点应随相位 4 前段迁入 cycle_stages")
        self.assertIn("_merge_cross_venue_positions(active_pos_list, xv_positions_by_venue, all_factors)",
                      stages_src, "三所汇总的调用点同上")
        # 门面仍须把这两个实现注入阶段函数（调用期解析 ⇒ patch 面有效）
        self.assertIn("_collect_okx_position_payloads=_collect_okx_position_payloads", facade_src)
        self.assertIn("_merge_cross_venue_positions=_merge_cross_venue_positions", facade_src)
        self.assertIn("from scripts.trader.position_universe import (", facade_src)

    def test_facade_no_longer_contains_inline_bodies(self):
        facade_src = FACADE.read_text(encoding="utf-8")
        for gone in ("position_payload.setdefault", "三所持仓全景] 外所持仓汇入异常",
                     'match_f = next((x for x in all_factors'):
            self.assertNotIn(gone, facade_src, f"门面仍残留 {gone!r}")

    def test_merge_uses_frozen_snapshot_not_a_refetch(self):
        """**不变量**：汇入必须复用**已冻结**的快照，不得调用任何取数函数。"""
        src = MODULE.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "merge_cross_venue_positions")
        called = {getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                  for n in ast.walk(fn) if isinstance(n, ast.Call)}
        for forbidden in ("query_positions", "fetch_positions", "positions",
                          "get_adapter", "signed_request", "requests", "urlopen"):
            self.assertNotIn(forbidden, called,
                             f"merge 不得自己取数（{forbidden}）—— 必须用冻结快照")

    def test_module_has_no_io_imports(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top |= {a.asname or a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                top |= {a.asname or a.name for a in node.names}
        for forbidden in ("requests", "urllib", "okx_rest", "subprocess", "os"):
            self.assertNotIn(forbidden, top, f"纯装配模块不应 import {forbidden}")

    def test_module_level_has_no_side_effects(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        body = list(tree.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        for node in body:
            self.assertNotIsInstance(node, ast.Expr, f"模块级裸表达式 L{node.lineno}")


if __name__ == "__main__":
    unittest.main()

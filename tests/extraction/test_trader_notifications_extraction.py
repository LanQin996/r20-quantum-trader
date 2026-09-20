"""B3（交易员侧第十六块）`scripts/trader/notifications.py` 的抽取回归。

## 这个测试在守什么

`execute_portfolio` 的 `if accepted:` 块里，长/空两侧各写一遍"该说什么、通知什么"。
方向只影响 6 处文案（加仓动作 / 开仓动作 / 失败动作 / 加仓通知 side / 开仓通知 side /
加仓通知 strategy），却散落在 74 行里 —— 即 **12 个必须保持一致的字符串常量**。

文案错了不会让程序崩，会让**交易通知说错方向**。而通知是用户判断"这单是多是空"
的唯一来源。这个测试把 12 个常量逐个钉住，并把 legacy 内联实现作为对照基准。

## 为什么 `leverage` 必须单独断言

门面的 4 处 `leverage=int(ai_lever)` 是**审计缺陷 D(2026-09-13)** 的守卫：
曾恒写 `3`，导致 5x 仓也通知「3x 杠杆」（谎报）。故这几行**刻意留在门面**，
由 `trade_open_kwargs` 只提供其余实参。本测试同时断言：
①内容函数**不**返回 `leverage`；②门面 4 处通知仍各自字面量传 `int(ai_lever)`。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from scripts.trader import notifications

ROOT = Path(__file__).resolve().parents[2]
FACADE = ROOT / "scripts" / "ai_factor_trader.py"
SUBMODULE = ROOT / "scripts" / "trader" / "notifications.py"
CYCLE_STAGES = ROOT / "scripts" / "trader" / "cycle_stages.py"   # 第九十二刀：相位段现住此
ENTRY = ROOT / "scripts" / "trader" / "entry_execution.py"   # 第九十刀：开多/开空两支现住此

# _STRAT / _REASON 是门面调用点的既有局部量
STRAT = "AI_STRAT"
REASON = "因为所以"


def _legacy_action_message(*, is_long, is_scale_in, name, sz, px, order_ref, tp_px, sl_px):
    """搬走前 facade 长/空两侧的动作文案（逐字原样，用 is_long 择一）。"""
    if is_long:
        if is_scale_in:
            return f"[{name}] 🚀 AI顺势浮盈金字塔加多挂单已提交 {sz}张@{px} (order={order_ref}, TP={tp_px}, SL={sl_px})"
        return f"[{name}] AI限价多单已提交待成交 {sz}张@{px} (order={order_ref}, TP={tp_px}, SL={sl_px})"
    if is_scale_in:
        return f"[{name}] 🌪️ AI顺势浮盈金字塔加空挂单已提交 {sz}张@{px} (order={order_ref}, TP={tp_px}, SL={sl_px})"
    return f"[{name}] AI限价空单已提交待成交 {sz}张@{px} (order={order_ref}, TP={tp_px}, SL={sl_px})"


def _legacy_failure_message(*, is_long, name, order_ref):
    if is_long:
        return f"[{name}] AI限价多单提交失败: {order_ref}"
    return f"[{name}] AI限价空单提交失败: {order_ref}"


def _legacy_trade_open_kwargs(*, is_long, is_scale_in, name, sz, px, strat_tag,
                              ai_reason, tp_px, sl_px):
    """搬走前 facade 的两处 notify_trade_open(**) 实参（不含 leverage）。"""
    if is_long:
        if is_scale_in:
            return dict(inst=name, side="多 (顺势加多)", sz=sz, px=px,
                        strategy="🚀 顺势金字塔加多", reason=str(ai_reason),
                        tp_px=tp_px, sl_px=sl_px)
        return dict(inst=name, side="多", sz=sz, px=px, strategy=strat_tag,
                    reason=str(ai_reason), tp_px=tp_px, sl_px=sl_px)
    if is_scale_in:
        return dict(inst=name, side="空 (顺势加空)", sz=sz, px=px,
                    strategy="🌪️ 顺势金字塔加空", reason=str(ai_reason),
                    tp_px=tp_px, sl_px=sl_px)
    return dict(inst=name, side="空", sz=sz, px=px, strategy=strat_tag,
                reason=str(ai_reason), tp_px=tp_px, sl_px=sl_px)


class ActionMessageParityTest(unittest.TestCase):
    def _call(self, fn, **kw):
        base = dict(name="BTC", sz=3, px=79000.0, order_ref="ORD1",
                    tp_px=80000.0, sl_px=78000.0)
        base.update(kw)
        return fn(**base)

    def test_all_four_action_messages_match_legacy(self):
        for is_long in (True, False):
            for is_scale_in in (True, False):
                got = notifications.entry_action_message(
                    is_long=is_long, is_scale_in=is_scale_in, name="BTC", sz=3,
                    px=79000.0, order_ref="ORD1", tp_px=80000.0, sl_px=78000.0)
                exp = _legacy_action_message(
                    is_long=is_long, is_scale_in=is_scale_in, name="BTC", sz=3,
                    px=79000.0, order_ref="ORD1", tp_px=80000.0, sl_px=78000.0)
                self.assertEqual(got, exp, f"is_long={is_long} scale_in={is_scale_in} 分叉")

    def test_direction_words_are_correct(self):
        """逐条钉住方向字 —— 文案说错方向 = 用户看反多空。"""
        cases = [
            (True, True, "🚀 AI顺势浮盈金字塔加多挂单已提交"),
            (True, False, "AI限价多单已提交待成交"),
            (False, True, "🌪️ AI顺势浮盈金字塔加空挂单已提交"),
            (False, False, "AI限价空单已提交待成交"),
        ]
        for is_long, is_scale_in, needle in cases:
            msg = self._call(notifications.entry_action_message,
                             is_long=is_long, is_scale_in=is_scale_in)
            self.assertIn(needle, msg, f"is_long={is_long} scale_in={is_scale_in} 文案错")

    def test_failure_message_direction(self):
        for is_long in (True, False):
            got = notifications.entry_failure_message(
                is_long=is_long, name="BTC", order_ref="ORD9")
            exp = _legacy_failure_message(is_long=is_long, name="BTC", order_ref="ORD9")
            self.assertEqual(got, exp)
        self.assertIn("多单提交失败", notifications.entry_failure_message(
            is_long=True, name="BTC", order_ref="O"))
        self.assertIn("空单提交失败", notifications.entry_failure_message(
            is_long=False, name="BTC", order_ref="O"))

    def test_scale_in_message_is_distinguishable_from_initial(self):
        """加仓与首发的文案必须能区分 —— 运维靠它判断是否又加了一张。"""
        a = self._call(notifications.entry_action_message, is_long=True, is_scale_in=True)
        b = self._call(notifications.entry_action_message, is_long=True, is_scale_in=False)
        self.assertNotEqual(a, b)
        self.assertIn("金字塔", a)
        self.assertNotIn("金字塔", b)


class TradeOpenKwargsParityTest(unittest.TestCase):
    def _kw(self, **over):
        base = dict(name="BTC", sz=3, px=79000.0, strat_tag=STRAT,
                    ai_reason=REASON, tp_px=80000.0, sl_px=78000.0)
        base.update(over)
        return base

    def test_all_four_variants_match_legacy(self):
        for is_long in (True, False):
            for is_scale_in in (True, False):
                got = notifications.trade_open_kwargs(
                    is_long=is_long, is_scale_in=is_scale_in, **self._kw())
                exp = _legacy_trade_open_kwargs(
                    is_long=is_long, is_scale_in=is_scale_in, **self._kw())
                self.assertEqual(got, exp, f"is_long={is_long} scale_in={is_scale_in} 分叉")

    def test_side_and_strategy_words(self):
        cases = [
            (True, True, "多 (顺势加多)", "🚀 顺势金字塔加多"),
            (False, True, "空 (顺势加空)", "🌪️ 顺势金字塔加空"),
        ]
        for is_long, is_scale_in, side, strategy in cases:
            kw = notifications.trade_open_kwargs(
                is_long=is_long, is_scale_in=is_scale_in, **self._kw())
            self.assertEqual(kw["side"], side)
            self.assertEqual(kw["strategy"], strategy)

    def test_initial_entry_uses_strat_tag_and_plain_side(self):
        for is_long, side in ((True, "多"), (False, "空")):
            kw = notifications.trade_open_kwargs(
                is_long=is_long, is_scale_in=False, **self._kw(strat_tag="自定义策略"))
            self.assertEqual(kw["side"], side)
            self.assertEqual(kw["strategy"], "自定义策略")

    def test_reason_is_stringified(self):
        kw = notifications.trade_open_kwargs(
            is_long=True, is_scale_in=False, **self._kw(ai_reason=12345))
        self.assertEqual(kw["reason"], "12345")
        self.assertIsInstance(kw["reason"], str)

    def test_never_returns_leverage(self):
        """`leverage` 必须由门面以字面量传入，不能从这里冒出来。

        若本函数开始返回 `leverage`，`notify_trade_open(**kw, leverage=int(ai_lever))`
        会抛 "got multiple values for keyword argument 'leverage'" —— 而且是
        **只在实盘下单成功那一刻**才抛。所以在这里静态钉住。
        """
        for is_long in (True, False):
            for is_scale_in in (True, False):
                kw = notifications.trade_open_kwargs(
                    is_long=is_long, is_scale_in=is_scale_in, **self._kw())
                self.assertNotIn("leverage", kw)
                self.assertNotIn("max_leverage", kw)

    def test_kwargs_can_be_merged_with_leverage_without_collision(self):
        """与门面真实调用形态对拍：`notify_trade_open(**kw, leverage=...)`。"""
        seen = {}

        def notify_trade_open(**kwargs):
            seen.update(kwargs)

        for is_long in (True, False):
            for is_scale_in in (True, False):
                seen.clear()
                kw = notifications.trade_open_kwargs(
                    is_long=is_long, is_scale_in=is_scale_in, **self._kw())
                notify_trade_open(**kw, leverage=int(5.0))
                self.assertEqual(seen["leverage"], 5)
                self.assertEqual(seen["sz"], 3)
                self.assertEqual(seen["px"], 79000.0)
                self.assertEqual(seen["tp_px"], 80000.0)
                self.assertEqual(seen["sl_px"], 78000.0)
                self.assertEqual(seen["inst"], "BTC")


class WiringTest(unittest.TestCase):
    def test_impl_lives_in_submodule_not_facade(self):
        facade = FACADE.read_text(encoding="utf-8")
        sub = SUBMODULE.read_text(encoding="utf-8")
        for fn in ("entry_action_message", "entry_failure_message", "trade_open_kwargs"):
            self.assertIn(f"def {fn}(", sub)
            self.assertNotIn(f"def {fn}(", facade)

    def test_facade_direction_literals_are_gone(self):
        """门面不应再残留任何一处方向文案字面量（全在子模块里）。"""
        facade = FACADE.read_text(encoding="utf-8")
        for literal in ("AI顺势浮盈金字塔加多挂单已提交", "AI顺势浮盈金字塔加空挂单已提交",
                        "AI限价多单提交失败", "AI限价空单提交失败",
                        "顺势金字塔加多", "顺势金字塔加空",
                        "多 (顺势加多)", "空 (顺势加空)"):
            self.assertNotIn(literal, facade, f"门面仍残留方向文案字面量 {literal!r}")

    def test_four_notifications_still_carry_leverage(self):
        """4 处 notify_trade_open 必须各自字面量传 `int(ai_lever)`。

        这是审计缺陷 D(2026-09-13) 的守卫：曾恒写 3，5x 仓也通知「3x 杠杆」。
        """
        from tests.source_scan import count_keyword_argument
        self.assertEqual(
            count_keyword_argument("scripts/ai_factor_trader.py", "notify_trade_open",
                                   "leverage", value_must_contain="int(ai_lever)",
                                   pkg_name="trader"), 4,
            "四处开仓通知必须携带钳制后的真实杠杆")

        tree = ast.parse(ENTRY.read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "notify_trade_open"]
        self.assertEqual(len(calls), 4, f"应有 4 处开仓通知，实际 {len(calls)}")
        for call in calls:
            lev = [k for k in call.keywords if k.arg == "leverage"]
            self.assertEqual(len(lev), 1, f"L{call.lineno} 缺 leverage")
            rendered = ast.unparse(lev[0].value)
            self.assertEqual(rendered, "int(ai_lever)",
                             f"L{call.lineno} 的 leverage 必须是 int(ai_lever)，"
                             f"实际 {rendered!r}（曾因此谎报杠杆）")

    def test_both_directions_call_helpers(self):
        entry = ENTRY.read_text(encoding="utf-8")
        self.assertEqual(entry.count("entry_action_message("), 4,
                         "加仓/首发 × 多/空 = 4 处动作文案")
        self.assertEqual(entry.count("entry_failure_message("), 2)
        self.assertEqual(entry.count("trade_open_kwargs("), 4)

    def test_side_effects_stay_in_facade(self):
        """状态变更必须仍留在门面 —— 抽进子模块会藏起副作用。

        **走 AST，不走文本。** 子模块的 docstring 里**提到了**这些副作用名字
        （解释"为什么不抽它们"），文本检查会因此误报；AST 只看真实语句。
        这与本轮 §21.3 修掉的锚点是同一类坑：别拿文档当行为。
        """

        def side_effect_names(path):
            """返回该文件里真实发生的副作用名集合。"""
            tree = ast.parse(path.read_text(encoding="utf-8"))
            found = set()
            for node in ast.walk(tree):
                # 调用：save_trackers(...) / pending_inst_ids.add(...)
                if isinstance(node, ast.Call):
                    f = node.func
                    if isinstance(f, ast.Name):
                        found.add(f.id)
                    elif isinstance(f, ast.Attribute):
                        found.add(f.attr)
                # 自增赋值：reserved_slot_count += 1
                if isinstance(node, ast.AugAssign):
                    t = node.target
                    if isinstance(t, ast.Name):
                        found.add(t.id)
            return found

        # 第九十二刀：相位 1（取持仓/挂单枚举/跨所封顶/预留对账）整体搬入
        # `scripts/trader/cycle_stages.py` ⇒ 它的副作用（`pending_inst_ids.add`、
        # `reserved_* += 1`）随之迁移。**原意不变**：状态变更必须留在
        # 「门面主执行路径」（门面本体 ∪ 它的阶段函数），不得藏进 notifications 域。
        facade_eff = side_effect_names(FACADE) | side_effect_names(CYCLE_STAGES)
        sub_eff = side_effect_names(SUBMODULE)
        for name in ("save_trackers", "add", "reserved_slot_count",
                     "reserved_long_count", "reserved_short_count"):
            self.assertIn(name, facade_eff,
                          f"门面主执行路径应保留副作用 {name!r}（门面或 cycle_stages）")
            self.assertNotIn(name, sub_eff, f"子模块不得包含副作用 {name!r}（AST 判定）")

    def test_both_call_sites_define_every_name_they_pass(self):
        """调用点必须在自己**这一支**里备好传给 helper 的每个名字。

        判据同前几块：只沿**到达该调用的唯一路径**收集定义，绝不下钻进兄弟分支。
        """
        tree = ast.parse(ENTRY.read_text(encoding="utf-8"))
        func = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "execute_entry_scan")

        from tests.source_scan import names_defined_at_call

        checked = 0
        for node in ast.walk(func):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("entry_action_message", "entry_failure_message",
                                         "trade_open_kwargs")):
                continue
            passed = set()
            for arg in node.args:
                for nm in ast.walk(arg):
                    if isinstance(nm, ast.Name):
                        passed.add(nm.id)
            for kw in node.keywords:
                for nm in ast.walk(kw.value):
                    if isinstance(nm, ast.Name):
                        passed.add(nm.id)
            available = names_defined_at_call(func, node, module_tree=tree)
            missing = sorted(n for n in passed if n not in available)
            self.assertEqual(missing, [],
                             f"{node.func.id} 调用（L{node.lineno}）所在分支引用了"
                             f"未定义的名字 {missing}；实盘走到该分支时会 NameError")
            checked += 1
        self.assertEqual(checked, 10,
                         f"应有 4+2+4=10 处 helper 调用，实际 {checked}")


if __name__ == "__main__":
    unittest.main()

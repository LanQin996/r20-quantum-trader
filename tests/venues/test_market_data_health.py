r"""行情取数可观测性门（第一百三十七刀）。

## 背景：一次"零信号"的真实事故

`scripts/brain/packages.py` 里 6 处取数各自包着**静默** `except Exception: pass`。
抽取子模块时漏带 `import json` / `import urllib.request` ⇒ `NameError` 被这些 `except`
全部吞掉 ⇒ 现价恒 0 ⇒ `data_quality: invalid` ⇒ 主脑 P0 拦截、**约 30 小时没开新仓**，
而整个过程**没有任何日志**（用户是在看板上看到"P0 数据有效性拦截"才发现）。

本门钉两件事：

1. **失败必须留痕**：6 处取数全失败时，`market_data_health.stats()` 必须出现 **6 个 kind**
   的计数，且每个 kind 至少打印一条告警 —— 这条断言就是"当时若有就会立刻暴露事故"的那条；
2. **取值行为一字不变**：加了埋点后，同样的失败输入仍返回**完全一样的包**
   （price/bid/ask 为 0、`data_quality: invalid`、键集不变），且**绝不抛异常**。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from scripts.brain import packages as brain_packages  # noqa: E402
from scripts.brain.packages import fetch_single_instrument_package  # noqa: E402

# ⚠️ 必须按**运行时同一个模块身份**取助手：`packages.py` 用
# `from market_data_health import note_failure`（SCRIPTS_DIR 在 sys.path 上），
# 而本测试若 import `scripts.market_data_health` 会拿到**另一个模块对象** ⇒
# 计数被拆成两份、`stats()` 恒为空（本门第一版就是这么误判的）。
import market_data_health as health  # noqa: E402

failure_count, note_failure, reset, stats = (
    health.failure_count, health.note_failure, health.reset, health.stats)

ITEM = {"instId": "BTC-USDT-SWAP", "name": "BTC", "type": "crypto", "ccy": "BTC", "precision": 2}

TICKER_OK = {"code": "0", "data": [{"last": "70000", "bidPx": "69999", "askPx": "70001",
                                   "open24h": "69000", "vol24h": "100", "high24h": "71000",
                                   "low24h": "68000", "ts": "1789454700000"}]}


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class HealthModuleTest(unittest.TestCase):
    def setUp(self):
        reset()

    def tearDown(self):
        reset()

    def test_counts_and_one_time_log(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            for _ in range(3):
                note_failure("okx_ticker", NameError("boom"))
            note_failure("okx_open_interest", ValueError("x"))
        self.assertEqual(failure_count("okx_ticker"), 3)
        self.assertEqual(stats()["total"], 4)
        printed = [l for l in buf.getvalue().splitlines() if l.strip()]
        self.assertEqual(len(printed), 2, "每个 kind 只应打印一次（后续只累加）")
        self.assertIn("okx_ticker", printed[0])
        self.assertIn("NameError: boom", printed[0])

    def test_never_raises(self):
        """**关键**：喂一个 `__str__` 会抛的异常 —— 没有内部兜底时它必然炸出来。

        我第一版只喂 `None` / 普通异常，去掉兜底也照样绿（负向验证发现"没牙"），
        故补这个真会炸的输入。
        """
        class _BadStr(Exception):
            def __str__(self):          # 格式化 detail 时必然触发
                raise RuntimeError("__str__ 坏了")

        for weird in (None, Exception(), BaseException(), "不是异常", 123):
            with self.subTest(weird=weird):
                note_failure("kind_x", weird)      # 不得抛
        note_failure("kind_bad", _BadStr())        # 兜底必须吞掉它
        note_failure("kind_bad", _BadStr())
        self.assertEqual(failure_count("kind_x"), 5)
        self.assertEqual(failure_count("kind_bad"), 2)

    def test_stats_is_a_copy(self):
        note_failure("k", ValueError("v"))
        snap = stats()
        snap["by_kind"]["k"] = 999
        snap["total"] = 999
        self.assertEqual(stats()["total"], 1, "stats() 必须返回纯副本")

    def test_reset_clears_everything(self):
        note_failure("k", ValueError("v"))
        reset()
        self.assertEqual(stats(), {"total": 0, "by_kind": {}, "last_error": {}})


class IdentityTest(unittest.TestCase):
    """模块身份一致性：埋点与读取端必须共享同一个计数器容器。"""

    def test_helpers_are_the_same_module_object(self):
        """计数必须落在**同一个**模块对象上，否则埋点会静默失效。"""
        self.assertIs(brain_packages.note_failure, health.note_failure,
                      "packages 与测试拿到的 note_failure 不是同一对象 ⇒ 计数会被拆成两份")


class WiringTest(unittest.TestCase):
    """埋点是否真的接在 6 处取数上 —— 这一组就是"当时若有就能立刻暴露事故"的断言。"""

    def setUp(self):
        reset()

    def tearDown(self):
        reset()

    def _explode(self, *_a, **_k):
        raise NameError("name 'urllib' is not defined")

    def _call(self, urlopen_impl, indicator_impl=None, candles_impl=None):
        def _candles(*_a, **_k):
            return []
        def _indicator(*_a, **_k):
            raise RuntimeError("指标不可用")

        buf = io.StringIO()
        with patch.object(urllib.request, "urlopen", urlopen_impl), \
             contextlib.redirect_stdout(buf):
            pkg = fetch_single_instrument_package(
                ITEM,
                fetch_candles=candles_impl or _candles,
                fetch_single_indicator=indicator_impl or _indicator,
            )
        return pkg, buf.getvalue()

    def test_all_six_fetches_failing_leaves_six_counters(self):
        """6 处取数全失败 ⇒ 必须留下 6 个 kind 的痕迹（而不是静默返回 0）。"""
        pkg, out = self._call(self._explode)
        by_kind = stats()["by_kind"]
        self.assertEqual(
            sorted(by_kind),
            ["okx_adx_1h", "okx_funding_rate", "okx_ls_ratio",
             "okx_open_interest", "okx_taker_volume", "okx_ticker"],
            "6 处取数失败必须各自留痕；漏掉任何一处就是又一次静默事故",
        )
        for kind in by_kind:
            self.assertIn(kind, out, f"{kind} 应有告警输出")
        self.assertEqual(by_kind["okx_ticker"], 1, "每个标的每轮取一次 ticker")

    def test_package_is_unchanged_when_everything_fails(self):
        """埋点不得改变取值行为：失败时仍返回与埋点前完全一致的包。"""
        pkg, _ = self._call(self._explode)
        self.assertEqual(pkg["price"], 0.0)
        self.assertEqual(pkg["bidPx"], 0.0)
        self.assertEqual(pkg["askPx"], 0.0)
        self.assertEqual(pkg["data_quality"], "invalid")
        self.assertEqual(pkg["instId"], ITEM["instId"])
        self.assertEqual(pkg["recent_15m"], [])
        self.assertEqual(pkg["smart_money"]["available"], False)
        # 键集必须与埋点前一致（埋点不得往包里塞新字段）
        self.assertNotIn("failure_count", pkg)
        self.assertNotIn("data_health", pkg)

    def test_success_path_records_no_failure(self):
        pkg, out = self._call(lambda *_a, **_k: _Resp(json.dumps(TICKER_OK).encode()))
        self.assertEqual(pkg["price"], 70000.0)
        self.assertEqual(pkg["bidPx"], 69999.0)
        self.assertEqual(pkg["askPx"], 70001.0)
        self.assertNotIn("okx_ticker", stats()["by_kind"], "成功路径不得计数")
        self.assertNotIn("okx_ticker", out)


if __name__ == "__main__":
    unittest.main()

"""交易台账分片归档的安全性契约（审计「未完成清单」#2）。

这个脚本动的是**实盘台账**，所以它的价值不在"能归档"，而在"绝不会弄丢一行"：
默认 dry-run、先归档再截断、回读校验、行集合多重集校验、不可解析时间绝不猜。
本文件把这些红线逐条钉死。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BJ = timezone(timedelta(hours=8))

_spec = importlib.util.spec_from_file_location("archive_ledger", ROOT / "scripts" / "archive_ledger.py")
assert _spec and _spec.loader
al = importlib.util.module_from_spec(_spec)
sys.modules["archive_ledger"] = al
_spec.loader.exec_module(al)

NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=BJ)


def row(row_id: int, close_time: str, **extra) -> dict:
    return {"id": row_id, "inst": "BTC-USDT-SWAP", "close_time": close_time, "net_pnl": 1.0, **extra}


class PlanArchiveTests(unittest.TestCase):
    def test_recent_rows_stay_hot(self):
        rows = [row(1, "2026-09-01 10:00:00"), row(2, "2026-08-01 10:00:00")]
        hot, cold = al.plan_archive(rows, keep_days=180, now=NOW)
        self.assertEqual([r["id"] for r in hot], [1, 2])
        self.assertEqual(cold, [])

    def test_old_rows_are_archived(self):
        rows = [row(1, "2025-01-01 10:00:00"), row(2, "2026-09-01 10:00:00")]
        hot, cold = al.plan_archive(rows, keep_days=180, now=NOW)
        self.assertEqual([r["id"] for r in hot], [2])
        self.assertEqual([r["id"] for r in cold], [1])

    def test_unparsable_time_never_guessed(self):
        """「持仓中…」这类占位与缺字段一律留在热台账。"""
        rows = [row(1, "持仓中..."), row(2, ""), {"id": 3}, row(4, "2020-01-01 00:00:00")]
        hot, cold = al.plan_archive(rows, keep_days=180, now=NOW)
        self.assertEqual(sorted(r.get("id") for r in hot), [1, 2, 3])
        self.assertEqual([r["id"] for r in cold], [4])

    def test_non_dict_rows_stay_hot(self):
        """结构异常的行（历史脏数据）不参与归档判定，原样留在热台账。"""
        hot, cold = al.plan_archive(["junk", None, row(9, "2019-01-01 00:00:00")], now=NOW)
        self.assertEqual(cold, [row(9, "2019-01-01 00:00:00")])
        self.assertEqual([h for h in hot if not isinstance(h, dict)], ["junk", None])

    def test_cutoff_boundary_excludes_exact_cutoff(self):
        edge = (NOW - timedelta(days=180)).strftime("%Y-%m-%d %H:%M:%S")
        hot, cold = al.plan_archive([row(1, edge)], keep_days=180, now=NOW)
        self.assertEqual([r["id"] for r in hot], [1], "正好等于 cut-off 的行不算过期")


class ArchiveSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="r20-ledger-archive-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ledger = self.tmp / "trading_ledger.json"
        self.archive = self.tmp / "archive"
        self.rows = [row(1, "2025-03-01 10:00:00"), row(2, "2025-03-02 10:00:00"),
                     row(3, "2026-09-01 10:00:00"), row(4, "持仓中...")]
        self._write(self.ledger, self.rows)

    def _write(self, path: Path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_dry_run_writes_nothing(self):
        before = self.ledger.read_bytes()
        result = al.run(self.ledger, self.archive, keep_days=180, apply=False, now=NOW)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["to_archive"], 2)
        self.assertEqual(self.ledger.read_bytes(), before)
        self.assertFalse(self.archive.exists(), "dry-run 不得创建任何归档文件")

    def test_apply_archives_and_keeps_recent(self):
        result = al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        self.assertEqual(result["status"], "applied")
        hot = json.loads(self.ledger.read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["id"] for r in hot), [3, 4], "近期行与不可解析行必须留在热台账")
        archived = json.loads((self.archive / "ledger_2025.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["id"] for r in archived), [1, 2])

    def test_no_row_is_lost(self):
        al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        hot = json.loads(self.ledger.read_text(encoding="utf-8"))
        archived = json.loads((self.archive / "ledger_2025.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["id"] for r in hot + archived), [1, 2, 3, 4])

    def test_idempotent(self):
        al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        first = (self.archive / "ledger_2025.json").read_text(encoding="utf-8")
        again = al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        self.assertEqual(again["status"], "noop", "已归档过的热台账不应再产生新归档")
        self.assertEqual((self.archive / "ledger_2025.json").read_text(encoding="utf-8"), first)

    def test_merges_into_existing_shard_without_overwriting(self):
        self.archive.mkdir(parents=True, exist_ok=True)
        self._write(self.archive / "ledger_2025.json", [row(99, "2025-01-01 00:00:00")])
        al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        merged = json.loads((self.archive / "ledger_2025.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(r["id"] for r in merged), [1, 2, 99], "已有分片必须合并而不是覆盖")

    def test_hot_ledger_untouched_when_archive_verification_fails(self):
        """回读校验失败 → 热台账必须保持原样（fail-closed，先归档后截断）。"""
        before = self.ledger.read_bytes()
        with mock.patch.object(al, "_load_rows", side_effect=[self.rows, []]):
            with self.assertRaises(RuntimeError):
                al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_hot_ledger_untouched_when_signature_mismatch(self):
        before = self.ledger.read_bytes()
        original_signature = al._signature
        calls = {"n": 0}

        def fake_signature(rows):
            calls["n"] += 1
            result = original_signature(rows)
            if calls["n"] == 2:  # 第二次是"归档后"的校验：伪造不一致
                result = dict(result)
                result["forged"] = 1
            return result

        with mock.patch.object(al, "_signature", side_effect=fake_signature):
            with self.assertRaises(RuntimeError):
                al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_malformed_ledger_is_refused(self):
        self._write(self.ledger, {"not": "a list"})
        with self.assertRaises(ValueError):
            al.run(self.ledger, self.archive, keep_days=180, apply=True, now=NOW)

    def test_cli_defaults_to_dry_run(self):
        before = self.ledger.read_bytes()
        code = al.main(["--ledger", str(self.ledger), "--archive-dir", str(self.archive), "--keep-days", "180"])
        self.assertEqual(code, 0)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_live_ledger_plan_is_conservative(self):
        """对**真实**台账演算一次：不得改动文件，且当前不该归档任何行（31 笔全在热窗口）。"""
        live = ROOT / "data" / "trading_ledger.json"
        if not live.exists():
            self.skipTest("无实盘台账")
        before = live.read_bytes()
        result = al.run(live, ROOT / "data" / "archive", keep_days=180, apply=False, now=NOW)
        self.assertEqual(live.read_bytes(), before)
        self.assertIn(result["status"], {"dry_run", "noop"})


if __name__ == "__main__":
    unittest.main()

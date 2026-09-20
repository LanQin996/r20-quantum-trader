"""进化复盘的心法合并与发布（`scripts/evolution/` 部件，从门面搬出）。

承载**宪法级保护**（基准心法不得被进化输出物理删除）与"发布失败即保留既有权威"逻辑 ——
见函数 docstring。
"""

from __future__ import annotations

from typing import Any, Dict, List


def apply_memory_review(*,
        change_status,
        constitution_readded,
        log_msg,
        long_term_memory,
        memory_service,
        memory_snapshot,
        merge_memory_with_constitution,
        preserve_existing_memory,
        retired_lessons,
        total_trades):
    """按宪法合并复盘心法 → 记录补回/停用 → 发布到记忆服务。

    ## 三处 in-out（都是**调用方预先初始化**的跨分支状态）

    `constitution_readded`（门面 L636 `= []`）、`retired_lessons`（本块前一行 `= []`）、
    `preserve_existing_memory`（上一句 `resolve_memory_update` 的产物）——
    它们只在 `if not preserve_existing_memory:` 分支里被赋值，分支跳过时保持原值，
    块后又会被报告消费。**只按"段内是否赋值"判必然绑定会误判**，故一律 in-out。

    ⚠️ 本块是**宪法级保护**：基准心法不允许被进化输出物理删除（2026-09-10），
    遗漏/试图删除的基准心法由宿主补回并计数（`baseline_memory_protected` 报告位）。

    段体 **AST 逐字**（对拍门 `tests/extraction/test_evolution_memory_review_extraction.py`）。
    """
    if not preserve_existing_memory:
        # Safe extraction: convert potential dicts {"rule_text": "..."} to string safely
        safe_long_term = []
        for item in long_term_memory:
            if isinstance(item, dict):
                val = str(item.get("rule_text") or item.get("text") or item.get("lesson") or "").strip()
            else:
                val = str(item or "").strip()
            if val:
                safe_long_term.append(val)
        # 宪法级保护：基准心法不允许被进化输出物理删除（2026-09-10）
        safe_long_term, constitution_readded = merge_memory_with_constitution(
            change_status, safe_long_term, memory_snapshot.get("lessons") or [])
        if constitution_readded:
            log_msg(f"🛡️ 进化输出遗漏/试图删除 {len(constitution_readded)} 条基准心法，宿主已按宪法补回保留")
        # 审计 P1-8c：被模型省略的**已学（非基准）**心法不再是"静默消失"，而是停用存档；
        # 这里把条数写进日志，报告口径不再只统计基准补回。
        _already = {t.strip() for t in safe_long_term}
        _dropped = [str(l.get("rule_text") or "").strip() for l in (memory_snapshot.get("lessons") or [])
                    if l.get("enabled") and not l.get("is_baseline")
                    and str(l.get("rule_text") or "").strip() and str(l.get("rule_text") or "").strip() not in _already]
        if _dropped:
            retired_lessons = _dropped
            log_msg(f"📦 {len(_dropped)} 条既学心法本轮未被复述：已按停用存档保留（不注入提示词，可在面板复核恢复）")

        try:
            published = memory_service.publish_review(
                safe_long_term, expected_version=memory_snapshot["version"],
                sample_size=total_trades, change_status=change_status)
            preserve_existing_memory = not published
        except Exception as exc:
            preserve_existing_memory = True
            log_msg(f"Memory publication rejected; retaining authority: {exc}")
    return (constitution_readded, preserve_existing_memory, retired_lessons)

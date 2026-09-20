"""主脑周期的**本地快照落盘**（B3 抽取，`ai_brain_trader.py` 第三块）。

从 `scripts/ai_brain_trader.py::execute_batch_ai_brain_cycle` 搬出三处"写本地快照"：

| 函数 | 内容 |
|---|---|
| `update_factor_library_snapshot` | 因子库自更新（`sys.path` 追加 + `factor_library.update_factor_library()`，失败仅告警） |
| `write_calculus_snapshot` | 演算快照 `tmp + os.replace` 原子替换（失败仅告警） |
| `write_prompt_snapshot` | 实时提示词快照落盘（Web 透明检视用；同样 tmp + replace） |

## 为什么这三块能安全搬

- 三段**都是纯副作用**（0 个输出、0 个 `return`）—— 失败被各自的 `try/except` 吞掉并打印告警，
  控制流不依赖它们的产物；
- 段体 **AST 逐字**（对拍门 `tests/extraction/test_brain_snapshots_extraction.py`）；
- 全部自由名**同名 kw-only 入参**（`os`/`sys`/`json` 亦按名注入）⇒ 门面调用期解析。

⚠️ `write_calculus_snapshot` 段内的 `with open(tmp_calc, "w") as f:` 是**段内局部句柄**：
抽取分析器曾把它当"段后仍被读的输出"（段后另一处 `with ... as f` 也是同名句柄）。
判据已修正为"段后首次读之前严格已有同名绑定 ⇒ 剪掉"，否则 `open` 抛错那条路径
回传 `f` 会 `UnboundLocalError`。
"""
from __future__ import annotations


def update_factor_library_snapshot(*,
        WORKSPACE_DIR,
        os,
        sys):
    try:
        sys.path.append(os.path.join(WORKSPACE_DIR, "scripts"))
        import factor_library
        factor_library.update_factor_library()
    except Exception as e:
        print(f"[AI Brain Batch] Factor Library update warning: {e}")


def write_calculus_snapshot(*,
        CALCULUS_SNAPSHOT_FILE,
        json,
        os,
        packages,
        time_str):
    try:
        calculus_snapshot = {
            "timestamp": time_str,
            "engine": "causal-calculus-v1",
            "instruments": [
                {"name": p.get("name"), "instId": p.get("instId"), "calculus": p.get("calculus", {})}
                for p in packages
            ],
        }
        tmp_calc = CALCULUS_SNAPSHOT_FILE + ".tmp"
        with open(tmp_calc, "w", encoding="utf-8") as f:
            json.dump(calculus_snapshot, f, ensure_ascii=False, indent=2)
        os.replace(tmp_calc, CALCULUS_SNAPSHOT_FILE)
    except Exception as exc:
        print(f"[AI Brain] Calculus snapshot warning: {exc}")


def write_prompt_snapshot(*,
        AI_LAST_PROMPT_FILE,
        _build_effective_prompt_text,
        effective_system_prompt,
        os,
        prompt,
        time_str):
    try:
        tmp_prompt = AI_LAST_PROMPT_FILE + ".tmp"
        with open(tmp_prompt, "w", encoding="utf-8") as f:
            f.write(_build_effective_prompt_text(
                effective_system_prompt=effective_system_prompt, policy_version="",
                time_str=time_str, prompt=prompt))
        os.replace(tmp_prompt, AI_LAST_PROMPT_FILE)
    except Exception:
        pass


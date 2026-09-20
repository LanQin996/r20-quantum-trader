"""微积分 / 定积分 / 概率论引擎的实现（结构优化阶段 4·B3 第四十五刀起）。

门面仍是单文件 `scripts/calculus_engine.py` —— 但它现在**只有文档与再导出**
（477 → 71 行，零函数定义）。所有实现都在本子包。

## 模块清单

| 模块 | 职责 | 依赖 |
|---|---|---|
| `primitives.py` | 数学原语（`_finite` / `_ema` / `_diff` / `_normalise` / `_sign` / `_normal_cdf`）+ 4 个状态分级器 | 无 |
| `calculate.py` | 定积分 / 概率论 / 因果微积分 / 多周期聚合（4 个 `calculate_*`） | 只依赖 `primitives` |
| `regime.py` | 宏观态势自适应识别引擎（`detect_macro_market_regime`） | 依赖 `primitives` |

依赖**单向无环**：`primitives` ← `calculate` ← 门面。
子模块**不得**反向 import 门面（否则 `ImportError: partially initialized module`，
第四十三刀已实测过环的后果）。

## 导入方式（本仓约定）

`scripts/` **不是** Python 包（无 `__init__.py`）。真实调用方一律以
`sys.path` 含 `scripts/` 为前提、用**裸名**导入：

    from calculus_engine import calculate_multi_timeframe

而本子包被门面以**双模导入**引用（`try: from scripts.calculus...` /
`except ImportError: from calculus...`）—— 已实测三种 `sys.path` 布局均可解析。

## ⚠️ 铁律：抽实现只能**复制**，不能重写

第四十五刀第一版 `primitives.py` 我按"印象"写出了 10 个函数，
逐字对拍后发现 **8 个是错的**（`_sign` 漏了 `0.08` 阈值、
`_normalise` 签名完全不同、`classify_regime` 的形参个数都错了……）。
**那会静默改坏交易逻辑却"看起来正常"。**

故 `tests/extraction/test_calculus_package_extraction.py::VerbatimCopyTest`
用 AST 从 git 历史取出原函数，逐字比对新模块里的同名函数 ——
**抽实现必须验证"逐字相同"，不能只验证"行为像我预期的那样"。**

## ⚠️ 这些阈值是业务语义，不是可调参数

`0.08`（`_sign`）、`1.8`/`0.8`（冲击）、`0.12`/`0.15`/`0.10`（分级）、
`1.5`（曲率）、`2.5`（偏离面积）、`3.0`（峰度）、`70.0`（概率百分比）、
`0.6`（偏度）—— 全部原样保留，**不抽常量、不改数值**。
"""

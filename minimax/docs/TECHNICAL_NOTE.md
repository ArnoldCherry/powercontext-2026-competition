# 一页技术说明：规有所依 · SDD 上下文管理系统

## 1. 模块结构

```text
sample_ledger/                 小型账本业务工程（CSV → 月报）
  specs/reporting.md           需求规格（REQ-REPORT-001..005）
  specs/traceability.json      需求 → 代码符号 / 测试的追踪矩阵
  tasks/TASK-001.md            本次开发任务
sdd_context/                   上下文模块（CLI）
  parsing.py                   规格 Markdown、Python AST 符号、pytest 测试与标记解析
  ingest.py                    文档/规格/关键词/追踪/符号/测试写入 seekdb
  context.py                   任务检索、来源校验、查询日志、PowerContext 状态读取
  powercontext.py              PowerContext HTTP 客户端（scope、Source、Memory）
  minimax_client.py            MiniMax OpenAI 兼容调用、完整文件输出、diff/record
  runner.py                    pytest 执行、git diff/版本、哈希
  cli.py                       init/import/context/baseline/assist/verify/status/spec-update/reset
```

## 2. 上下文选择方式

1. **规格检索**：规格关键词来自追踪矩阵，按任务文本命中关键词的长度和数量打分；`sdd_spec_keywords` 与任务文本在 SQL 结果集上匹配，取最多 8 条规格。
2. **代码定位**：`sdd_spec_links` 将需求映射到 `ledger/report.py::monthly_report`；同一符号关联的其它需求用于扩展回归面。
3. **测试分层**：`relation=verifies` 的测试为目标测试（必须改后通过），`relation=regression` 的测试为兼容性测试（改前改后都必须通过）；`@pytest.mark.sdd` 与追踪矩阵交叉校验，冲突时导入失败。
4. **真实来源校验**：输出前读取源文件，比对文件 SHA-256 和 `line_start..line_end` 切片；规格正文、符号源码、测试源码都带来源路径、行号、哈希。任何不一致都会在 `validation.issues` 中报告。
5. **可审计查询**：每次 `context` 记录 SQL、参数、命中需求、上下文 SHA-256 到 `sdd_query_log`；更新规格并重新导入后，规格哈希与上下文 SHA-256 都会变化。

## 3. 三项技术的分工

| 技术 | 承担的作用 | 实际证据 |
| --- | --- | --- |
| seekdb | 保存规格、关键词、追踪关系、代码符号、测试、导入批次和查询日志；上下文中的每个来源都来自真实 SQL 查询 | `evidence/01-import.json`、`evidence/context/initial.json` 的 `query.statements`、`sdd_query_log` |
| MiniMax | 接收检索出的规格/代码/目标与回归测试/基线失败日志，生成完整的 `ledger/report.py`；本次使用 `MiniMax-M3`，1 次尝试（4515 tokens）通过；模块保存请求、脱敏响应、usage、模型输出与 diff | `evidence/minimax/attempt-01/request.json`、`response.json`、`final/record.json`、`final/diff.patch` |
| PowerContext | 用 Source 保存详细阶段证据，用 Memory 保存 `context/baseline/patch/validation` 四阶段摘要；重启后 `status`/`context` 读取并展示已完成/待办/验证结果 | `evidence/restart/status.json`、`evidence/restart/context-after-restart.json` |

## 4. 一次开发任务的数据流

```text
TASK-001（退款分离 + min_amount）
  → 关键词命中 REQ-REPORT-004 / 005（并扩展 001/002/003）
  → 代码符号 ledger/report.py::monthly_report
  → 目标测试 3 个（verifies）+ 回归测试 8 个（regression）
  → 修改前：目标测试失败、回归通过（baseline）
  → MiniMax 根据此上下文输出完整 report.py（记录 attempt-01）
  → 修改后：同一目标测试与全部回归通过（verify）
  → PowerContext 记录验证结果并可在重启后读取
```

基线测试失败是功能缺失导致的断言失败（3 failed），不是依赖缺失或无法启动；改后目标/回归/全量分别为 3/8/11 passed。测试文件在改前改后哈希一致，未修改断言或预期。

## 5. 设计取舍与限制

- 规格与代码/测试的关联采用显式追踪矩阵，而不是仅靠相似度：适合 SDD 的可审计要求，关键词只负责从任务找到需求，需求再定位代码和测试。
- 全部来源都做文件哈希与行号切片校验；规格更新后需要重新导入，否则 `context` 会报告来源不一致。
- MiniMax 采用“完整文件输出 + 本地应用”的方式，避免 diff 格式差异；记录保存模型输出哈希、应用后文件哈希和最终 git diff 哈希，便于核对。
- 当前样例刻意保持小体量（1 个业务模块、11 个测试），重点验证流程与证据链，而不是模型能力上限。
- 已提交证据在 Windows 11 + Python 3.11 上生成；PowerContext 1.2.0 的 Linux 支持是官方推荐路径。

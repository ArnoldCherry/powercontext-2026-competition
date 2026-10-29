# 规有所依：SDD 任务上下文管理系统（MiniMax 赛道）

本作品是一个可运行的规格驱动开发（SDD）上下文模块，配套一个小型账本月报工程。模块把规格、代码符号和测试导入 seekdb，按任务检索出**适用规格、待改文件/符号、功能测试和回归测试**，再把上下文交给 MiniMax 完成一次真实的局部代码修改；任务进展和验证结果保存在 PowerContext，重启后继续使用。

- 样例工程：`sample_ledger/`（账本 CSV 与月报）
- 上下文模块：`sdd_context/`（CLI，Python 3.11+）
- 规格与追踪矩阵：`sample_ledger/specs/reporting.md`、`sample_ledger/specs/traceability.json`
- 任务：`sample_ledger/tasks/TASK-001.md`
- 所有验证证据：`evidence/`

## 1. 六项硬门槛对应

| 验收项 | 本作品的证据 |
| --- | --- |
| 能够运行 | 本 README 的从零步骤；`python scripts/replay_verification.py` 可只依赖 Python 复现改前失败/改后通过 |
| MiniMax 实际参与 | `evidence/minimax/attempt-01/request.json`、`response.json`、`model-output.py`、`final/record.json`、`final/diff.patch`；请求中包含实际检索上下文，最终文件与 diff 对应 |
| 上下文正确 | `evidence/context/initial.json` 包含真实规格、代码、测试的路径与行号；`evidence/spec-update/summary.json` 证明规格更新并重新导入后上下文使用新内容 |
| 数据库实际参与 | `evidence/01-import.json`、`evidence/context/initial.json` 的 SQL 与结果；seekdb 中保存规格/符号/测试/查询日志 |
| 状态能够复用 | `evidence/restart/status.json`、`evidence/restart/context-after-restart.json`；PowerContext 重启后读取到 context/baseline/patch/validation 四个阶段 |
| 代码修改有效 | `evidence/baseline/`（3 个目标测试因功能缺失失败、8 个回归通过）；`evidence/after/`（同一组目标测试与全部回归通过）；测试文件哈希在改前改后一致 |

## 2. 技术栈与版本

| 技术 | 实际使用 |
| --- | --- |
| MiniMax | OpenAI 兼容 HTTP API（`https://api.minimaxi.com/v1/chat/completions`）；本次证据使用 `MiniMax-M3`，1 次尝试（4515 tokens，8.2s），开发记录保存请求、脱敏后的最终回答和 usage |
| seekdb | `5.7.25-OceanBase seekdb-v1.4.0.0`，独立数据库 `sdd_context`；MySQL 协议，PyMySQL |
| PowerContext | `powercontext[cli,server]==1.2.0`，本地 HTTP Server（SQLite 持久化），显式 Memory + Source 记录 |
| Python | 3.11.9（证据运行环境；代码要求 3.11+） |
| pytest | 8.x |

上下文模块不依赖 PowerContext 的 Python 包，只通过 HTTP 调用 Server，因此数据库、PowerContext 和 MiniMax 都可以替换为兼容服务。

## 3. 架构

```text
sample_ledger/
  specs/reporting.md + specs/traceability.json
  ledger/*.py（AST 解析符号）
  tests/test_*.py（pytest 标记）
        │
        │  python -m sdd_context import
        ▼
seekdb  sdd_context
  sdd_documents / sdd_spec_entries / sdd_spec_keywords
  sdd_spec_links / sdd_code_symbols / sdd_test_cases
  sdd_import_runs / sdd_query_log
        │
        │  python -m sdd_context context
        ▼
上下文选择（关键词 + 需求-代码/测试追踪 + 同行号校验）
        │
        ├── MiniMax：把规格、代码、目标/回归测试、基线失败日志作为 prompt，输出完整 report.py
        └── PowerContext：Source + Memory 保存 context/baseline/patch/validation
```

### 3.1 上下文选择规则

1. `sdd_spec_keywords` 中的关键词与任务文本匹配，按命中关键词长度和数量排序，取最多 8 条规格。
2. `sdd_spec_links` 把需求映射到代码符号和测试；同一代码符号关联的其它需求会补充回归测试。
3. 测试标记（`@pytest.mark.sdd("REQ-...", kind="target")`）与追踪矩阵交叉校验，冲突时导入失败。
4. 输出前重新读取源文件，校验文件 SHA-256 和行号切片；任何不一致都会使 `validation.verified=false`。
5. 每次查询写入 `sdd_query_log`，记录 SQL、参数、命中的需求、上下文 SHA-256。

## 4. 从零运行

### 4.1 前置条件

- Python 3.11+
- 一个可用的 seekdb 或 OceanBase（MySQL 协议）
- 能访问 PyPI 和 MiniMax API
- MiniMax API Key（只在重新生成修改时需要；复现已提交的测试结果不需要）

### 4.2 安装

```bash
cd minimax
python -m venv .venv
# Linux/macOS
. .venv/bin/activate
# Windows Git Bash
# . .venv/Scripts/activate

pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，至少填写 `SEEKDB_PASSWORD`；如果要重新调用 MiniMax，再填写 `MINIMAX_API_KEY`。`.env` 已被 `.gitignore` 忽略，不会提交。

### 4.3 启动 seekdb

如果本机没有可用的 seekdb，启动一个专用实例（不要复用其它作品的数据库）：

```bash
docker run -d --name sdd-seekdb \
  -p 2881:2881 \
  -e ROOT_PASSWORD='choose-a-strong-password' \
  -e MEMORY_LIMIT=2G \
  oceanbase/seekdb:latest
```

把同样的密码写入 `.env` 的 `SEEKDB_PASSWORD`。如果 2881 已被占用，把 `-p 2881:2881` 改成 `-p 2882:2881`，并设置 `SEEKDB_PORT=2882`。

模块只创建和使用 `SEEKDB_DATABASE` 指定的数据库（默认 `sdd_context`），不会改写其它库。

### 4.4 启动 PowerContext

准备一个专用数据目录，保持 Server 在前台运行：

```bash
mkdir -p runtime/powercontext-home
export POWERCONTEXT_HOME="$PWD/runtime/powercontext-home"
.venv/bin/powercontext server run --port 17429 --no-env-file
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force runtime\powercontext-home | Out-Null
$env:POWERCONTEXT_HOME = "$PWD\runtime\powercontext-home"
.venv\Scripts\powercontext.exe server run --port 17429 --no-env-file
```

PowerContext 默认使用 SQLite；同一 `POWERCONTEXT_HOME` 重启后会读回同一份数据。若端口冲突，修改 `.env` 的 `POWERCONTEXT_URL`。

### 4.5 检查配置

```bash
python -m sdd_context check
```

预期：`seekdb.ok=true`、`powercontext.ok=true`；未配置 Key 时 `minimax.api_key_set=false`。输出不会包含密码或 Key。

### 4.6 初始化数据并生成上下文

```bash
python -m sdd_context reset --yes          # 可选：从空状态开始
python -m sdd_context init
python -m sdd_context import
python -m sdd_context context --output evidence/context/current.json --record
```

预期 `import`：5 条规格、7 个代码符号、11 个测试、16 条追踪链接；`context`：1 个待改代码符号、3 个目标测试、8 个回归测试，`validation.verified=true`。

当前仓库的代码已经是修改后的版本；**修改前的上下文**保存在 `evidence/context/initial.json`（基线提交 `e1ba2abc` 时生成）。要完整复现修改前流程，请使用基线提交：

```bash
git switch -c replay-minimax e1ba2abcea315b23dba64891af61a0443ef52bfe
```

## 5. 验证代码修改（无需 MiniMax Key）

最快的复现方式：

```bash
python scripts/replay_verification.py
```

脚本会：

1. 备份当前 `sample_ledger/ledger/report.py`；
2. 用 `evidence/baseline/code/ledger/report.py` 覆盖为修改前快照；
3. 运行 3 个目标测试（应失败）和 8 个回归测试（应通过）；
4. 恢复当前代码，运行目标、回归和全量测试（应全部通过）；
5. 校验测试文件哈希未变化，写出 `evidence/replay/summary.json` 和日志。

脚本无论成功失败都会恢复当前代码。它不需要 seekdb、PowerContext 或 MiniMax Key。

> 已提交的实际前后证据见 `evidence/baseline/baseline.json` 和 `evidence/after/verify.json`，其中记录命令、退出码、通过/失败数和文件哈希。

## 6. 完整重跑 MiniMax 开发流程

以下步骤会覆盖 `evidence/` 中的开发记录；建议先复制工作区，或使用独立的 `--evidence` 目录（全局参数要放在子命令前）：

```bash
# 从基线提交开始
git switch -c replay-minimax e1ba2abcea315b23dba64891af61a0443ef52bfe

# 使用独立证据目录，避免覆盖已提交记录
python -m sdd_context --evidence runtime/replay-evidence reset --yes
python -m sdd_context --evidence runtime/replay-evidence init
python -m sdd_context --evidence runtime/replay-evidence import
python -m sdd_context --evidence runtime/replay-evidence context \
  --output runtime/replay-evidence/context/initial.json --record
python -m sdd_context --evidence runtime/replay-evidence baseline \
  --context runtime/replay-evidence/context/initial.json --record
```

确认基线有效后，配置 MiniMax 并执行修改：

```bash
export MINIMAX_API_KEY='...'
# 可选：export MINIMAX_BASE_URL='https://api.minimaxi.com/v1'
# 可选：export MINIMAX_MODEL='MiniMax-M3'
python -m sdd_context --evidence runtime/replay-evidence check --minimax
python -m sdd_context --evidence runtime/replay-evidence assist \
  --context runtime/replay-evidence/context/initial.json --attempts 2
python -m sdd_context --evidence runtime/replay-evidence verify \
  --context runtime/replay-evidence/context/initial.json --record
```

`assist` 的行为：

- 只允许 `ledger/report.py` 一个目标文件；如果该文件已有未提交修改，直接拒绝。
- 把上下文中的规格、代码、3 个目标测试、8 个回归测试和基线失败日志发给 MiniMax。
- 要求模型返回完整的新文件；保存每次请求/响应、模型输出、测试日志。
- 模型输出必须通过语法检查和目标+回归测试；失败会带着测试输出重试，最多 `--attempts` 次；全部失败会恢复原文件。
- 成功后写出 `evidence/minimax/final/record.json`、`before.py`、`after.py`、`diff.patch`，并记录 PowerContext `patch` 阶段。

`verify` 会重新运行目标、回归和全量测试，检查测试文件哈希、目标/回归集合是否与基线一致、git diff 是否非空，并核对 MiniMax 记录中的文件哈希、diff 哈希和上下文哈希；全部通过才记录 `validation` 阶段。

## 7. PowerContext 状态复用

`context`、`baseline`、`assist`、`verify` 分别写入 `sdd-context`、`sdd-baseline`、`sdd-patch`、`sdd-validation` 四类 Memory，并把详细证据写成 Source。停止 PowerContext Server（Ctrl-C）后，用同一个 `POWERCONTEXT_HOME` 重启，再执行：

```bash
python -m sdd_context status
```

预期 `completed` 包含四个阶段，`pending` 为空；`status` 同时返回 PowerContext 的 search 与 prepared context。实际证据：

- `evidence/restart/status.json`
- `evidence/restart/context-after-restart.json`

复现重启时，请确认 `.env` 的 `POWERCONTEXT_URL` 指向同一个 Server 和 `/runtime/powercontext-scope.json` 中的 scope。

## 8. 规格更新与重新导入

最终仓库中的 `sample_ledger/specs/reporting.md` 已包含更新后的 REQ-REPORT-004（新增“退款超过支出时 net_total 不得截断为 0”）。更新前后的对比见：

- `evidence/spec-update/reporting.before.md`
- `evidence/spec-update/reporting.updated.md`
- `evidence/spec-update/summary.json`（spec SHA、正文、上下文 SHA 对比）
- `evidence/spec-update/after.json`（更新后重新查询的上下文）

从基线复现：

```bash
git show e1ba2abcea315b23dba64891af61a0443ef52bfe:minimax/sample_ledger/specs/reporting.md \
  > sample_ledger/specs/reporting.md
python -m sdd_context import
python -m sdd_context context --output runtime/pre-update.json
python -m sdd_context spec-update \
  --updated-file demo/reporting.updated.md \
  --before-context runtime/pre-update.json
```

该命令会写回更新后的规格并重新导入，输出旧/新 spec 哈希、正文和上下文哈希，证明上下文使用的是新内容而不是固定答案。

## 9. 证据索引

| 路径 | 内容 |
| --- | --- |
| `evidence/00-reset.json`、`00-init.json`、`01-import.json` | 空状态、初始化、导入（含 seekdb 版本、文档哈希、计数） |
| `evidence/context/initial.json` / `.md` | 修改前上下文；5 条规格、`ledger/report.py::monthly_report`、3 目标 + 8 回归、真实行号 |
| `evidence/baseline/baseline.json` | 基线校验结果、git HEAD `e1ba2abc`、命令、退出码、测试哈希、代码快照哈希 |
| `evidence/baseline/target-tests.txt` | 修改前目标测试 3 failed / 0 passed |
| `evidence/baseline/regression-tests.txt` | 修改前回归测试 8 passed |
| `evidence/baseline/code/ledger/report.py` | 修改前代码快照 |
| `evidence/minimax/attempt-01/request.json` | MiniMax 实际请求（含检索上下文，不含 Key） |
| `evidence/minimax/attempt-01/response.json` | MiniMax 最终回答、usage、时延；`<think>` 段已脱敏并记录 `raw_content_sha256` |
| `evidence/minimax/final/record.json` | 模型、文件前后哈希、diff 哈希、上下文哈希、尝试记录 |
| `evidence/minimax/final/diff.patch` | MiniMax 应用后的代码 diff |
| `evidence/after/verify.json` | 修改后目标/回归/全量测试，测试哈希与 diff 对应校验 |
| `evidence/diff/final.diff` | MiniMax 代码 diff（`verify` 对应校验使用） |
| `evidence/diff/final-with-spec.diff` | 包含规格更新的完整样例 diff（代码 + 规格） |
| `evidence/restart/status.json` | PowerContext 重启后读取的四个阶段 |
| `evidence/database/query-log.json` | seekdb 表计数、版本和每次上下文查询的 SQL/参数/命中需求 |
| `evidence/spec-update/summary.json` | 规格更新前后哈希/正文/上下文对比 |
| `evidence/replay/summary.json` | 仅用 Python 的改前失败/改后通过复现 |

## 10. 已知限制

- MiniMax 生成具有非确定性；已提交的 `record.json` 是实际运行记录，`verify` 不要求模型重新生成相同补丁。为遵守不提交私有思维链的约定，`response.json` 中的 `<think>` 段已替换为 `[reasoning redacted]`，同时保存原始内容的 SHA-256。
- PowerContext 1.2.0 的 Windows 支持是 experimental；本证据在 Windows 11 + Python 3.11.9 上运行，Linux 是官方支持平台。
- seekdb 证据使用了已运行的共享实例中的独立数据库 `sdd_context`；仓库不包含数据库密码。评审请使用 4.3 的专用实例和 `.env`。
- 样例工程是刻意缩小的业务工程，只覆盖月报、CSV 解析和 CLI 测试；不包含 UI。
- `evidence/context/initial.json` 是修改前上下文；在最终代码上重新运行 `context` 会看到已修改的 `monthly_report`，这是预期的。

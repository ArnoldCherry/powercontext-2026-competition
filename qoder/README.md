# 生态赛交付看板

> Qoder × OceanBase 2026 生态赛 · Qoder 赛道作品  
> 用一块看板记录「任务 → 证据 → 提交 → 得分」，用 PowerContext 记住「偏好 → 进展 → 下一步」。
>
> 展示站：<https://relay-board-wxl92p9nfrd.qoder.zone>（Qoder Sites，公开访问）

## 一、解决的问题

并行参加多个赛题时，任务信息散落在提示词、聊天记录、分支、提交页和验收报告里。常见问题是：

- 不知道哪些任务已经可以提交、哪些卡住了；
- 提交后忘记回填平台任务编号、得分和证据链接；
- 换一个 Agent 或隔天回来，要重新翻聊天记录才能恢复上下文；
- 只记在脑子里，容易出现“代码推了但没提交”“证据找不到”的漏交。

本作品把任务与提交证据集中到一个可运行的本地面板，并把用户偏好、任务进展写入 **真实运行的 PowerContext**。应用会自行启动和停止受管理的 PowerContext 服务，不依赖开发机预先运行的 `127.0.0.1:18801`。

## 二、功能

- **任务列表**：任务编号、标题、方向、难度、优先级、负责人、状态、下一步。
- **筛选与搜索**：按方向、难度、状态筛选，按任务/仓库/下一步等关键词搜索。
- **提交证据字段**：仓库、分支、Commit、提交网址、平台任务编号、得分、证据链接。
- **风险优先**：阻塞、待提交、进行中、待开始、已提交、已评分按风险排序；顶部提示需要关注的任务。
- **状态即记忆**：修改状态、下一步、证据、提交信息时，自动向 PowerContext 写入一条进展记忆。
- **偏好复用**：保存看板标题、默认方向和默认状态到 PowerContext；下次打开自动恢复。
- **记忆接力**：右侧时间线展示最近的进展记忆，包含任务、状态、下一步和时间。
- **自包含运行**：单命令启动，自动拉起真实 PowerContext、自动播种、自动选择空闲端口。
- **可验证**：接口验证、重启验证、浏览器交互验证、异常场景验证。

## 三、架构与自包含运行时

| 组成 | 作用 |
| --- | --- |
| Qoder | 需求拆解、后端/前端编码、调试、测试、Qoder Sites 展示站与发布 |
| PowerContext 1.2.0 | 真实记忆服务：保存并检索偏好（`[preference]`）与进展（`[progress]`）；SQLite 存储 + FTS 检索，不依赖模型 API |
| Managed Runtime | 应用启动时自动拉起 PowerContext 子进程，选择空闲 loopback 端口，等待 `/health/ready`；应用退出时停止子进程 |
| SQLite | 看板业务数据（任务、字段、证据链接）与 PowerContext 数据分别持久化到 `data/` |
| FastAPI + uvicorn | 提供 `/api/*` 接口与静态页面 |
| Playwright | 可选的浏览器交互与截图验证 |

### 启动链路

1. `python -m board` 启动看板，`PowerContextRuntime` 默认以 `managed` 模式工作。
2. 运行环境必须已安装 `requirements.txt`（`powercontext[cli,server]==1.2.0`）。
3. Runtime 在 `data/powercontext/` 创建独立数据目录，选择空闲 loopback 端口，启动真实 `powercontext server run` 子进程，并轮询 `/health/ready`。
4. 服务就绪后，看板自动初始化演示任务、偏好记忆与进展记忆；页面读取同一 Scope 的记忆。
5. 应用退出时终止受管理的 PowerContext 子进程；SQLite 数据保留，下次启动读回。

### PowerContext 进入核心流程

1. 页面打开时，`/api/bootstrap` 从 PowerContext 读取最新偏好和进展，前端用偏好初始化标题与筛选，用进展渲染“最近进展”。
2. 任务状态/证据/提交信息变化时，`PATCH /api/tasks/{id}` 同步写入 `[progress]` 记忆。
3. 用户保存偏好时，`PUT /api/preferences` 写入 `[preference]` 记忆。
4. 验证脚本通过 PowerContext 的 `memory/search` 按 `run_id` 检索写入内容；**停止全部项目进程并重启**后仍能读回。

### 明确的异常信息

- **受管理端口被占用**：`startup_error` 显示“PowerContext 端口 X 已被占用”，并提示 `POWERCONTEXT_PORT=0` 自动选择或释放端口。
- **外部 PowerContext 不可达**：`startup_error` 显示“外部 PowerContext 不可达：URL（连接错误）”，并提示改用 `managed` 模式。
- **看板端口被占用**：启动前直接输出“看板端口 X 已被占用，请设置 BOARD_PORT”，退出码 2。
- **运行时崩溃**：`/api/health` 的 `runtime.error`、`memory.error` 与 `data/powercontext-managed.log` 给出原因。
- **记忆不可用**：页面仍可查看任务，但顶部显示“记忆离线”，不会静默假装成功。

## 四、版本与环境

- Python 3.11+（本机实测 3.14.3，Windows 10/11）
- PowerContext `1.2.0`（`powercontext[cli,server]`，服务端 SQLite 模式、Access 关闭、无模型 API）
- 依赖见 [requirements.txt](requirements.txt)；Playwright 见 [requirements-dev.txt](requirements-dev.txt)
- 默认端口：看板 `18800`；受管理 PowerContext 自动选择空闲 loopback 端口
- 数据目录：`data/`（看板 SQLite、PowerContext SQLite、运行日志、验证状态），已加入 `.gitignore`

配置项（均为可选，默认值即可跑通）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `BOARD_HOST` | `127.0.0.1` | 看板监听地址 |
| `BOARD_PORT` | `18800` | 看板监听端口 |
| `BOARD_DB` | `data/board.db` | 看板 SQLite 文件 |
| `BOARD_AUTO_SEED` | `1` | 首次启动自动播种演示任务与记忆 |
| `POWERCONTEXT_MODE` | `managed` | `managed` 自动启动；`external` 连接现成服务 |
| `POWERCONTEXT_PORT` | `0` | `0` 自动选择空闲端口；非 0 时端口冲突会明确报错 |
| `POWERCONTEXT_DATA_DIR` | `data/powercontext` | 受管理 PowerContext 的 SQLite 数据目录 |
| `POWERCONTEXT_URL` | 空 | 仅 `external` 模式使用 |
| `POWERCONTEXT_TOKEN` | 空 | 仅外部鉴权服务使用，示例中保持占位 |
| `BOARD_SCOPE_TITLE` | `生态赛交付看板` | PowerContext Scope 标题 |
| `BOARD_REQUEST_TIMEOUT` | `8` | 调用 PowerContext 的超时秒数 |

本作品不需要任何真实 API Key、密码或平台凭据；仓库不含凭据。

## 五、启动

以下命令均在 `qoder/` 目录执行。

```bash
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m board
# Linux / macOS
# .venv/bin/python -m pip install -r requirements.txt
# .venv/bin/python -m board
```

打开 <http://127.0.0.1:18800>。首次启动会自动：

1. 在后台拉起真实 PowerContext（独立 SQLite 数据目录、自动选空闲端口）；
2. 初始化 11 个演示任务、偏好记忆和 6 条进展记忆；
3. 打开“记忆在线”的看板。

不需要另开终端启动 PowerContext，也不需要预先运行 `scripts/seed.py`。首次启动需要等待 PowerContext 就绪（通常数秒，较慢机器可能十几秒）。停止时直接结束 `python -m board` 即可，受管理的 PowerContext 会一并停止。

可选操作：

```bash
# 重置演示数据（先停止看板）
.venv\Scripts\python scripts\seed.py --reset

# 连接已有的外部 PowerContext（非默认，不推荐用于评测）
POWERCONTEXT_MODE=external POWERCONTEXT_URL=http://127.0.0.1:18801 .venv\Scripts\python -m board
```

## 六、验证样例

### 样例 A：自包含启动与偏好/进展读写

```bash
.venv\Scripts\python scripts\verify.py
```

脚本会：

1. 读取 `/api/health`，确认数据库与 PowerContext 在线，并确认 PowerContext 由应用自动管理（`mode=managed`、有 managed PID）；
2. 确认首次启动自动播种完成、无启动/播种错误；
3. 读取种子任务、偏好和进展；
4. 写入临时偏好（方向 Qoder、状态 ready），重新加载并确认偏好被复用，再恢复原偏好；
5. 把 `DEMO-01` 更新为待提交，写入带 `run_id` 的下一步和证据；
6. 通过 `/api/memory/search?q=<run_id>` 确认 PowerContext 能检索到这条进展；
7. 再次 `/api/bootstrap`，确认刷新后任务与进展仍在。

预期：16 项全部 `[PASS]`，生成 `evidence/verification_write.json`。

### 样例 B：停止全部项目进程后重启读回

1. 完成样例 A；
2. 停止 `python -m board`（受管理 PowerContext 同时停止；确认 18800 与 PowerContext 随机端口均无监听）；
3. 重新执行 `.venv\Scripts\python -m board`；
4. 执行：

```bash
.venv\Scripts\python scripts\verify.py --after-restart
```

验证内容：

- 看板 SQLite 中 `DEMO-01` 的状态和下一步仍在；
- 新的受管理 PowerContext 进程启动后，仍能按原 `run_id` 检索到进展；
- 偏好仍能从同一 Scope 恢复；
- 健康检查显示 `mode=managed`、`startup_error`/`seed_error` 为空。

预期：8 项全部 `[PASS]`，生成 `evidence/verification_after_restart.json`。

### 样例 C：浏览器交互（可选）

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python scripts\ui_check.py
```

脚本使用本机 Edge/Chrome（无头模式）检查：页面加载、状态切换、阻塞筛选、PDF 搜索、编辑弹窗、偏好保存后刷新恢复、移动端无横向溢出、控制台无错误。截图输出在 `evidence/`。

### 样例 D：异常场景

- 受管理端口被占用：用 `POWERCONTEXT_PORT` 指定已占用端口启动，`/api/health` 返回明确 `startup_error`，应用仍可打开任务列表；
- 外部服务不可达：`POWERCONTEXT_MODE=external` + 不可达 URL，健康检查返回明确连接错误；
- 看板端口被占用：`python -m board` 在启动前输出明确提示并返回 2。

实跑证据：`evidence/error_cases.json`。

已有实跑结果：

- `evidence/verification_write.json`：16 项全部通过；
- `evidence/verification_after_restart.json`：8 项全部通过；
- `evidence/ui_check.json`：18 项全部通过，控制台错误 0；
- `evidence/clean_install_health.json`：全新虚拟环境安装 `requirements.txt` 后单命令启动，PowerContext 由应用管理，`127.0.0.1:18801` 未监听；
- `evidence/error_cases.json`：端口占用、外部不可达、看板端口冲突的明确提示；
- `evidence/performance.json`：30 轮本地实测；
- `evidence/ui-overview.png`、`ui-edit-dialog.png`、`ui-filter-blocked.png`、`ui-mobile.png`：界面截图；
- `evidence/qoder-session.md`、`qoder-session.png`：Qoder 开发会话导出与记录截图；
- `evidence/site_public_check.json`、`site-live.png`：公网无痕访问验证。

## 七、数据与目录

```text
qoder/
├── board/
│   ├── __main__.py         # 单命令入口：python -m board
│   ├── app.py              # FastAPI 路由、静态页面、生命周期
│   ├── config.py           # 环境变量与路径
│   ├── memory.py           # PowerContext 记忆客户端
│   ├── runtime.py          # 受管理 PowerContext 启动/停止/就绪检查
│   ├── seed.py             # 任务与记忆种子
│   ├── store.py            # SQLite 任务存储
│   └── static/             # index.html / styles.css / app.js
├── scripts/
│   ├── seed.py             # 可选的显式重置/初始化
│   ├── verify.py           # 读写 + 重启恢复验证
│   └── ui_check.py         # Playwright 交互验证
├── tests/test_store.py     # SQLite 单元测试
├── site/                   # Qoder Sites 展示站静态源文件
├── evidence/               # 截图、验证 JSON、Qoder 会话记录
├── requirements.txt
└── data/                   # 运行时数据（不提交）
```

## 八、Qoder 开发记录

- 开发环境：Qoder Desktop（Qoder CN）
- 会话标题：`DS-06.md`；会话 ID：`e4f5152c-33a6-4e62-b861-48f089fe4bcf`
- Qoder 账号 UID：`01a18285-6bd1-78e0-abad-5d5ee482486e`
- 主要开发过程：需求拆解 → PowerContext 服务与记忆 API 验证 → 初版看板 → 平台反馈“数据或记忆无法正确读回和复用” → 干净环境复现 → 自包含运行时/自动播种 → 空环境安装与重启验证 → 展示站更新与发布
- 会话记录导出：`evidence/qoder-session.md` 与 `evidence/qoder-session.png`（由本机 Qoder 会话记录生成）
- 展示站源码：`site/`；线上地址：<https://relay-board-wxl92p9nfrd.qoder.zone>（Qoder Sites，任何人可访问）
- 公网无痕验证：HTTP 200、图片全部加载、无登录跳转、无横向溢出、控制台错误 0，证据见 `evidence/site_public_check.json`

> 评审可在 Qoder 中打开 `DS-06.md` 会话，结合 UID 核对开发过程与站点归属。

## 九、提交信息

- 仓库：<https://github.com/ArnoldCherry/powercontext-2026-competition>
- 分支：`fix/qoder-self-contained-memory`
- Commit：`<FIX_COMMIT_SHA>`（推送后回填）
- 初版实现：`89a1400c5d110ee685871ae6be174561658b0bc3`
- 代码目录：`qoder/`

## 十、已知限制

- 这是面向个人/小团队的本地单用户看板，未实现登录与多租户；如部署到公网需自行加鉴权。
- PowerContext 采用基础记忆能力：显式 `remember` + FTS 检索，不依赖生成/嵌入模型；因此进展检索使用 `[progress]` 标签与 ASCII `run_id` 作为稳定锚点。
- 受管理的 PowerContext 与看板同生命周期；应用退出时会停止子进程，SQLite 数据保留。若强制杀进程，可能留下孤儿 PowerContext；再次启动会使用同一数据目录。
- 种子任务中的分支、Commit 和状态用于演示筛选与证据记录；真实提交信息由使用者回填。
- 展示站是项目介绍与证据页，不会在浏览器中直接运行本地看板；看板需按第五节在本地启动。
- Windows 下 PowerContext 官方支持标记为 experimental，本作品已在 Windows 11 + Python 3.14.3 实跑通过。

## 十一、开源引用

- [PowerContext](https://github.com/oceanbase/powercontext)（Apache License 2.0）：记忆服务与 HTTP API。
- 本项目基于 OceanBase 2026 生态赛仓库的 `2026-competition-ecosystem` 分支开发，资产放在 `qoder/` 目录。

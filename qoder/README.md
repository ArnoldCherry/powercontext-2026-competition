# 生态赛交付看板

> Qoder × OceanBase 2026 生态赛 · Qoder 赛道作品  
> 用一块看板记录「任务 → 证据 → 提交 → 得分」，用 PowerContext 记住「偏好 → 进展 → 下一步」。

## 一、解决的问题

并行参加多个赛题时，任务信息散落在提示词、聊天记录、分支、提交页和验收报告里。常见问题是：

- 不知道哪些任务已经可以提交、哪些卡住了；
- 提交后忘记回填平台任务编号、得分和证据链接；
- 换一个 Agent 或隔天回来，要重新翻聊天记录才能恢复上下文；
- 只记在脑子里，容易出现“代码推了但没提交”“证据找不到”的漏交。

本作品把任务与提交证据集中到一个可运行的本地面板，并把用户偏好、任务进展写入 PowerContext。重启服务或刷新页面后，看板会恢复上次关注的视图和最近的进展，接手者可以直接继续。

## 二、功能

- **任务列表**：任务编号、标题、方向、难度、优先级、负责人、状态、下一步。
- **筛选与搜索**：按方向、难度、状态筛选，按任务/仓库/下一步等关键词搜索。
- **提交证据字段**：仓库、分支、Commit、提交网址、平台任务编号、得分、证据链接。
- **风险优先**：阻塞、待提交、进行中、待开始、已提交、已评分按风险排序；顶部提示需要关注的任务。
- **状态即记忆**：修改状态、下一步、证据、提交信息时，自动向 PowerContext 写入一条进展记忆。
- **偏好复用**：保存看板标题、默认方向和默认状态到 PowerContext；下次打开自动恢复。
- **记忆接力**：右侧时间线展示最近的进展记忆，包含任务、状态、下一步和时间。
- **可验证**：提供种子数据、接口验证、重启验证、浏览器交互验证。

## 三、技术分工

| 组成 | 作用 |
| --- | --- |
| Qoder | 需求拆解、后端/前端编码、调试、测试、Qoder Sites 展示站与发布 |
| PowerContext 1.2.0 | 保存并检索偏好记忆（`[preference]`）与进展记忆（`[progress]`）；SQLite 存储 + FTS 检索，不依赖模型 API |
| SQLite | 看板业务数据（任务、字段、证据链接）的本地持久化 |
| FastAPI + uvicorn | 提供 `/api/*` 接口与静态页面 |
| Playwright | 可选的浏览器交互与截图验证 |

PowerContext 进入核心流程，而不是只做连接测试：

1. 页面打开时，`/api/bootstrap` 从 PowerContext 读取最新偏好和进展，前端用偏好初始化标题与筛选，用进展渲染“最近进展”。
2. 任务状态/证据/提交信息变化时，`PATCH /api/tasks/{id}` 同步写入 `[progress]` 记忆。
3. 用户保存偏好时，`PUT /api/preferences` 写入 `[preference]` 记忆。
4. 验证脚本通过 PowerContext 的 `memory/search` 按 `run_id` 检索写入内容；重启 PowerContext 后仍能检索到。

## 四、版本与环境

- Python 3.11+（本机实测 3.14.3，Windows 10/11）
- PowerContext `1.2.0`（服务端 SQLite 模式、Access 关闭、无模型 API）
- 依赖见 [requirements.txt](requirements.txt)；Playwright 见 [requirements-dev.txt](requirements-dev.txt)
- 默认端口：看板 `18800`，PowerContext `18801`
- 数据目录：`data/`（SQLite 看板库、PowerContext 数据、验证状态），已加入 `.gitignore`

配置项（均为可选，默认值即可跑通）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `POWERCONTEXT_URL` | `http://127.0.0.1:18801` | PowerContext 服务地址 |
| `POWERCONTEXT_TOKEN` | 空 | 启用鉴权时使用，示例中保持占位 |
| `BOARD_DB` | `data/board.db` | 看板 SQLite 文件 |
| `BOARD_SCOPE_TITLE` | `生态赛交付看板` | PowerContext Scope 标题 |
| `BOARD_REQUEST_TIMEOUT` | `8` | 调用 PowerContext 的超时秒数 |

本作品不需要任何真实 API Key、密码或平台凭据；`powercontext.env` 中不含凭据。

## 五、启动

以下命令均在 `qoder/` 目录执行。

### 1. 安装依赖

```bash
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install -r requirements.txt
# Linux / macOS
# .venv/bin/python -m pip install -r requirements.txt
```

### 2. 启动 PowerContext

```bash
# Windows
.venv\Scripts\powercontext server run --env-file powercontext.env --host 127.0.0.1 --port 18801
# Linux / macOS
# .venv/bin/powercontext server run --env-file powercontext.env --host 127.0.0.1 --port 18801
```

看到 `PowerContext Server is ready` 后，另开一个终端执行：

```bash
curl http://127.0.0.1:18801/health/ready
# {"status":"ready",...}
```

### 3. 初始化数据与记忆

```bash
# Windows
.venv\Scripts\python scripts\seed.py
# Linux / macOS
# .venv/bin/python scripts/seed.py
```

预期输出包含：

```text
Scope: scp_...
任务总数: 11（本次新增 11）
偏好已写入 PowerContext: 生态赛交付看板
进展记忆: 6 条
凭据: ...\data\seed_receipt.json
```

`seed.py` 是幂等的：重复执行不会重复插入任务；需要重置演示数据时加 `--reset`。

### 4. 启动看板

```bash
# Windows
.venv\Scripts\python -m uvicorn board.app:app --host 127.0.0.1 --port 18800
# Linux / macOS
# .venv/bin/python -m uvicorn board.app:app --host 127.0.0.1 --port 18800
```

打开 <http://127.0.0.1:18800>。顶部显示“记忆在线”说明 PowerContext 已接入；在任务卡片上切换状态，右侧“最近进展”会立即出现一条新记忆。

## 六、验证样例

### 样例 A：偏好与进展读写（不重启）

```bash
.venv\Scripts\python scripts\verify.py
```

脚本会：

1. 读取健康状态，确认 SQLite 与 PowerContext 在线；
2. 读取种子任务、偏好和进展；
3. 写入临时偏好（方向 Qoder、状态 ready），重新加载并确认偏好被复用，再恢复原偏好；
4. 把 `DEMO-01` 更新为待提交，写入带 `run_id` 的下一步和证据；
5. 通过 `/api/memory/search?q=<run_id>` 确认 PowerContext 能检索到这条进展；
6. 再次 `/api/bootstrap`，确认刷新后任务与进展仍在。

预期：全部 `[PASS]`，并生成 `evidence/verification_write.json`。

### 样例 B：重启后恢复

1. 完成样例 A；
2. 停止看板和 PowerContext 进程；
3. 按第五节的命令重新启动两者；
4. 执行：

```bash
.venv\Scripts\python scripts\verify.py --after-restart
```

验证内容：

- 看板 SQLite 中 `DEMO-01` 的状态和下一步仍在；
- PowerContext 重启后仍能按原 `run_id` 检索到进展；
- 偏好仍能从 PowerContext 恢复。

预期：全部 `[PASS]`，生成 `evidence/verification_after_restart.json`。

### 样例 C：浏览器交互（可选）

```bash
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python scripts\ui_check.py
```

脚本使用本机 Edge/Chrome（无头模式）检查：页面加载、状态切换、阻塞筛选、PDF 搜索、编辑弹窗、偏好保存后刷新恢复、移动端无横向溢出、控制台无错误。截图输出在 `evidence/`。

已有实跑结果：

- `evidence/verification_write.json`：14 项全部通过；
- `evidence/verification_after_restart.json`：6 项全部通过；
- `evidence/ui_check.json`：18 项全部通过，控制台错误 0；
- `evidence/performance.json`：30 轮本地实测，`/api/tasks` p50 1.54 ms，`/api/bootstrap`（含两次 PowerContext 检索）p50 1059.77 ms；
- `evidence/ui-overview.png`、`ui-edit-dialog.png`、`ui-filter-blocked.png`、`ui-mobile.png`：界面截图；
- `evidence/qoder-session.md`、`qoder-session.png`：Qoder 开发会话导出与记录截图。

## 七、数据与目录

```text
qoder/
├── board/
│   ├── app.py              # FastAPI 路由、静态页面
│   ├── config.py           # 环境变量与路径
│   ├── memory.py           # PowerContext 记忆客户端
│   ├── store.py            # SQLite 任务存储
│   └── static/             # index.html / styles.css / app.js
├── scripts/
│   ├── seed.py             # 幂等初始化任务、偏好、进展
│   ├── verify.py           # 读写 + 重启恢复验证
│   └── ui_check.py         # Playwright 交互验证
├── tests/test_store.py     # SQLite 单元测试
├── site/                   # Qoder Sites 展示站静态源文件
├── evidence/               # 截图、验证 JSON、Qoder 会话记录
├── powercontext.env        # PowerContext SQLite 配置
├── requirements.txt
└── data/                   # 运行时数据（不提交）
```

## 八、Qoder 开发记录

- 开发环境：Qoder Desktop（Qoder CN）
- 会话标题：`DS-06.md`；会话 ID：`e4f5152c-33a6-4e62-b861-48f089fe4bcf`
- Qoder 账号 UID：`01a18285-6bd1-78e0-abad-5d5ee482486e`
- 主要开发过程：需求拆解 → PowerContext 本地服务验证 → 后端/前端实现 → 单元与接口验证 → 重启恢复验证 → Playwright 界面验证 → Qoder Sites 展示站发布
- 会话记录导出：`evidence/qoder-session.md` 与 `evidence/qoder-session.png`（由本机 Qoder 会话记录生成，含需求、关键实现、工具调用统计与验证步骤）
- 展示站源码：`site/`；线上地址：`<QODER_SITES_URL>`（发布后回填）

> 评审可在 Qoder 中打开 `DS-06.md` 会话，结合 UID 核对开发过程与站点归属。

## 九、提交信息

- 仓库：<https://github.com/ArnoldCherry/powercontext-2026-competition>
- 分支：`feat/eco-qoder`
- 实现 Commit：`89a1400c5d110ee685871ae6be174561658b0bc3`
- 后续文档/展示站 Commit：`<FINAL_COMMIT_SHA>`（提交后回填）
- 代码目录：`qoder/`

## 十、已知限制

- 这是面向个人/小团队的本地单用户看板，未实现登录与多租户；如部署到公网需自行加鉴权。
- PowerContext 采用基础记忆能力：显式 `remember` + FTS 检索，不依赖生成/嵌入模型；因此进展检索使用 `[progress]` 标签与 ASCII `run_id` 作为稳定锚点。
- 种子任务中的分支、Commit 和状态用于演示筛选与证据记录；真实提交信息由使用者回填。
- 展示站是项目介绍与证据页，不会在浏览器中直接运行本地看板；看板需按第五节在本地启动。
- Windows 下 PowerContext 官方支持标记为 experimental，本作品已在 Windows 11 + Python 3.14.3 实跑通过。

## 十一、开源引用

- [PowerContext](https://github.com/oceanbase/powercontext)（Apache License 2.0）：记忆服务与 HTTP API。
- 本项目基于 OceanBase 2026 生态赛仓库的 `2026-competition-ecosystem` 分支开发，资产放在 `qoder/` 目录。

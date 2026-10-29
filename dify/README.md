# 不二客栈 · 记仇掌柜

> 「你三年前欠的那碗面，掌柜还记着。」

一个用 Dify Chatflow 编排、由 seekdb 记账的互动角色应用。玩家用自然语言和临安城外「不二客栈」的掌柜钱不二聊天，他会翻旧账、记人情、催赊账、泄露今日暗号，并根据你之前来过几次、做过什么，决定是请你喝女儿红还是赶你出门。

- Dify：输入理解、条件分支、回复生成、最终输出
- seekdb：玩家记忆、历史事件、赊账、江湖异闻全文检索、每日暗号与领取记录
- 模型：DeepSeek `deepseek-flash`（Dify Marketplace 插件 `langgenius/deepseek` 0.0.26）

---

## 1. 玩法与体验

### 1.1 角色与设定

掌柜钱不二，五十来岁，抠门、记仇、嘴硬心软。柜台下有本赊账簿，后院埋着桂花酿，地窖里有一坛十八年女儿红。他嘴上不饶人，但熟客真有事，他会留一碗面。

### 1.2 一次对话发生什么

1. 玩家自由输入一句话（点菜、吹牛、赊账、打听、砸店都可以）。
2. Dify 的「小二听音」把话拆成行为、检索关键词和玩家名号。
3. 「查账本」调用后端 `/api/visit`，后端在 seekdb 中读取玩家、旧事、赊账，并用全文检索找相关江湖异闻。
4. 「掌柜回话」拿着账本密报生成符合当前称号的回复。
5. 后端把本轮事件写入 seekdb；下一次对话（包括新开会话）还能被翻出来。

### 1.3 可玩的机制

| 机制 | 说明 |
| --- | --- |
| 人情值 / 称号 | 请安、点菜、请客、叙旧、还账会加人情；赊账、捣乱、威胁、偷窃会扣。称号从「仇人」到「生死之交」共 6 档，掌柜语气随称号变化。 |
| 旧事记忆 | 每次互动写入 `inn_events`；新会话回来时，掌柜会主动提起上次做过的事。 |
| 赊账与还账 | 赊账 +20 文债务、人情 -5；还账最多抵扣 20 文、人情 +8。账本会一直催。 |
| 今日暗号 | 每天按日期从 7 个暗号中轮换一个；问「今日暗号是什么」可以得到提示，说对暗号人情 +15，并解锁地窖女儿红。 |
| 江湖异闻 | 14 条预置设定（断肠面、桂花酿、燕十三、地窖、灶王爷等），按关键词做 seekdb 全文检索，高人情值才能查到部分秘密。 |
| 捣乱升级 | 重复捣乱/威胁/偷窃会让扣分递增，掌柜的忍耐有上限。 |

### 1.4 体验入口

部署完成后，在 Dify 控制台打开「不二客栈 · 记仇掌柜」→「预览」，或直接访问：

```text
http://localhost:8080/chat/<应用站点访问码>
```

站点访问码在 Dify 应用的「访问 API / 站点」页面可以查看。应用自带 4 个建议问题，点击即可开始。跨会话记忆：在同一浏览器里点「开启新对话」再说话，掌柜会记得上一回；清除浏览器站点数据会变成新客人（Dify 的匿名用户标识会变化）。

---

## 2. 技术方案

### 2.1 架构

```text
Dify Web / API
   │
   ▼
Dify Chatflow
  开始 → 小二听音（Parameter Extractor，DeepSeek Flash）
       → 查账本（HTTP Request，POST http://inn-backend:8000/api/visit）
       → 账房先生（Code：解析 JSON、校验结果）
       → 账房接上了吗（IF/ELSE：HTTP 200 走正常回复，否则走兜底）
       → 掌柜回话（LLM，DeepSeek Flash）
       → 落笔归档（Code：去掉重复状态标记，追加准确状态）
       → 回答
   │
   ▼
inn-backend（FastAPI）
   │  MySQL 协议 / 参数化 SQL
   ▼
seekdb（数据库 inn）
   ├─ inn_players    玩家：名号、人情值、赊账、访问次数
   ├─ inn_events     历史事件：每轮行为、原话、增减、会话号
   ├─ inn_lore       江湖异闻：14 条预置设定 + FULLTEXT(title, body, keywords)
   ├─ inn_daily      每日暗号：日期、暗号、提示
   ├─ inn_secrets    暗号领取记录：玩家 + 日期
   └─ inn_requests   幂等账：玩家 + workflow run id → 已返回的响应
```

### 2.2 Dify 节点分工

| 节点 | 类型 | 作用 |
| --- | --- | --- |
| 开始 | start | 接收 `sys.query`，不要求额外开场参数 |
| 小二听音 | parameter-extractor | 用 DeepSeek Flash 提取 `action`、`keywords`、`player_name`；提示词限定 11 类行为 |
| 查账本 | http-request | 以 `application/x-www-form-urlencoded` 发送原话/会话/提取结果；身份来自 `sys.user_id` 请求头，服务鉴权来自应用环境变量 `INN_SERVICE_TOKEN`，幂等键来自 `sys.workflow_run_id` |
| 账房先生 | code (python3) | 解析后端 JSON，输出 `ok`、`status_line`、`director_note`、`tier`、`reputation`、`secret_hit`、`is_returning` |
| 账房接上了吗 | if-else | 根据 HTTP 200 分支；失败时给玩家一句兜底话，不把异常栈暴露给玩家 |
| 掌柜回话 | llm | 按「账本密报」生成 120–220 字的角色回复；温度 1.0，关闭思考模式以降低延迟 |
| 落笔归档 | code (python3) | 模型偶尔仍会自己写状态行；这里用正则去掉末尾重复的【人情…】，统一追加后端给出的准确状态 |
| 掌柜答话 / 账房打盹 | answer | 正常回复 / 异常兜底 |

### 2.3 seekdb 的职责

本作品让 seekdb 承担**应用业务数据存储与检索**：

- 玩家记忆和游戏状态持久化（跨会话、跨进程重启）；
- 每轮事件写入，用于「上次你……」式回忆；
- 赊账、人情值、称号、暗号领取记录；
- 江湖异闻的全文检索（`MATCH ... AGAINST`），并在中文分词不命中时用 `LIKE` 兜底；
- 每轮请求的幂等响应（`player_id + request_id` 唯一），用于 Dify 重试/响应丢失时不重复加扣分；
- 后端启动时自动建库建表并幂等种入 14 条异闻。

Dify 侧的 `.env` 同时配置了 `VECTOR_STORE=seekdb` 和 `OCEANBASE_VECTOR_*`，seekdb 容器也作为 Dify 的向量存储后端在运行；**本作品没有创建 Dify 知识库，因此没有向量数据写入**。验收时请以 `inn` 库中的业务数据和全文检索记录为准，不要把它误认为「只装了一个组件」。向量存储与业务库共用同一个 seekdb 实例，但使用不同的 database（`test` 与 `inn`）。

### 2.4 关键工程点

- **SSRF 放行 + 回环端口**：Dify 的 HTTP Request 节点经过 Squid 正向代理，默认拒绝私网。`SSRF_PROXY_ALLOW_PRIVATE_DOMAINS=inn-backend` 只放行后端容器名，不放开整个私网；后端宿主机端口只绑定 `127.0.0.1`，不暴露到局域网。
- **身份与服务边界**：DSL 不接收玩家输入的 `player_id`。玩家身份只来自 `X-Inn-Player-Id: {{#sys.user_id#}}`，后端忽略/拒绝 body 里的伪造 `player_id`；所有业务接口要求 `X-Inn-Service-Token`，值只存在 Dify 应用环境变量和 docker/.env 中。
- **可靠输入编码**：HTTP 节点改用 `application/x-www-form-urlencoded` 键值对，由 Dify/httpx 负责转义；双引号、换行、Unicode 不再拼进手写 JSON。
- **幂等与并发**：`X-Inn-Request-Id: {{#sys.workflow_run_id#}}` 在 `inn_requests` 中唯一；重复请求直接返回已存响应。玩家行用 `SELECT ... FOR UPDATE` 串行化，跨进程并发不会丢更新。
- **启动守卫**：`preflight.sh` 拒绝 `ChangeMe2026!`/`CHANGE_ME_*` 等示例值；后端启动也拒绝占位 `INN_SERVICE_TOKEN`、`INN_ADMIN_TOKEN`、`SEEKDB_PASSWORD`。
- **状态行准确性**：称号和人情由后端计算，LLM 不负责算数；最后用 Code 节点清洗重复状态行，避免模型把 2 算成 9。
- **失败兜底**：IF/ELSE 根据 HTTP 状态码分流；HTTP 节点自身配置 2 次重试。网络完全断开时 Dify 仍可能直接失败，这是已知限制。
- **可复现**：DSL 用 `include_secret=false` 导出，包内不含 API Key；模型 provider 以 Marketplace 依赖声明，安装时只需配置自己的 Key。

---

## 3. 版本与环境

| 项目 | 版本 / 说明 |
| --- | --- |
| Dify | 1.18.0（官方 Docker Compose 自托管） |
| Dify 插件 | `langgenius/deepseek` 0.0.26（Marketplace 安装） |
| 模型 | `deepseek-flash`；`thinking=false`，温度 1.0（掌柜）/ 0.1（识意） |
| seekdb | `oceanbase/seekdb:latest`，实测 `5.7.25-OceanBase seekdb-v1.4.0.0` |
| 后端 | Python 3.12 + FastAPI 0.115.6 + Uvicorn 0.34.0 + PyMySQL 1.1.1 + python-multipart 0.0.20 |
| 编排 | Docker Engine + Docker Compose v2 |
| 运行环境 | WSL2 Ubuntu 24.04；8 GB 内存可跑（seekdb 容器限 2G） |

---

## 4. 从零运行

### 4.1 前置条件

- Docker Engine 与 `docker compose` 可用；
- 能访问 Dify Marketplace（安装 DeepSeek 插件）和 `https://api.deepseek.com`；
- 一个 DeepSeek API Key；
- 端口 8080（Dify）与 8000（后端，仅 `127.0.0.1`）空闲；如果 2881 已被其他 seekdb 占用，请先停掉或改端口。

### 4.2 准备 Dify 1.18.0

```bash
git clone --depth 1 --branch 1.18.0 https://github.com/langgenius/dify.git
cd dify/docker
cp .env.example .env
```

把本目录的 `env.example` 中各项合并进 `dify/docker/.env`（至少包括 `EXPOSE_NGINX_PORT`、`VECTOR_STORE`、`COMPOSE_PROFILES`、`OCEANBASE_VECTOR_*`、`SSRF_PROXY_ALLOW_PRIVATE_DOMAINS`、`INIT_PASSWORD`）。`OCEANBASE_VECTOR_PASSWORD`、`INN_SERVICE_TOKEN`、`INN_ADMIN_TOKEN` 要记下来：后端用第一个变量连接 seekdb，第二个用于 Dify→后端的服务鉴权，第三个用于重置演示。三个值都必须换成真实随机值；`INN_SERVICE_TOKEN` 还要填进 Dify 应用环境变量。

> 若本机 2881 被占用：停掉旧的 seekdb，或把 Compose 的 seekdb 宿主机映射改成 2882（只改 ports，不要改 `OCEANBASE_VECTOR_PORT`，否则 api/worker 会连错内部端口）。

### 4.3 放入后端与 Compose override

```bash
# 假设当前目录是 dify/docker
cp -r /path/to/powercontext-2026-competition/dify/inn-backend ./inn-backend
cp /path/to/powercontext-2026-competition/dify/docker-compose.inn.yaml ./docker-compose.inn.yaml
cp /path/to/powercontext-2026-competition/dify/preflight.sh ./preflight.sh
```

`inn-backend` 会启动在 Compose 默认网络，容器名 `inn-backend`；Dify 的 HTTP 节点通过 `http://inn-backend:8000` 访问它。

> Dockerfile 默认用阿里云 PyPI 镜像；若你的网络访问官方 PyPI 更快，构建时追加 `--build-arg PIP_INDEX_URL=https://pypi.org/simple`。

### 4.4 启动

先检查占位值（示例值会被拒绝，退出码非 0 时先改 `.env`）：

```bash
cd dify/docker
bash preflight.sh .env
```

然后启动：

```bash
docker compose -f docker-compose.yaml -f docker-compose.inn.yaml \
  --profile seekdb --profile postgresql up -d --build \
  api worker worker_beat web db_postgres redis plugin_daemon nginx \
  ssrf_proxy sandbox seekdb inn-backend init_permissions
```

检查：

```bash
docker compose -f docker-compose.yaml -f docker-compose.inn.yaml ps
curl http://localhost:8000/health
# {"ok":true,"seekdb":"5.7.25-OceanBase seekdb-v1.4.0.0","database":"inn"}
```

后端首次启动会自动创建 `inn` 库、6 张表（含 `inn_requests` 幂等账），并幂等种入 14 条异闻和今天的暗号；不需要手工跑 SQL。

### 4.5 初始化 Dify 并安装模型插件

1. 打开 `http://localhost:8080/install`，用 `.env` 里的 `INIT_PASSWORD` 完成初始化，创建管理员。
2. 右上角设置 → 模型供应商 → Marketplace，搜索并安装 **DeepSeek**（`langgenius/deepseek` 0.0.26 或更高兼容版本）。
3. 在 DeepSeek 供应商里填入自己的 API Key，Base URL 用 `https://api.deepseek.com`；保存后确认模型列表里有 `deepseek-flash`。

DSL 已声明 Marketplace 依赖 `langgenius/deepseek:0.0.26`。如果导入时提示缺依赖，先完成第 2 步再导入。

### 4.6 导入并发布

Dify 控制台 → Studio → 创建空白应用 → 导入 DSL：

- 推荐上传 `dsl/不二客栈.yml`（Dify 1.18 导出的 app.yaml）；
- 也可以在 Dify 1.18 中上传 `dsl/不二客栈.ifpkg`（应用包，含 manifest 与 app.yaml）。

导入后：

1. 打开应用 → 环境变量，把 `INN_SERVICE_TOKEN` 改成与 `docker/.env` 完全一致的真实值（DSL 里是 `CHANGE_ME_INN_SERVICE_TOKEN` 占位）。
2. 「发布」→「运行」。首次运行如果报模型未配置，回到模型供应商确认 DeepSeek 凭据；如果返回 401，检查应用环境变量与后端 token 是否一致。

### 4.7 重置演示

玩家身份来自 Dify 的 `sys.user_id`。要重新体验「第一次见面」，可以：

```bash
# 查看玩家 id
curl http://localhost:8000/api/ledger/<player_id>

# 清空该玩家的记忆/赊账（需要 compose 里的 INN_ADMIN_TOKEN）
curl -X POST -H "X-Admin-Token: $INN_ADMIN_TOKEN" \
  http://localhost:8000/api/players/<player_id>/reset
```

更彻底的方式：删除 `inn` 库并重启后端（后端会自动重建和种数据）：

```sql
DROP DATABASE inn;
```

```bash
docker compose -f docker-compose.yaml -f docker-compose.inn.yaml restart inn-backend
```

`GET /api/daily` 需要 `X-Inn-Service-Token`，可以查看当天暗号（用于验收，不建议暴露给玩家）。

---

## 5. 验证样例

以下用例在**服务鉴权 + 表单编码 + 幂等**版本上实际执行，原始节点日志在 `evidence/case*-secure.sse`，可读摘要在同名 `.parsed.txt`。初始状态：已按 4.7 清空玩家 `楚留香`（`player_id` 为 Dify 控制台账号的 `sys.user_id`）。案例 1 的原话包含双引号、换行和 emoji，用来验证表单编码与逐字保真。

| # | 输入 | 关键预期 | 实际证据 |
| --- | --- | --- | --- |
| 1 | `我叫楚留香，他说“来碗面”，
第二行 🍜。` | 表单原样送达并入库；提取 `点菜`；首次来访，人情 2，生客 | `case1-secure.*`：`utterance` 逐字一致，回复称呼「楚留香少侠」 |
| 2 | 掌柜的，还认得我吗？上次那碗断肠面，我可记得清清楚楚。 | 新会话仍记得上一次点菜；人情 5，熟客 | `case2-secure.*`：`is_returning=true`，`memories` 含第一次带引号/换行的原话 |
| 3 | 掌柜的，今日暗号是什么？ | 返回当天暗号「招财今天吃小鱼干」 | `case3-secure.*`：`daily_hint` 字段与回复一致 |
| 4 | 招财今天吃小鱼干！ | 暗号命中，人情 +18（含 +15 奖励），触发地窖女儿红；检索到《招财》《地窖》 | `case4-secure.*`：`secret_hit=true`，人情 25，座上宾，回复提到女儿红 |
| 5 | 这破店我砸了！ | 识别捣乱，人情 -10，掌柜冷脸但还留客 | `case5-secure.*`：`action=捣乱`，回复冷脸但留客 |
| 6 | 先赊二十文酒钱，记我账上。 | 赊账 -5，债务 20，末尾催账 | `case6-secure.*`：`debt=20`，`memories` 含「赊账簿上记着 20 文未还」 |
| 7 | 我还你二十文，把账清了。 | 还账 +8，债务归零 | `case7-secure.*`：`debt=0`，回复「二十文我收下了」 |
| 8 | 给我讲讲燕十三的事。 | 全文检索《燕十三》，回忆之前砸店/暗号 | `case8-secure.*`：`retrieval=fulltext`，回复引用燕十三设定 |

跨会话验证的要点：案例 2 没有携带案例 1 的 `conversation_id`，Dify 新建了会话；后端通过可信请求头 `X-Inn-Player-Id: sys.user_id` 找到同一玩家，`visit_no` 从 1 变 2，`memories` 来自上一会话。这正是「新会话仍记得」的验收点。

安全/幂等回归（`evidence/security-regression.txt`，全部 PASS）：

- 无/错误服务 token、缺玩家 header 拒绝；
- body 伪造 `player_id` 拒绝且不创建玩家；
- 未授权 ledger 拒绝，管理员或本人可读；
- 无管理员 token reset 拒绝；
- 双引号/换行/Unicode 原话逐字保真；
- 重复 `request_id` 只记一次；
- 两个后端进程并发不同请求不丢更新（reputation=4）；
- 两个后端进程并发同一 `request_id` 只记一次（reputation=2）。

### 5.1 网页端证据

- `evidence/ui-secure-0-opening.png`：应用开场与 4 个建议问题；
- `evidence/ui-secure-1-first-visit.png`：第一次带引号/换行/emoji 点菜；
- `evidence/ui-secure-2-return-memory.png`：新会话点「掌柜的，还认得我吗」，掌柜复述上次点菜。

网页端用无头 Chrome 实际点击输入框、发送消息、等待回复后截图；截图中的对话就是 Dify Web App 的真实渲染结果。

---

## 6. seekdb 验证与证据

`evidence/seekdb-verification.txt` 记录了对运行中 seekdb 的实际 SQL：

```sql
SELECT VERSION();                 -- 5.7.25-OceanBase seekdb-v1.4.0.0
SHOW DATABASES;                   -- inn / test / mysql / oceanbase ...
USE inn; SHOW TABLES;             -- inn_players, inn_events, inn_lore, inn_daily, inn_secrets, inn_requests
SELECT * FROM inn_players;        -- 楚留香等玩家的名号、人情、赊账、访问次数
SELECT * FROM inn_events;         -- 每轮行为、原话（含引号/换行）、delta、会话号
SELECT * FROM inn_requests;       -- 每个 workflow run 的幂等响应
SELECT * FROM inn_lore;           -- 14 条异闻
SHOW INDEX FROM inn_lore;         -- ft_inn_lore: FULLTEXT(title, body, keywords)
SELECT ... MATCH ... AGAINST ('燕十三' IN NATURAL LANGUAGE MODE);
SELECT * FROM inn_secrets;        -- 暗号领取记录
SELECT * FROM inn_daily;          -- 当天暗号「招财今天吃小鱼干」
```

可以看到：

- 玩家「楚留香」的 `visits=2`，说明网页端第一次对话和「开启新对话」后的第二次对话都落到了同一个 seekdb 玩家行；
- `inn_events` 中有对应的点菜、叙旧、打听、暗号等事件；
- `inn_lore` 的 FULLTEXT 索引真实存在，`MATCH ... AGAINST ('燕十三')` 返回 score 3.404；
- `inn_secrets` 记录了当天说对暗号的玩家；
- `inn_requests` 保留了 Dify 传入的 workflow run id 与响应，重复请求可原地重放；
- `inn_events` 中的原话保留了双引号、换行和 emoji。

`evidence/fresh-db-init.txt` 是另一份证据：用全新的 `inn_verify` 库启动后端，后端自动建表、种入 14 条异闻，第一次调用 `/api/visit` 就能通过全文检索返回《桂花酿》。这证明评审不需要作者本地的旧数据库。

`evidence/dify-stack.txt` 还包含：

- 容器状态（api/worker/seekdb/inn-backend 全部 healthy）；
- seekdb 镜像与 `SELECT VERSION()`；
- `inn-backend` 的访问日志（含 `retrieval=fulltext`、lore 标题）；
- Squid access log 中对 `inn-backend:8000` 的请求；
- DeepSeek 插件安装目录；
- 应用 id / mode / 站点开关；
- 后端 8000 只监听 `127.0.0.1`。

`evidence/startup-guards.txt` 记录占位 token/密码被后端和 preflight 拒绝、preflight 接受真实值、以及回环绑定。`evidence/security-regression.txt` 是覆盖伪造身份、未授权访问、幂等与双进程并发的完整回归。

---

## 7. 交付物

```text
dify/
├── README.md                     # 本文件
├── env.example                   # Dify .env 需要合并的变量（占位值）
├── preflight.sh                  # 拒绝示例/占位凭据
├── docker-compose.inn.yaml       # 增加 inn-backend 服务（仅回环端口）
├── tests/
│   └── security_regression.py    # 身份、鉴权、幂等、并发回归
├── dsl/
│   ├── 不二客栈.yml               # Dify 1.18 导出的 app.yaml（推荐导入）
│   └── 不二客栈.ifpkg             # Dify 1.18 应用包（备用导入）
├── inn-backend/
│   ├── app.py                    # FastAPI + seekdb 记账/检索后端
│   ├── requirements.txt
│   └── Dockerfile
└── evidence/
    ├── case1..case8-secure       # 8 组鉴权版 SSE 原始日志 + 可读摘要
    ├── ui-secure-*.png           # 网页端截图
    ├── seekdb-verification.txt   # SQL 读写/全文检索/幂等账证据
    ├── fresh-db-init.txt         # 空库自动初始化证据
    ├── startup-guards.txt        # 占位凭据与回环绑定证据
    ├── security-regression.txt   # 鉴权/伪造身份/幂等/并发回归
    └── dify-stack.txt            # 容器/版本/网络/插件证据
```

### 7.1 外部依赖与来源

- Dify 1.18.0：<https://github.com/langgenius/dify>（官方 Docker Compose）；
- DeepSeek 插件：Dify Marketplace `langgenius/deepseek` 0.0.26；
- seekdb 镜像：`oceanbase/seekdb:latest`（Docker Hub）；
- 本作品的 Dify 应用、后端代码、异闻文本均为原创；未复用第三方工作流或数据。

### 7.2 安全与凭据

- 仓库和 DSL 中不包含 DeepSeek API Key、Dify 管理员密码或 seekdb 密码；DSL 用 `include_secret=false` 导出。
- 运行前把 `env.example` 里的 `CHANGE_ME_*` 换成自己的值，并先跑 `preflight.sh`；后端启动也会拒绝占位 `INN_SERVICE_TOKEN`/`INN_ADMIN_TOKEN`/`SEEKDB_PASSWORD`。
- `/api/visit`、`/api/daily` 要求服务 token；ledger 还要求本人 header 或管理员 token；reset 只接受管理员 token。后端宿主机端口只绑定 `127.0.0.1`。
- 玩家身份只来自 Dify 的 `sys.user_id` 请求头；body 中的 `player_id` 会被拒绝，不能用它覆盖身份。
- `inn-backend` 的 SQL 全部参数化；玩家输入长度受限；异闻内容是预置数据，不执行玩家输入。

---

## 8. 已知限制

- Dify 1.18 的导出格式是 `.ifpkg`（内含 `app.yaml`），本目录同时提供 YAML 和包，二者都已验证可导入。
- 每日暗号按运行后端的机器本地日期轮换；跨时区部署时以服务器日期为准。
- 玩家身份来自 Dify 的 `sys.user_id`：同一 Dify 账号/同一浏览器共享记忆；换账号或清除站点数据会被当成新客人。这是有意的「记住你」设计，不是会话隔离缺陷。
- 后端每请求一条同步数据库连接，适合单人/小范围体验；`inn_players` 行锁保证跨进程并发不丢更新，但更高吞吐仍需连接池和限流。
- `inn_requests` 当前不做定期清理；长期运行可按日期归档。
- Dify 应用环境变量里的 `INN_SERVICE_TOKEN` 必须与 `docker/.env` 完全一致，修改任一侧后要同步并重新发布。
- HTTP 节点对完全断网（连接被拒绝）不会进入 200/非 200 的 IF 分支，会由 Dify 的工作流错误处理接管；IF/ELSE 主要覆盖后端可达但返回非 200 的情况。
- 本地 Dify 首次启动需要拉取镜像和安装 Marketplace 插件；离线环境需要提前导入插件包。

# Benchmark：端侧推理与 seekdb 混合检索

> 所有数字来自本提交应用的实际运行，原始数据见 `evidence/`。质量、配置与脚本均可复现。

## 1. 测试条件

| 项 | 值 |
| --- | --- |
| 设备 | Windows 11（10.0.26200），NVIDIA RTX 4060 Laptop（WebGPU adapter: nvidia / lovelace），Intel UHD，32 逻辑线程 |
| 浏览器 | Chrome 156.0.8078.49（Playwright `channel=chrome`，headless；WebGPU 可用） |
| 后端 | WSL2 Ubuntu 24.04，kernel 6.18.33.2-microsoft-standard-WSL2，Node 22.20.0 |
| 数据库 | seekdb 1.4.0（server 模式，独立端口 2885，数据目录 `~/.stepfun/seekdb-server/runtime`），seekdb-js 1.4.0 |
| 模型 | BAAI/bge-small-zh-v1.5（512 维，CLS + L2，查询指令），ONNX FP32/FP16/INT8；Transformers.js 3.8.1 / ONNX Runtime Web 1.22 |
| 运行时分发 | ONNX Runtime Web 的 `.mjs/.wasm` 由 `npm run build` 复制到 `public/ort/`，运行期不访问外部 CDN |
| 语料 | `data/corpus/kb-seekdb.json`：46 篇，3,569 字符（含 10 篇生活类干扰文档） |
| 查询集 | `data/queries.json`：24 条，人工标注 1–3 篇 relevant；标签在测量前固定 |
| 索引 | HNSW cosine（m=16, ef_construction=200）+ FULLTEXT IK(smart) |
| 默认参数 | 批大小 16，Top K=10，RRF rankWindowSize=40，rankConstant=60 |
| 命名说明 | 配置名中的 `q8` 是 Transformers.js 的 INT8 量化 dtype 名称，对应文件 `onnx/model_quantized.onnx` |

### 计时口径

- **冷加载**：每次评测新开一个 Chrome 浏览器进程（新 profile、模型未在 Cache API 中），从 `pipeline()` 开始到模型加载完成；不含预热推理。
- **热重载**：同一页面内 dispose 后再次初始化，命中浏览器 Cache API。
- **文档向量化**：Worker 内对 46 篇文档分批编码的总耗时（含批间开销）。
- **写入**：浏览器→Express→seekdb-js `upsert` 的墙钟时间，2 批（32 + 14）。
- **刷新**：`collection.refresh_index()`（`CALL dbms_index_manager.refresh()`）耗时。
- **查询向量化**：单条查询在 Worker 内编码的中位数（24 条查询，缓存旁路）。
- **seekdb 检索**：服务端在 `hybridSearch` 调用外层计时，不含 HTTP/JSON。
- **端到端**：查询向量化开始 → `/api/search` 响应返回；包含 Windows Chrome → WSL 的 HTTP 回环（约 45 ms），不含 React 渲染。
- **质量**：固定语料与查询集；混合检索的 Recall@5 / Recall@10 / nDCG@10（二值相关性）；仅向量检索作对照。

### 重复与中位数

- `webgpu-fp16-b16`、`wasm-q8-b16` 各重复 3 次；`wasm-fp32-b16` 重复 3 次（含 1 次矩阵初测）。
- 其余配置（批大小 1/32、WebGPU FP32、每批刷新）为单次运行；结论优先采用重复配置的中位数。
- 原始 run 在 `evidence/bench-runs.csv`；中位数汇总在 `evidence/bench-summary.{json,md}`。

## 2. 性能矩阵（中位数）

| 配置 | 后端 / 精度 | 模型体积 | 批大小 | 冷加载 | 热重载 | 文档向量化 | 写入 | 刷新 | 查询向量化 | seekdb 检索 | 端到端 | 热端到端 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `wasm-fp32-b16` | WASM / FP32（基线） | 90.5 MiB | 16 | 1.63 s | – | 73.4 docs/s | 172 ms | 511 ms | 11.48 ms | 3.16 ms | 62.03 ms | 62.1 ms |
| `wasm-q8-b16` | WASM / INT8 | 22.9 MiB | 16 | **958 ms** | **81 ms** | 99.1 docs/s | 169 ms | 505 ms | 7.80 ms | 3.17 ms | 57.63 ms | 58.7 ms |
| `webgpu-fp16-b16` | WebGPU / FP16 | 45.3 MiB | 16 | 1.64 s | **118 ms** | **283.6 docs/s** | 183 ms | 552 ms | 10.93 ms | 3.25 ms | 61.68 ms | 63.5 ms |
| `webgpu-fp32-b16` | WebGPU / FP32 | 90.5 MiB | 16 | 2.20 s | – | 255.0 docs/s | 128 ms | 504 ms | 14.92 ms | 3.37 ms | 65.44 ms | 67.9 ms |

单次补充（批大小/刷新策略趋势）：

| 配置 | 冷加载 | 文档向量化 | 刷新 | 说明 |
| --- | ---: | ---: | ---: | --- |
| `webgpu-fp16-b1` | 1.61 s | 112.3 docs/s | 602 ms | 批大小 1 |
| `webgpu-fp16-b16` | 1.64 s | 283.6 docs/s | 552 ms | **默认**；较 b1 吞吐 2.53× |
| `webgpu-fp16-b32` | 1.61 s | 293.9 docs/s | 502 ms | 批大小 32，较 b16 仅 +3.6%，显存更高 |
| `wasm-q8-b1` | 849 ms | 80.5 docs/s | 504 ms | 批大小 1 |
| `wasm-q8-b16` | 958 ms | 99.1 docs/s | 505 ms | **默认**；较 b1 吞吐 1.23× |
| `wasm-q8-b32` | 1.08 s | 85.1 docs/s | 704 ms | 批大小 32 反而下降 |
| `webgpu-fp16-perbatch-b16` | 1.63 s | 283.6 docs/s | **1308 ms** | 每批写入后刷新（2 批） |

默认批大小取 16：WebGPU 下已获得大部分吞吐收益，显存与单批等待更可控；WASM 下 b32 反而下降。

## 3. 检索质量（46 篇 / 24 查询）

| 配置 | 混合 Recall@5 | 混合 Recall@10 | 混合 nDCG@10 | 仅向量 Recall@5 | 仅向量 nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| FP32 / WASM（基线） | 82.6% | 92.4% | 86.6% | 79.9% | 80.5% |
| FP32 / WebGPU | 82.6% | 92.4% | 86.6% | 79.9% | 80.5% |
| FP16 / WebGPU | 84.0% | 92.4% | 86.7% | 79.9% | 80.5% |
| INT8 / WASM | 82.6% | 92.4% | 86.5% | 82.6% | 80.5% |

结论：

- **量化没有带来有意义的质量下降**：INT8 与 FP32 的 Recall@5/Recall@10 相同，nDCG@10 低 0.1pp；FP16 的 +1.4pp Recall@5 视为 24 条查询上的数值/排序噪声（FP16 与 FP32 在 WebGPU 上的 nDCG 差 0.1pp）。
- **混合检索优于仅向量**：nDCG@10 约 86.5% vs 80.5%（+6pp），Recall@5 高 0–4pp；全文分支对关键词/专名查询有补充。
- 语料仅 46 篇、查询 24 条；指标用于同一评测集上的相对比较，不代表大规模语料的绝对值。

## 4. 优化前后对比

| 优化项 | 优化前 | 优化后 | 变化 | 证据 |
| --- | --- | --- | --- | --- |
| 文档向量化批大小（WebGPU） | b1 112.3 docs/s | b16 283.6 docs/s | **2.53×** | `bench-webgpu-fp16-b1/b16` |
| 文档向量化批大小（WASM） | b1 80.5 docs/s | b16 99.1 docs/s | 1.23× | `bench-wasm-q8-b1/b16` |
| 模型重复加载（WebGPU） | FP16 冷 1.64 s | Cache API 热重载 118 ms | **13.9×** | `bench-webgpu-fp16-b16` |
| 模型重复加载（WASM） | INT8 冷 958 ms | 热重载 81 ms | **11.8×** | `bench-wasm-q8-b16` |
| 量化模型体积 | FP32 90.5 MiB | INT8 22.9 MiB | **3.95× 压缩** | `model-manifest.json` |
| 量化模型体积 | FP32 90.5 MiB | FP16 45.3 MiB | 2.00× 压缩 | `model-manifest.json` |
| FP16 vs FP32（WebGPU） | 2.20 s 冷加载 / 255.0 docs/s | 1.64 s / 283.6 docs/s | 加载 1.34×，吞吐 1.11× | 性能矩阵 |
| INT8 vs FP32（WASM） | 1.63 s 冷加载 / 73.4 docs/s | 958 ms / 99.1 docs/s | 加载 1.70×，吞吐 1.35× | 性能矩阵 |
| 索引刷新策略 | 每批刷新 1308 ms（2 批） | 导入结束统一刷新 552 ms | **2.37×** | `bench-webgpu-fp16-perbatch-b16` vs `b16` |
| 查询缓存 | 首次查询向量化 13.6 ms | 相同查询命中缓存 10 µs | 约 1300×；端到端 75.1 → 56.6 ms | `evidence/e2e-ui-webgpu.json` |

## 5. 端到端延迟分解

以 `webgpu-fp16-b16` 中位数为例：

```
查询向量化 10.93 ms  +  seekdb 检索 3.25 ms  +  HTTP/会话/JSON ≈ 47.5 ms  =  端到端 61.68 ms
```

WSL2 的 Windows↔Linux 回环是本测试拓扑的固定开销；模型和数据库都在本机，同主机部署时该部分会显著降低。首次查询可能因全文索引/连接预热略高（E2E 首次检索 17.5 ms），之后稳定在 ~3 ms。

## 6. 自动降级验证

- 正常 Chrome（WebGPU 可用）：`?backend=auto` → 徽标 `WebGPU · FP16 45.3 MB`。
- `--disable-gpu --disable-features=WebGPU`：`?backend=auto` → 徽标 `WASM · INT8 22.9 MB`，界面显示“未检测到可用的 WebGPU 适配器，已自动降级到 WASM / INT8”。
- 降级后完成 46 篇导入 + 混合检索：8 条结果；查询向量化 6.37 ms、seekdb 检索 7.60 ms、端到端 68.0 ms（`evidence/fallback-test.json`）。

## 7. 原始数据与复现

```bash
node tests/e2e.mjs          # 完整 UI 流程、隔离、查询缓存、截图
node tests/bench.mjs        # 默认矩阵 9 项；CONFIGS='[...]' 覆盖
node tests/fallback.mjs     # WebGPU→WASM 自动降级
node tests/network.mjs      # 零外部请求检查
node scripts/api-test.mjs   # 20 项 API/状态/隔离测试
node scripts/summarize-bench.mjs
```

| 文件 | 内容 |
| --- | --- |
| `evidence/bench-runs.csv` | 17 个原始 run 的逐项计时 |
| `evidence/bench-summary.json` / `.md` | 分组中位数与表格 |
| `evidence/bench-*.json` | 单次 run 的完整结果、per-query、日志、console 警告 |
| `evidence/e2e-ui-webgpu.json` | 完整 UI 流程 + 查询缓存 + 隔离断言 + 截图索引 |
| `evidence/fallback-test.json` | 自动降级 + WASM 全流程 |
| `evidence/network-check.json` | 零外部请求检查（模型与 ORT 运行时均来自本机） |
| `evidence/api-test.json` | 20 项 API/状态/隔离测试 |
| `evidence/quantization-manifest.json` | 模型来源、量化参数、体积、SHA-256、工具版本 |
| `evidence/server-events-final.txt` | 最终 E2E 对应的 seekdb 服务端事件 |
| `evidence/ui-*.png` | 应用、检索、导入、隔离、降级、离线截图 |

## 8. 已知偏差

- 46 篇短文档（合计 3,569 字符）使文档向量化耗时较短；大规模语料下写入/刷新占比会上升，但相对结论（批处理、结束刷新、缓存）仍成立。
- 质量标签为单人标注，24 条查询样本小；未做显著性检验。
- 冷加载包含 Chrome 进程启动后的首次 ONNX 初始化与本地 ORT 运行时编译；不同 run 有 0.8–2.2 s 波动，结论使用重复中位数。
- 测试使用 headless Chrome；headless 与 headed 的 GPU 调度可能略有差异。
- 端到端包含 WSL 回环开销；不应直接与同机部署的绝对延迟对比。

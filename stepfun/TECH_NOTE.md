# 一页技术说明：端侧推理与 seekdb 混合检索

## 1. 模型与量化

- **模型**：`BAAI/bge-small-zh-v1.5`（MIT），ONNX 权重来自 `Xenova/bge-small-zh-v1.5`；基座 revision `7999e1d3…`，ONNX revision `75c43b06…`。512 维，CLS pooling + L2 normalize；查询侧加 BGE 指令 `为这个句子生成表示以用于检索相关文章：`，文档侧不加。
- **量化**（本仓库脚本 `scripts/quantize_embedding.py`，onnxruntime 1.23.2）：
  - INT8：`quantize_dynamic(weight_type=QInt8, per_channel=True)`，无需校准集；脚本对产物做 ONNX checker 与 CPU 推理校验（输出 `[1,8,512]`）。
  - FP16：`convert_float_to_float16(keep_io_types=True)`。
- **体积**（同一口径，ONNX 文件字节数）：FP32 94,851,877 B → INT8 **24,011,364 B（3.95×，22.9 MiB）**；FP16 **47,497,427 B（2.00×，45.3 MiB）**。
- **质量**（固定 46 篇中文语料 + 24 条人工标注查询，同批大小 16，混合检索）：

| 精度 / 后端 | Recall@5 | Recall@10 | nDCG@10 |
| --- | ---: | ---: | ---: |
| FP32 / WASM（基线） | 82.6% | 92.4% | 86.6% |
| FP32 / WebGPU | 82.6% | 92.4% | 86.6% |
| FP16 / WebGPU | 84.0% | 92.4% | 86.7% |
| INT8 / WASM | 82.6% | 92.4% | 86.5% |

量化后无有意义的质量下降（INT8 nDCG −0.1pp；FP16 的小幅提升视为数值/排序噪声，样本仅 24 条查询）。

## 2. 端侧运行

- **运行时**：Transformers.js 3.8.1 + ONNX Runtime Web；模型与推理都在浏览器 **Web Worker** 内，主线程只处理 UI。
- **后端选择**：检测 `navigator.gpu` 与 `requestAdapter()`；可用时加载 `WebGPU + FP16`，不可用或加载失败时自动改用 `WASM + INT8`，失败原因写入界面。顶部徽标与侧栏明确显示 `WebGPU · FP16` / `WASM · INT8`；自动降级时显示“未检测到可用的 WebGPU 适配器，已自动降级到 WASM / INT8”。
- **无云端推理/无外部依赖**：`env.allowRemoteModels=false`、`env.allowLocalModels=true`；模型由同源 `/models/` 静态提供，ORT 的 `.mjs/.wasm` 运行时由构建脚本复制到 `/ort/`，计算只发生在设备上。网络检查记录 0 个外部请求（`evidence/network-check.json`）。跨源隔离头（COOP/COEP）开启后 WASM 使用 4 线程。
- **验证**：在 Chrome 156 中分别验证 WebGPU 自动选中、`--disable-gpu` 下自动降级 WASM，以及 WASM 下的完整导入 + 混合检索流程。

## 3. seekdb 使用

- 每个知识库一个集合，由 seekdb-js 1.4.0 建表；字段为 `id VARBINARY(512) PRIMARY KEY, document STRING, embedding VECTOR(512), metadata JSON`。
- 索引：`FULLTEXT INDEX ... IK(smart)` + `VECTOR INDEX ... HNSW(distance=cosine, m=16, ef_construction=200)`；向量由浏览器写入，**不使用数据库 embedding 函数**。
- 混合检索：单次 `collection.hybridSearch({ query: { whereDocument: { $contains } }, knn: { queryEmbeddings }, rank: { rrf } })`，内部执行 `DBMS_HYBRID_SEARCH`，数据库完成全文 + 向量召回与 RRF 融合，客户端只接收一个排序列表，不做两路拼接。
- 写入与刷新：浏览器生成稳定文档 ID，`collection.upsert` 增量写入；导入结束后 `collection.refresh_index()` → `CALL dbms_index_manager.refresh()`；刷新后新文档可检索。
- **集合/会话状态**：服务端 HttpOnly session 记录 `activeKbId`；所有检索/导入均校验 `expectedKbId`，旧知识库请求返回 `409 KB_CHANGED`；前端切换时清空结果并中止导入。集合名包含随机 ID + 创建 epoch，避免复用旧表；每条文档元数据写入 `kbId`/`collection` 供结果溯源。

## 4. 优化与实测

环境：Windows 11 + Chrome 156.0.8078.49 + RTX 4060 Laptop；WSL2 Ubuntu 24.04；Node 22.20；seekdb 1.4.0（server 模式，独立端口 2885）；46 篇 / 24 查询；表中为重复运行中位数（关键配置 3 次，冷加载每次新开浏览器）。

| 配置 | 模型体积 | 冷加载 | 热重载 | 文档向量化 | 写入 | 刷新 | 查询向量化 | seekdb 检索 | 端到端 | Recall@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FP32 + WASM（基线） | 90.5 MiB | 1.63 s | – | 73.4 docs/s | 172 ms | 511 ms | 11.48 ms | 3.16 ms | 62.03 ms | 82.6% |
| FP32 + WebGPU | 90.5 MiB | 2.20 s | – | 255.0 docs/s | 128 ms | 504 ms | 14.92 ms | 3.37 ms | 65.44 ms | 82.6% |
| FP16 + WebGPU | 45.3 MiB | 1.64 s | **118 ms** | **283.6 docs/s** | 183 ms | 552 ms | 10.93 ms | 3.25 ms | 61.68 ms | 84.0% |
| INT8 + WASM | 22.9 MiB | **958 ms** | **81 ms** | 99.1 docs/s | 169 ms | 505 ms | 7.80 ms | 3.17 ms | 57.63 ms | 82.6% |

- **批处理**：WebGPU FP16 批大小 1 → 16 时吞吐 112.3 → 283.6 docs/s（2.53×；批大小 1 为单次运行）；WASM 批大小 1 → 16 为 80.5 → 99.1 docs/s。默认批大小 16（b32 在 WebGPU 仅 +3.6%，WASM 反而下降）。
- **模型加载**：冷加载（新浏览器）包含权重读取、ONNX 初始化与本地 ORT 运行时编译；同页面第二次加载命中浏览器 Cache API，FP16 118 ms / INT8 81 ms。FP16/WebGPU 冷加载 1.64 s，FP32/WebGPU 2.20 s（1.34×）。
- **刷新策略**：46 篇分 2 批写入，导入结束统一刷新 552 ms；每批刷新合计 1308 ms（2.37×），因此默认“结束刷新”。
- **查询缓存**：Worker 内对相同查询文本做 LRU 缓存；同一查询第二次向量化 13.6 ms → **10 µs（缓存命中）**，端到端 75.1 → 56.6 ms（见 `evidence/e2e-ui-webgpu.json`）。
- **端到端**：~58–65 ms；其中查询向量化 + seekdb 检索约 11–18 ms，其余约 45 ms 为 Windows Chrome → WSL 回环 HTTP/会话/JSON 开销（本机部署可忽略；这是测试拓扑开销，不是模型或数据库开销）。

## 5. 复现

```bash
bash scripts/start-seekdb.sh                 # 独立 2885 端口，官方 seekdb 1.4.0
npm install --ignore-scripts
npm run build
SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2885 SEEKDB_DATABASE=stepfun PORT=8788 npm start
node scripts/api-test.mjs                    # 20 项 API/状态/隔离测试
node tests/e2e.mjs                           # 完整 UI 流程 + 截图
node tests/bench.mjs                         # 性能/质量矩阵
node tests/fallback.mjs                      # WebGPU→WASM 自动降级
node tests/network.mjs                       # 零外部请求检查
```

原始数据：`evidence/bench-*.json`、`evidence/bench-summary.{json,md}`、`evidence/e2e-ui-webgpu.json`、`evidence/fallback-test.json`、`evidence/network-check.json`、`evidence/api-test.json`、`evidence/quantization-manifest.json`。

## 6. 未验证项

- 未在 Edge 上单独运行（同为 Chromium 内核；Chrome 156 已验证）。
- 未在非 NVIDIA GPU / 不支持 WebGPU 的真实旧设备上测量性能；降级路径用 `--disable-gpu` 模拟并完成 WASM 全流程。
- 未测 Windows 原生嵌入模式与本轮 server 模式的性能差异；验收统一使用 WSL2 + seekdb server 模式。
- 语料仅 46 篇、查询 24 条，质量指标用于同一条件下的相对比较，不代表大规模语料绝对值。
- 超过 512 token 的长文档仅部分进入向量（存储原文完整），未做分块语义拼接。
- 未使用 Docker 部署；`scripts/start-seekdb.sh` 为本次实际验证的启动方式。

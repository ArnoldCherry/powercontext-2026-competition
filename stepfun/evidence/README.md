# Evidence 索引

本目录保存提交应用实际运行产生的截图、原始 JSON/CSV 与 seekdb 服务端事件。

| 文件 | 说明 |
| --- | --- |
| `bench-runs.csv` | 17 个 benchmark run 的逐项计时（便于复核中位数） |
| `bench-summary.json` / `bench-summary.md` | 分组中位数、质量与模型体积汇总 |
| `bench-*.json` | 单次 benchmark 完整结果：配置、文件体积、分阶段耗时、per-query 结果、日志、console 警告 |
| `bench-*.png` | 评测台结果页截图 |
| `e2e-ui-webgpu.json` | 完整 UI E2E：创建库、导入 46 篇、混合/语义/生活查询、查询缓存、46 篇增量重复导入、文档页、新建隔离库导入 8 篇、切换隔离与切回 |
| `fallback-test.json` | WebGPU 自动选中、禁用 WebGPU 后自动降级 WASM、WASM 下完整导入+混合检索 |
| `api-test.json` | 20 项 API 测试：增量 upsert、刷新、三种检索、切换隔离、409 守卫、维度校验、删除 |
| `network-check.json` | 完整导入 + 检索期间的外部网络请求检查（零外部请求；模型与 ORT 运行时均来自本机） |
| `quantization-manifest.json` | 模型来源/revision、INT8/FP16 量化参数、体积、SHA-256、工具版本 |
| `server-events-final.txt` | 最终 E2E 对应的 seekdb server 事件（建库/写入/刷新/检索）；完整 `server.log` 保留在本地未提交 |
| `ui-10..17*.png` | 主应用截图：模型就绪、导入完成、混合检索、语义检索、查询缓存、文档、知识库隔离、切回 |
| `ui-20..22*.png` | 自动选择 WebGPU、自动降级 WASM、WASM 下检索结果 |
| `ui-23-network-local.png` | 零外部网络请求下的本地导入与检索 |

`bench-*` 中的 `modelLoadMs` 为每次新开浏览器进程的冷加载；同一页面内 `warmLoadMs` 为 Cache API 热重载。`e2eMs` 包含 Windows Chrome → WSL 后端的 HTTP 回环（约 45 ms）。

截图与数据均来自 2026-10-29 的最终构建；配置见 `BENCHMARK.md`。

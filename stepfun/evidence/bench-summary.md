# Benchmark summary

Generated 2026-10-29T13:50:03.098Z from 17 raw runs. Values are medians across repeated runs where available.

## Performance

| 配置 | 后端 / 精度 | 批大小 | 冷加载中位数 | 热重载中位数 | 向量化 docs/s | 写入 ms | 刷新 ms | 查询向量化 ms | seekdb 检索 ms | 端到端 ms |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| wasm-fp32-b16 | wasm / fp32 | 16 | 1.63 s | - | 73.4 | 172 | 511 | 11.48 | 3.16 | 62.03 |
| wasm-q8-b1 | wasm / q8 | 1 | 849 ms | - | 80.5 | 153 | 504 | 6.69 | 3.14 | 57.23 |
| wasm-q8-b16 | wasm / q8 | 16 | 958 ms | 81 ms | 99.1 | 169 | 505 | 7.80 | 3.17 | 57.63 |
| wasm-q8-b32 | wasm / q8 | 32 | 1.08 s | - | 85.1 | 186 | 704 | 6.88 | 3.27 | 56.20 |
| webgpu-fp16-b1 | webgpu / fp16 | 1 | 1.61 s | - | 112.3 | 192 | 602 | 8.92 | 3.19 | 57.43 |
| webgpu-fp16-b16 | webgpu / fp16 | 16 | 1.64 s | 118 ms | 283.6 | 183 | 552 | 10.93 | 3.25 | 61.68 |
| webgpu-fp16-b32 | webgpu / fp16 | 32 | 1.61 s | - | 293.9 | 198 | 502 | 11.60 | 3.20 | 61.79 |
| webgpu-fp16-perbatch-b16 | webgpu / fp16 | 16 | 1.63 s | - | 283.6 | 1485 | 1308 | 12.24 | 3.07 | 61.45 |
| webgpu-fp32-b16 | webgpu / fp32 | 16 | 2.20 s | - | 255.0 | 128 | 504 | 14.92 | 3.37 | 65.44 |

## Retrieval quality (46 docs / 24 labeled queries)

| 配置 | 混合 R@5 | 混合 R@10 | 混合 nDCG@10 | 仅向量 R@5 | 仅向量 nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| wasm-fp32-b16 | 82.6% | 92.4% | 86.6% | 79.9% | 80.5% |
| wasm-q8-b1 | 81.3% | 91.0% | 84.3% | 75.7% | 79.7% |
| wasm-q8-b16 | 82.6% | 92.4% | 86.5% | 82.6% | 80.5% |
| wasm-q8-b32 | 82.6% | 92.4% | 86.5% | 82.6% | 80.5% |
| webgpu-fp16-b1 | 84.0% | 92.4% | 85.2% | 75.7% | 80.4% |
| webgpu-fp16-b16 | 84.0% | 92.4% | 86.7% | 79.9% | 80.5% |
| webgpu-fp16-b32 | 84.0% | 92.4% | 86.7% | 79.9% | 80.5% |
| webgpu-fp16-perbatch-b16 | 84.0% | 92.4% | 86.7% | 79.9% | 80.5% |
| webgpu-fp32-b16 | 82.6% | 92.4% | 86.6% | 79.9% | 80.5% |

## Model sizes

| 精度 | 体积 (bytes) | 体积 (MiB) |
| --- | ---: | ---: |
| fp32 | 94851877 | 90.5 |
| fp16 | 47497427 | 45.3 |
| q8 | 24011364 | 22.9 |

Notes: e2e includes query embedding + HTTP round trip (Windows Chrome → WSL backend) + seekdb retrieval + JSON; seekdb retrieval is measured inside the server around the hybridSearch call.
# BGE-small-zh-v1.5（端侧 embedding 模型）

## 来源与版本

| 项目 | 值 |
| --- | --- |
| 基座模型 | [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5) |
| 基座 revision | `7999e1d3359715c523056ef9478215996d62a620` |
| 许可证 | MIT |
| ONNX 权重 | [Xenova/bge-small-zh-v1.5](https://huggingface.co/Xenova/bge-small-zh-v1.5) |
| ONNX revision | `75c43b069aac4d136ba6bc1122f995fedcfd2781` |
| 向量维度 | 512 |
| 池化 / 归一化 | CLS pooling / L2 normalize |
| 查询指令 | `为这个句子生成表示以用于检索相关文章：` |

`config.json`、`tokenizer.json`、`tokenizer_config.json`、`special_tokens_map.json` 随仓库提交；tokenizer 与模型配置总大小 < 0.5 MB。

## 提交在本仓库的模型文件

| 文件 | 精度 | 体积 | SHA-256 |
| --- | --- | ---: | --- |
| `onnx/model_quantized.onnx` | INT8（动态量化，per-channel） | 24,011,364 B（22.9 MiB） | `3878b70c12c509d4ada124df758cbeebf331a4f174b0d47a30bea5dc41aab9a4` |
| `onnx/model_fp16.onnx` | FP16（保留 FP32 I/O） | 47,497,427 B（45.3 MiB） | `8e785b3017e6ab0b78310118ead69ffed5a6049b67f6ac1350eabccc8f1bf6bc` |

## 未提交的基线模型

`onnx/model.onnx`（FP32，94,851,877 B，SHA-256 `69a0b846f4f116b5e6aabf9546ea6754d02264f3211a13a1bd69b31b8040749a`）超过单文件提交的合理体积，按需下载：

```bash
npm run models:fetch -- --variant fp32
```

浏览器中的量化基线对比会用到该文件；应用默认只加载 INT8/FP16，不需要 FP32。

## 量化方法

工具环境（`scripts/requirements-quant.txt`）：Python 3.11.9、onnx 1.19.1、onnxruntime 1.23.2。

```bash
python -m venv .venv-quant
.venv-quant/bin/pip install -r scripts/requirements-quant.txt
.venv-quant/bin/python scripts/quantize_embedding.py \
  --input models/bge-small-zh-v1.5/onnx/model.onnx \
  --outdir models/bge-small-zh-v1.5/onnx \
  --modes int8,fp16
```

- **INT8**：`onnxruntime.quantization.quantize_dynamic(..., weight_type=QInt8, per_channel=True)`，动态量化激活，无需校准集；脚本内置 ONNX checker 与 CPU 推理校验（输出 `[1, 8, 512]`）。
- **FP16**：`onnxruntime.transformers.float16.convert_float_to_float16(..., keep_io_types=True)`，保留 tokenizer 接口的 int64 输入与 FP32 输出边界。

## 体积与压缩比

| 精度 | 体积 | 相对 FP32 | 说明 |
| --- | ---: | ---: | --- |
| FP32 | 94,851,877 B（90.5 MiB） | 1.00× | 原始 ONNX |
| FP16 | 47,497,427 B（45.3 MiB） | 2.00×（压缩比 1.997） | WebGPU 首选 |
| INT8 | 24,011,364 B（22.9 MiB） | **3.95×** | WASM 首选 |

质量对比（同一 46 篇语料、24 条查询、混合检索）见 `TECH_NOTE.md` 与 `BENCHMARK.md`；原始数据在 `evidence/bench-*.json`。

## 运行位置

模型文件由本地 Web 服务 `/models/` 提供，Transformers.js 在浏览器 Worker 中加载并推理。
**不调用任何云端推理 API**；断网后只要模型文件已随应用部署，推理仍可运行。

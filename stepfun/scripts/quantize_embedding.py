#!/usr/bin/env python3
"""Quantize the BGE-small-zh-v1.5 ONNX embedding model to INT8 / FP16.

INT8 uses ONNX Runtime dynamic quantization (QInt8 weights, per-channel).
FP16 uses ONNX Runtime's float16 converter with float32 I/O kept for the tokenizer interface.

Usage:
  python scripts/quantize_embedding.py \
    --input models/bge-small-zh-v1.5/onnx/model.onnx \
    --outdir models/bge-small-zh-v1.5/onnx \
    --modes int8,fp16
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import time

import numpy as np
import onnx
import onnxruntime as ort
from onnxruntime.quantization import QuantType, quantize_dynamic
from onnxruntime.transformers.float16 import convert_float_to_float16


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_model(path: str, run_inference: bool) -> dict:
    onnx.checker.check_model(path)
    result = {"checker": "ok", "inference": "skipped"}
    if not run_inference:
        return result
    session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    feeds = {}
    for item in session.get_inputs():
        shape = []
        for index, dim in enumerate(item.shape):
            if isinstance(dim, int) and dim > 0:
                shape.append(dim)
            else:
                shape.append(1 if index == 0 else 8)
        if item.type == "tensor(int64)" or "int" in item.type:
            feeds[item.name] = np.ones(shape, dtype=np.int64)
        else:
            feeds[item.name] = np.ones(shape, dtype=np.float32)
    if "attention_mask" in feeds:
        feeds["attention_mask"] = np.ones_like(feeds["attention_mask"])
    outputs = session.run(None, feeds)
    result["inference"] = "ok"
    result["output_shape"] = list(outputs[0].shape)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="models/bge-small-zh-v1.5/onnx/model.onnx")
    parser.add_argument("--outdir", default="models/bge-small-zh-v1.5/onnx")
    parser.add_argument("--modes", default="int8,fp16")
    parser.add_argument("--manifest", default="models/bge-small-zh-v1.5/model-manifest.json")
    args = parser.parse_args()

    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip()]
    os.makedirs(args.outdir, exist_ok=True)
    before_size = os.path.getsize(args.input)
    before_hash = sha256(args.input)
    variants: dict[str, dict] = {}
    started = time.time()

    if "int8" in modes:
        out = os.path.join(args.outdir, "model_quantized.onnx")
        t0 = time.time()
        quantize_dynamic(
            args.input,
            out,
            weight_type=QuantType.QInt8,
            per_channel=True,
            reduce_range=False,
        )
        variants["model_quantized.onnx"] = {
            "mode": "INT8 dynamic quantization",
            "size_bytes": os.path.getsize(out),
            "sha256": sha256(out),
            "seconds": round(time.time() - t0, 2),
            "validation": validate_model(out, run_inference=True),
        }

    if "fp16" in modes:
        out = os.path.join(args.outdir, "model_fp16.onnx")
        t0 = time.time()
        model = onnx.load(args.input)
        converted = convert_float_to_float16(model, keep_io_types=True, disable_shape_infer=False)
        onnx.save(converted, out)
        variants["model_fp16.onnx"] = {
            "mode": "FP16 conversion (float32 I/O kept)",
            "size_bytes": os.path.getsize(out),
            "sha256": sha256(out),
            "seconds": round(time.time() - t0, 2),
            "validation": validate_model(out, run_inference=False),
        }

    q8_size = variants.get("model_quantized.onnx", {}).get("size_bytes")
    fp16_size = variants.get("model_fp16.onnx", {}).get("size_bytes")
    manifest = {
        "model": "bge-small-zh-v1.5",
        "source": {
            "base_model": "https://huggingface.co/BAAI/bge-small-zh-v1.5",
            "base_revision": "7999e1d3359715c523056ef9478215996d62a620",
            "license": "MIT",
            "onnx_weights": "https://huggingface.co/Xenova/bge-small-zh-v1.5",
            "onnx_revision": "75c43b069aac4d136ba6bc1122f995fedcfd2781",
            "onnx_file": "onnx/model.onnx (FP32)",
        },
        "dimension": 512,
        "pooling": "cls",
        "normalize": True,
        "query_instruction": "为这个句子生成表示以用于检索相关文章：",
        "input_fp32": {"size_bytes": before_size, "sha256": before_hash},
        "quantization": {
            "int8": "onnxruntime.quantization.quantize_dynamic(weight_type=QInt8, per_channel=True)",
            "fp16": "onnxruntime.transformers.float16.convert_float_to_float16(keep_io_types=True)",
            "tools": {
                "onnx": onnx.__version__,
                "onnxruntime": ort.__version__,
                "numpy": np.__version__,
                "python": platform.python_version(),
            },
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "seconds_total": round(time.time() - started, 2),
        },
        "compression": {
            "int8_ratio": round(before_size / q8_size, 3) if q8_size else None,
            "fp16_ratio": round(before_size / fp16_size, 3) if fp16_size else None,
        },
        "files": variants,
    }

    os.makedirs(os.path.dirname(args.manifest), exist_ok=True)
    with open(args.manifest, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

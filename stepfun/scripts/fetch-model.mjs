#!/usr/bin/env node
/**
 * Fetch the public BGE-small-zh-v1.5 ONNX files.
 *
 * Committed in this repo: tokenizer/config files + INT8/FP16 quantized ONNX.
 * Not committed: FP32 ONNX (95 MB), fetched on demand for the quality baseline.
 *
 * Usage:
 *   node scripts/fetch-model.mjs                  # fetch missing tokenizer/config
 *   node scripts/fetch-model.mjs --variant fp32   # also fetch FP32 model
 *   node scripts/fetch-model.mjs --variant all --force
 */

import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";

const root = path.resolve("models/bge-small-zh-v1.5");
const primary = process.env.HF_ENDPOINT || "https://huggingface.co";
const mirror = process.env.HF_MIRROR || "https://hf-mirror.com";
const repo = "Xenova/bge-small-zh-v1.5";
const revision = "75c43b069aac4d136ba6bc1122f995fedcfd2781";
const args = process.argv.slice(2);
const variant = args.includes("--variant") ? args[args.indexOf("--variant") + 1] : "base";
const force = args.includes("--force");

const baseFiles = [
  "config.json",
  "tokenizer.json",
  "tokenizer_config.json",
  "special_tokens_map.json",
];
const quantized = ["onnx/model_quantized.onnx", "onnx/model_fp16.onnx"];
const fp32 = ["onnx/model.onnx"];

const hashes = {
  "onnx/model.onnx": "69a0b846f4f116b5e6aabf9546ea6754d02264f3211a13a1bd69b31b8040749a",
  "onnx/model_quantized.onnx": "3878b70c12c509d4ada124df758cbeebf331a4f174b0d47a30bea5dc41aab9a4",
  "onnx/model_fp16.onnx": "8e785b3017e6ab0b78310118ead69ffed5a6049b67f6ac1350eabccc8f1bf6bc",
};

function sha256(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

async function download(file) {
  const dest = path.join(root, file);
  if (fs.existsSync(dest) && fs.statSync(dest).size > 0 && !force) {
    console.log(`skip ${file} (${fs.statSync(dest).size} bytes)`);
    return;
  }
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  const errors = [];
  for (const host of [primary, mirror]) {
    const url = `${host}/${repo}/resolve/${revision}/${file}`;
    try {
      console.log(`GET ${url}`);
      const response = await fetch(url, { redirect: "follow" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const buffer = Buffer.from(await response.arrayBuffer());
      fs.writeFileSync(dest, buffer);
      console.log(`saved ${file} (${buffer.length} bytes)`);
      return;
    } catch (error) {
      errors.push(`${host}: ${error.message}`);
      console.warn(`  failed: ${error.message}`);
    }
  }
  throw new Error(`could not fetch ${file}\n${errors.join("\n")}`);
}

function verify(file) {
  const dest = path.join(root, file);
  if (!fs.existsSync(dest)) {
    console.error(`missing ${file}`);
    process.exitCode = 1;
    return;
  }
  const buffer = fs.readFileSync(dest);
  const digest = sha256(buffer);
  const expected = hashes[file];
  if (expected && digest !== expected) {
    console.error(`hash mismatch ${file}\n  expected ${expected}\n  actual   ${digest}`);
    process.exitCode = 1;
  } else {
    console.log(`ok ${file} ${buffer.length} bytes sha256=${digest}${expected ? "" : " (no pinned hash)"}`);
  }
}

const files = [...baseFiles];
if (variant === "fp32" || variant === "all") files.push(...fp32);
if (variant === "all") files.push(...quantized);
for (const file of files) await download(file);
for (const file of files) verify(file);

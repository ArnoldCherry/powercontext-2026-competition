/// <reference lib="webworker" />

import { env, pipeline } from "@huggingface/transformers";

type Backend = "webgpu" | "wasm";
type Dtype = "fp16" | "fp32" | "q8";

type InitMessage = {
  type: "init";
  backend: "auto" | Backend;
  dtype: "auto" | Dtype;
};

type EmbedMessage = {
  type: "embed";
  requestId: number;
  texts: string[];
  kind: "doc" | "query";
  batchSize?: number;
  bypassCache?: boolean;
};

type Message = InitMessage | EmbedMessage | { type: "dispose" } | { type: "ping" };

const MODEL_ID = "bge-small-zh-v1.5";
const QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章：";
const MODEL_BYTES: Record<Dtype, number> = {
  fp32: 94851877,
  fp16: 47497427,
  q8: 24011364,
};

env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = `${self.location.origin}/models/`;
env.useBrowserCache = true;
(env as any).logLevel = 40;

const threads = (self as any).crossOriginIsolated
  ? Math.max(1, Math.min(4, navigator.hardwareConcurrency || 2))
  : 1;
try {
  (env.backends as any).onnx.wasm.numThreads = threads;
  (env.backends as any).onnx.wasm.proxy = false;
  // Keep the ONNX Runtime WASM loader local; the default is a jsDelivr CDN.
  (env.backends as any).onnx.wasm.wasmPaths = new URL("/ort/", self.location.origin).href;
} catch {
  // ignore
}

let extractor: any = null;
let currentBackend: Backend | null = null;
let currentDtype: Dtype | null = null;
let adapterLabel: string | undefined;
let fallbackReason: string | undefined;
let queryCache = new Map<string, number[]>();

function post(message: unknown): void {
  (self as any).postMessage(message);
}

function toError(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

async function webgpuAdapter(): Promise<any> {
  const gpu = (navigator as any).gpu;
  if (!gpu) return null;
  try {
    return await gpu.requestAdapter();
  } catch {
    return null;
  }
}

function adapterString(adapter: any): string {
  const info = adapter.info;
  if (!info) return "WebGPU adapter";
  return [info.vendor, info.architecture, info.device, info.description]
    .filter(Boolean)
    .join(" / ") || "WebGPU adapter";
}

function candidates(requested: "auto" | Backend, dtype: "auto" | Dtype): Array<{ backend: Backend; dtype: Dtype }> {
  const out: Array<{ backend: Backend; dtype: Dtype }> = [];
  if (requested === "wasm") {
    out.push({ backend: "wasm", dtype: dtype === "auto" ? "q8" : dtype });
    return out;
  }
  if (requested === "webgpu") {
    out.push({ backend: "webgpu", dtype: dtype === "auto" ? "fp16" : dtype });
    if (dtype === "auto") out.push({ backend: "webgpu", dtype: "fp32" });
    out.push({ backend: "wasm", dtype: "q8" });
    return out;
  }
  out.push({ backend: "wasm", dtype: "q8" });
  return out;
}

async function init(message: InitMessage): Promise<void> {
  const started = performance.now();
  await disposePipeline();
  fallbackReason = undefined;
  adapterLabel = undefined;

  let adapter: any = null;
  if (message.backend !== "wasm") {
    post({ type: "status", stage: "checking", message: "检测 WebGPU 适配器" });
    adapter = await webgpuAdapter();
    if (adapter) adapterLabel = adapterString(adapter);
  }
  if (message.backend === "auto" && !adapter) {
    fallbackReason = "未检测到可用的 WebGPU 适配器，已自动降级到 WASM / INT8";
  }

  let ordered: Array<{ backend: Backend; dtype: Dtype }>;
  if (message.backend === "auto") {
    ordered = [];
    if (adapter) {
      const supportsF16 = (adapter.features as any).has?.("shader-f16");
      ordered.push({ backend: "webgpu", dtype: message.dtype === "auto" ? (supportsF16 ? "fp16" : "fp32") : message.dtype });
      ordered.push({ backend: "webgpu", dtype: "fp32" });
    }
    ordered.push({ backend: "wasm", dtype: message.dtype === "auto" ? "q8" : message.dtype });
    if (message.dtype !== "auto" && message.dtype !== "q8") {
      ordered.push({ backend: "wasm", dtype: "q8" });
    }
    ordered = ordered.filter(
      (item, index) =>
        ordered.findIndex((other) => other.backend === item.backend && other.dtype === item.dtype) === index
    );
  } else {
    ordered = candidates(message.backend, message.dtype);
  }

  const errors: string[] = [];
  for (const candidate of ordered) {
    try {
      post({
        type: "status",
        stage: "loading",
        backend: candidate.backend,
        dtype: candidate.dtype,
        message: `加载 ${candidate.backend.toUpperCase()} / ${candidate.dtype.toUpperCase()} 模型`,
        loadMs: performance.now() - started,
        adapter: adapterLabel,
        threads,
      });
      const pipe = await pipeline("feature-extraction", MODEL_ID, {
        device: candidate.backend,
        dtype: candidate.dtype,
        local_files_only: true,
        progress_callback: (progress: any) => {
          post({
            type: "status",
            stage: "loading",
            backend: candidate.backend,
            dtype: candidate.dtype,
            message: progress?.status === "progress" ? `下载 ${progress.file ?? ""}` : "初始化模型",
            loadMs: performance.now() - started,
            progress: progress?.progress,
            loaded: progress?.loaded,
            total: progress?.total,
            file: progress?.file,
            adapter: adapterLabel,
            threads,
          });
        },
      });
      extractor = pipe;
      currentBackend = candidate.backend;
      currentDtype = candidate.dtype;
      const warmStarted = performance.now();
      const warm = await extractor("seekdb 端侧向量检索预热", { pooling: "cls", normalize: true });
      warm?.dispose?.();
      const warmupMs = performance.now() - warmStarted;
      post({
        type: "status",
        stage: "ready",
        backend: currentBackend,
        dtype: currentDtype,
        device: candidate.backend,
        modelId: MODEL_ID,
        loadMs: performance.now() - started,
        warmupMs,
        threads,
        adapter: adapterLabel,
        fallbackReason,
        modelBytes: MODEL_BYTES[currentDtype],
        message: "模型就绪",
      });
      return;
    } catch (error) {
      const messageText = toError(error);
      errors.push(`${candidate.backend}/${candidate.dtype}: ${messageText}`);
      if (candidate.backend === "webgpu") {
        fallbackReason = `WebGPU ${candidate.dtype} 加载失败，自动降级 WASM：${messageText}`;
      }
      await disposePipeline();
    }
  }

  post({
    type: "status",
    stage: "error",
    message: `模型加载失败：${errors.join(" | ")}`,
    adapter: adapterLabel,
    threads,
  });
}

async function disposePipeline(): Promise<void> {
  if (extractor) {
    try {
      await extractor.dispose?.();
    } catch {
      // ignore
    }
  }
  extractor = null;
  currentBackend = null;
  currentDtype = null;
  queryCache.clear();
}

function rememberQuery(key: string, embedding: number[]): void {
  if (queryCache.has(key)) queryCache.delete(key);
  queryCache.set(key, embedding);
  while (queryCache.size > 64) {
    const first = queryCache.keys().next().value;
    if (first === undefined) break;
    queryCache.delete(first);
  }
}

async function embed(message: EmbedMessage): Promise<void> {
  if (!extractor || !currentBackend || !currentDtype) {
    post({ type: "error", requestId: message.requestId, message: "模型尚未加载" });
    return;
  }
  if (!Array.isArray(message.texts) || message.texts.length === 0) {
    post({ type: "error", requestId: message.requestId, message: "texts 不能为空" });
    return;
  }
  const batchSize = Math.max(1, Math.min(32, message.batchSize || 16));
  const started = performance.now();
  const embeddings: number[][] = new Array(message.texts.length);
  let cacheHits = 0;
  let dim = 512;

  try {
    for (let start = 0; start < message.texts.length; start += batchSize) {
      const batchIndexes: number[] = [];
      const batch: string[] = [];
      for (let i = start; i < Math.min(start + batchSize, message.texts.length); i++) {
        const raw = String(message.texts[i] ?? "");
        const text = message.kind === "query" ? QUERY_PREFIX + raw : raw;
        if (message.kind === "query" && !message.bypassCache) {
          const cached = queryCache.get(text);
          if (cached) {
            embeddings[i] = cached;
            cacheHits++;
            continue;
          }
        }
        batchIndexes.push(i);
        batch.push(text);
      }
      if (batch.length > 0) {
        const batchStarted = performance.now();
        const output = await extractor(batch, { pooling: "cls", normalize: true });
        const data = output.data as Float32Array;
        dim = output.dims[output.dims.length - 1];
        for (let row = 0; row < batch.length; row++) {
          const vector = Array.from(data.slice(row * dim, (row + 1) * dim));
          embeddings[batchIndexes[row]] = vector;
          if (message.kind === "query" && !message.bypassCache) {
            rememberQuery(batch[row], vector);
          }
        }
        const batchMs = performance.now() - batchStarted;
        output?.dispose?.();
        post({
          type: "progress",
          requestId: message.requestId,
          kind: message.kind,
          done: Math.min(start + batch.length, message.texts.length),
          total: message.texts.length,
          batchMs,
          elapsedMs: performance.now() - started,
        });
      }
    }
    post({
      type: "result",
      requestId: message.requestId,
      embeddings,
      dim,
      embedMs: performance.now() - started,
      cacheHit: cacheHits === message.texts.length,
    });
  } catch (error) {
    post({ type: "error", requestId: message.requestId, message: toError(error) });
  }
}

self.onmessage = (event: MessageEvent<Message>) => {
  const message = event.data;
  if (!message || typeof message !== "object") return;
  if (message.type === "init") void init(message);
  else if (message.type === "embed") void embed(message);
  else if (message.type === "dispose") void disposePipeline().then(() => post({ type: "status", stage: "disposed" }));
  else if (message.type === "ping") post({ type: "pong", backend: currentBackend, dtype: currentDtype });
};

post({ type: "status", stage: "boot", message: "worker 已启动", threads });

import type { EmbedderStatus } from "../types.ts";

export type EmbedResult = {
  embeddings: number[][];
  dim: number;
  embedMs: number;
  cacheHit: boolean;
};

export type EmbedProgress = {
  done: number;
  total: number;
  batchMs: number;
  elapsedMs: number;
};

export type EmbedOptions = {
  batchSize?: number;
  bypassCache?: boolean;
  onProgress?: (progress: EmbedProgress) => void;
};

export class EmbedderClient {
  private worker: Worker;
  private nextId = 1;
  private pending = new Map<
    number,
    {
      resolve: (result: EmbedResult) => void;
      reject: (error: Error) => void;
      onProgress?: (progress: EmbedProgress) => void;
    }
  >();
  private initResolve: ((status: EmbedderStatus) => void) | null = null;
  private initReject: ((error: Error) => void) | null = null;
  private disposed = false;

  status: EmbedderStatus | null = null;
  loadProgress: number | null = null;
  lastMessage = "worker 初始化";

  constructor(
    private readonly onStatus: (status: EmbedderStatus | null, detail: any) => void,
    private readonly onStatusDetail: (detail: any) => void
  ) {
    this.worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
    this.worker.onmessage = (event) => this.handleMessage(event.data);
    this.worker.onerror = (event) => {
      const message = event.message || "embedding worker error";
      this.onStatusDetail({ stage: "error", message });
      if (this.initReject) {
        this.initReject(new Error(message));
        this.initReject = null;
        this.initResolve = null;
      }
      for (const [, item] of this.pending) item.reject(new Error(message));
      this.pending.clear();
    };
  }

  private handleMessage(message: any): void {
    if (!message || typeof message !== "object") return;
    if (message.type === "status") {
      this.onStatusDetail(message);
      if (message.stage === "ready") {
        const status: EmbedderStatus = {
          backend: message.backend,
          dtype: message.dtype,
          device: message.device ?? message.backend,
          modelId: message.modelId ?? "bge-small-zh-v1.5",
          loadMs: message.loadMs ?? 0,
          warmupMs: message.warmupMs ?? 0,
          threads: message.threads ?? 1,
          adapter: message.adapter,
          fallbackReason: message.fallbackReason,
        };
        this.status = status;
        this.onStatus(status, message);
        this.loadProgress = 1;
        this.initResolve?.(status);
        this.initResolve = null;
        this.initReject = null;
      } else if (message.stage === "error") {
        this.lastMessage = message.message ?? "模型加载失败";
        this.onStatus(null, message);
        this.initReject?.(new Error(this.lastMessage));
        this.initReject = null;
        this.initResolve = null;
      } else if (message.stage === "loading") {
        if (typeof message.progress === "number") {
          this.loadProgress = message.progress > 1 ? message.progress / 100 : message.progress;
        }
        this.lastMessage = message.message ?? this.lastMessage;
      } else {
        this.lastMessage = message.message ?? this.lastMessage;
      }
      return;
    }
    if (message.type === "progress") {
      const pending = this.pending.get(message.requestId);
      pending?.onProgress?.({
        done: message.done,
        total: message.total,
        batchMs: message.batchMs,
        elapsedMs: message.elapsedMs,
      });
      return;
    }
    if (message.type === "result") {
      const pending = this.pending.get(message.requestId);
      if (!pending) return;
      this.pending.delete(message.requestId);
      pending.resolve({
        embeddings: message.embeddings,
        dim: message.dim,
        embedMs: message.embedMs,
        cacheHit: message.cacheHit,
      });
      return;
    }
    if (message.type === "error") {
      const pending = message.requestId ? this.pending.get(message.requestId) : null;
      if (pending) {
        this.pending.delete(message.requestId);
        pending.reject(new Error(message.message));
      } else {
        this.lastMessage = message.message;
      }
    }
  }

  async init(backend: "auto" | "webgpu" | "wasm", dtype: "auto" | "fp16" | "fp32" | "q8"): Promise<EmbedderStatus> {
    if (this.disposed) throw new Error("embedder disposed");
    if (this.initReject) this.initReject(new Error("init superseded"));
    return new Promise<EmbedderStatus>((resolve, reject) => {
      this.initResolve = resolve;
      this.initReject = reject;
      this.worker.postMessage({ type: "init", backend, dtype });
    });
  }

  async embed(texts: string[], kind: "doc" | "query", options: EmbedOptions = {}): Promise<EmbedResult> {
    if (!this.status) throw new Error("模型尚未加载");
    const requestId = this.nextId++;
    return new Promise<EmbedResult>((resolve, reject) => {
      this.pending.set(requestId, {
        resolve,
        reject,
        onProgress: options.onProgress,
      });
      this.worker.postMessage({
        type: "embed",
        requestId,
        texts,
        kind,
        batchSize: options.batchSize ?? 16,
        bypassCache: options.bypassCache ?? false,
      });
    });
  }

  async dispose(): Promise<void> {
    this.worker.postMessage({ type: "dispose" });
    this.status = null;
    this.loadProgress = null;
  }

  terminate(): void {
    this.disposed = true;
    this.worker.terminate();
    for (const [, item] of this.pending) item.reject(new Error("embedder terminated"));
    this.pending.clear();
  }
}

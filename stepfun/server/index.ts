import fs from "node:fs";
import path from "node:path";
import express from "express";
import type { NextFunction, Request, Response } from "express";
import { SeekdbStore } from "./db.ts";
import { SessionStore } from "./sessions.ts";
import type { KbRecord, SessionState } from "./types.ts";

const PORT = Number(process.env.PORT || 8788);
const ROOT = process.cwd();
const MODELS_DIR = path.resolve(process.env.MODELS_DIR || "models");
const DIST_DIR = path.resolve("dist");
const COOKIE = "stepfun_sid";

const store = new SeekdbStore();
const sessions = new SessionStore();
const app = express();

app.disable("x-powered-by");
app.use((req, res, next) => {
  res.setHeader("Cross-Origin-Opener-Policy", "same-origin");
  res.setHeader("Cross-Origin-Embedder-Policy", "require-corp");
  res.setHeader("Cross-Origin-Resource-Policy", "same-origin");
  next();
});
app.use(express.json({ limit: "16mb" }));

function parseCookies(header: string | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  if (!header) return out;
  for (const part of header.split(";")) {
    const index = part.indexOf("=");
    if (index < 0) continue;
    out[part.slice(0, index).trim()] = decodeURIComponent(part.slice(index + 1).trim());
  }
  return out;
}

function ensureSession(req: Request, res: Response): SessionState {
  const cookies = parseCookies(req.headers.cookie);
  const before = cookies[COOKIE];
  const session = sessions.getOrCreate(before);
  if (before !== session.id) {
    res.setHeader(
      "Set-Cookie",
      `${COOKIE}=${encodeURIComponent(session.id)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800`
    );
  }
  return session;
}

function log(event: string, data: Record<string, unknown>): void {
  const line = JSON.stringify({ ts: new Date().toISOString(), event, ...data });
  process.stdout.write(line + "\n");
}

function route(
  handler: (req: Request, res: Response) => Promise<void>
): (req: Request, res: Response, next: NextFunction) => void {
  return (req, res, next) => {
    const started = performance.now();
    handler(req, res)
      .catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        const code = (error as any)?.code;
        log("api_error", { method: req.method, path: req.path, message, code });
        if (!res.headersSent) {
          if (code === "KB_CHANGED") {
            res.status(409).json({
              error: "KB_CHANGED",
              message,
              activeKbId: (error as any).activeKbId ?? null,
            });
          } else {
            res.status(400).json({ error: message });
          }
        }
      })
      .finally(() => {
        log("api_request", {
          method: req.method,
          path: req.path,
          status: res.statusCode,
          ms: Math.round((performance.now() - started) * 100) / 100,
        });
      });
  };
}

async function requireActiveKb(session: SessionState): Promise<KbRecord> {
  if (!session.activeKbId) throw new Error("当前会话没有激活的知识库");
  const kb = await store.getKb(session.activeKbId);
  if (!kb) throw new Error("当前知识库不存在，请重新选择");
  return kb;
}

function assertActive(session: SessionState, expected: unknown): void {
  if (expected !== undefined && expected !== null && String(expected) !== session.activeKbId) {
    const error = new Error("知识库已切换，请基于当前知识库重新操作");
    (error as any).code = "KB_CHANGED";
    (error as any).activeKbId = session.activeKbId;
    throw error;
  }
}

app.get("/api/health", route(async (_req, res) => {
  res.json(await store.health());
}));

app.get("/api/state", route(async (req, res) => {
  const session = ensureSession(req, res);
  const kbs = await store.listKbs();
  if (!session.activeKbId && kbs.length > 0) {
    session.activeKbId = kbs[0].id;
  }
  if (session.activeKbId && !kbs.some((kb) => kb.id === session.activeKbId)) {
    session.activeKbId = kbs[0]?.id ?? null;
  }
  res.json({
    sessionId: session.id,
    activeKbId: session.activeKbId,
    kbs,
    backend: { mode: store.mode, database: store.database },
  });
}));

app.get("/api/model-info", route(async (_req, res) => {
  const modelDir = path.join(MODELS_DIR, "bge-small-zh-v1.5");
  const manifestPath = path.join(modelDir, "model-manifest.json");
  const manifest = fs.existsSync(manifestPath)
    ? JSON.parse(fs.readFileSync(manifestPath, "utf8"))
    : {};
  const files = [
    { dtype: "fp32", file: "onnx/model.onnx", label: "FP32 原始模型" },
    { dtype: "fp16", file: "onnx/model_fp16.onnx", label: "FP16 量化模型" },
    { dtype: "q8", file: "onnx/model_quantized.onnx", label: "INT8 动态量化模型" },
  ].map((item) => {
    const full = path.join(modelDir, item.file);
    const exists = fs.existsSync(full);
    const fallbackBytes =
      item.dtype === "fp32"
        ? manifest?.input_fp32?.size_bytes
        : manifest?.files?.[path.basename(item.file)]?.size_bytes;
    return {
      ...item,
      exists,
      bytes: exists ? fs.statSync(full).size : Number(fallbackBytes ?? 0),
      sha256: manifest?.files?.[path.basename(item.file)]?.sha256 ?? null,
    };
  });
  res.json({
    id: "bge-small-zh-v1.5",
    dim: 512,
    pooling: "cls",
    queryInstruction: "为这个句子生成表示以用于检索相关文章：",
    source: "https://huggingface.co/BAAI/bge-small-zh-v1.5 (ONNX: Xenova/bge-small-zh-v1.5)",
    runtime: "@huggingface/transformers 4.3.2 (ONNX Runtime Web)",
    manifest,
    files,
  });
}));

app.get("/api/kbs", route(async (_req, res) => {
  res.json({ kbs: await store.listKbs() });
}));

app.post("/api/kbs", route(async (req, res) => {
  const session = ensureSession(req, res);
  const kb = await store.createKb(String(req.body?.name ?? ""));
  session.activeKbId = kb.id;
  log("kb_created", { kbId: kb.id, name: kb.name, collection: kb.collection });
  res.json({ kb, activeKbId: kb.id });
}));

app.post("/api/kbs/:id/activate", route(async (req, res) => {
  const session = ensureSession(req, res);
  const kb = await store.getKb(String(req.params.id));
  if (!kb) throw new Error("知识库不存在");
  session.activeKbId = kb.id;
  log("kb_activated", { kbId: kb.id, collection: kb.collection, sessionId: session.id });
  res.json({ activeKbId: kb.id });
}));

app.delete("/api/kbs/:id", route(async (req, res) => {
  const session = ensureSession(req, res);
  const kbId = String(req.params.id);
  const wasActive = session.activeKbId === kbId;
  const deleted = await store.deleteKb(kbId);
  if (deleted) sessions.clearKb(kbId);
  if (wasActive || !session.activeKbId) {
    const kbs = await store.listKbs();
    session.activeKbId = kbs[0]?.id ?? null;
  }
  log("kb_deleted", { kbId, activeKbId: session.activeKbId });
  res.json({ deleted, activeKbId: session.activeKbId });
}));

app.get("/api/kbs/:id/docs", route(async (req, res) => {
  const session = ensureSession(req, res);
  assertActive(session, String(req.params.id));
  const kb = await requireActiveKb(session);
  const limit = Math.min(200, Math.max(1, Number(req.query.limit) || 100));
  const offset = Math.max(0, Number(req.query.offset) || 0);
  const data = await store.getDocs(kb, limit, offset);
  res.json({ kb, ...data });
}));

app.post("/api/kbs/:id/docs", route(async (req, res) => {
  const session = ensureSession(req, res);
  assertActive(session, req.body?.expectedKbId);
  assertActive(session, String(req.params.id));
  const kb = await requireActiveKb(session);
  const docs = Array.isArray(req.body?.docs) ? req.body.docs : [];
  const embeddings = Array.isArray(req.body?.embeddings) ? req.body.embeddings : [];
  if (docs.length !== embeddings.length) throw new Error("docs/embeddings 数量不一致");
  const inputs = docs.map((doc: any, index: number) => ({
    id: String(doc.id ?? ""),
    document: doc.document ?? doc.text ?? "",
    metadata: doc.metadata ?? {},
    embedding: embeddings[index],
  }));
  const result = await store.addDocs(kb, inputs);
  let refreshMs: number | undefined;
  if (req.body?.refresh === true) {
    refreshMs = (await store.refresh(kb)).refreshMs;
  }
  log("docs_imported", {
    kbId: kb.id,
    docs: result.written,
    writeMs: Math.round(result.writeMs),
    refreshMs: refreshMs === undefined ? undefined : Math.round(refreshMs),
    count: result.count,
  });
  res.json({ kbId: kb.id, ...result, refreshMs });
}));

app.post("/api/kbs/:id/refresh", route(async (req, res) => {
  const session = ensureSession(req, res);
  assertActive(session, req.body?.expectedKbId);
  assertActive(session, String(req.params.id));
  const kb = await requireActiveKb(session);
  const result = await store.refresh(kb);
  log("index_refreshed", { kbId: kb.id, refreshMs: Math.round(result.refreshMs) });
  res.json({ kbId: kb.id, ...result });
}));

app.delete("/api/kbs/:id/docs/:docId", route(async (req, res) => {
  const session = ensureSession(req, res);
  assertActive(session, String(req.params.id));
  const kb = await requireActiveKb(session);
  const result = await store.deleteDocs(kb, [String(req.params.docId)]);
  log("docs_deleted", { kbId: kb.id, count: result.count, deleteMs: Math.round(result.deleteMs) });
  res.json({ kbId: kb.id, ...result });
}));

app.post("/api/search", route(async (req, res) => {
  const session = ensureSession(req, res);
  assertActive(session, req.body?.expectedKbId);
  const kb = await requireActiveKb(session);
  const mode = ["hybrid", "vector", "keyword"].includes(req.body?.mode)
    ? req.body.mode
    : "hybrid";
  const result = await store.search(
    kb,
    String(req.body?.query ?? ""),
    req.body?.embedding,
    mode,
    Number(req.body?.topK) || 8
  );
  log("search", {
    kbId: kb.id,
    mode,
    q: result.query.slice(0, 80),
    hits: result.results.length,
    retrievalMs: Math.round(result.retrievalMs * 100) / 100,
  });
  res.json(result);
}));

app.get("/api/demo-corpus", route(async (req, res) => {
  const file = String(req.query.file ?? "kb-seekdb.json").replace(/[^a-zA-Z0-9._-]/g, "");
  const full = path.resolve("data/corpus", file);
  if (!full.startsWith(path.resolve("data/corpus")) || !fs.existsSync(full)) {
    res.status(404).json({ error: "corpus not found" });
    return;
  }
  res.type("application/json").send(fs.readFileSync(full));
}));

app.get("/api/eval-queries", route(async (_req, res) => {
  const full = path.resolve("data/queries.json");
  if (!fs.existsSync(full)) {
    res.status(404).json({ error: "queries not found" });
    return;
  }
  res.type("application/json").send(fs.readFileSync(full));
}));

app.use("/models", express.static(MODELS_DIR, {
  setHeaders: (res) => {
    res.setHeader("Cache-Control", "public, max-age=31536000, immutable");
  },
}));

if (fs.existsSync(DIST_DIR)) {
  app.use(express.static(DIST_DIR, { index: false }));
  app.use((req, res, next) => {
    if (req.method !== "GET" || req.path.startsWith("/api/") || req.path.startsWith("/models/")) {
      next();
      return;
    }
    res.sendFile(path.join(DIST_DIR, "index.html"));
  });
}

async function main(): Promise<void> {
  await store.init();
  const health = await store.health();
  log("server_start", { ...health, httpPort: PORT, root: ROOT });
  const server = app.listen(PORT, "0.0.0.0", () => {
    console.log(`\nStepFun on-device KB running at http://localhost:${PORT}`);
    console.log(`seekdb mode=${store.mode} database=${store.database}`);
  });

  const shutdown = async (signal: string) => {
    log("server_stop", { signal });
    server.close();
    await store.close();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));
}

main().catch((error) => {
  console.error("server failed to start", error);
  process.exit(1);
});

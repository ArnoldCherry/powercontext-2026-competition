import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import {
  FulltextIndexConfig,
  Schema,
  SeekdbClient,
  VectorIndexConfig,
} from "seekdb";
import type { Collection } from "seekdb";
import type {
  DocInput,
  DocMetadata,
  KbRecord,
  SearchMode,
  SearchResponse,
  SearchResult,
} from "./types.ts";

export const MODEL_ID = "bge-small-zh-v1.5";
export const EMBED_DIM = 512;
const MAX_DOCS_PER_BATCH = 64;
const MAX_DOC_CHARS = 8000;

type DbMode = "embedded" | "server";

type SeekdbConfig = {
  mode: DbMode;
  dataDir: string;
  host?: string;
  port?: number;
  user?: string;
  password?: string;
  database: string;
  tenant?: string;
};

function readConfig(): SeekdbConfig {
  const host = process.env.SEEKDB_HOST?.trim();
  return {
    mode: host ? "server" : "embedded",
    dataDir: process.env.SEEKDB_DATA_DIR
      ? path.resolve(process.env.SEEKDB_DATA_DIR)
      : path.resolve("data/seekdb"),
    host: host || undefined,
    port: process.env.SEEKDB_PORT ? Number(process.env.SEEKDB_PORT) : 2881,
    user: process.env.SEEKDB_USER || "root",
    password: process.env.SEEKDB_PASSWORD || "",
    database: process.env.SEEKDB_DATABASE || "stepfun",
    tenant: process.env.SEEKDB_TENANT || undefined,
  };
}

function normalizeText(text: unknown): string {
  if (typeof text !== "string") return "";
  return text.replace(/\r\n/g, "\n").trim();
}

function sanitizeFulltext(query: string): string {
  return query
    .replace(/[+\-&|!(){}[\]^"~*?:\\/]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 240);
}

function normalizeEmbedding(values: unknown): number[] {
  if (!Array.isArray(values) || values.length !== EMBED_DIM) {
    throw new Error(`embedding must be an array of ${EMBED_DIM} numbers`);
  }
  const out = new Array<number>(EMBED_DIM);
  let norm = 0;
  for (let i = 0; i < EMBED_DIM; i++) {
    const value = Number(values[i]);
    if (!Number.isFinite(value)) throw new Error("embedding contains non-finite value");
    out[i] = value;
    norm += value * value;
  }
  norm = Math.sqrt(norm);
  if (!(norm > 1e-6) || !Number.isFinite(norm)) throw new Error("embedding norm is invalid");
  for (let i = 0; i < EMBED_DIM; i++) out[i] /= norm;
  return out;
}

function splitNested<T>(values: T[] | T[][] | undefined): T[] {
  if (!values || values.length === 0) return [];
  const first = values[0];
  return (Array.isArray(first) ? (values as T[][])[0] : (values as T[])) ?? [];
}

export class SeekdbStore {
  private readonly config = readConfig();
  private client!: SeekdbClient;
  private adminClient: SeekdbClient | null = null;
  private readonly collections = new Map<string, Collection>();
  private readonly counts = new Map<string, number>();
  private readonly kbLocks = new Map<string, Promise<unknown>>();
  private initialized = false;

  get mode(): DbMode {
    return this.config.mode;
  }

  get database(): string {
    return this.config.database;
  }

  get dataDir(): string {
    return this.config.dataDir;
  }

  async init(): Promise<void> {
    if (this.initialized) return;
    if (this.config.mode === "server") {
      const admin = new SeekdbClient({
        host: this.config.host,
        port: this.config.port,
        user: this.config.user,
        password: this.config.password,
        database: "test",
        tenant: this.config.tenant,
      });
      this.adminClient = admin;
      try {
        await admin.createDatabase(this.config.database);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        if (!/exist|already/i.test(message)) {
          console.warn(`[seekdb] createDatabase note: ${message}`);
        }
      }
      this.client = new SeekdbClient({
        host: this.config.host,
        port: this.config.port,
        user: this.config.user,
        password: this.config.password,
        database: this.config.database,
        tenant: this.config.tenant,
      });
    } else {
      fs.mkdirSync(this.config.dataDir, { recursive: true });
      this.client = new SeekdbClient({
        path: this.config.dataDir,
        database: this.config.database,
      });
    }

    await this.client.execute(`
      CREATE TABLE IF NOT EXISTS kb_registry (
        id VARCHAR(32) PRIMARY KEY,
        name VARCHAR(128) NOT NULL,
        collection_name VARCHAR(160) NOT NULL,
        model VARCHAR(64),
        dim INT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
      )
    `);

    this.initialized = true;
    if ((await this.listKbs()).length === 0) {
      await this.createKb("默认知识库");
    }
  }

  async health(): Promise<Record<string, unknown>> {
    const rows = await this.client.execute("SELECT VERSION() AS version");
    const version = rows?.[0]?.version ?? "unknown";
    return {
      ok: true,
      mode: this.config.mode,
      database: this.config.database,
      dataDir: this.config.mode === "embedded" ? this.config.dataDir : undefined,
      host: this.config.mode === "server" ? this.config.host : undefined,
      port: this.config.mode === "server" ? this.config.port : undefined,
      version,
      node: process.version,
      platform: `${process.platform}-${process.arch}`,
    };
  }

  async listKbs(): Promise<KbRecord[]> {
    const rows =
      (await this.client.execute(
        "SELECT id, name, collection_name, model, dim, created_at FROM kb_registry ORDER BY created_at ASC"
      )) ?? [];
    const kbs: KbRecord[] = [];
    for (const row of rows) {
      const id = String(row.id);
      kbs.push({
        id,
        name: String(row.name),
        collection: String(row.collection_name),
        model: String(row.model ?? MODEL_ID),
        dim: Number(row.dim ?? EMBED_DIM),
        createdAt:
          row.created_at instanceof Date
            ? row.created_at.toISOString()
            : String(row.created_at ?? ""),
        docCount: this.counts.get(id) ?? 0,
      });
    }
    await Promise.all(
      kbs.map(async (kb) => {
        if (this.counts.has(kb.id)) return;
        try {
          const collection = await this.ensureCollection(kb);
          const count = await collection.count();
          this.counts.set(kb.id, count);
          kb.docCount = count;
        } catch {
          kb.docCount = 0;
        }
      })
    );
    return kbs;
  }

  async getKb(id: string): Promise<KbRecord | null> {
    const rows =
      (await this.client.execute(
        "SELECT id, name, collection_name, model, dim, created_at FROM kb_registry WHERE id = ?",
        [id]
      )) ?? [];
    if (rows.length === 0) return null;
    const row = rows[0];
    return {
      id: String(row.id),
      name: String(row.name),
      collection: String(row.collection_name),
      model: String(row.model ?? MODEL_ID),
      dim: Number(row.dim ?? EMBED_DIM),
      createdAt:
        row.created_at instanceof Date
          ? row.created_at.toISOString()
          : String(row.created_at ?? ""),
      docCount: this.counts.get(id) ?? 0,
    };
  }

  async createKb(name: string): Promise<KbRecord> {
    const cleanName = name.trim().slice(0, 60);
    if (!cleanName) throw new Error("知识库名称不能为空");
    const existing = await this.listKbs();
    if (existing.some((kb) => kb.name.toLowerCase() === cleanName.toLowerCase())) {
      throw new Error("同名知识库已存在");
    }
    const id = crypto.randomBytes(6).toString("hex");
    const collection = `kb_${id}_${Date.now().toString(36)}`;
    const schema = new Schema({
      fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
      vectorIndex: new VectorIndexConfig({
        hnsw: {
          dimension: EMBED_DIM,
          distance: "cosine",
          m: 16,
          ef_construction: 200,
        },
        embeddingFunction: null,
      }),
    });
    await this.client.createCollection({ name: collection, schema });
    await this.client.execute(
      "INSERT INTO kb_registry (id, name, collection_name, model, dim) VALUES (?, ?, ?, ?, ?)",
      [id, cleanName, collection, MODEL_ID, EMBED_DIM]
    );
    this.counts.set(id, 0);
    const kb = await this.getKb(id);
    if (!kb) throw new Error("知识库创建后读取失败");
    return kb;
  }

  async deleteKb(id: string): Promise<boolean> {
    const kb = await this.getKb(id);
    if (!kb) return false;
    await this.client.deleteCollection(kb.collection);
    await this.client.execute("DELETE FROM kb_registry WHERE id = ?", [id]);
    this.collections.delete(kb.collection);
    this.counts.delete(id);
    return true;
  }

  async ensureCollection(kb: KbRecord): Promise<Collection> {
    const cached = this.collections.get(kb.collection);
    if (cached) return cached;
    const collection = await this.client.getCollection({
      name: kb.collection,
      embeddingFunction: null,
    });
    this.collections.set(kb.collection, collection);
    return collection;
  }

  private runExclusive<T>(kbId: string, task: () => Promise<T>): Promise<T> {
    const previous = this.kbLocks.get(kbId) ?? Promise.resolve();
    const run = previous.then(task, task);
    this.kbLocks.set(
      kbId,
      run.then(
        () => undefined,
        () => undefined
      )
    );
    return run;
  }

  async addDocs(kb: KbRecord, docs: DocInput[]): Promise<{ written: number; count: number; writeMs: number }> {
    if (kb.model !== MODEL_ID) throw new Error(`unsupported model ${kb.model}`);
    if (!Array.isArray(docs) || docs.length === 0) throw new Error("docs must not be empty");
    if (docs.length > MAX_DOCS_PER_BATCH) {
      throw new Error(`at most ${MAX_DOCS_PER_BATCH} docs per batch`);
    }
    const ids: string[] = [];
    const documents: string[] = [];
    const embeddings: number[][] = [];
    const metadatas: DocMetadata[] = [];
    for (const doc of docs) {
      const id = String(doc.id ?? "").trim();
      const document = normalizeText(doc.document);
      if (!id || id.length > 160) throw new Error("invalid doc id");
      if (!document) throw new Error(`doc ${id} has empty text`);
      if (document.length > MAX_DOC_CHARS) {
        throw new Error(`doc ${id} exceeds ${MAX_DOC_CHARS} chars`);
      }
      ids.push(id);
      documents.push(document);
      embeddings.push(normalizeEmbedding(doc.embedding));
      metadatas.push({
        ...(doc.metadata ?? {}),
        kbId: kb.id,
        collection: kb.collection,
        importedAt: new Date().toISOString(),
        contentHash: id,
      });
    }

    return this.runExclusive(kb.id, async () => {
      const collection = await this.ensureCollection(kb);
      const started = performance.now();
      await collection.upsert({ ids, documents, embeddings, metadatas });
      const writeMs = performance.now() - started;
      const count = await collection.count();
      this.counts.set(kb.id, count);
      return { written: ids.length, count, writeMs };
    });
  }

  async deleteDocs(kb: KbRecord, ids: string[]): Promise<{ count: number; deleteMs: number }> {
    if (!Array.isArray(ids) || ids.length === 0) throw new Error("ids must not be empty");
    return this.runExclusive(kb.id, async () => {
      const collection = await this.ensureCollection(kb);
      const started = performance.now();
      await collection.delete({ ids });
      const deleteMs = performance.now() - started;
      const count = await collection.count();
      this.counts.set(kb.id, count);
      return { count, deleteMs };
    });
  }

  async refresh(kb: KbRecord): Promise<{ refreshMs: number; supported: boolean }> {
    return this.runExclusive(kb.id, async () => {
      const collection = await this.ensureCollection(kb);
      const started = performance.now();
      await collection.refresh_index();
      return { refreshMs: performance.now() - started, supported: true };
    });
  }

  async getDocs(
    kb: KbRecord,
    limit = 100,
    offset = 0
  ): Promise<{ docs: Array<{ id: string; document: string; metadata: DocMetadata | null }>; total: number }> {
    const collection = await this.ensureCollection(kb);
    const result = await collection.get({
      limit,
      offset,
      include: ["documents", "metadatas"],
    });
    const ids = splitNested(result.ids as unknown as string[]);
    const documents = splitNested(result.documents as unknown as string[]);
    const metadatas = splitNested(result.metadatas as unknown as DocMetadata[]);
    const docs = ids.map((id, index) => ({
      id: String(id),
      document: documents[index] ?? "",
      metadata: (metadatas[index] as DocMetadata) ?? null,
    }));
    const total = this.counts.get(kb.id) ?? (await collection.count());
    this.counts.set(kb.id, total);
    return { docs, total };
  }

  async search(
    kb: KbRecord,
    query: string,
    embedding: number[],
    mode: SearchMode,
    topK: number
  ): Promise<SearchResponse> {
    const cleanQuery = normalizeText(query).slice(0, 300);
    if (!cleanQuery) throw new Error("查询不能为空");
    const vector = normalizeEmbedding(embedding);
    const k = Math.max(1, Math.min(50, Math.floor(topK) || 8));
    const collection = await this.ensureCollection(kb);
    let result: any;
    let scoreKind: SearchResult["scoreKind"] = "rrf";
    let rawBranchNote = "";
    const keywordQuery = sanitizeFulltext(cleanQuery);

    const started = performance.now();
    if (mode === "vector") {
      result = await collection.query({
        queryEmbeddings: vector,
        nResults: k,
        include: ["documents", "metadatas"],
      });
      scoreKind = "cosine_distance";
      rawBranchNote = "seekdb vector branch only (HNSW cosine)";
    } else if (mode === "keyword") {
      if (!keywordQuery) {
        return {
          mode,
          query: cleanQuery,
          kbId: kb.id,
          collection: kb.collection,
          results: [],
          retrievalMs: performance.now() - started,
          rawBranchNote: "empty keyword query after sanitization",
        };
      }
      result = await collection.hybridSearch({
        query: { whereDocument: { $contains: keywordQuery } },
        nResults: k,
        include: ["documents", "metadatas"],
      });
      scoreKind = "keyword_score";
      rawBranchNote = "seekdb full-text branch only (IK analyzer)";
    } else {
      if (!keywordQuery) {
        result = await collection.query({
          queryEmbeddings: vector,
          nResults: k,
          include: ["documents", "metadatas"],
        });
        scoreKind = "cosine_distance";
        rawBranchNote = "hybrid requested but keyword text empty; vector branch used";
      } else {
        result = await collection.hybridSearch({
          query: { whereDocument: { $contains: keywordQuery } },
          knn: { queryEmbeddings: vector },
          rank: { rrf: { rankWindowSize: Math.max(20, k * 4), rankConstant: 60 } },
          nResults: k,
          include: ["documents", "metadatas"],
        });
        scoreKind = "rrf";
        rawBranchNote = "seekdb DBMS_HYBRID_SEARCH: full-text + vector fused with RRF";
      }
    }
    const retrievalMs = performance.now() - started;

    const ids = splitNested(result?.ids as unknown as string[]);
    const documents = splitNested(result?.documents as unknown as string[]);
    const metadatas = splitNested(result?.metadatas as unknown as DocMetadata[]);
    const distances = splitNested(result?.distances as unknown as number[]);
    const results: SearchResult[] = ids.map((id, index) => ({
      id: String(id),
      document: documents[index] ?? "",
      metadata: (metadatas[index] as DocMetadata) ?? null,
      score: Number(distances[index] ?? 0),
      scoreKind,
      rank: index + 1,
    }));
    return {
      mode,
      query: cleanQuery,
      kbId: kb.id,
      collection: kb.collection,
      results,
      retrievalMs,
      rawBranchNote,
    };
  }

  async close(): Promise<void> {
    try {
      await this.client?.close();
      await this.adminClient?.close();
    } catch {
      // best effort
    }
    if (this.config.mode === "embedded") {
      try {
        const delegate = (this.client as any)?._delegate;
        const internals = [delegate?._internal, delegate?._adminInternal].filter(Boolean);
        const handles = new Set(internals.map((item: any) => item?._db).filter(Boolean));
        if (handles.size > 0) {
          const mod: any = await import("@seekdb/js-bindings");
          const loader = mod.getNativeBindingAsync ?? mod.default?.getNativeBindingAsync;
          const binding: any = loader ? await loader() : mod.default ?? mod;
          for (const handle of handles) {
            try {
              binding.close_sync?.(handle);
            } catch {
              // ignore
            }
          }
        }
      } catch {
        // ignore
      }
    }
  }
}

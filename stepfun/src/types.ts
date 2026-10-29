export type Kb = {
  id: string;
  name: string;
  collection: string;
  model: string;
  dim: number;
  createdAt: string;
  docCount: number;
};

export type AppState = {
  sessionId: string;
  activeKbId: string | null;
  kbs: Kb[];
  backend: { mode: "embedded" | "server"; database: string };
};

export type ModelFile = {
  dtype: "fp32" | "fp16" | "q8";
  file: string;
  label: string;
  exists: boolean;
  bytes: number;
  sha256: string | null;
};

export type ModelInfo = {
  id: string;
  dim: number;
  pooling: string;
  queryInstruction: string;
  source: string;
  runtime: string;
  manifest: Record<string, unknown>;
  files: ModelFile[];
};

export type DocMetadata = Record<string, string | number | boolean>;

export type DocItem = {
  id: string;
  document: string;
  metadata: DocMetadata | null;
};

export type SearchResult = {
  id: string;
  document: string;
  metadata: DocMetadata | null;
  score: number;
  scoreKind: "rrf" | "cosine_distance" | "keyword_score";
  rank: number;
};

export type SearchResponse = {
  mode: "hybrid" | "vector" | "keyword";
  query: string;
  kbId: string;
  collection: string;
  results: SearchResult[];
  retrievalMs: number;
  rawBranchNote: string;
};

export type HealthInfo = {
  ok: boolean;
  mode: string;
  database: string;
  version: string;
  host?: string;
  port?: number;
  node: string;
  platform: string;
};

export type EmbedderStatus = {
  backend: "webgpu" | "wasm";
  dtype: "fp16" | "fp32" | "q8";
  device: string;
  modelId: string;
  loadMs: number;
  warmupMs: number;
  threads: number;
  adapter?: string;
  fallbackReason?: string;
};

export type ImportStage =
  | "idle"
  | "parsing"
  | "embedding"
  | "writing"
  | "refreshing"
  | "done"
  | "error"
  | "aborted";

export type ImportProgress = {
  stage: ImportStage;
  message: string;
  done: number;
  total: number;
  embedMs?: number;
  writeMs?: number;
  refreshMs?: number;
  totalMs?: number;
  docsPerSec?: number;
};

export type QueryTiming = {
  embedMs: number;
  retrievalMs: number;
  e2eMs: number;
  cacheHit: boolean;
};

export type CorpusDoc = {
  id: string;
  title?: string;
  text: string;
  source?: string;
  topic?: string;
};

export type EvalQuery = {
  query: string;
  relevant: string[];
  kind?: string;
};

export type BenchConfig = {
  backend: "auto" | "webgpu" | "wasm";
  dtype: "auto" | "fp16" | "fp32" | "q8";
  batchSize: number;
  topK: number;
  docLimit?: number;
  measureReload?: boolean;
  refreshMode?: "end" | "per-batch";
  label?: string;
};

export type BenchResult = {
  label: string;
  startedAt: string;
  userAgent: string;
  hardwareConcurrency: number;
  backend: string;
  dtype: string;
  device: string;
  adapter?: string;
  fallbackReason?: string;
  modelLoadMs: number;
  warmLoadMs?: number;
  warmupMs: number;
  docCount: number;
  docChars: number;
  docEmbedMs: number;
  docEmbedMsPerDoc: number;
  docsPerSec: number;
  batchSize: number;
  writeMs: number;
  serverWriteMs: number;
  refreshMs: number;
  ingestTotalMs: number;
  incrementalUpsertMs: number;
  countAfterReimport: number;
  queryCount: number;
  queryEmbedMsMedian: number;
  queryEmbedMsP90: number;
  retrievalMsMedian: number;
  retrievalMsP90: number;
  e2eMsMedian: number;
  e2eMsP90: number;
  firstQueryE2eMs: number;
  warmQueryE2eMsMedian: number;
  hybrid: {
    recall5: number;
    recall10: number;
    ndcg10: number;
  };
  vector: {
    recall5: number;
    recall10: number;
    ndcg10: number;
  };
  perQuery: Array<{
    query: string;
    hybridIds: string[];
    vectorIds: string[];
    hybridMs: number;
    vectorMs: number;
    embedMs: number;
    e2eMs: number;
    cacheHit: boolean;
  }>;
  modelSizes: Record<string, number>;
  notes: string[];
};

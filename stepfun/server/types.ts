export type KbRecord = {
  id: string;
  name: string;
  collection: string;
  model: string;
  dim: number;
  createdAt: string;
  docCount: number;
};

export type DocMetadata = Record<string, string | number | boolean> & {
  title?: string;
  source?: string;
  chunk?: number;
  importedAt?: string;
  contentHash?: string;
};

export type DocInput = {
  id: string;
  document: string;
  metadata: DocMetadata;
  embedding: number[];
};

export type SearchMode = "hybrid" | "vector" | "keyword";

export type SearchResult = {
  id: string;
  document: string;
  metadata: DocMetadata | null;
  score: number;
  scoreKind: "rrf" | "cosine_distance" | "keyword_score";
  rank: number;
};

export type SearchResponse = {
  mode: SearchMode;
  query: string;
  kbId: string;
  collection: string;
  results: SearchResult[];
  retrievalMs: number;
  rawBranchNote: string;
};

export type StageTiming = {
  label: string;
  ms: number;
};

export type SessionState = {
  id: string;
  activeKbId: string | null;
  createdAt: number;
};

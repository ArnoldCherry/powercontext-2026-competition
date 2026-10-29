import type {
  AppState,
  CorpusDoc,
  DocItem,
  EvalQuery,
  HealthInfo,
  ModelInfo,
  SearchResponse,
} from "./types.ts";

export class ApiError extends Error {
  status: number;
  code?: string;
  activeKbId?: string | null;

  constructor(message: string, status: number, code?: string, activeKbId?: string | null) {
    super(message);
    this.status = status;
    this.code = code;
    this.activeKbId = activeKbId;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  const text = await response.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: text };
  }
  if (!response.ok) {
    throw new ApiError(
      data?.message ?? data?.error ?? `HTTP ${response.status}`,
      response.status,
      data?.error,
      data?.activeKbId
    );
  }
  return data as T;
}

export type ImportBatchResponse = {
  kbId: string;
  written: number;
  count: number;
  writeMs: number;
  refreshMs?: number;
};

export const api = {
  health: () => request<HealthInfo>("/api/health"),
  state: () => request<AppState>("/api/state"),
  modelInfo: () => request<ModelInfo>("/api/model-info"),

  createKb: (name: string) =>
    request<{ kb: AppState["kbs"][number]; activeKbId: string }>("/api/kbs", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  activateKb: (id: string) =>
    request<{ activeKbId: string }>(`/api/kbs/${encodeURIComponent(id)}/activate`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  deleteKb: (id: string) =>
    request<{ deleted: boolean; activeKbId: string | null }>(
      `/api/kbs/${encodeURIComponent(id)}`,
      { method: "DELETE" }
    ),

  docs: (id: string, limit = 100, offset = 0) =>
    request<{ docs: DocItem[]; total: number }>(
      `/api/kbs/${encodeURIComponent(id)}/docs?limit=${limit}&offset=${offset}`
    ),

  importBatch: (
    id: string,
    expectedKbId: string,
    docs: Array<{ id: string; document: string; metadata: Record<string, string | number | boolean> }>,
    embeddings: number[][],
    refresh = false
  ) =>
    request<ImportBatchResponse>(`/api/kbs/${encodeURIComponent(id)}/docs`, {
      method: "POST",
      body: JSON.stringify({ expectedKbId, docs, embeddings, refresh }),
    }),

  refresh: (id: string, expectedKbId: string) =>
    request<{ kbId: string; refreshMs: number; supported: boolean }>(
      `/api/kbs/${encodeURIComponent(id)}/refresh`,
      { method: "POST", body: JSON.stringify({ expectedKbId }) }
    ),

  deleteDoc: (id: string, docId: string) =>
    request<{ count: number; deleteMs: number }>(
      `/api/kbs/${encodeURIComponent(id)}/docs/${encodeURIComponent(docId)}`,
      { method: "DELETE" }
    ),

  search: (payload: {
    query: string;
    embedding: number[];
    mode: "hybrid" | "vector" | "keyword";
    topK: number;
    expectedKbId: string;
  }) =>
    request<SearchResponse>("/api/search", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  corpus: (file = "kb-seekdb.json") =>
    request<{ name: string; docs: CorpusDoc[] }>(
      `/api/demo-corpus?file=${encodeURIComponent(file)}`
    ),

  evalQueries: () => request<{ queries: EvalQuery[] }>("/api/eval-queries"),
};

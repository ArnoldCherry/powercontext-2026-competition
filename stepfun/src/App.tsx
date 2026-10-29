import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api.ts";
import { DocsPanel } from "./components/DocsPanel.tsx";
import { ImportPanel } from "./components/ImportPanel.tsx";
import { SearchPanel } from "./components/SearchPanel.tsx";
import { Sidebar } from "./components/Sidebar.tsx";
import { StatusBadges } from "./components/StatusBadges.tsx";
import { EmbedderClient } from "./embedding/client.ts";
import type {
  AppState,
  DocItem,
  EmbedderStatus,
  HealthInfo,
  ImportProgress,
  Kb,
  ModelInfo,
  QueryTiming,
  SearchResponse,
} from "./types.ts";
import {
  chunkText,
  corpusToSources,
  formatMs,
  parsePastedText,
  parseUploadedFiles,
  sha256Hex,
} from "./utils.ts";

type Tab = "import" | "search" | "docs";

const params = new URLSearchParams(window.location.search);
const forcedBackend = (params.get("backend") as "auto" | "webgpu" | "wasm" | null) ?? "auto";
const forcedDtype = (params.get("dtype") as "auto" | "fp16" | "fp32" | "q8" | null) ?? "auto";
const initialBatch = Math.max(1, Math.min(32, Number(params.get("batch") ?? 16) || 16));

type SourceDoc = { id?: string; title: string; text: string; source: string; topic?: string };

export default function App() {
  const [state, setState] = useState<AppState | null>(null);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [modelInfo, setModelInfo] = useState<ModelInfo | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [embedStatus, setEmbedStatus] = useState<EmbedderStatus | null>(null);
  const [embedDetail, setEmbedDetail] = useState<any>({ stage: "boot", message: "准备加载模型" });
  const [embedError, setEmbedError] = useState<string | null>(null);

  const [tab, setTab] = useState<Tab>("import");
  const [docs, setDocs] = useState<DocItem[]>([]);
  const [docsTotal, setDocsTotal] = useState(0);
  const [docsLoading, setDocsLoading] = useState(false);
  const [refreshMs, setRefreshMs] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [newKbName, setNewKbName] = useState("");
  const [creating, setCreating] = useState(false);
  const [batchSize, setBatchSize] = useState(initialBatch);
  const [importProgress, setImportProgress] = useState<ImportProgress>({
    stage: "idle",
    message: "等待导入",
    done: 0,
    total: 0,
  });
  const [importLogs, setImportLogs] = useState<string[]>([]);

  const [searchQuery, setSearchQuery] = useState("");
  const [searchMode, setSearchMode] = useState<"hybrid" | "vector" | "keyword">("hybrid");
  const [topK, setTopK] = useState(8);
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<SearchResponse | null>(null);
  const [searchTiming, setSearchTiming] = useState<QueryTiming | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const embedderRef = useRef<EmbedderClient | null>(null);
  const importRunRef = useRef(0);
  const activeKbRef = useRef<Kb | null>(null);
  const toastTimer = useRef<number | null>(null);
  const embedBooted = useRef(false);

  const activeKb = state?.kbs.find((kb) => kb.id === state.activeKbId) ?? null;
  activeKbRef.current = activeKb;

  function showToast(message: string) {
    setToast(message);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4800);
  }

  async function refreshState(): Promise<AppState | null> {
    try {
      const next = await api.state();
      setState(next);
      return next;
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error));
      return null;
    }
  }

  async function loadDocs(kb: Kb | null = activeKbRef.current) {
    if (!kb) {
      setDocs([]);
      setDocsTotal(0);
      return;
    }
    setDocsLoading(true);
    try {
      const data = await api.docs(kb.id, 100, 0);
      setDocs(data.docs);
      setDocsTotal(data.total);
    } catch (error) {
      if (error instanceof ApiError && error.code === "KB_CHANGED") {
        await refreshState();
      } else {
        showToast(error instanceof Error ? error.message : String(error));
      }
    } finally {
      setDocsLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [nextState, info, healthInfo] = await Promise.all([
          api.state(),
          api.modelInfo(),
          api.health(),
        ]);
        if (cancelled) return;
        setState(nextState);
        setModelInfo(info);
        setHealth(healthInfo);
      } catch (error) {
        if (!cancelled) setBootError(error instanceof Error ? error.message : String(error));
      }
    })();

    if (!embedBooted.current) {
      embedBooted.current = true;
      const client = new EmbedderClient(
        (status) => {
          setEmbedStatus(status);
          if (status) setEmbedError(null);
        },
        (detail) => {
          setEmbedDetail(detail);
          if (detail?.stage === "error") setEmbedError(detail.message ?? "模型加载失败");
        }
      );
      embedderRef.current = client;
      client.init(forcedBackend, forcedDtype).catch((error) => {
        setEmbedError(error instanceof Error ? error.message : String(error));
      });
    }

    return () => {
      cancelled = true;
      embedderRef.current?.terminate();
      embedderRef.current = null;
      embedBooted.current = false;
    };
  }, []);

  useEffect(() => {
    setSearchResult(null);
    setSearchTiming(null);
    setSearchError(null);
    setRefreshMs(null);
    void loadDocs(activeKb);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKb?.id]);

  function abortImport() {
    importRunRef.current += 1;
  }

  async function activateKb(id: string) {
    if (id === activeKb?.id) return;
    abortImport();
    setImportProgress((progress) =>
      ["parsing", "embedding", "writing", "refreshing"].includes(progress.stage)
        ? { ...progress, stage: "aborted", message: "切换知识库，导入已中止" }
        : progress
    );
    setSearchResult(null);
    setSearchTiming(null);
    setSearchError(null);
    try {
      await api.activateKb(id);
      await refreshState();
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error));
    }
  }

  async function createKb() {
    const name = newKbName.trim();
    if (!name) return;
    setCreating(true);
    abortImport();
    try {
      const result = await api.createKb(name);
      setNewKbName("");
      setSearchResult(null);
      setSearchTiming(null);
      await refreshState();
      setTab("import");
      showToast(`已创建并切换到「${result.kb.name}」`);
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error));
    } finally {
      setCreating(false);
    }
  }

  async function deleteKb(kb: Kb) {
    if (!window.confirm(`删除知识库「${kb.name}」及其集合？此操作不可恢复。`)) return;
    if (kb.id === activeKb?.id) {
      abortImport();
      setSearchResult(null);
      setSearchTiming(null);
    }
    try {
      await api.deleteKb(kb.id);
      await refreshState();
      showToast(`已删除「${kb.name}」`);
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error));
    }
  }

  async function runImport(sources: SourceDoc[]) {
    const kb = activeKbRef.current;
    if (!kb) {
      showToast("请先选择知识库");
      return;
    }
    if (!embedStatus || !embedderRef.current) {
      showToast("模型尚未就绪，请等待加载完成");
      return;
    }
    if (sources.length === 0) {
      showToast("没有可导入的文本");
      return;
    }
    const runId = ++importRunRef.current;
    const started = performance.now();
    setImportLogs([]);
    setImportProgress({ stage: "parsing", message: "切分文本并计算稳定文档 ID", done: 0, total: 0 });

    try {
      const collected = new Map<string, { id: string; document: string; metadata: Record<string, string | number | boolean> }>();
      for (const source of sources) {
        const chunks = chunkText(source.text, 900);
        for (let index = 0; index < chunks.length; index++) {
          const text = chunks[index];
          const id =
            source.id && chunks.length === 1
              ? source.id
              : source.id
                ? `${source.id}-${index + 1}`
                : (await sha256Hex(`${kb.id}|${source.source}|${index}|${text}`)).slice(0, 32);
          collected.set(id, {
            id,
            document: text,
            metadata: {
              title: source.title,
              source: source.source,
              chunk: index,
              chars: text.length,
              ...(source.topic ? { topic: source.topic } : {}),
            },
          });
        }
      }
      const prepared = [...collected.values()];
      if (prepared.length === 0) throw new Error("切分后没有有效文档");

      setImportProgress({
        stage: "parsing",
        message: `切分为 ${prepared.length} 个文档块`,
        done: prepared.length,
        total: prepared.length,
      });

      const embedStarted = performance.now();
      setImportProgress({
        stage: "embedding",
        message: `本地向量化 ${prepared.length} 个文档块`,
        done: 0,
        total: prepared.length,
      });
      const embedResult = await embedderRef.current.embed(
        prepared.map((doc) => doc.document),
        "doc",
        {
          batchSize,
          onProgress: (progress) => {
            setImportProgress({
              stage: "embedding",
              message: `本地向量化 ${progress.done}/${progress.total}`,
              done: progress.done,
              total: progress.total,
              embedMs: progress.elapsedMs,
            });
          },
        }
      );
      const embedMs = performance.now() - embedStarted;
      if (runId !== importRunRef.current) throw new Error("aborted");

      let serverWriteMs = 0;
      let written = 0;
      const writeStarted = performance.now();
      for (let start = 0; start < prepared.length; start += 32) {
        if (runId !== importRunRef.current) throw new Error("aborted");
        const batchDocs = prepared.slice(start, start + 32);
        const batchEmbeddings = embedResult.embeddings.slice(start, start + 32);
        setImportProgress({
          stage: "writing",
          message: `写入 seekdb ${Math.min(start + batchDocs.length, prepared.length)}/${prepared.length}`,
          done: Math.min(start + batchDocs.length, prepared.length),
          total: prepared.length,
          embedMs,
        });
        const response = await api.importBatch(kb.id, kb.id, batchDocs, batchEmbeddings, false);
        serverWriteMs += response.writeMs;
        written += response.written;
      }
      const writeMs = performance.now() - writeStarted;
      if (runId !== importRunRef.current) throw new Error("aborted");

      setImportProgress({
        stage: "refreshing",
        message: "调用 dbms_index_manager.refresh() 刷新索引",
        done: prepared.length,
        total: prepared.length,
        embedMs,
        writeMs,
      });
      let refresh = 0;
      let refreshNote = "";
      try {
        const response = await api.refresh(kb.id, kb.id);
        refresh = response.refreshMs;
      } catch (error) {
        refreshNote = `索引刷新提示：${error instanceof Error ? error.message : String(error)}`;
      }
      if (runId !== importRunRef.current) throw new Error("aborted");

      const totalMs = performance.now() - started;
      const docsPerSec = prepared.length / (embedMs / 1000);
      setImportProgress({
        stage: "done",
        message: `${refreshNote ? refreshNote + "；" : ""}已导入 ${written} 篇并刷新索引`,
        done: prepared.length,
        total: prepared.length,
        embedMs,
        writeMs,
        refreshMs: refresh,
        totalMs,
        docsPerSec,
      });
      setImportLogs([
        `backend=${embedStatus.backend}/${embedStatus.dtype} batch=${batchSize}`,
        `embed ${prepared.length} docs in ${formatMs(embedMs)} (${docsPerSec.toFixed(1)} docs/s)`,
        `seekdb write ${formatMs(writeMs)} (server ${formatMs(serverWriteMs)})`,
        `refresh_index ${formatMs(refresh)}`,
        `total ${formatMs(totalMs)} · collection=${kb.collection}`,
      ]);
      await refreshState();
      await loadDocs(kb);
      showToast(`导入完成：${written} 篇，刷新 ${formatMs(refresh)}`);
    } catch (error) {
      if (runId !== importRunRef.current || (error instanceof Error && error.message === "aborted")) {
        setImportProgress((progress) => ({
          ...progress,
          stage: "aborted",
          message: "导入已中止（知识库切换或页面操作）",
        }));
        return;
      }
      if (error instanceof ApiError && error.code === "KB_CHANGED") {
        setImportProgress({ stage: "error", message: "知识库已切换，本次导入被服务端拒绝", done: 0, total: 0 });
        await refreshState();
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      setImportProgress({ stage: "error", message, done: 0, total: 0 });
      setImportLogs((logs) => [...logs, `error: ${message}`]);
    }
  }

  async function importText(text: string) {
    const parsed = parsePastedText(text);
    await runImport(parsed);
  }

  async function importFiles(files: FileList | File[]) {
    const parsed = await parseUploadedFiles(files);
    await runImport(parsed);
  }

  async function importDemo(file: string) {
    try {
      const corpus = await api.corpus(file);
      const sources = corpusToSources(corpus.docs).map((source) => ({
        ...source,
        topic: corpus.docs.find((doc) => doc.id === source.id)?.topic,
      }));
      await runImport(sources);
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error));
    }
  }

  async function runSearch(override?: string) {
    const query = (override ?? searchQuery).trim();
    const kb = activeKbRef.current;
    if (!query || !kb || !embedderRef.current || !embedStatus) return;
    setSearchQuery(query);
    setSearching(true);
    setSearchResult(null);
    setSearchTiming(null);
    setSearchError(null);
    const started = performance.now();
    try {
      const embed = await embedderRef.current.embed([query], "query", { batchSize: 1 });
      if (activeKbRef.current?.id !== kb.id) return;
      const response = await api.search({
        query,
        embedding: embed.embeddings[0],
        mode: searchMode,
        topK,
        expectedKbId: kb.id,
      });
      const e2eMs = performance.now() - started;
      if (activeKbRef.current?.id !== kb.id) return;
      setSearchResult(response);
      setSearchTiming({
        embedMs: embed.embedMs,
        retrievalMs: response.retrievalMs,
        e2eMs,
        cacheHit: embed.cacheHit,
      });
    } catch (error) {
      if (error instanceof ApiError && error.code === "KB_CHANGED") {
        setSearchError("知识库已切换，旧查询结果已丢弃。请基于当前知识库重新检索。");
        await refreshState();
      } else {
        setSearchError(error instanceof Error ? error.message : String(error));
      }
    } finally {
      setSearching(false);
    }
  }

  async function refreshIndex() {
    const kb = activeKbRef.current;
    if (!kb) return;
    setRefreshing(true);
    try {
      const response = await api.refresh(kb.id, kb.id);
      setRefreshMs(response.refreshMs);
      showToast(`索引刷新完成：${formatMs(response.refreshMs)}`);
      await loadDocs(kb);
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error));
    } finally {
      setRefreshing(false);
    }
  }

  async function deleteDoc(id: string) {
    const kb = activeKbRef.current;
    if (!kb) return;
    try {
      await api.deleteDoc(kb.id, id);
      await loadDocs(kb);
      await refreshState();
      showToast("文档已从 seekdb 删除");
    } catch (error) {
      showToast(error instanceof Error ? error.message : String(error));
    }
  }

  function retryModel() {
    embedderRef.current?.init(forcedBackend, forcedDtype).catch((error) => {
      setEmbedError(error instanceof Error ? error.message : String(error));
    });
  }

  const stats = [
    { label: "文档", value: activeKb?.docCount ?? 0, unit: "篇", note: activeKb?.name ?? "无知识库" },
    { label: "向量维度", value: activeKb?.dim ?? 512, unit: "d", note: "BGE-small-zh-v1.5" },
    { label: "检索", value: "RRF", unit: "混合", note: "seekdb 数据库内融合" },
    {
      label: "最近端到端",
      value: searchTiming ? formatMs(searchTiming.e2eMs) : embedStatus ? formatMs(embedStatus.loadMs) : "-",
      unit: searchTiming ? "" : "加载",
      note: searchTiming ? "输入到结果" : embedStatus ? "模型冷加载" : "加载中",
    },
  ];

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="logo">S</div>
          <div>
            <h1>StepFun 端侧知识库</h1>
            <p>浏览器本地向量化 × seekdb 原生混合检索</p>
          </div>
        </div>
        <StatusBadges status={embedStatus} detail={embedDetail} health={health} modelInfo={modelInfo} />
        <a className="bench-link" href="/bench">
          评测台 →
        </a>
      </header>

      {bootError && <div className="notice error boot-error">后端连接失败：{bootError}</div>}

      <div className="layout">
        <Sidebar
          state={state}
          activeKb={activeKb}
          status={embedStatus}
          detail={embedDetail}
          modelInfo={modelInfo}
          health={health}
          creating={creating}
          newKbName={newKbName}
          setNewKbName={setNewKbName}
          onActivate={activateKb}
          onCreate={createKb}
          onDelete={deleteKb}
        />

        <main className="main">
          <section className="stats-row">
            {stats.map((stat) => (
              <div className="stat-card" key={stat.label}>
                <span>{stat.label}</span>
                <strong>
                  {stat.value}
                  <em>{stat.unit}</em>
                </strong>
                <small>{stat.note}</small>
              </div>
            ))}
          </section>

          {embedError && (
            <div className="notice error model-error">
              模型加载失败：{embedError}
              <button className="ghost small" onClick={retryModel}>
                重试
              </button>
            </div>
          )}

          <nav className="tabs">
            <button className={tab === "import" ? "active" : ""} onClick={() => setTab("import")}>
              导入语料
              {activeKb?.docCount ? <span>{activeKb.docCount}</span> : null}
            </button>
            <button className={tab === "search" ? "active" : ""} onClick={() => setTab("search")}>
              混合检索
              {searchResult ? <span>{searchResult.results.length}</span> : null}
            </button>
            <button className={tab === "docs" ? "active" : ""} onClick={() => setTab("docs")}>
              文档
              {docsTotal ? <span>{docsTotal}</span> : null}
            </button>
            <div className="tab-note">
              推理位置：浏览器 · 存储/检索：seekdb · 无云端 API
            </div>
          </nav>

          {tab === "import" && (
            <ImportPanel
              activeKb={activeKb}
              embedStatus={embedStatus}
              batchSize={batchSize}
              setBatchSize={setBatchSize}
              progress={importProgress}
              logs={importLogs}
              onImportText={importText}
              onImportFiles={importFiles}
              onImportDemo={importDemo}
              onGoSearch={() => setTab("search")}
            />
          )}

          {tab === "search" && (
            <SearchPanel
              activeKb={activeKb}
              embedStatus={embedStatus}
              query={searchQuery}
              setQuery={setSearchQuery}
              mode={searchMode}
              setMode={setSearchMode}
              topK={topK}
              setTopK={setTopK}
              searching={searching}
              result={searchResult}
              timing={searchTiming}
              error={searchError}
              onSearch={() => void runSearch()}
              onExample={(value) => void runSearch(value)}
            />
          )}

          {tab === "docs" && (
            <DocsPanel
              activeKb={activeKb}
              docs={docs}
              total={docsTotal}
              loading={docsLoading}
              refreshing={refreshing}
              refreshMs={refreshMs}
              onRefresh={() => void refreshIndex()}
              onDeleteDoc={(id) => void deleteDoc(id)}
            />
          )}
        </main>
      </div>

      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

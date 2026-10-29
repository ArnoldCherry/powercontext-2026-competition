import { useEffect, useRef, useState } from "react";
import { api } from "../api.ts";
import { EmbedderClient } from "../embedding/client.ts";
import type { BenchConfig, BenchResult, CorpusDoc, EvalQuery } from "../types.ts";
import { downloadJson, formatMs, median, percentile } from "../utils.ts";

declare global {
  interface Window {
    __runStepFunBench?: (config?: Partial<BenchConfig>) => Promise<BenchResult>;
    __stepFunBenchReady?: boolean;
    __stepFunBenchLogs?: string[];
  }
}

const URL_PARAMS = new URLSearchParams(window.location.search);
const DEFAULT_CONFIG: BenchConfig = {
  backend: (URL_PARAMS.get("backend") as BenchConfig["backend"]) || "auto",
  dtype: (URL_PARAMS.get("dtype") as BenchConfig["dtype"]) || "auto",
  batchSize: Number(URL_PARAMS.get("batch") ?? 16) || 16,
  topK: Number(URL_PARAMS.get("topk") ?? 10) || 10,
  measureReload: URL_PARAMS.get("reload") === "1",
  refreshMode: (URL_PARAMS.get("refresh") as BenchConfig["refreshMode"]) || "end",
  label: URL_PARAMS.get("label") ?? "manual",
};

function recallAt(ids: string[], relevant: string[], k: number): number {
  if (relevant.length === 0) return 0;
  const set = new Set(relevant);
  const hits = ids.slice(0, k).filter((id) => set.has(id)).length;
  return hits / relevant.length;
}

function ndcgAt(ids: string[], relevant: string[], k: number): number {
  if (relevant.length === 0) return 0;
  const set = new Set(relevant);
  let dcg = 0;
  ids.slice(0, k).forEach((id, index) => {
    if (set.has(id)) dcg += 1 / Math.log2(index + 2);
  });
  let idcg = 0;
  for (let index = 0; index < Math.min(relevant.length, k); index++) {
    idcg += 1 / Math.log2(index + 2);
  }
  return idcg > 0 ? dcg / idcg : 0;
}

function average(values: number[]): number {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

export default function BenchPage() {
  const [config, setConfig] = useState<BenchConfig>(DEFAULT_CONFIG);
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [result, setResult] = useState<BenchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const runningRef = useRef(false);
  const logsRef = useRef<string[]>([]);
  const autoRan = useRef(false);

  function log(line: string) {
    const stamped = `${new Date().toISOString().slice(11, 23)} ${line}`;
    logsRef.current = [...logsRef.current, stamped];
    window.__stepFunBenchLogs = logsRef.current;
    setLogs(logsRef.current);
  }

  async function runBench(override: Partial<BenchConfig> = {}): Promise<BenchResult> {
    if (runningRef.current) throw new Error("benchmark already running");
    runningRef.current = true;
    setRunning(true);
    setError(null);
    setResult(null);
    logsRef.current = [];
    setLogs([]);
    const benchConfig: BenchConfig = { ...DEFAULT_CONFIG, ...config, ...override };
    window.__stepFunBenchLogs = logsRef.current;
    const startedAt = new Date().toISOString();
    log(`start config=${JSON.stringify(benchConfig)}`);

    const before = await api.state();
    const previousActive = before.activeKbId;
    const modelInfo = await api.modelInfo();
    const modelSizes: Record<string, number> = {};
    for (const file of modelInfo.files) modelSizes[file.dtype] = file.bytes;
    const corpusResponse = await api.corpus("kb-seekdb.json");
    const queriesResponse = await api.evalQueries();
    const corpus: CorpusDoc[] = corpusResponse.docs;
    const queries: EvalQuery[] = queriesResponse.queries;
    log(`corpus=${corpus.length} docs queries=${queries.length}`);

    const kbName = `bench-${benchConfig.label}-${Date.now().toString(36)}`;
    const created = await api.createKb(kbName);
    const kb = created.kb;
    log(`created kb=${kb.id} collection=${kb.collection}`);

    let client: EmbedderClient | null = null;
    let status: any = null;
    try {
      client = new EmbedderClient(
        (ready) => {
          if (ready) log(`ready backend=${ready.backend} dtype=${ready.dtype} load=${ready.loadMs.toFixed(0)}ms warmup=${ready.warmupMs.toFixed(0)}ms`);
        },
        (detail) => {
          if (detail?.stage === "loading" && detail.progress !== undefined) {
            const pct = Math.round((detail.progress > 1 ? detail.progress / 100 : detail.progress) * 100);
            if (pct % 25 === 0) log(`loading ${pct}% ${detail.file ?? ""}`);
          } else if (detail?.stage === "checking") {
            log(detail.message ?? "checking backend");
          }
        }
      );
      log("initializing embedder (cold browser cache expected in fresh context)");
      status = await client.init(benchConfig.backend, benchConfig.dtype);
      const modelLoadMs = status.loadMs;
      let warmLoadMs: number | undefined;
      if (benchConfig.measureReload) {
        log("reloading model to measure warm-cache load");
        status = await client.init(benchConfig.backend, benchConfig.dtype);
        warmLoadMs = status.loadMs;
        log(`warm reload ${(warmLoadMs ?? 0).toFixed(0)}ms`);
      }
      log(`backend=${status.backend} dtype=${status.dtype} modelLoad=${formatMs(status.loadMs)} warmup=${formatMs(status.warmupMs)}`);

      const docTexts = corpus.map((doc) => doc.text);
      const docChars = docTexts.reduce((sum, text) => sum + text.length, 0);
      const embedStarted = performance.now();
      const docEmbed = await client.embed(docTexts, "doc", {
        batchSize: benchConfig.batchSize,
        onProgress: (progress) => {
          if (progress.done === progress.total || progress.done % (benchConfig.batchSize * 2) === 0) {
            log(`doc embed ${progress.done}/${progress.total} batch=${formatMs(progress.batchMs)}`);
          }
        },
      });
      const docEmbedMs = performance.now() - embedStarted;
      log(`doc embedding ${formatMs(docEmbedMs)} ${(docTexts.length / (docEmbedMs / 1000)).toFixed(2)} docs/s`);

      const prepared = corpus.map((doc, index) => ({
        id: doc.id,
        document: doc.text,
        metadata: {
          title: doc.title ?? doc.id,
          source: doc.source ?? "demo",
          topic: doc.topic ?? "",
          chunk: 0,
          chars: doc.text.length,
        },
        embedding: docEmbed.embeddings[index],
      }));

      let serverWriteMs = 0;
      const perBatchRefresh = benchConfig.refreshMode === "per-batch";
      let batchedRefreshMs = 0;
      const writeStarted = performance.now();
      for (let start = 0; start < prepared.length; start += 32) {
        const batch = prepared.slice(start, start + 32);
        const response = await api.importBatch(
          kb.id,
          kb.id,
          batch.map((doc) => ({ id: doc.id, document: doc.document, metadata: doc.metadata })),
          batch.map((doc) => doc.embedding),
          perBatchRefresh
        );
        serverWriteMs += response.writeMs;
        if (perBatchRefresh) batchedRefreshMs += response.refreshMs ?? 0;
        log(
          `write ${Math.min(start + batch.length, prepared.length)}/${prepared.length} server=${formatMs(response.writeMs)}${
            perBatchRefresh ? ` + refresh=${formatMs(response.refreshMs ?? 0)}` : ""
          }`
        );
      }
      const writeMs = performance.now() - writeStarted;

      let refreshMs: number;
      if (perBatchRefresh) {
        refreshMs = batchedRefreshMs;
        log(`refresh_index per-batch total=${formatMs(refreshMs)}`);
      } else {
        const refreshResponse = await api.refresh(kb.id, kb.id);
        refreshMs = refreshResponse.refreshMs;
        log(`refresh_index end-of-import ${formatMs(refreshMs)}`);
      }

      const incrementalStarted = performance.now();
      const incremental = await api.importBatch(
        kb.id,
        kb.id,
        [{ id: prepared[0].id, document: prepared[0].document, metadata: prepared[0].metadata }],
        [prepared[0].embedding],
        false
      );
      const incrementalUpsertMs = performance.now() - incrementalStarted;
      log(`incremental upsert count=${incremental.count} (expected ${prepared.length}) ${formatMs(incrementalUpsertMs)}`);

      const perQuery: BenchResult["perQuery"] = [];
      const firstRoundEmbed: number[] = [];
      const hybridRetrieval: number[] = [];
      const hybridE2e: number[] = [];
      const hybridIds: string[][] = [];
      const vectorIds: string[][] = [];
      const vectorRetrieval: number[] = [];
      log(`running ${queries.length} queries (round 1, query cache bypassed)`);
      for (let index = 0; index < queries.length; index++) {
        const item = queries[index];
        const queryStart = performance.now();
        const embed = await client.embed([item.query], "query", { batchSize: 1, bypassCache: true });
        const afterEmbed = performance.now();
        const hybrid = await api.search({
          query: item.query,
          embedding: embed.embeddings[0],
          mode: "hybrid",
          topK: Math.max(10, benchConfig.topK),
          expectedKbId: kb.id,
        });
        const afterHybrid = performance.now();
        const vector = await api.search({
          query: item.query,
          embedding: embed.embeddings[0],
          mode: "vector",
          topK: Math.max(10, benchConfig.topK),
          expectedKbId: kb.id,
        });
        const afterVector = performance.now();
        firstRoundEmbed.push(afterEmbed - queryStart);
        hybridRetrieval.push(hybrid.retrievalMs);
        hybridE2e.push(afterHybrid - queryStart);
        vectorRetrieval.push(vector.retrievalMs);
        hybridIds.push(hybrid.results.map((result) => result.id));
        vectorIds.push(vector.results.map((result) => result.id));
        perQuery.push({
          query: item.query,
          hybridIds: hybrid.results.map((result) => result.id),
          vectorIds: vector.results.map((result) => result.id),
          hybridMs: hybrid.retrievalMs,
          vectorMs: vector.retrievalMs,
          embedMs: afterEmbed - queryStart,
          e2eMs: afterHybrid - queryStart,
          cacheHit: embed.cacheHit,
        });
        if (index === 0) log(`first query e2e=${formatMs(afterHybrid - queryStart)} embed=${formatMs(afterEmbed - queryStart)} retrieval=${formatMs(hybrid.retrievalMs)}`);
        if ((index + 1) % 6 === 0) log(`queries ${index + 1}/${queries.length}`);
        // avoid unused variable warning
        void afterVector;
      }

      const warmE2e: number[] = [];
      const warmRounds = 2;
      for (let round = 0; round < warmRounds; round++) {
        log(`warm round ${round + 1}/${warmRounds}`);
        for (const item of queries) {
          const start = performance.now();
          const embed = await client.embed([item.query], "query", { batchSize: 1, bypassCache: true });
          const hybrid = await api.search({
            query: item.query,
            embedding: embed.embeddings[0],
            mode: "hybrid",
            topK: Math.max(10, benchConfig.topK),
            expectedKbId: kb.id,
          });
          warmE2e.push(performance.now() - start);
        }
      }

      const hybridMetrics = {
        recall5: average(hybridIds.map((ids, index) => recallAt(ids, queries[index].relevant, 5))),
        recall10: average(hybridIds.map((ids, index) => recallAt(ids, queries[index].relevant, 10))),
        ndcg10: average(hybridIds.map((ids, index) => ndcgAt(ids, queries[index].relevant, 10))),
      };
      const vectorMetrics = {
        recall5: average(vectorIds.map((ids, index) => recallAt(ids, queries[index].relevant, 5))),
        recall10: average(vectorIds.map((ids, index) => recallAt(ids, queries[index].relevant, 10))),
        ndcg10: average(vectorIds.map((ids, index) => ndcgAt(ids, queries[index].relevant, 10))),
      };

      const benchResult: BenchResult = {
        label: benchConfig.label ?? "manual",
        startedAt,
        userAgent: navigator.userAgent,
        hardwareConcurrency: navigator.hardwareConcurrency,
        backend: status.backend,
        dtype: status.dtype,
        device: status.device,
        adapter: status.adapter,
        fallbackReason: status.fallbackReason,
        modelLoadMs,
        warmLoadMs,
        warmupMs: status.warmupMs,
        docCount: prepared.length,
        docChars,
        docEmbedMs,
        docEmbedMsPerDoc: docEmbedMs / prepared.length,
        docsPerSec: prepared.length / (docEmbedMs / 1000),
        batchSize: benchConfig.batchSize,
        writeMs,
        serverWriteMs,
        refreshMs,
        ingestTotalMs: docEmbedMs + writeMs + refreshMs,
        incrementalUpsertMs,
        countAfterReimport: incremental.count,
        queryCount: queries.length,
        queryEmbedMsMedian: median(firstRoundEmbed),
        queryEmbedMsP90: percentile(firstRoundEmbed, 90),
        retrievalMsMedian: median(hybridRetrieval),
        retrievalMsP90: percentile(hybridRetrieval, 90),
        e2eMsMedian: median(hybridE2e),
        e2eMsP90: percentile(hybridE2e, 90),
        firstQueryE2eMs: hybridE2e[0] ?? 0,
        warmQueryE2eMsMedian: median(warmE2e),
        hybrid: hybridMetrics,
        vector: vectorMetrics,
        perQuery,
        modelSizes,
        notes: [
          "Hybrid retrieval is one seekdb DBMS_HYBRID_SEARCH call (full-text + vector, RRF); no client-side fusion.",
          "E2E timing runs from query embedding start to search response; React render excluded (typically <2ms).",
          "Quality labels are fixed in data/queries.json and authored before measurement.",
          `Incremental upsert of the same doc id kept count=${incremental.count} (expected ${prepared.length}).`,
          `seekdb collection=${kb.collection}; refresh via collection.refresh_index() -> CALL dbms_index_manager.refresh().`,
          `Refresh strategy=${benchConfig.refreshMode ?? "end"} (end-of-import vs per-batch).`,
          `Warm reload measured=${benchConfig.measureReload}; browser Cache API used for model files.`,
        ],
      };
      log(`done hybrid recall@5=${benchResult.hybrid.recall5.toFixed(3)} ndcg@10=${benchResult.hybrid.ndcg10.toFixed(3)} e2e median=${formatMs(benchResult.e2eMsMedian)}`);
      setResult(benchResult);
      return benchResult;
    } finally {
      client?.terminate();
      try {
        await api.deleteKb(kb.id);
        log(`deleted bench kb ${kb.id}`);
      } catch (cleanupError) {
        log(`cleanup failed: ${String(cleanupError)}`);
      }
      if (previousActive) {
        try {
          await api.activateKb(previousActive);
        } catch {
          // ignore
        }
      }
      runningRef.current = false;
      setRunning(false);
    }
  }

  useEffect(() => {
    window.__runStepFunBench = (override) => runBench(override ?? {});
    window.__stepFunBenchReady = true;
    if (URL_PARAMS.get("autorun") === "1" && !autoRan.current) {
      autoRan.current = true;
      window.setTimeout(() => {
        runBench().catch((err) => setError(err instanceof Error ? err.message : String(err)));
      }, 100);
    }
    return () => {
      window.__stepFunBenchReady = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function runFromUi() {
    setRunning(true);
    try {
      await runBench();
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
      log(`error: ${String(error)}`);
      setRunning(false);
    }
  }

  return (
    <div className="app bench-app">
      <header className="topbar">
        <div className="brand">
          <div className="logo">B</div>
          <div>
            <h1>端侧检索评测台</h1>
            <p>同一语料 / 查询集 / 浏览器 · 对比后端、精度、批大小与冷热启动</p>
          </div>
        </div>
        <div className="badges">
          <span className="badge local">固定评测集 24 queries</span>
          <a className="bench-link" href="/">
            ← 返回应用
          </a>
        </div>
      </header>

      <div className="bench-layout">
        <section className="panel bench-config">
          <div className="panel-head">
            <span>实验配置</span>
            <span className={running ? "stage-pill running" : "stage-pill done"}>{running ? "运行中" : "就绪"}</span>
          </div>
          <div className="config-grid">
            <label>
              推理后端
              <select
                value={config.backend}
                onChange={(event) => setConfig({ ...config, backend: event.target.value as BenchConfig["backend"] })}
                disabled={running}
              >
                <option value="auto">auto（WebGPU 优先）</option>
                <option value="webgpu">webgpu</option>
                <option value="wasm">wasm</option>
              </select>
            </label>
            <label>
              模型精度
              <select
                value={config.dtype}
                onChange={(event) => setConfig({ ...config, dtype: event.target.value as BenchConfig["dtype"] })}
                disabled={running}
              >
                <option value="auto">auto</option>
                <option value="fp16">fp16</option>
                <option value="fp32">fp32</option>
                <option value="q8">q8 / int8</option>
              </select>
            </label>
            <label>
              向量化批大小
              <select
                value={config.batchSize}
                onChange={(event) => setConfig({ ...config, batchSize: Number(event.target.value) })}
                disabled={running}
              >
                {[1, 4, 8, 16, 32].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Top K
              <select
                value={config.topK}
                onChange={(event) => setConfig({ ...config, topK: Number(event.target.value) })}
                disabled={running}
              >
                {[10, 8, 6, 4].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
            <label>
              索引刷新
              <select
                value={config.refreshMode ?? "end"}
                onChange={(event) =>
                  setConfig({ ...config, refreshMode: event.target.value as BenchConfig["refreshMode"] })
                }
                disabled={running}
              >
                <option value="end">导入结束后刷新</option>
                <option value="per-batch">每批写入后刷新</option>
              </select>
            </label>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={config.measureReload ?? false}
                onChange={(event) => setConfig({ ...config, measureReload: event.target.checked })}
                disabled={running}
              />
              测量热缓存重载
            </label>
            <label>
              标签
              <input
                value={config.label ?? ""}
                onChange={(event) => setConfig({ ...config, label: event.target.value })}
                disabled={running}
              />
            </label>
          </div>
          <div className="bench-actions">
            <button className="primary" onClick={() => void runFromUi()} disabled={running}>
              {running ? "运行中…" : "开始评测（24 查询 × 3 轮）"}
            </button>
            <button
              className="ghost"
              onClick={() => result && downloadJson(`bench-${result.backend}-${result.dtype}-b${result.batchSize}.json`, result)}
              disabled={!result}
            >
              下载结果 JSON
            </button>
          </div>
          <p className="hint">
            评测会创建临时知识库、导入 46 篇固定语料、执行 24 条查询（1 轮测量 + 2 轮预热），完成后删除临时集合并恢复原知识库。
            模型文件从本机 <code>/models</code> 加载，推理全程在浏览器内。
          </p>
          {error && <div className="notice error">{error}</div>}
        </section>

        <section className="panel bench-log-panel">
          <div className="panel-head">
            <span>运行日志</span>
            <span className="muted">{logs.length} lines</span>
          </div>
          <pre className="bench-log">{logs.length ? logs.join("\n") : "点击开始评测后显示分阶段日志…"}</pre>
        </section>

        {result && (
          <>
            <section className="panel bench-summary">
              <div className="panel-head">
                <span>结果矩阵</span>
                <span className="tag">
                  {result.backend}/{result.dtype}/b{result.batchSize}
                </span>
              </div>
              <div className="metric-grid">
                <div>
                  <span>模型加载</span>
                  <strong>{formatMs(result.modelLoadMs)}</strong>
                  <em>{result.warmLoadMs ? `热重载 ${formatMs(result.warmLoadMs)}` : "冷加载"}</em>
                </div>
                <div>
                  <span>文档向量化</span>
                  <strong>{formatMs(result.docEmbedMs)}</strong>
                  <em>{result.docsPerSec.toFixed(1)} docs/s · {formatMs(result.docEmbedMsPerDoc)}/doc</em>
                </div>
                <div>
                  <span>写入 seekdb</span>
                  <strong>{formatMs(result.writeMs)}</strong>
                  <em>server {formatMs(result.serverWriteMs)}</em>
                </div>
                <div>
                  <span>索引刷新</span>
                  <strong>{formatMs(result.refreshMs)}</strong>
                  <em>dbms_index_manager.refresh</em>
                </div>
                <div>
                  <span>查询向量化</span>
                  <strong>{formatMs(result.queryEmbedMsMedian)}</strong>
                  <em>中位数 · P90 {formatMs(result.queryEmbedMsP90)}</em>
                </div>
                <div>
                  <span>seekdb 检索</span>
                  <strong>{formatMs(result.retrievalMsMedian)}</strong>
                  <em>中位数 · P90 {formatMs(result.retrievalMsP90)}</em>
                </div>
                <div>
                  <span>端到端</span>
                  <strong>{formatMs(result.e2eMsMedian)}</strong>
                  <em>首个请求 {formatMs(result.firstQueryE2eMs)} · 预热 {formatMs(result.warmQueryE2eMsMedian)}</em>
                </div>
                <div>
                  <span>增量 upsert</span>
                  <strong>{result.countAfterReimport} 篇</strong>
                  <em>{formatMs(result.incrementalUpsertMs)} · 数量不变</em>
                </div>
              </div>

              <div className="quality-grid">
                <div className="quality-card">
                  <h3>混合检索质量</h3>
                  <div>
                    <span>Recall@5</span>
                    <strong>{(result.hybrid.recall5 * 100).toFixed(1)}%</strong>
                  </div>
                  <div>
                    <span>Recall@10</span>
                    <strong>{(result.hybrid.recall10 * 100).toFixed(1)}%</strong>
                  </div>
                  <div>
                    <span>nDCG@10</span>
                    <strong>{(result.hybrid.ndcg10 * 100).toFixed(1)}%</strong>
                  </div>
                </div>
                <div className="quality-card">
                  <h3>仅向量检索</h3>
                  <div>
                    <span>Recall@5</span>
                    <strong>{(result.vector.recall5 * 100).toFixed(1)}%</strong>
                  </div>
                  <div>
                    <span>Recall@10</span>
                    <strong>{(result.vector.recall10 * 100).toFixed(1)}%</strong>
                  </div>
                  <div>
                    <span>nDCG@10</span>
                    <strong>{(result.vector.ndcg10 * 100).toFixed(1)}%</strong>
                  </div>
                </div>
              </div>

              <details className="raw-result">
                <summary>原始结果 JSON</summary>
                <pre>{JSON.stringify(result, null, 2)}</pre>
              </details>
            </section>
          </>
        )}
      </div>
    </div>
  );
}

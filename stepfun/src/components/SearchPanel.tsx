import type { EmbedderStatus, Kb, QueryTiming, SearchResponse } from "../types.ts";
import { formatMs, highlight } from "../utils.ts";

const EXAMPLES = [
  "seekdb 的混合检索是怎么实现的？",
  "BGE 查询时要加什么前缀？",
  "浏览器如何自动降级到 WASM？",
  "番茄炒蛋怎么做？",
];

const MODE_INFO: Record<string, string> = {
  hybrid: "seekdb DBMS_HYBRID_SEARCH：全文 + 向量，RRF 融合",
  vector: "仅 HNSW 向量近邻（cosine）",
  keyword: "仅 seekdb 全文索引（IK 分词）",
};

function Score({ result }: { result: SearchResponse["results"][number] }) {
  if (result.scoreKind === "cosine_distance") {
    return <span className="score">相似度 {(1 - result.score).toFixed(4)}</span>;
  }
  if (result.scoreKind === "keyword_score") {
    return <span className="score">BM25 {result.score.toFixed(3)}</span>;
  }
  return <span className="score">RRF {result.score.toFixed(4)}</span>;
}

export function SearchPanel({
  activeKb,
  embedStatus,
  query,
  setQuery,
  mode,
  setMode,
  topK,
  setTopK,
  searching,
  result,
  timing,
  error,
  onSearch,
  onExample,
}: {
  activeKb: Kb | null;
  embedStatus: EmbedderStatus | null;
  query: string;
  setQuery: (value: string) => void;
  mode: "hybrid" | "vector" | "keyword";
  setMode: (value: "hybrid" | "vector" | "keyword") => void;
  topK: number;
  setTopK: (value: number) => void;
  searching: boolean;
  result: SearchResponse | null;
  timing: QueryTiming | null;
  error: string | null;
  onSearch: () => void;
  onExample: (value: string) => void;
}) {
  const disabled = !activeKb || !embedStatus || searching;

  return (
    <div className="search-layout">
      <section className="panel search-card">
        <div className="search-bar">
          <input
            className="search-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.nativeEvent.isComposing) onSearch();
            }}
            placeholder="用自然语言提问，或输入精确关键词…"
            autoFocus
          />
          <button className="primary search-button" onClick={onSearch} disabled={disabled || !query.trim()}>
            {searching ? "检索中…" : "混合检索"}
          </button>
        </div>

        <div className="search-options">
          <div className="segmented">
            {(["hybrid", "vector", "keyword"] as const).map((value) => (
              <button
                key={value}
                className={mode === value ? "active" : ""}
                onClick={() => setMode(value)}
                type="button"
              >
                {value === "hybrid" ? "混合" : value === "vector" ? "向量" : "全文"}
              </button>
            ))}
          </div>
          <span className="mode-info">{MODE_INFO[mode]}</span>
          <label className="topk">
            Top
            <select value={topK} onChange={(event) => setTopK(Number(event.target.value))}>
              {[4, 6, 8, 10, 20].map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="examples">
          <span>试试</span>
          {EXAMPLES.map((example) => (
            <button key={example} className="chip" onClick={() => onExample(example)} disabled={disabled}>
              {example}
            </button>
          ))}
        </div>

        {timing && (
          <div className="timing-strip">
            <div>
              <span>查询向量化</span>
              <strong>{formatMs(timing.embedMs)}</strong>
              <em>{timing.cacheHit ? "缓存命中" : embedStatus?.backend === "webgpu" ? "WebGPU" : "WASM"}</em>
            </div>
            <div>
              <span>seekdb 检索</span>
              <strong>{formatMs(timing.retrievalMs)}</strong>
              <em>数据库内融合</em>
            </div>
            <div className="highlight">
              <span>端到端</span>
              <strong>{formatMs(timing.e2eMs)}</strong>
              <em>输入 → 结果可展示</em>
            </div>
          </div>
        )}

        {error && <div className="notice error">{error}</div>}
      </section>

      <section className="results-section">
        {result && (
          <div className="results-head">
            <div>
              <strong>{result.results.length}</strong> 条结果
              <span className="dot">·</span>
              <span>{result.mode === "hybrid" ? "混合检索" : result.mode === "vector" ? "向量检索" : "全文检索"}</span>
              <span className="dot">·</span>
              <code>{result.collection}</code>
            </div>
            <div className="branch-note">{result.rawBranchNote}</div>
          </div>
        )}

        {result && result.results.length === 0 && (
          <div className="empty-state">
            <div className="empty-mark">∅</div>
            <h3>没有命中结果</h3>
            <p>当前集合中没有匹配内容。换一种表述，或先导入演示语料。</p>
          </div>
        )}

        {result && result.results.length > 0 && (
          <ol className="results">
            {result.results.map((item) => (
              <li className="result-card" key={item.id}>
                <div className={`rank ${item.rank <= 3 ? "top" : ""}`}>{item.rank}</div>
                <div className="result-body">
                  <div className="result-top">
                    <h3>{String(item.metadata?.title ?? item.id)}</h3>
                    <Score result={item} />
                  </div>
                  <p
                    className="result-text"
                    dangerouslySetInnerHTML={{ __html: highlight(item.document, result.query) }}
                  />
                  <div className="result-meta">
                    <code>{item.id}</code>
                    {item.metadata?.kbId && (
                      <span className="meta-chip provenance" title={String(item.metadata.collection ?? "")}>
                        kb {String(item.metadata.kbId).slice(0, 8)}
                      </span>
                    )}
                    {item.metadata?.topic && <span className="meta-chip">{String(item.metadata.topic)}</span>}
                    {item.metadata?.source && <span className="meta-chip">{String(item.metadata.source)}</span>}
                    {item.metadata?.chars ? <span>{String(item.metadata.chars)} 字</span> : null}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        )}

        {!result && !error && (
          <div className="empty-state search-empty">
            <div className="radar">
              <span />
              <span />
              <span />
            </div>
            <h3>本地生成查询向量，seekdb 内完成混合检索</h3>
            <p>
              查询文本先在浏览器 Worker 中向量化，再与全文关键词一起交给 seekdb 的
              <code> DBMS_HYBRID_SEARCH</code>，由数据库用 RRF 融合后返回唯一排序。
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

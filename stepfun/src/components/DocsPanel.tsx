import type { DocItem, Kb } from "../types.ts";
import { formatMs } from "../utils.ts";

export function DocsPanel({
  activeKb,
  docs,
  total,
  loading,
  refreshing,
  refreshMs,
  onRefresh,
  onDeleteDoc,
}: {
  activeKb: Kb | null;
  docs: DocItem[];
  total: number;
  loading: boolean;
  refreshing: boolean;
  refreshMs: number | null;
  onRefresh: () => void;
  onDeleteDoc: (id: string) => void;
}) {
  return (
    <section className="panel docs-panel">
      <div className="panel-head">
        <span>集合文档</span>
        <div className="docs-actions">
          {refreshMs !== null && <span className="muted">刷新 {formatMs(refreshMs)}</span>}
          <button className="ghost small" onClick={onRefresh} disabled={!activeKb || refreshing}>
            {refreshing ? "刷新中…" : "刷新索引"}
          </button>
        </div>
      </div>
      <div className="docs-summary">
        <div>
          <span>集合</span>
          <code>{activeKb?.collection ?? "-"}</code>
        </div>
        <div>
          <span>文档数</span>
          <strong>{total}</strong>
        </div>
        <div>
          <span>向量维度</span>
          <strong>{activeKb?.dim ?? "-"}</strong>
        </div>
        <div>
          <span>索引</span>
          <strong>HNSW + IK</strong>
        </div>
      </div>

      {loading ? (
        <div className="empty-state compact">读取 seekdb 文档…</div>
      ) : docs.length === 0 ? (
        <div className="empty-state compact">当前集合为空，去“导入”页添加语料。</div>
      ) : (
        <div className="doc-table">
          <div className="doc-row doc-head">
            <span>标题 / 原文</span>
            <span>主题</span>
            <span>ID</span>
            <span>字数</span>
            <span />
          </div>
          {docs.map((doc) => (
            <div className="doc-row" key={doc.id}>
              <div className="doc-main">
                <strong>{String(doc.metadata?.title ?? doc.id)}</strong>
                <p>{doc.document}</p>
              </div>
              <span className="meta-chip">{String(doc.metadata?.topic ?? doc.metadata?.source ?? "-")}</span>
              <code title={doc.id}>{doc.id}</code>
              <span>{doc.document.length}</span>
              <button className="danger-text" onClick={() => onDeleteDoc(doc.id)} title="删除文档">
                删除
              </button>
            </div>
          ))}
        </div>
      )}
      {total > docs.length && docs.length > 0 && (
        <p className="hint">仅显示前 {docs.length} 条，共 {total} 条。检索仍然覆盖完整集合。</p>
      )}
    </section>
  );
}

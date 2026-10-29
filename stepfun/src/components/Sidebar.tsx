import type { AppState, EmbedderStatus, HealthInfo, Kb, ModelInfo } from "../types.ts";
import { formatBytes, formatMs } from "../utils.ts";

export function Sidebar({
  state,
  activeKb,
  status,
  detail,
  modelInfo,
  health,
  creating,
  newKbName,
  setNewKbName,
  onActivate,
  onCreate,
  onDelete,
}: {
  state: AppState | null;
  activeKb: Kb | null;
  status: EmbedderStatus | null;
  detail: any;
  modelInfo: ModelInfo | null;
  health: HealthInfo | null;
  creating: boolean;
  newKbName: string;
  setNewKbName: (value: string) => void;
  onActivate: (id: string) => void;
  onCreate: () => void;
  onDelete: (kb: Kb) => void;
}) {
  const fp32 = modelInfo?.files.find((file) => file.dtype === "fp32");
  const q8 = modelInfo?.files.find((file) => file.dtype === "q8");
  const fp16 = modelInfo?.files.find((file) => file.dtype === "fp16");
  const maxBytes = fp32?.bytes || q8?.bytes || 1;
  const progress =
    typeof detail?.progress === "number"
      ? Math.round((detail.progress > 1 ? detail.progress / 100 : detail.progress) * 100)
      : null;

  return (
    <aside className="sidebar">
      <section className="panel kb-panel">
        <div className="panel-head">
          <span>知识库</span>
          <span className="count">{state?.kbs.length ?? 0}</span>
        </div>
        <ul className="kb-list">
          {state?.kbs.map((kb) => (
            <li key={kb.id}>
              <button
                className={`kb-item ${activeKb?.id === kb.id ? "active" : ""}`}
                onClick={() => onActivate(kb.id)}
                title={kb.collection}
              >
                <span className="kb-dot" />
                <span className="kb-body">
                  <span className="kb-name">{kb.name}</span>
                  <span className="kb-meta">
                    {kb.docCount} 篇 · {kb.id.slice(0, 8)}
                  </span>
                </span>
                <span className="kb-collection">{kb.collection}</span>
              </button>
              <button
                className="kb-delete"
                title="删除知识库"
                onClick={() => onDelete(kb)}
                aria-label={`删除 ${kb.name}`}
              >
                ×
              </button>
            </li>
          ))}
          {state?.kbs.length === 0 && <li className="empty-hint">还没有知识库，创建一个开始。</li>}
        </ul>
        <form
          className="kb-create"
          onSubmit={(event) => {
            event.preventDefault();
            onCreate();
          }}
        >
          <input
            value={newKbName}
            onChange={(event) => setNewKbName(event.target.value)}
            placeholder="新知识库名称"
            maxLength={40}
          />
          <button type="submit" disabled={creating || !newKbName.trim()}>
            {creating ? "创建中" : "创建"}
          </button>
        </form>
        <p className="hint">
          切换知识库会立即清空旧结果；检索请求只认服务端会话中的 activeKbId，旧集合请求返回 409。
        </p>
      </section>

      <section className="panel model-panel">
        <div className="panel-head">
          <span>本地模型</span>
          <span className={`status-dot ${status ? "ok" : detail?.stage === "error" ? "error" : "busy"}`} />
        </div>
        <div className="model-title">BGE-small-zh-v1.5</div>
        <div className="model-sub">512 维 · CLS pooling · BAAI / Xenova ONNX</div>

        {status ? (
          <div className="backend-detail">
            <div>
              <span>当前后端</span>
              <strong className={status.backend === "wasm" ? "warn-text" : "ok-text"}>
                {status.backend === "webgpu" ? "WebGPU" : "WASM"} ·{" "}
                {status.dtype === "q8" ? "INT8" : status.dtype.toUpperCase()}
              </strong>
            </div>
            <div>
              <span>模型加载</span>
              <strong>{formatMs(status.loadMs)}</strong>
            </div>
            <div>
              <span>预热推理</span>
              <strong>{formatMs(status.warmupMs)}</strong>
            </div>
            {status.backend === "wasm" && (
              <div>
                <span>WASM 线程</span>
                <strong>{status.threads}</strong>
              </div>
            )}
            {status.adapter && (
              <div className="adapter">
                <span>GPU</span>
                <strong>{status.adapter}</strong>
              </div>
            )}
          </div>
        ) : (
          <div className="model-loading">
            <div className="loading-line">
              <span>{detail?.message ?? "正在加载模型"}</span>
              {progress !== null && <strong>{progress}%</strong>}
            </div>
            <div className="progress-track slim">
              <span style={{ width: `${progress ?? 8}%` }} />
            </div>
            <p className="hint tiny">首次加载需下载权重 → 初始化 ONNX 图 → GPU 内核编译。</p>
          </div>
        )}

        {status?.fallbackReason && <div className="notice warn">{status.fallbackReason}</div>}
        {detail?.stage === "error" && <div className="notice error">{detail.message}</div>}

        <div className="size-list">
          {[
            { label: "FP32 原始", file: fp32, tone: "fp32" },
            { label: "FP16", file: fp16, tone: "fp16" },
            { label: "INT8 动态", file: q8, tone: "q8" },
          ].map(({ label, file, tone }) => (
            <div
              className={`size-row ${status?.dtype === file?.dtype ? "active" : ""}`}
              key={label}
              title={!file?.exists ? "需运行 npm run models:fetch -- --variant fp32" : undefined}
            >
              <span className="size-label">{label}</span>
              <span className="size-bar">
                <i className={tone} style={{ width: file ? `${Math.max(4, (file.bytes / maxBytes) * 100)}%` : "0%" }} />
              </span>
              <span className="size-value">
                {file?.bytes ? `${formatBytes(file.bytes)}${file.exists ? "" : "*"}` : "未提供"}
              </span>
            </div>
          ))}
        </div>
        {fp32?.exists && q8?.exists && (
          <div className="compression">
            压缩比 <strong>{(fp32.bytes / q8.bytes).toFixed(2)}×</strong>（FP32 → INT8）
          </div>
        )}
      </section>

      <section className="panel session-panel">
        <div className="panel-head">
          <span>会话状态</span>
          <span className="status-dot ok" />
        </div>
        <div className="kv">
          <span>session</span>
          <code>{state?.sessionId.slice(0, 13) ?? "-"}</code>
        </div>
        <div className="kv">
          <span>active</span>
          <code>{activeKb?.id ?? "none"}</code>
        </div>
        <div className="kv">
          <span>collection</span>
          <code title={activeKb?.collection}>{activeKb?.collection ?? "-"}</code>
        </div>
        <div className="kv">
          <span>seekdb</span>
          <code>{health?.mode === "server" ? `${health.host}:${health.port}` : health?.mode ?? "-"}</code>
        </div>
      </section>
    </aside>
  );
}

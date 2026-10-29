import { useRef, useState } from "react";
import type { EmbedderStatus, ImportProgress, Kb } from "../types.ts";
import { formatMs } from "../utils.ts";

const STAGES = [
  { key: "parsing", label: "解析与切分" },
  { key: "embedding", label: "浏览器本地向量化" },
  { key: "writing", label: "写入 seekdb" },
  { key: "refreshing", label: "刷新索引" },
] as const;

function stageIndex(stage: ImportProgress["stage"]): number {
  if (stage === "parsing") return 0;
  if (stage === "embedding") return 1;
  if (stage === "writing") return 2;
  if (stage === "refreshing") return 3;
  if (stage === "done") return 4;
  return -1;
}

export function ImportPanel({
  activeKb,
  embedStatus,
  batchSize,
  setBatchSize,
  progress,
  logs,
  onImportText,
  onImportFiles,
  onImportDemo,
  onGoSearch,
}: {
  activeKb: Kb | null;
  embedStatus: EmbedderStatus | null;
  batchSize: number;
  setBatchSize: (value: number) => void;
  progress: ImportProgress;
  logs: string[];
  onImportText: (text: string) => Promise<void>;
  onImportFiles: (files: FileList | File[]) => Promise<void>;
  onImportDemo: (file: string) => Promise<void>;
  onGoSearch: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const current = stageIndex(progress.stage);
  const running = ["parsing", "embedding", "writing", "refreshing"].includes(progress.stage);
  const disabled = !activeKb || !embedStatus || busy || running;

  async function wrap(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="import-layout">
      <section className="panel import-card">
        <div className="panel-head">
          <span>导入语料</span>
          <span className="tag">{activeKb?.name ?? "未选择知识库"}</span>
        </div>
        <p className="panel-lead">
          粘贴文本或上传 <code>.txt / .md / .json</code>。应用会在浏览器 Worker 中分批向量化，只把向量与原文写入
          seekdb，不调用任何云端推理。
        </p>

        <textarea
          className="paste-area"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={"粘贴至少一段文本。空行或 Markdown 标题会被切分为文档。\n\n例如：\nseekdb 通过 DBMS_HYBRID_SEARCH 在数据库内完成向量与全文的混合检索。\n\n浏览器端使用 WebGPU 优先执行 embedding，不支持时自动降级 WASM。"}
          rows={9}
        />

        <div className="import-actions">
          <button
            className="primary"
            disabled={disabled || !text.trim()}
            onClick={() => wrap(async () => {
              await onImportText(text);
              setText("");
            })}
          >
            {running ? "导入中…" : `导入 ${activeKb ? "当前知识库" : ""}`}
          </button>
          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".txt,.md,.json,text/plain,text/markdown,application/json"
            style={{ display: "none" }}
            onChange={(event) => {
              const files = event.target.files;
              if (!files || files.length === 0) return;
              void wrap(async () => {
                await onImportFiles(files);
                if (fileRef.current) fileRef.current.value = "";
              });
            }}
          />
          <button className="ghost" disabled={disabled} onClick={() => fileRef.current?.click()}>
            上传文件
          </button>
          <select
            className="select"
            value={batchSize}
            disabled={running}
            onChange={(event) => setBatchSize(Number(event.target.value))}
            title="文档向量化批大小"
          >
            {[1, 4, 8, 16, 32].map((value) => (
              <option key={value} value={value}>
                batch {value}
              </option>
            ))}
          </select>
        </div>

        <div className="demo-row">
          <span>固定演示语料</span>
          <button className="link-button" disabled={disabled} onClick={() => wrap(() => onImportDemo("kb-seekdb.json"))}>
            46 篇 seekdb 语料
          </button>
          <button className="link-button" disabled={disabled} onClick={() => wrap(() => onImportDemo("kb-web.json"))}>
            Web 隔离语料（8 篇）
          </button>
        </div>

        {!embedStatus && (
          <div className="notice warn">
            模型尚未就绪，导入按钮会在后端加载完成后启用。当前模型加载由本地静态文件完成，不经过云端。
          </div>
        )}
      </section>

      <section className="panel progress-card">
        <div className="panel-head">
          <span>导入流水线</span>
          <span className={`stage-pill ${progress.stage}`}>{progress.stage === "idle" ? "待开始" : progress.stage}</span>
        </div>

        <div className="stage-list">
          {STAGES.map((stage, index) => {
            const state =
              current > index || progress.stage === "done"
                ? "done"
                : current === index
                  ? progress.stage === "error" || progress.stage === "aborted"
                    ? "error"
                    : "active"
                  : "idle";
            return (
              <div className={`stage ${state}`} key={stage.key}>
                <span className="stage-index">{state === "done" ? "✓" : index + 1}</span>
                <div className="stage-body">
                  <div className="stage-title">{stage.label}</div>
                  <div className="stage-sub">
                    {stage.key === "parsing" && progress.stage !== "idle" && "切分文本并生成稳定文档 ID"}
                    {stage.key === "embedding" &&
                      (progress.stage === "embedding" || progress.embedMs
                        ? `${progress.done}/${progress.total} 篇 · ${progress.embedMs ? formatMs(progress.embedMs) : "进行中"}`
                        : "WebGPU 优先，自动降级 WASM")}
                    {stage.key === "writing" &&
                      (progress.writeMs ? `${progress.total} 篇 · ${formatMs(progress.writeMs)}` : "upsert 向量 + 原文 + 元数据")}
                    {stage.key === "refreshing" &&
                      (progress.refreshMs ? formatMs(progress.refreshMs) : "刷新后新文档立即可检索")}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <div className="progress-track">
          <span
            style={{
              width:
                progress.stage === "done"
                  ? "100%"
                  : progress.stage === "idle" || progress.stage === "error" || progress.stage === "aborted"
                    ? "0%"
                    : `${Math.min(96, ((current + (progress.total ? progress.done / progress.total : 0)) / 4) * 100)}%`,
            }}
          />
        </div>

        <div className="import-stats">
          <div>
            <span>向量化</span>
            <strong>{progress.embedMs ? formatMs(progress.embedMs) : "-"}</strong>
          </div>
          <div>
            <span>写入</span>
            <strong>{progress.writeMs ? formatMs(progress.writeMs) : "-"}</strong>
          </div>
          <div>
            <span>刷新</span>
            <strong>{progress.refreshMs ? formatMs(progress.refreshMs) : "-"}</strong>
          </div>
          <div>
            <span>总耗时</span>
            <strong>{progress.totalMs ? formatMs(progress.totalMs) : "-"}</strong>
          </div>
        </div>

        {progress.message && (
          <div className={`notice ${progress.stage === "error" ? "error" : progress.stage === "done" ? "ok" : ""}`}>
            {progress.message}
            {progress.docsPerSec ? ` · ${progress.docsPerSec.toFixed(1)} docs/s` : ""}
          </div>
        )}

        {progress.stage === "done" && (
          <button className="primary wide" onClick={onGoSearch}>
            去检索当前知识库
          </button>
        )}

        {logs.length > 0 && (
          <div className="import-log">
            {logs.slice(-6).map((line, index) => (
              <div key={index}>{line}</div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

import type { EmbedderStatus, HealthInfo, ModelInfo } from "../types.ts";
import { formatBytes } from "../utils.ts";

function CpuIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
      <rect x="7" y="7" width="10" height="10" rx="2" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

function DbIcon() {
  return (
    <svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">
      <ellipse cx="12" cy="5" rx="7" ry="3" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path d="M5 5v7c0 1.7 3.1 3 7 3s7-1.3 7-3V5M5 12v7c0 1.7 3.1 3 7 3s7-1.3 7-3v-7" fill="none" stroke="currentColor" strokeWidth="1.7" />
    </svg>
  );
}

function LockIcon() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">
      <rect x="5" y="10" width="14" height="10" rx="2" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" strokeWidth="1.7" />
    </svg>
  );
}

export function StatusBadges({
  status,
  detail,
  health,
  modelInfo,
}: {
  status: EmbedderStatus | null;
  detail: any;
  health: HealthInfo | null;
  modelInfo: ModelInfo | null;
}) {
  const loading = !status && detail?.stage !== "error";
  const progress =
    typeof detail?.progress === "number"
      ? Math.round((detail.progress > 1 ? detail.progress / 100 : detail.progress) * 100)
      : null;
  const activeSize = modelInfo?.files.find((file) => file.dtype === status?.dtype)?.bytes;
  const dtypeLabel = status?.dtype === "q8" ? "INT8" : status?.dtype.toUpperCase();
  const backendLabel = status
    ? `${status.backend === "webgpu" ? "WebGPU" : "WASM"} · ${dtypeLabel}`
    : detail?.stage === "error"
      ? "模型加载失败"
      : "模型加载中";

  return (
    <div className="badges">
      <span
        className={`badge backend ${status?.backend === "wasm" ? "warn" : "ok"} ${loading ? "pulse" : ""}`}
        title={
          status
            ? `adapter: ${status.adapter ?? "n/a"}\nthreads: ${status.threads}\nload: ${status.loadMs.toFixed(0)}ms\nwarmup: ${status.warmupMs.toFixed(0)}ms`
            : detail?.message
        }
      >
        <CpuIcon />
        {backendLabel}
        {loading && progress !== null ? ` ${progress}%` : ""}
        {activeSize ? <em>{formatBytes(activeSize)}</em> : null}
      </span>
      <span className="badge db" title={health?.version}>
        <DbIcon />
        seekdb {health?.mode === "server" ? `server:${health?.port}` : health?.mode ?? "..."}
        <em>{health?.version?.replace("5.7.25-", "") ?? ""}</em>
      </span>
      <span className="badge local">
        <LockIcon />
        本地推理
      </span>
    </div>
  );
}

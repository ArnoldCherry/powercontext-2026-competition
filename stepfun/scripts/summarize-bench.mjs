#!/usr/bin/env node
/** Summarize evidence/bench-*.json into medians and markdown tables. */
import fs from "node:fs";
import path from "node:path";

const dir = path.resolve("evidence");
const files = fs
  .readdirSync(dir)
  .filter((name) => /^bench-.*\.json$/.test(name) && !name.includes("-error"))
  .sort();

function median(values) {
  const valid = values.filter((value) => typeof value === "number" && Number.isFinite(value));
  if (valid.length === 0) return null;
  const sorted = [...valid].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function fmt(value, digits = 1) {
  if (value === null || value === undefined) return "-";
  if (Math.abs(value) >= 1000) return `${(value / 1000).toFixed(2)} s`;
  return `${value.toFixed(digits)} ms`;
}

const groups = new Map();
for (const file of files) {
  const data = JSON.parse(fs.readFileSync(path.join(dir, file), "utf8"));
  if (!data || data.backend === undefined) continue;
  const key = file.replace(/^bench-/, "").replace(/\.json$/, "").replace(/-r\d+$/, "");
  if (!groups.has(key)) groups.set(key, []);
  groups.get(key).push({ file, data });
}

const summary = [];
const csv = [
  "group,run,backend,dtype,batch,modelLoadMs,warmLoadMs,docEmbedMs,docsPerSec,writeMs,refreshMs,queryEmbedMs,retrievalMs,e2eMs,warmE2eMs,recall5,recall10,ndcg10,vectorRecall5,vectorNdcg10",
];
for (const [key, runs] of [...groups.entries()].sort()) {
  const first = runs[0].data;
  const pick = (field) => median(runs.map((run) => run.data[field]));
  const hybrid = (field) => median(runs.map((run) => run.data.hybrid?.[field]));
  const vector = (field) => median(runs.map((run) => run.data.vector?.[field]));
  const row = {
    key,
    runs: runs.length,
    backend: first.backend,
    dtype: first.dtype,
    batch: first.batchSize,
    modelLoadMs: pick("modelLoadMs"),
    warmLoadMs: pick("warmLoadMs"),
    docEmbedMs: pick("docEmbedMs"),
    docsPerSec: pick("docsPerSec"),
    writeMs: pick("writeMs"),
    refreshMs: pick("refreshMs"),
    queryEmbedMs: pick("queryEmbedMsMedian"),
    retrievalMs: pick("retrievalMsMedian"),
    e2eMs: pick("e2eMsMedian"),
    warmE2eMs: pick("warmQueryE2eMsMedian"),
    recall5: hybrid("recall5"),
    recall10: hybrid("recall10"),
    ndcg10: hybrid("ndcg10"),
    vectorRecall5: vector("recall5"),
    vectorNdcg10: vector("ndcg10"),
    modelSizes: first.modelSizes,
    refreshMode: first.notes?.some((note) => note.includes("per-batch")) ? "per-batch" : "end",
  };
  summary.push(row);
  for (const run of runs) {
    const d = run.data;
    csv.push(
      [
        key,
        run.file,
        d.backend,
        d.dtype,
        d.batchSize,
        Math.round(d.modelLoadMs ?? 0),
        Math.round(d.warmLoadMs ?? 0),
        Math.round(d.docEmbedMs ?? 0),
        Number((d.docsPerSec ?? 0).toFixed(2)),
        Math.round(d.writeMs ?? 0),
        Math.round(d.refreshMs ?? 0),
        Number((d.queryEmbedMsMedian ?? 0).toFixed(2)),
        Number((d.retrievalMsMedian ?? 0).toFixed(2)),
        Number((d.e2eMsMedian ?? 0).toFixed(2)),
        Number((d.warmQueryE2eMsMedian ?? 0).toFixed(2)),
        Number((d.hybrid?.recall5 ?? 0).toFixed(4)),
        Number((d.hybrid?.recall10 ?? 0).toFixed(4)),
        Number((d.hybrid?.ndcg10 ?? 0).toFixed(4)),
        Number((d.vector?.recall5 ?? 0).toFixed(4)),
        Number((d.vector?.ndcg10 ?? 0).toFixed(4)),
      ].join(",")
    );
  }
}

fs.writeFileSync(path.join(dir, "bench-summary.json"), JSON.stringify({ generatedAt: new Date().toISOString(), groups: summary }, null, 2));
fs.writeFileSync(path.join(dir, "bench-runs.csv"), csv.join("\n") + "\n");

const perf = [
  "| 配置 | 后端 / 精度 | 批大小 | 冷加载中位数 | 热重载中位数 | 向量化 docs/s | 写入 ms | 刷新 ms | 查询向量化 ms | seekdb 检索 ms | 端到端 ms |",
  "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
  ...summary.map(
    (row) =>
      `| ${row.key} | ${row.backend} / ${row.dtype} | ${row.batch} | ${fmt(row.modelLoadMs, 0)} | ${fmt(row.warmLoadMs, 0)} | ${row.docsPerSec?.toFixed(1) ?? "-"} | ${row.writeMs?.toFixed(0) ?? "-"} | ${row.refreshMs?.toFixed(0) ?? "-"} | ${row.queryEmbedMs?.toFixed(2) ?? "-"} | ${row.retrievalMs?.toFixed(2) ?? "-"} | ${row.e2eMs?.toFixed(2) ?? "-"} |`
  ),
];
const quality = [
  "| 配置 | 混合 R@5 | 混合 R@10 | 混合 nDCG@10 | 仅向量 R@5 | 仅向量 nDCG@10 |",
  "| --- | ---: | ---: | ---: | ---: | ---: |",
  ...summary.map(
    (row) =>
      `| ${row.key} | ${((row.recall5 ?? 0) * 100).toFixed(1)}% | ${((row.recall10 ?? 0) * 100).toFixed(1)}% | ${((row.ndcg10 ?? 0) * 100).toFixed(1)}% | ${((row.vectorRecall5 ?? 0) * 100).toFixed(1)}% | ${((row.vectorNdcg10 ?? 0) * 100).toFixed(1)}% |`
  ),
];
const sizes = summary[0]?.modelSizes
  ? [
      "| 精度 | 体积 (bytes) | 体积 (MiB) |",
      "| --- | ---: | ---: |",
      ...Object.entries(summary[0].modelSizes).map(
        ([dtype, bytes]) => `| ${dtype} | ${bytes} | ${(bytes / 1024 / 1024).toFixed(1)} |`
      ),
    ].join("\n")
  : "";
const markdown = [
  `# Benchmark summary`,
  ``,
  `Generated ${new Date().toISOString()} from ${files.length} raw runs. Values are medians across repeated runs where available.`,
  ``,
  `## Performance`,
  ``,
  ...perf,
  ``,
  `## Retrieval quality (46 docs / 24 labeled queries)`,
  ``,
  ...quality,
  ``,
  `## Model sizes`,
  ``,
  sizes,
  ``,
  `Notes: e2e includes query embedding + HTTP round trip (Windows Chrome → WSL backend) + seekdb retrieval + JSON; seekdb retrieval is measured inside the server around the hybridSearch call.`,
].join("\n");
fs.writeFileSync(path.join(dir, "bench-summary.md"), markdown);
console.log(markdown);
console.error(`wrote ${path.join(dir, "bench-summary.json")}, bench-runs.csv, bench-summary.md`);

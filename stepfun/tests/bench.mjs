import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const BASE = process.env.APP_URL || "http://127.0.0.1:8788";
const EVIDENCE = process.env.EVIDENCE || path.resolve("evidence");
fs.mkdirSync(EVIDENCE, { recursive: true });

const configs = JSON.parse(
  process.env.CONFIGS ||
    JSON.stringify([
      { backend: "webgpu", dtype: "fp16", batchSize: 16, topK: 10, measureReload: true, refreshMode: "end", label: "webgpu-fp16-b16" },
      { backend: "webgpu", dtype: "fp16", batchSize: 1, topK: 10, refreshMode: "end", label: "webgpu-fp16-b1" },
      { backend: "webgpu", dtype: "fp16", batchSize: 32, topK: 10, refreshMode: "end", label: "webgpu-fp16-b32" },
      { backend: "wasm", dtype: "q8", batchSize: 16, topK: 10, measureReload: true, refreshMode: "end", label: "wasm-q8-b16" },
      { backend: "wasm", dtype: "q8", batchSize: 1, topK: 10, refreshMode: "end", label: "wasm-q8-b1" },
      { backend: "wasm", dtype: "q8", batchSize: 32, topK: 10, refreshMode: "end", label: "wasm-q8-b32" },
      { backend: "wasm", dtype: "fp32", batchSize: 16, topK: 10, refreshMode: "end", label: "wasm-fp32-b16" },
      { backend: "webgpu", dtype: "fp32", batchSize: 16, topK: 10, refreshMode: "end", label: "webgpu-fp32-b16" },
      { backend: "webgpu", dtype: "fp16", batchSize: 16, topK: 10, refreshMode: "per-batch", label: "webgpu-fp16-perbatch-b16" },
    ])
);

for (const config of configs) {
  const label = config.label;
  console.log(`\n=== ${label} ===`);
  const browser = await chromium.launch({
    channel: "chrome",
    headless: true,
    args: ["--enable-unsafe-webgpu", "--disable-background-timer-throttling"],
  });
  const context = await browser.newContext({ viewport: { width: 1500, height: 1100 } });
  const page = await context.newPage();
  page.setDefaultTimeout(900000);
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text().slice(0, 400));
  });
  page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));
  try {
    const url = `${BASE}/bench?backend=${config.backend}&dtype=${config.dtype}&batch=${config.batchSize}&label=${label}`;
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForFunction(() => window.__stepFunBenchReady === true, { timeout: 60000 });
    const started = Date.now();
    const result = await page.evaluate((cfg) => window.__runStepFunBench(cfg), config);
    const logs = await page.evaluate(() => window.__stepFunBenchLogs ?? []);
    const out = {
      ...result,
      wallClockMs: Date.now() - started,
      consoleErrors: consoleErrors.slice(0, 30),
      logs,
    };
    fs.writeFileSync(path.join(EVIDENCE, `bench-${label}.json`), JSON.stringify(out, null, 2));
    await page.screenshot({ path: path.join(EVIDENCE, `bench-${label}.png`), fullPage: true });
    console.log(
      JSON.stringify(
        {
          label,
          backend: result.backend,
          dtype: result.dtype,
          modelLoadMs: Math.round(result.modelLoadMs),
          warmLoadMs: result.warmLoadMs ? Math.round(result.warmLoadMs) : null,
          docEmbedMs: Math.round(result.docEmbedMs),
          docsPerSec: Number(result.docsPerSec.toFixed(2)),
          writeMs: Math.round(result.writeMs),
          refreshMs: Math.round(result.refreshMs),
          queryEmbedMsMedian: Number(result.queryEmbedMsMedian.toFixed(2)),
          retrievalMsMedian: Number(result.retrievalMsMedian.toFixed(2)),
          e2eMsMedian: Number(result.e2eMsMedian.toFixed(2)),
          warmE2e: Number(result.warmQueryE2eMsMedian.toFixed(2)),
          recall5: result.hybrid.recall5,
          recall10: result.hybrid.recall10,
          ndcg10: result.hybrid.ndcg10,
          vectorRecall5: result.vector.recall5,
          vectorNdcg10: result.vector.ndcg10,
          countAfterReimport: result.countAfterReimport,
          errors: consoleErrors.length,
        },
        null,
        2
      )
    );
  } catch (error) {
    console.error(`FAILED ${label}`, error);
    fs.writeFileSync(
      path.join(EVIDENCE, `bench-${label}-error.json`),
      JSON.stringify({ label, error: String(error), stack: error?.stack, consoleErrors }, null, 2)
    );
    await page.screenshot({ path: path.join(EVIDENCE, `bench-${label}-error.png`), fullPage: true }).catch(() => {});
  } finally {
    await context.close();
    await browser.close();
  }
}

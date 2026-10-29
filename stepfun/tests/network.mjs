import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const APP = process.env.APP_URL || "http://127.0.0.1:8788/";
const EVIDENCE = process.env.EVIDENCE || path.resolve("evidence");
fs.mkdirSync(EVIDENCE, { recursive: true });

const browser = await chromium.launch({
  channel: "chrome",
  headless: true,
  args: ["--enable-unsafe-webgpu", "--disable-background-timer-throttling"],
});
const context = await browser.newContext({ viewport: { width: 1500, height: 1100 } });
const page = await context.newPage();
const external = [];
const modelRequests = [];
const ortRequests = [];
page.on("request", (request) => {
  try {
    const url = new URL(request.url());
    if (url.protocol === "blob:" || url.protocol === "data:") return;
    if (!["127.0.0.1", "localhost"].includes(url.hostname)) {
      external.push(request.url());
    } else if (url.pathname.startsWith("/models/")) {
      modelRequests.push(url.pathname);
    } else if (url.pathname.startsWith("/ort/")) {
      ortRequests.push(url.pathname);
    }
  } catch {
    // ignore
  }
});
const report = { generatedAt: new Date().toISOString(), external, modelRequests, ortRequests, steps: [] };

async function waitModel() {
  await page.waitForFunction(
    () => {
      const badge = document.querySelector(".badge.backend");
      return badge && !badge.classList.contains("pulse") && /WebGPU|WASM/.test(badge.textContent || "");
    },
    { timeout: 180000 }
  );
  return (await page.locator(".badge.backend").innerText()).replace(/\s+/g, " ");
}

try {
  await page.goto(APP + "?backend=auto", { waitUntil: "domcontentloaded" });
  const backend = await waitModel();
  report.steps.push({ step: "model-loaded-locally", ok: true, backend });

  await page.locator(".tabs button", { hasText: "导入语料" }).click();
  await page.locator(".paste-area").fill(
    "seekdb 使用 DBMS_HYBRID_SEARCH 在数据库内完成向量与全文的混合检索。\n\n浏览器端使用 WebGPU 或 WASM 在本地运行 embedding 模型，不调用云端推理。"
  );
  await page.locator(".import-actions .primary").click();
  await page.waitForFunction(
    () => {
      const pill = document.querySelector(".stage-pill");
      const notice = document.querySelector(".progress-card .notice");
      return pill && pill.classList.contains("done") && notice && notice.textContent.includes("已导入 2 篇");
    },
    { timeout: 120000 }
  );
  report.steps.push({ step: "local-paste-import", ok: true, docs: 2 });

  await page.locator(".tabs button", { hasText: "混合检索" }).click();
  await page.locator(".search-input").fill("数据库内的混合检索怎么实现？");
  await page.locator(".search-button").click();
  await page.waitForFunction(
    () => document.querySelector(".results-head") || document.querySelector(".empty-state:not(.search-empty)"),
    { timeout: 60000 }
  );
  await page.waitForTimeout(500);
  const results = await page.locator(".result-card").evaluateAll((cards) =>
    cards.map((card) => ({
      title: card.querySelector("h3")?.textContent ?? "",
      text: card.querySelector(".result-text")?.textContent ?? "",
    }))
  );
  const timing = (await page.locator(".timing-strip").innerText().catch(() => "")).replace(/\s+/g, " ");
  await page.screenshot({ path: path.join(EVIDENCE, "ui-23-network-local.png"), fullPage: true });
  report.steps.push({
    step: "local-query-and-search",
    ok: results.length > 0 && results.some((item) => /混合检索|DBMS_HYBRID_SEARCH/.test(item.text)),
    hits: results.length,
    timing,
  });
  report.steps.push({
    step: "no-external-network",
    ok: external.length === 0,
    external,
    localModelRequests: modelRequests.length,
    localOrtRequests: ortRequests.length,
  });
} catch (error) {
  report.error = error instanceof Error ? `${error.message}\n${error.stack}` : String(error);
  await page.screenshot({ path: path.join(EVIDENCE, "ui-23-network-error.png"), fullPage: true }).catch(() => {});
} finally {
  fs.writeFileSync(path.join(EVIDENCE, "network-check.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await context.close();
  await browser.close();
}

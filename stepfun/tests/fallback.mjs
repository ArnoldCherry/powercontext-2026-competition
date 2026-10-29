import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const APP = process.env.APP_URL || "http://127.0.0.1:8788/";
const EVIDENCE = process.env.EVIDENCE || path.resolve("evidence");
fs.mkdirSync(EVIDENCE, { recursive: true });
const report = { generatedAt: new Date().toISOString(), steps: [], events: [] };
const stamp = Date.now().toString(36);

async function waitModel(page, timeout = 180000) {
  await page.waitForFunction(
    () => {
      const badge = document.querySelector(".badge.backend");
      return badge && !badge.classList.contains("pulse") && /WebGPU|WASM/.test(badge.textContent || "");
    },
    { timeout }
  );
  return (await page.locator(".badge.backend").innerText()).replace(/\s+/g, " ");
}

try {
  // 1. WebGPU available, backend=auto must choose WebGPU.
  const browser1 = await chromium.launch({
    channel: "chrome",
    headless: true,
    args: ["--enable-unsafe-webgpu", "--disable-background-timer-throttling"],
  });
  const context1 = await browser1.newContext({ viewport: { width: 1500, height: 1100 } });
  const page1 = await context1.newPage();
  await page1.goto(APP + "?backend=auto", { waitUntil: "domcontentloaded" });
  const webgpuBadge = await waitModel(page1);
  await page1.screenshot({ path: path.join(EVIDENCE, "ui-20-auto-webgpu.png"), fullPage: true });
  report.steps.push({ step: "auto-selects-webgpu", ok: /WebGPU/.test(webgpuBadge), badge: webgpuBadge });
  await browser1.close();

  // 2. WebGPU disabled, backend=auto must fall back to WASM and show a notice.
  const browser2 = await chromium.launch({
    channel: "chrome",
    headless: true,
    args: [
      "--disable-gpu",
      "--disable-software-rasterizer",
      "--disable-features=WebGPU",
      "--disable-background-timer-throttling",
    ],
  });
  const context2 = await browser2.newContext({ viewport: { width: 1500, height: 1100 } });
  const page2 = await context2.newPage();
  page2.on("pageerror", (err) => report.events.push(`pageerror: ${err.message}`));
  await page2.goto(APP + "?backend=auto", { waitUntil: "domcontentloaded" });
  const wasmBadge = await waitModel(page2);
  const notice = await page2.locator(".model-panel .notice.warn").innerText().catch(() => "");
  await page2.screenshot({ path: path.join(EVIDENCE, "ui-21-auto-wasm.png"), fullPage: true });
  report.steps.push({
    step: "auto-falls-back-wasm",
    ok: /WASM/.test(wasmBadge) && /自动降级/.test(notice),
    badge: wasmBadge,
    notice: notice.replace(/\s+/g, " "),
  });

  // 3. Core flow under WASM: create KB, import demo corpus, hybrid search.
  const kbName = `WASM 降级验证 ${stamp}`;
  await page2.locator(".kb-create input").fill(kbName);
  await page2.locator(".kb-create button").click();
  await page2.waitForFunction(
    (name) => document.querySelector(".kb-item.active .kb-name")?.textContent === name,
    kbName,
    { timeout: 30000 }
  );
  await page2.locator(".tabs button", { hasText: "导入语料" }).click();
  await page2.locator(".demo-row .link-button", { hasText: "46 篇" }).click();
  await page2.waitForFunction(
    () => {
      const pill = document.querySelector(".stage-pill");
      const notice = document.querySelector(".progress-card .notice");
      return pill && pill.classList.contains("done") && notice && notice.textContent.includes("已导入 46 篇");
    },
    { timeout: 180000 }
  );
  await page2.locator(".tabs button", { hasText: "混合检索" }).click();
  await page2.locator(".search-input").fill("seekdb 的混合检索是怎么实现的？");
  await page2.locator(".search-button").click();
  await page2.waitForFunction(
    () => {
      const head = document.querySelector(".results-head");
      return head || document.querySelector(".empty-state:not(.search-empty)");
    },
    { timeout: 60000 }
  );
  await page2.waitForTimeout(400);
  const results = await page2.locator(".result-card").evaluateAll((cards) =>
    cards.map((card) => ({
      title: card.querySelector("h3")?.textContent ?? "",
      text: card.querySelector(".result-text")?.textContent ?? "",
      meta: card.querySelector(".result-meta")?.textContent ?? "",
    }))
  );
  const timing = await page2.locator(".timing-strip").innerText().catch(() => "");
  await page2.screenshot({ path: path.join(EVIDENCE, "ui-22-auto-wasm-search.png"), fullPage: true });
  report.steps.push({
    step: "wasm-import-and-hybrid-search",
    ok: results.length > 0 && results.some((item) => /混合检索|DBMS_HYBRID_SEARCH/.test(item.text)),
    hits: results.length,
    top: results.slice(0, 2),
    timing: timing.replace(/\s+/g, " "),
  });
  await browser2.close();
} catch (error) {
  report.error = error instanceof Error ? `${error.message}\n${error.stack}` : String(error);
} finally {
  fs.writeFileSync(path.join(EVIDENCE, "fallback-test.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}

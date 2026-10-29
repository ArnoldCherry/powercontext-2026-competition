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
const events = [];
page.on("console", (msg) => {
  if (msg.type() === "error") events.push(`console.error: ${msg.text().slice(0, 300)}`);
});
page.on("pageerror", (err) => events.push(`pageerror: ${err.message}`));
page.on("requestfailed", (req) => events.push(`requestfailed: ${req.url().slice(0, 160)} ${req.failure()?.errorText}`));

async function shot(name) {
  await page.screenshot({ path: path.join(EVIDENCE, name), fullPage: true });
  console.log("screenshot", name);
}

async function waitModel(timeout = 180000) {
  await page.waitForFunction(
    () => {
      const badge = document.querySelector(".badge.backend");
      return badge && !badge.classList.contains("pulse") && /WebGPU|WASM/.test(badge.textContent || "");
    },
    { timeout }
  );
  return (await page.locator(".badge.backend").innerText()).replace(/\s+/g, " ");
}

async function createKb(name) {
  await page.locator(".kb-create input").fill(name);
  await page.locator(".kb-create button").click();
  await page.waitForFunction(
    (kbName) => document.querySelector(".kb-item.active .kb-name")?.textContent === kbName,
    name,
    { timeout: 30000 }
  );
  await page.waitForTimeout(600);
}

async function waitImportDone(expectedText, timeout = 180000) {
  // First wait (briefly) for a non-done state so we don't accept a stale "done" pill.
  await page
    .waitForFunction(
      () => {
        const pill = document.querySelector(".stage-pill");
        return pill && !pill.classList.contains("done");
      },
      { timeout: 4000 }
    )
    .catch(() => {});
  await page.waitForFunction(
    (expected) => {
      const pill = document.querySelector(".stage-pill");
      const notice = document.querySelector(".progress-card .notice");
      return (
        pill &&
        pill.classList.contains("done") &&
        notice &&
        (!expected || notice.textContent.includes(expected))
      );
    },
    expectedText,
    { timeout }
  );
  return (await page.locator(".progress-card .notice").last().innerText()).replace(/\s+/g, " ");
}

async function runSearch(query) {
  await page.locator(".tabs button", { hasText: "混合检索" }).click();
  await page.locator(".search-input").fill(query);
  await page.locator(".search-button").click();
  await page.waitForFunction(
    () => {
      const head = document.querySelector(".results-head");
      return head || document.querySelector(".empty-state:not(.search-empty)");
    },
    { timeout: 60000 }
  );
  await page.waitForTimeout(400);
  const results = await page.locator(".result-card").evaluateAll((cards) =>
    cards.map((card) => ({
      title: card.querySelector("h3")?.textContent ?? "",
      text: card.querySelector(".result-text")?.textContent ?? "",
      score: card.querySelector(".score")?.textContent ?? "",
      meta: card.querySelector(".result-meta")?.textContent ?? "",
    }))
  );
  const head = await page.locator(".results-head").innerText().catch(() => "");
  return { results, head };
}

const stamp = Date.now().toString(36);
const mainName = `E2E 主库 ${stamp}`;
const isoName = `E2E 隔离库 ${stamp}`;
const report = { backend: null, steps: [], events };

try {
  await page.goto(APP + "?backend=webgpu&dtype=fp16&batch=16", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".app");
  await shot("ui-10-initial.png");
  report.backend = await waitModel();
  console.log("backend", report.backend);
  await shot("ui-11-model-ready.png");

  await createKb(mainName);
  report.steps.push({ step: "create-kb-main", ok: true });

  await page.locator(".tabs button", { hasText: "导入语料" }).click();
  const demoButton = page.locator(".demo-row .link-button", { hasText: "46 篇" });
  await demoButton.click();
  const importMessage = await waitImportDone("已导入 46 篇");
  console.log("import:", importMessage);
  report.steps.push({ step: "import-demo", ok: /已导入 46 篇/.test(importMessage), message: importMessage });
  await shot("ui-12-import-done.png");

  const search1 = await runSearch("seekdb 的混合检索是怎么实现的？");
  console.log("search1", search1.head, search1.results.slice(0, 3));
  report.steps.push({
    step: "search-hybrid",
    ok: search1.results.length > 0 && search1.results.some((r) => /混合检索|DBMS_HYBRID_SEARCH/.test(r.text)),
    head: search1.head,
    top: search1.results.slice(0, 3),
  });
  await shot("ui-13-hybrid-search.png");
  const firstTiming = (await page.locator(".timing-strip").innerText()).replace(/\s+/g, " ");
  const cachedSearch = await runSearch("seekdb 的混合检索是怎么实现的？");
  const cachedTiming = (await page.locator(".timing-strip").innerText()).replace(/\s+/g, " ");
  report.steps.push({
    step: "query-cache",
    ok: /缓存命中/.test(cachedTiming) && cachedSearch.results.length > 0,
    firstTiming,
    cachedTiming,
  });
  await shot("ui-13b-query-cache.png");

  const search2 = await runSearch("番茄炒蛋怎么做？");
  console.log("search2", search2.results.slice(0, 2));
  report.steps.push({
    step: "search-misc",
    ok: search2.results.some((r) => /番茄炒蛋/.test(r.text)),
    top: search2.results.slice(0, 2),
  });

  const search3 = await runSearch("如何在用户设备上完成文本向量化而不上云？");
  report.steps.push({
    step: "search-semantic",
    ok: search3.results.length > 0 && search3.results.some((r) => /Transformers.js|浏览器|WebGPU|本地/.test(r.text)),
    top: search3.results.slice(0, 3),
  });
  await shot("ui-14-semantic-search.png");

  // incremental re-import: same stable ids, count must stay 46
  await page.locator(".tabs button", { hasText: "导入语料" }).click();
  await demoButton.click();
  const importMessage2 = await waitImportDone("已导入 46 篇");
  await page.waitForTimeout(1000);
  const countAfterReimport = await page.locator(".stat-card").first().innerText();
  console.log("reimport:", importMessage2, countAfterReimport.replace(/\s+/g, " "));
  report.steps.push({
    step: "incremental-reimport",
    ok: /已导入 46 篇/.test(importMessage2) && /46/.test(countAfterReimport),
    message: importMessage2,
    count: countAfterReimport.replace(/\s+/g, " "),
  });

  // docs tab
  await page.locator(".tabs button", { hasText: "文档" }).click();
  await page.waitForSelector(".doc-row:not(.doc-head)");
  const docRows = await page.locator(".doc-row:not(.doc-head)").count();
  report.steps.push({ step: "docs-tab", ok: docRows === 46, rows: docRows });
  await shot("ui-15-docs.png");

  // create isolated KB, import web corpus, verify isolation
  await page.locator(".tabs button", { hasText: "导入语料" }).click();
  await createKb(isoName);
  const webButton = page.locator(".demo-row .link-button", { hasText: "Web 隔离语料" });
  await webButton.click();
  const importMessage3 = await waitImportDone("已导入 8 篇");
  console.log("import web:", importMessage3);
  report.steps.push({ step: "import-isolation-kb", ok: /已导入 8 篇/.test(importMessage3), message: importMessage3 });

  const isoOld = await runSearch("seekdb 的混合检索是怎么实现的？");
  console.log("iso old", isoOld.results.length, isoOld.head);
  const mainCollection = search1.head.split("\n")[0].split("·").pop()?.trim();
  const isoCollection = isoOld.head.split("\n")[0].split("·").pop()?.trim();
  const leaked = isoOld.results.filter((result) => /seekdb|DBMS_HYBRID_SEARCH|番茄/.test(result.text));
  report.steps.push({
    step: "kb-isolation-old-query",
    ok:
      isoOld.results.length > 0 &&
      leaked.length === 0 &&
      isoCollection !== mainCollection &&
      isoOld.results.every((result) => /kb [0-9a-f]{8}/.test(result.meta)),
    hits: isoOld.results.length,
    mainCollection,
    isoCollection,
    leaked: leaked.length,
    head: isoOld.head,
  });
  const isoNew = await runSearch("蓝莓种植要注意什么？");
  console.log("iso new", isoNew.results.slice(0, 2));
  report.steps.push({
    step: "kb-isolation-new-query",
    ok: isoNew.results.length > 0 && isoNew.results.some((r) => /蓝莓/.test(r.text)),
    top: isoNew.results.slice(0, 2),
  });
  await shot("ui-16-kb-isolation.png");

  // switch back to main KB, confirm old isolation results are cleared and main results return
  await page.locator(".kb-item", { hasText: mainName }).click();
  await page.waitForTimeout(800);
  const afterSwitchResults = await page.locator(".result-card").count();
  const mainSearch = await runSearch("seekdb 的混合检索是怎么实现的？");
  report.steps.push({
    step: "switch-back-main",
    ok: afterSwitchResults === 0 && mainSearch.results.some((r) => /混合检索|DBMS_HYBRID_SEARCH/.test(r.text)),
    cleared: afterSwitchResults === 0,
    top: mainSearch.results.slice(0, 2),
  });
  await shot("ui-17-switch-back.png");
} catch (error) {
  report.error = error instanceof Error ? `${error.message}\n${error.stack}` : String(error);
  await shot("ui-99-error.png");
} finally {
  report.generatedAt = new Date().toISOString();
  fs.writeFileSync(path.join(EVIDENCE, "e2e-ui-webgpu.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await context.close();
  await browser.close();
}

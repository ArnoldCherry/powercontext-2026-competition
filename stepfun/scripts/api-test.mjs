#!/usr/bin/env node
/**
 * End-to-end API test against a running StepFun app.
 * Verifies: incremental upsert, refresh, all three retrieval modes,
 * collection isolation after KB switch, stale-request guard and dimension validation.
 *
 * Usage: BASE_URL=http://127.0.0.1:8788 node scripts/api-test.mjs
 */

import fs from "node:fs";
import path from "node:path";

const BASE = process.env.BASE_URL || "http://127.0.0.1:8788";
let cookie = "";

async function request(route, { method = "GET", body } = {}) {
  const response = await fetch(BASE + route, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(cookie ? { Cookie: cookie } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const setCookies = response.headers.getSetCookie?.() ?? [];
  if (setCookies.length) cookie = setCookies.map((value) => value.split(";")[0]).join("; ");
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  return { status: response.status, data };
}

function vector(seed, dim = 512) {
  const out = [];
  for (let i = 0; i < dim; i++) out.push(Math.sin(seed * 1.7 + i * 0.37));
  const norm = Math.sqrt(out.reduce((sum, value) => sum + value * value, 0));
  return out.map((value) => value / norm);
}

const results = [];
function check(name, ok, detail = {}) {
  results.push({ name, ok, ...detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}`, JSON.stringify(detail).slice(0, 300));
  if (!ok) process.exitCode = 1;
}

const stamp = Date.now().toString(36);
const kbA = `api-A-${stamp}`;
const kbB = `api-B-${stamp}`;
let aId = null;
let bId = null;

try {
  const state = await request("/api/state");
  check("session + state", state.status === 200 && !!state.data.sessionId, { active: state.data?.activeKbId });

  const createdA = await request("/api/kbs", { method: "POST", body: { name: kbA } });
  aId = createdA.data?.kb?.id;
  check("create KB A", createdA.status === 200 && !!aId, { collection: createdA.data?.kb?.collection });

  const docsA = [
    { id: "a-1", document: "seekdb 使用 DBMS_HYBRID_SEARCH 在数据库内完成向量与全文混合检索。", metadata: { title: "混合检索" } },
    { id: "a-2", document: "浏览器端 embedding 在 WebGPU 或 WASM 上本地运行。", metadata: { title: "端侧推理" } },
    { id: "a-3", document: "番茄炒蛋需要先炒鸡蛋再炒番茄。", metadata: { title: "番茄炒蛋" } },
  ];
  const imported = await request(`/api/kbs/${aId}/docs`, {
    method: "POST",
    body: {
      expectedKbId: aId,
      docs: docsA,
      embeddings: docsA.map((_, index) => vector(index + 1)),
    },
  });
  check("import KB A", imported.status === 200 && imported.data.count === 3, { count: imported.data?.count });

  const refresh = await request(`/api/kbs/${aId}/refresh`, { method: "POST", body: { expectedKbId: aId } });
  check("refresh index", refresh.status === 200 && refresh.data.refreshMs > 0, { refreshMs: refresh.data?.refreshMs });

  const hybridA = await request("/api/search", {
    method: "POST",
    body: {
      query: "数据库内的混合检索怎么实现？",
      embedding: vector(1),
      mode: "hybrid",
      topK: 5,
      expectedKbId: aId,
    },
  });
  const hybridAIds = hybridA.data?.results?.map((item) => item.id) ?? [];
  check("hybrid search returns A collection", hybridA.status === 200 && hybridAIds.length > 0, {
    ids: hybridAIds,
    collection: hybridA.data?.collection,
    note: hybridA.data?.rawBranchNote,
  });
  check(
    "hybrid result provenance = A",
    hybridA.data?.results?.every((item) => item.metadata?.kbId === aId && item.metadata?.collection === hybridA.data.collection),
    { provenance: hybridA.data?.results?.map((item) => item.metadata?.kbId) }
  );

  const keywordA = await request("/api/search", {
    method: "POST",
    body: { query: "番茄炒蛋", embedding: vector(2), mode: "keyword", topK: 5, expectedKbId: aId },
  });
  check("keyword search", keywordA.status === 200 && keywordA.data.results.some((item) => item.id === "a-3"), {
    ids: keywordA.data?.results?.map((item) => item.id),
  });

  const vectorA = await request("/api/search", {
    method: "POST",
    body: { query: "任意", embedding: vector(1), mode: "vector", topK: 5, expectedKbId: aId },
  });
  check("vector search", vectorA.status === 200 && vectorA.data.results.length === 3, {
    ids: vectorA.data?.results?.map((item) => item.id),
  });

  const createdB = await request("/api/kbs", { method: "POST", body: { name: kbB } });
  bId = createdB.data?.kb?.id;
  check("create + activate KB B", createdB.status === 200 && !!bId && createdB.data.activeKbId === bId, {
    bId,
    collection: createdB.data?.kb?.collection,
  });

  const docsB = [
    { id: "b-1", document: "蓝莓适合酸性土壤，需要充足光照。", metadata: { title: "蓝莓种植" } },
    { id: "b-2", document: "芒果成熟后会散发果香。", metadata: { title: "芒果" } },
  ];
  const importedB = await request(`/api/kbs/${bId}/docs`, {
    method: "POST",
    body: { expectedKbId: bId, docs: docsB, embeddings: docsB.map((_, index) => vector(index + 4)) },
  });
  await request(`/api/kbs/${bId}/refresh`, { method: "POST", body: { expectedKbId: bId } });
  check("import KB B", importedB.status === 200 && importedB.data.count === 2, { count: importedB.data?.count });

  const crossSearch = await request("/api/search", {
    method: "POST",
    body: {
      query: "数据库内的混合检索怎么实现？",
      embedding: vector(1),
      mode: "hybrid",
      topK: 5,
      expectedKbId: bId,
    },
  });
  const crossIds = crossSearch.data?.results?.map((item) => item.id) ?? [];
  const leaked = crossIds.filter((id) => ["a-1", "a-2", "a-3"].includes(id));
  check("KB isolation: no A docs in B results", crossSearch.status === 200 && leaked.length === 0, {
    ids: crossIds,
    collection: crossSearch.data?.collection,
    provenance: crossSearch.data?.results?.map((item) => item.metadata?.kbId),
  });
  check(
    "KB isolation: all results belong to B",
    (crossSearch.data?.results ?? []).every((item) => item.metadata?.kbId === bId),
    { provenance: crossSearch.data?.results?.map((item) => item.metadata?.kbId) }
  );

  const blueberry = await request("/api/search", {
    method: "POST",
    body: { query: "蓝莓", embedding: vector(4), mode: "hybrid", topK: 5, expectedKbId: bId },
  });
  check("B query hits B doc", blueberry.data?.results?.some((item) => item.id === "b-1"), {
    ids: blueberry.data?.results?.map((item) => item.id),
  });

  const stale = await request("/api/search", {
    method: "POST",
    body: { query: "混合检索", embedding: vector(1), mode: "hybrid", topK: 5, expectedKbId: aId },
  });
  check("stale expectedKbId rejected with 409", stale.status === 409 && stale.data?.error === "KB_CHANGED", {
    status: stale.status,
    error: stale.data?.error,
    activeKbId: stale.data?.activeKbId,
  });

  const staleImport = await request(`/api/kbs/${aId}/docs`, {
    method: "POST",
    body: { expectedKbId: aId, docs: [docsA[0]], embeddings: [vector(1)] },
  });
  check("stale import rejected with 409", staleImport.status === 409, { status: staleImport.status });

  await request(`/api/kbs/${aId}/activate`, { method: "POST", body: {} });
  const incremental = await request(`/api/kbs/${aId}/docs`, {
    method: "POST",
    body: { expectedKbId: aId, docs: [docsA[0]], embeddings: [vector(1)] },
  });
  check("incremental upsert keeps count", incremental.status === 200 && incremental.data.count === 3, {
    count: incremental.data?.count,
  });

  const badDim = await request(`/api/kbs/${aId}/docs`, {
    method: "POST",
    body: { expectedKbId: aId, docs: [docsA[0]], embeddings: [vector(1, 511)] },
  });
  check("dimension mismatch rejected", badDim.status === 400, { status: badDim.status, error: badDim.data?.error });

  const docsAfter = await request(`/api/kbs/${aId}/docs?limit=10`);
  check("get docs", docsAfter.status === 200 && docsAfter.data.docs.length === 3, { total: docsAfter.data?.total });

  const deleteA = await request(`/api/kbs/${aId}`, { method: "DELETE" });
  aId = null;
  check("delete KB A", deleteA.status === 200 && deleteA.data.deleted === true, { activeKbId: deleteA.data?.activeKbId });
  const deleteB = await request(`/api/kbs/${bId}`, { method: "DELETE" });
  bId = null;
  check("delete KB B", deleteB.status === 200 && deleteB.data.deleted === true, {});
} catch (error) {
  check("unexpected error", false, { error: error instanceof Error ? error.message : String(error) });
} finally {
  if (aId) await request(`/api/kbs/${aId}`, { method: "DELETE" }).catch(() => {});
  if (bId) await request(`/api/kbs/${bId}`, { method: "DELETE" }).catch(() => {});
  const summary = {
    base: BASE,
    generatedAt: new Date().toISOString(),
    passed: results.filter((item) => item.ok).length,
    failed: results.filter((item) => !item.ok).length,
    results,
  };
  const out = path.resolve("evidence/api-test.json");
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, JSON.stringify(summary, null, 2));
  console.log(`\n${summary.passed} passed, ${summary.failed} failed -> ${out}`);
}

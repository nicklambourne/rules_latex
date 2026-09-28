// Unit tests for the PDF byte-range assembly planner backing
// ChunkedTransport. Run: node --test, or bazel test //tests/js:test_chunks.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { planRangeSegments } from "../../latex/private/serve_web_chunks.js";

const R = (start, end, hash) => ({ start, end, hash });

test("a range fully inside one chunk -> a single chunk slice", () => {
  assert.deepEqual(planRangeSegments([R(0, 100, "a")], 10, 50), [
    { kind: "chunk", hash: "a", sliceStart: 10, sliceEnd: 50 },
  ]);
});

test("a gap before the first chunk -> skeleton then chunk", () => {
  assert.deepEqual(planRangeSegments([R(20, 40, "a")], 0, 40), [
    { kind: "skeleton", begin: 0, end: 20 },
    { kind: "chunk", hash: "a", sliceStart: 0, sliceEnd: 20 },
  ]);
});

test("spanning two chunks with a skeleton gap between them", () => {
  assert.deepEqual(planRangeSegments([R(0, 10, "a"), R(20, 30, "b")], 0, 30), [
    { kind: "chunk", hash: "a", sliceStart: 0, sliceEnd: 10 },
    { kind: "skeleton", begin: 10, end: 20 },
    { kind: "chunk", hash: "b", sliceStart: 0, sliceEnd: 10 },
  ]);
});

test("past the last chunk -> a trailing skeleton segment", () => {
  assert.deepEqual(planRangeSegments([R(0, 10, "a")], 0, 25), [
    { kind: "chunk", hash: "a", sliceStart: 0, sliceEnd: 10 },
    { kind: "skeleton", begin: 10, end: 25 },
  ]);
});

test("no chunks at all -> one skeleton segment", () => {
  assert.deepEqual(planRangeSegments([], 5, 15), [
    { kind: "skeleton", begin: 5, end: 15 },
  ]);
});

test("a range beginning partway into a chunk", () => {
  assert.deepEqual(planRangeSegments([R(0, 100, "a")], 60, 80), [
    { kind: "chunk", hash: "a", sliceStart: 60, sliceEnd: 80 },
  ]);
});

test("chunks ending before begin are skipped", () => {
  assert.deepEqual(planRangeSegments([R(0, 10, "a"), R(10, 20, "b")], 12, 18), [
    { kind: "chunk", hash: "b", sliceStart: 2, sliceEnd: 8 },
  ]);
});

test("WebSocket manifest falls back after an evicted chunk is skipped", () => {
  const client = readFileSync(
    new URL("../../latex/private/serve_web.js", import.meta.url), "utf8"
  );
  const code = client.split("let _wsPendingManifest = null;")[1]
    .split("async function _flushWsRender()")[0];
  const cache = new Map();
  for (let i = 1; i <= 1000; i++) cache.set(`chunk-${i}`, new Uint8Array());
  let fallback;
  let renders = 0;
  const context = {
    chunkCache: cache,
    clearTimeout: () => {},
    setTimeout: (callback) => { fallback = callback; return 1; },
    _flushWsRender: () => { renders++; },
  };
  runInNewContext(`let _wsPendingManifest = null;${code}`, context);
  const ranges = Array.from({ length: 1001 }, (_, i) => ({ hash: `chunk-${i}` }));
  context._handleWsMessage({ data: JSON.stringify({ type: "manifest", ranges }) });
  assert.equal(renders, 0);
  assert.equal(typeof fallback, "function");
  fallback();
  assert.equal(renders, 1);
});

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

// Exercise the actual DOM-independent transport code, using the same source
// evaluation approach as the WebSocket test above. Chrome covers real PDF.js.
function loadTransport({ fetch, fetchChunk } = {}) {
  const client = readFileSync(
    new URL("../../latex/private/serve_web.js", import.meta.url), "utf8"
  );
  const code = client.slice(client.indexOf("async function fetchPdfRange("),
    client.indexOf("function failPdfGeneration("));
  const errors = [];
  class PDFDataRangeTransport {
    deliveries = [];
    transportReady() {}
    abort() {}
    onDataRange(begin, bytes) { this.deliveries.push({ begin, bytes }); }
  }
  const Transport = runInNewContext(`${code}; ChunkedTransport`, {
    pdfjsLib: { PDFDataRangeTransport }, planRangeSegments,
    fetch, fetchChunk, AbortController, Uint8Array, queueMicrotask,
    console: { error: (...args) => errors.push(args) },
  });
  return { Transport, errors };
}

test("aborted skeleton fetch cannot deliver a late response body", async () => {
  const body = deferred();
  const started = deferred();
  let signal;
  const { Transport, errors } = loadTransport({
    fetch: async (_url, options) => {
      signal = options.signal;
      return { status: 206, arrayBuffer: () => {
        started.resolve();
        return body.promise; // Deliberately ignores cancellation.
      } };
    },
  });
  const transport = new Transport({ pdfSize: 4, pdfHash: "old", ranges: [] });
  let failures = 0;
  transport.onFailure = () => failures++;
  const pending = transport.requestDataRange(0, 4);
  await started.promise;
  transport.abort();
  transport.abort(); // PDF.js can tear down a task more than once.
  body.resolve(new Uint8Array([1, 2, 3, 4]).buffer);
  await pending;
  assert.deepEqual(transport.deliveries, []);
  assert.equal(signal.aborted, true, "owned HTTP ranges must be cancelled too");
  assert.deepEqual(errors, []);
  assert.equal(failures, 0);
});

test("cancelling an old consumer preserves shared chunks for the new transport", async () => {
  const chunk = deferred();
  const calls = [];
  const { Transport, errors } = loadTransport({
    fetchChunk: (hash) => {
      calls.push(hash);
      return hash === "shared" ? chunk.promise : Promise.resolve(new Uint8Array([3, 4]));
    },
  });
  const manifest = { pdfSize: 4, ranges: [R(0, 2, "shared"), R(2, 4, "next")] };
  const old = new Transport(manifest);
  const current = new Transport(manifest);
  const obsolete = old.requestDataRange(0, 4);
  const live = current.requestDataRange(0, 4);
  old.abort();
  chunk.resolve(new Uint8Array([1, 2]));
  await Promise.all([obsolete, live]);
  assert.deepEqual(old.deliveries, []);
  assert.deepEqual(current.deliveries, [{ begin: 0, bytes: new Uint8Array([1, 2, 3, 4]) }]);
  assert.deepEqual(calls, ["shared", "shared", "next"], "obsolete assembly must stop fetching");
  assert.deepEqual(errors, []);
});

test("late failures and new range requests are inert after abort", async () => {
  const chunk = deferred();
  let calls = 0;
  const { Transport, errors } = loadTransport({
    fetchChunk: () => { calls++; return chunk.promise; },
  });
  const transport = new Transport({ pdfSize: 4, ranges: [R(0, 4, "a")] });
  let failures = 0;
  transport.onFailure = () => failures++;
  const pending = transport.requestDataRange(0, 4);
  transport.abort();
  chunk.reject(new Error("late network failure"));
  await pending;
  await transport.requestDataRange(0, 4);
  await transport.requestDataRange(4, 4);
  assert.equal(calls, 1);
  assert.deepEqual(transport.deliveries, []);
  assert.deepEqual(errors, []);
  assert.equal(failures, 0);
});

test("active transport errors still reach recovery, including 410 and short ranges", async () => {
  for (const response of [
    { status: 410 },
    { status: 206, arrayBuffer: async () => new Uint8Array([1]).buffer },
    Object.assign(new Error("unrelated abort"), { name: "AbortError" }),
  ]) {
    const { Transport, errors } = loadTransport({ fetch: async () => {
      if (response instanceof Error) throw response;
      return response;
    } });
    const transport = new Transport({ pdfSize: 4, pdfHash: "current", ranges: [] });
    const failures = [];
    transport.onFailure = (error) => failures.push(error);
    await transport.requestDataRange(0, 4);
    assert.equal(failures.length, 1);
    assert.equal(errors.length, 1);
    assert.equal(!!failures[0].generationExpired, response.status === 410);
    assert.deepEqual(transport.deliveries, []);
  }
});

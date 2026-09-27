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

// Unit tests for the PDF byte-range assembly planner backing
// ChunkedTransport. Run: node --test, or bazel test //tests/js:test_chunks.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { BoundedChunkCache, createChunkFetcher, planRangeSegments } from "../../latex/private/serve_web_chunks.js";

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

test("indexed lookup preserves every byte across chunk and gap boundaries", () => {
  // Exhaustive small ranges exercise exact ends, empty requests, gaps,
  // first/last objects, and requests past the final object.
  const ranges = [R(3, 9, "a"), R(9, 12, "b"), R(17, 25, "c")];
  for (let begin = 0; begin <= 30; begin++) {
    for (let end = begin; end <= 30; end++) {
      const bytes = [];
      for (const segment of planRangeSegments(ranges, begin, end)) {
        const chunk = ranges.find(r => r.hash === segment.hash);
        const start = chunk ? chunk.start + segment.sliceStart : segment.begin;
        const stop = chunk ? chunk.start + segment.sliceEnd : segment.end;
        for (let offset = start; offset < stop; offset++) bytes.push(offset);
      }
      assert.deepEqual(bytes, Array.from({ length: end - begin }, (_, i) => begin + i));
    }
  }
});

test("lookup does not scan preceding objects in a large manifest", () => {
  let reads = 0;
  const ranges = Array.from({ length: 65536 }, (_, i) => ({
    start: i * 10,
    get end() { reads++; return (i + 1) * 10; },
    hash: String(i),
  }));
  assert.deepEqual(planRangeSegments(ranges, 655350, 655360), [
    { kind: "chunk", hash: "65535", sliceStart: 0, sliceEnd: 10 },
  ]);
  assert.ok(reads < 25, `expected logarithmic lookup, read ${reads} ends`);
});

test("chunk cache evicts by bytes and handles replacement", () => {
  const cache = new BoundedChunkCache(10, 8);
  cache.remember("a", new Uint8Array(4));
  cache.remember("b", new Uint8Array(4));
  cache.remember("a", new Uint8Array(6));
  assert.equal(cache.byteSize, 6);
  assert.deepEqual([...cache.keys()], ["a"]);
  cache.remember("large", new Uint8Array(9));
  assert.equal(cache.has("large"), false);
  assert.equal(cache.byteSize, 6);
});

test("overlapping consumers share one fetch and the retained bytes", async () => {
  let calls = 0;
  let finish;
  const bytes = new Uint8Array([1, 2]);
  const cache = new BoundedChunkCache(2, 8);
  const fetch = createChunkFetcher(cache, () => {
    calls++;
    return new Promise(resolve => { finish = resolve; });
  });
  const requests = Array.from({ length: 8 }, () => fetch("a"));
  assert.equal(calls, 1);
  finish(bytes);
  for (const result of await Promise.all(requests)) assert.equal(result, bytes);
  assert.equal(await fetch("a"), bytes);
  assert.equal(calls, 1);
  assert.equal(cache.byteSize, 2);
});

test("failed loads are shared, cleared, and retryable", async () => {
  for (const synchronous of [false, true]) {
    let calls = 0;
    const bytes = new Uint8Array([3]);
    const fetch = createChunkFetcher(new BoundedChunkCache(2, 8), () => {
      if (++calls === 1) {
        if (synchronous) throw new Error("unavailable");
        return Promise.reject(new Error("unavailable"));
      }
      return bytes;
    });
    const first = fetch("a");
    const second = fetch("a");
    await Promise.all([
      assert.rejects(first, /unavailable/), assert.rejects(second, /unavailable/),
    ]);
    assert.equal(await fetch("a"), bytes);
    assert.equal(calls, 2);
  }
});

test("different hashes load independently and oversize bytes are not retained", async () => {
  const calls = [];
  const cache = new BoundedChunkCache(1, 1);
  const fetch = createChunkFetcher(cache, async hash => {
    calls.push(hash);
    return new Uint8Array(2);
  });
  await Promise.all([fetch("a"), fetch("a"), fetch("b")]);
  assert.deepEqual(calls, ["a", "b"]);
  assert.equal(cache.size, 0);
  await fetch("a");
  assert.deepEqual(calls, ["a", "b", "a"]);
});

// Unit tests for the PDF byte-range assembly planner backing
// ChunkedTransport. Run: node --test, or bazel test //tests/js:test_chunks.

import { test } from "node:test";
import assert from "node:assert/strict";
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

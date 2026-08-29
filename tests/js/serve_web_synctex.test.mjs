// Unit tests for the pure SyncTeX coordinate math extracted from the
// live-preview client. Run with node's built-in test runner (no deps):
//   node --test tests/js/serve_web_synctex.test.mjs
// or via Bazel: bazel test //tests/js:test_synctex_math

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  clientPointToPdfPoint,
  pdfBoxToViewportRect,
} from "../../latex/private/serve_web_synctex.js";

// A stand-in for a PDF.js viewport. Real viewports flip the y-axis
// (PDF points up, device space down); this models that with a known
// page height so the expected numbers are easy to reason about.
function flipViewport(height) {
  return { convertToViewportPoint: (x, y) => [x, height - y] };
}

test("maps a PDF-point box to a normalised viewport rect", () => {
  const vp = flipViewport(100);
  // box origin (10,20), size 30x40 -> corners (10,20) and (40,60),
  // which flip to viewport (10,80) and (40,40).
  const r = pdfBoxToViewportRect(vp, 10, 20, 30, 40);
  assert.equal(r.left, 10); // min(10, 40)
  assert.equal(r.top, 40); // min(80, 40)
  assert.equal(r.width, 30); // |40 - 10|
  assert.equal(r.height, 40); // |80 - 40|
});

test("normalises axis flips so width/height stay non-negative", () => {
  const vp = { convertToViewportPoint: (x, y) => [-x, -y] };
  const r = pdfBoxToViewportRect(vp, 5, 5, 10, 10);
  assert.equal(r.left, -15); // min(-5, -15)
  assert.equal(r.top, -15);
  assert.equal(r.width, 10);
  assert.equal(r.height, 10);
});

test("a zero-size box yields a zero-size rect at the point", () => {
  const vp = flipViewport(50);
  const r = pdfBoxToViewportRect(vp, 7, 7, 0, 0);
  assert.equal(r.width, 0);
  assert.equal(r.height, 0);
  assert.equal(r.left, 7);
  assert.equal(r.top, 43);
});

test("maps CSS client coordinates without using canvas backing dimensions", () => {
  const viewport = {
    width: 200,
    height: 400,
    convertToPdfPoint: (x, y) => [x / 2, 400 - y],
  };
  const rect = { left: 10, top: 20, width: 100, height: 200 };
  // Midpoint in the CSS rect -> midpoint in the viewport -> PDF transform.
  assert.deepEqual(
    clientPointToPdfPoint(viewport, rect, 60, 120),
    [50, 200],
  );
});

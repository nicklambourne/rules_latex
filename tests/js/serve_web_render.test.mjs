// Unit tests for the lazy-paint orchestration (the render state machine
// and observer decision shipped in #62). Run: node --test, or
// bazel test //tests/js:test_render.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  paintPage,
  renderObserverAction,
} from "../../latex/private/serve_web_render.js";

const fakeWrap = () => ({ dataset: {}, _renderTask: null });
const resolvedTask = () => ({ promise: Promise.resolve(), cancel() {} });
const rejectedTask = (err) => ({ promise: Promise.reject(err), cancel() {} });

test("paintPage rasterizes once and marks the wrap rendered", async () => {
  const wrap = fakeWrap();
  let calls = 0;
  await paintPage(wrap, () => {
    calls++;
    return resolvedTask();
  });
  assert.equal(calls, 1);
  assert.equal(wrap.dataset.rendered, "1");
  assert.equal(wrap.dataset.rendering, "");
  assert.equal(wrap._renderTask, null);
});

test("paintPage is idempotent — an already-rendered wrap is skipped", async () => {
  const wrap = fakeWrap();
  wrap.dataset.rendered = "1";
  let calls = 0;
  await paintPage(wrap, () => {
    calls++;
    return resolvedTask();
  });
  assert.equal(calls, 0);
});

test("paintPage skips a wrap that is mid-paint", async () => {
  const wrap = fakeWrap();
  wrap.dataset.rendering = "1";
  let calls = 0;
  await paintPage(wrap, () => {
    calls++;
    return resolvedTask();
  });
  assert.equal(calls, 0);
});

test("a cancelled raster leaves the wrap unpainted so it repaints later", async () => {
  const wrap = fakeWrap();
  const err = new Error("cancelled");
  err.name = "RenderingCancelledException";
  await paintPage(wrap, () => rejectedTask(err));
  assert.notEqual(wrap.dataset.rendered, "1");
  assert.equal(wrap.dataset.rendering, "");
  assert.equal(wrap._renderTask, null);
});

test("a real render error is swallowed and the wrap stays unpainted", async () => {
  const wrap = fakeWrap();
  const orig = console.warn;
  console.warn = () => {}; // expected warning; keep test output clean
  try {
    await paintPage(wrap, () => rejectedTask(new Error("boom")));
  } finally {
    console.warn = orig;
  }
  assert.notEqual(wrap.dataset.rendered, "1");
  assert.equal(wrap.dataset.rendering, "");
});

test("renderObserverAction: a page coming into view -> paint", () => {
  const entry = { isIntersecting: true, target: { dataset: {} } };
  assert.equal(renderObserverAction(entry), "paint");
});

test("renderObserverAction: off-screen mid-paint -> cancel", () => {
  const target = { dataset: {}, _renderTask: { cancel() {} } };
  assert.equal(renderObserverAction({ isIntersecting: false, target }), "cancel");
});

test("renderObserverAction: off-screen but already painted -> skip", () => {
  const target = { dataset: { rendered: "1" }, _renderTask: { cancel() {} } };
  assert.equal(renderObserverAction({ isIntersecting: false, target }), "skip");
});

test("renderObserverAction: off-screen with nothing in flight -> skip", () => {
  const target = { dataset: {}, _renderTask: null };
  assert.equal(renderObserverAction({ isIntersecting: false, target }), "skip");
});

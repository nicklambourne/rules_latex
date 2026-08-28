// serve_web.js — browser-side live-preview client for latex_live.
//
// Extracted from serve_web.py.tpl; the server inlines this file (and
// serve_web.css) into the page at serve time, and serves them at
// /_assets/. Runtime config arrives via window.__SERVE_CONFIG__.
// Pure-logic helpers live in sibling modules and are unit-tested under
// tests/js/ with `node --test`.

import * as pdfjsLib from "/_pdfjs/pdf.mjs";
import {
  clientPointToPdfPoint,
  pdfBoxToViewportRect,
} from "./serve_web_synctex.js";
import {
  newRenderGeneration,
  paintPage as paintPageImpl,
  renderObserverAction,
  planPageReconciliation,
  newRenderStats,
  recordRenderTiming,
  recordLongTask,
} from "./serve_web_render.js";
import { planRangeSegments } from "./serve_web_chunks.js";
pdfjsLib.GlobalWorkerOptions.workerSrc = "/_pdfjs/pdf.worker.mjs";

const SYNCTEX_ENABLED = window.__SERVE_CONFIG__.synctexEnabled;
// Render-timing aggregate (option 0 — measure before committing to
// off-main-thread rendering). Inspect in the console via
// `__serveWebRenderStats`; set `__SERVE_DEBUG__ = true` for per-page logs.
const _renderStats = newRenderStats();
window.__serveWebRenderStats = _renderStats;
try {
  if (
    typeof PerformanceObserver !== "undefined" &&
    PerformanceObserver.supportedEntryTypes?.includes("longtask")
  ) {
    const observer = new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) {
        recordLongTask(_renderStats, entry.duration);
      }
    });
    observer.observe({ type: "longtask", buffered: true });
  }
} catch {
  // Long Task API support is optional; the rest of the stats still work.
}
const viewer = document.getElementById("viewer");
const statusEl = document.getElementById("status");
const syncResultEl = document.getElementById("sync-result");
const pageInput = document.getElementById("page-input");
const pageTotalEl = document.getElementById("page-total");
const pagePrevBtn = document.getElementById("page-prev");
const pageNextBtn = document.getElementById("page-next");
const zoomDisplayEl = document.getElementById("zoom-display");
const fitWidthBtn = document.getElementById("fit-width");
const fitPageBtn = document.getElementById("fit-page");
const fullscreenBtn = document.getElementById("fullscreen");
const downloadLink = document.getElementById("download-pdf");
let currentDoc = null;
let currentPageNum = 1;
let scale = 1.5;

// Document loads, live reloads, and zooms can overlap. Only the newest
// generation may commit a page tree; the currently displayed generation
// remains usable until that commit happens.
const _renderGenerations = newRenderGeneration();
let _displayGeneration = 0;
let _activeLoadingTask = null;

function _beginRenderGeneration() {
  const generation = _renderGenerations.begin();
  _renderStats.generations += 1;
  _renderStats.current = {
    generation,
    startedAt: performance.now(),
    pages: null,
    reusedPages: null,
    scale: null,
    dpr: window.devicePixelRatio || 1,
    committedMs: null,
    firstPaintMs: null,
    firstTextMs: null,
    textLayers: 0,
    textLayerMs: 0,
    searchIndexMs: null,
    paintQueueMax: 0,
    longTaskCount: 0,
    longTaskTotalMs: 0,
    longTaskMaxMs: 0,
    canvasBytes: 0,
    renderedCanvases: 0,
    releasedCanvases: 0,
  };
  if (_activeLoadingTask) {
    const staleTask = _activeLoadingTask;
    _activeLoadingTask = null;
    try {
      Promise.resolve(staleTask.destroy()).catch(() => {});
    } catch {
      // A loading task may already have torn down synchronously.
    }
  }
  return generation;
}

function _isDisplayGeneration(generation) {
  return generation === _displayGeneration;
}

function _stopLiveRenderWork() {
  if (_renderObserver) {
    _renderObserver.disconnect();
    _renderObserver = null;
  }
  if (_pageObserver) {
    _pageObserver.disconnect();
    _pageObserver = null;
  }
  for (const wrap of viewer.querySelectorAll(".page-wrap")) {
    if (wrap._paintTimer) {
      clearTimeout(wrap._paintTimer);
      wrap._paintTimer = null;
    }
    if (wrap._renderTask) {
      try { wrap._renderTask.cancel(); } catch { /* best-effort */ }
    }
    if (wrap._textLayer) {
      try { wrap._textLayer.cancel(); } catch { /* best-effort */ }
    }
    wrap._textLayer = null;
    wrap._textPromise = null;
  }
  _clearPaintQueue();
}

async function _destroyPdf(pdf) {
  if (!pdf || pdf === currentDoc) return;
  try {
    await pdf.destroy();
  } catch {
    // Cleanup is best-effort. A superseded PDF must never fail the
    // generation that replaced it.
  }
}

// Zoom mode: "manual" (user picked a fixed scale via +/-/0),
// "fit-width" (track viewer.clientWidth), or "fit-page" (fit
// both dimensions). The fit modes auto-recompute on window
// resize; manual sticks to whatever value the user set.
let scaleMode = "manual";
// Map canvas DOM element -> its rendered viewport (so click handlers
// can convert client coords back to PDF coords).
const canvasViewports = new WeakMap();

// -----------------------------------------------------------------------
// Content-addressed PDF chunk cache
// -----------------------------------------------------------------------
//
// Mirrors the server-side chunk store (see tools/pdf_chunks.py):
// each PDF object is stored once under its SHA-256 hash. On every
// reload we fetch the latest manifest, which lists object byte
// ranges and hashes; chunks whose hashes the client already has
// are served from this in-memory cache, and only new hashes are
// fetched from /chunk/<hash>.
//
// The cache survives across reloads (it's module-scope) but is
// reset by a page refresh. That's fine: the user's browser will
// fetch /chunk/<hash> with `Cache-Control: public, max-age=...,
// immutable` headers, so the second fetch comes from the
// browser's HTTP cache and is nearly free.
//
// Cap the cache at a modest number of entries to bound memory
// usage. LRU eviction would be ideal but is overkill: the bound
// here is loose, and chunks evicted from this Map can still be
// re-fetched (the server keeps them on disk and the browser's
// HTTP cache reduces wire transfer to ~0).
const CHUNK_CACHE_MAX_ENTRIES = 1000;
const chunkCache = new Map();

function rememberChunk(hash, bytes) {
  if (chunkCache.size >= CHUNK_CACHE_MAX_ENTRIES) {
    // Evict the oldest entry (Map preserves insertion order).
    const oldestKey = chunkCache.keys().next().value;
    if (oldestKey !== undefined) chunkCache.delete(oldestKey);
  }
  chunkCache.set(hash, bytes);
}

async function fetchChunk(hash) {
  const cached = chunkCache.get(hash);
  if (cached) return cached;
  const resp = await fetch(`/chunk/${hash}`);
  if (!resp.ok) {
    throw new Error(`chunk ${hash} fetch failed: ${resp.status}`);
  }
  const buf = new Uint8Array(await resp.arrayBuffer());
  rememberChunk(hash, buf);
  return buf;
}

async function fetchPdfRange(begin, end) {
  // Fetch [begin, end) from /pdf using HTTP Range. Used for
  // skeleton ranges (PDF header, gaps between objects, the
  // trailer) — anything not covered by a content-addressed
  // chunk.
  const resp = await fetch("/pdf", {
    headers: { "Range": `bytes=${begin}-${end - 1}` },
  });
  if (!(resp.status === 206 || resp.status === 200)) {
    throw new Error(`/pdf range ${begin}-${end - 1} failed: ${resp.status}`);
  }
  return new Uint8Array(await resp.arrayBuffer());
}

// Custom PDFDataRangeTransport that serves byte ranges from the
// content-addressed chunk cache where possible, falling back to
// /pdf with HTTP Range for skeleton bytes (header/xref/trailer).
class ChunkedTransport extends pdfjsLib.PDFDataRangeTransport {
  constructor(manifest) {
    // initialData is empty: PDF.js will request the ranges it
    // needs via requestDataRange.
    super(manifest.pdfSize, new Uint8Array(0));
    this.manifest = manifest;
    // Sort chunks by start offset for fast lookups; the server
    // emits them sorted but defending against a future change is
    // cheap.
    this.sortedRanges = [...manifest.ranges].sort(
      (a, b) => a.start - b.start
    );
    queueMicrotask(() => this.transportReady());
  }

  async requestDataRange(begin, end) {
    // PDF.js's API has end inclusive in some places and exclusive
    // in others; the spec the worker sends here is half-open
    // [begin, end). Clamp to pdfSize for safety.
    end = Math.min(end, this.manifest.pdfSize);
    if (begin >= end) {
      this.onDataRange(begin, new Uint8Array(0));
      return;
    }
    try {
      const bytes = await this._assemble(begin, end);
      this.onDataRange(begin, bytes);
    } catch (err) {
      console.error("ChunkedTransport: range fetch failed", err);
      // PDF.js retries on failure. Don't call onDataRange; the
      // request just times out. Better than throwing here, which
      // would break the worker.
    }
  }

  async _assemble(begin, end) {
    // Plan the segment layout (pure; see serve_web_chunks.js), then fetch
    // each: chunk slices from the content-addressed cache, skeleton gaps
    // (header/xref/trailer) from /pdf via HTTP Range. Concatenate.
    const segments = [];
    for (const seg of planRangeSegments(this.sortedRanges, begin, end)) {
      if (seg.kind === "chunk") {
        const chunkBytes = await fetchChunk(seg.hash);
        segments.push(chunkBytes.subarray(seg.sliceStart, seg.sliceEnd));
      } else {
        segments.push(await fetchPdfRange(seg.begin, seg.end));
      }
    }
    if (segments.length === 1) return segments[0];
    // Concatenate segments into one buffer.
    const total = segments.reduce((s, seg) => s + seg.length, 0);
    const out = new Uint8Array(total);
    let off = 0;
    for (const seg of segments) {
      out.set(seg, off);
      off += seg.length;
    }
    return out;
  }
}

// Background-prefetch every chunk in the manifest. Runs after
// PDF.js's initial getDocument promise resolves so we don't
// compete with the worker for bandwidth on the first render. By
// the time the user starts scrolling, every chunk is in
// `chunkCache` and PDF.js's per-page byte fetches are
// instant. Best-effort: failures are silent and don't affect
// rendering (PDF.js will fetch on-demand if a chunk's missing).
async function prefetchChunks(manifest) {
  // Concurrency: keep it modest so we don't saturate localhost
  // connections and starve user-initiated fetches.
  const queue = manifest.ranges
    .filter(r => !chunkCache.has(r.hash))
    .map(r => r.hash);
  const workerCount = 4;
  async function worker() {
    while (queue.length) {
      const hash = queue.shift();
      try {
        await fetchChunk(hash);
      } catch (e) { /* ignore */ }
    }
  }
  await Promise.all(
    Array.from({ length: workerCount }, () => worker())
  );
}

function setStatus(cls, text) {
  statusEl.className = cls;
  statusEl.textContent = text;
}

async function fetchManifest() {
  // Returns a manifest object, or null if the server can't
  // produce one (e.g. cross-reference-stream PDF the chunker
  // doesn't understand, or chunking is disabled). On null we
  // fall back to whole-PDF transport.
  try {
    const resp = await fetch("/pdf-manifest");
    if (!resp.ok) return null;
    return await resp.json();
  } catch (err) {
    return null;
  }
}

async function renderDocument() {
  const generation = _beginRenderGeneration();
  setStatus("building", "rendering…");
  let pdf = null;
  let loadingTask = null;
  try {
    const manifest = await fetchManifest();
    if (!_renderGenerations.isCurrent(generation)) return;

    if (manifest && manifest.ranges && manifest.ranges.length > 0) {
      // Chunked path: serve byte ranges from the content-addressed cache
      // (with HTTP-Range fallback for skeleton bytes).
      const transport = new ChunkedTransport(manifest);
      loadingTask = pdfjsLib.getDocument({ range: transport });
      _activeLoadingTask = loadingTask;
      pdf = await loadingTask.promise;
      // Kick off prefetch in the background; don't await.
      prefetchChunks(manifest).catch(() => {});
    } else {
      // Fallback: pull the whole PDF in one request. Cache-bust so the
      // browser fetches fresh bytes.
      const bust = Date.now();
      loadingTask = pdfjsLib.getDocument(`/pdf?t=${bust}`);
      _activeLoadingTask = loadingTask;
      pdf = await loadingTask.promise;
    }
    if (_activeLoadingTask === loadingTask) _activeLoadingTask = null;
    if (!_renderGenerations.isCurrent(generation)) {
      await _destroyPdf(pdf);
      return;
    }
    await _renderLoadedDocument(
      pdf,
      manifest && manifest.pages ? manifest.pages : null,
      generation,
      true,
    );
  } catch (err) {
    if (_activeLoadingTask === loadingTask) _activeLoadingTask = null;
    if (!_renderGenerations.isCurrent(generation)) {
      await _destroyPdf(pdf);
      return;
    }
    setStatus("fail", `render error: ${err.message || err}`);
    console.error(err);
  }
}

async function _renderLoadedDocument(
  pdf,
  manifestPages,
  generation,
  renderOutline,
) {
  if (!_renderGenerations.isCurrent(generation)) {
    await _destroyPdf(pdf);
    return false;
  }

  currentPageNum = Math.max(1, Math.min(pdf.numPages, currentPageNum));
  if (scaleMode !== "manual") {
    const page = await pdf.getPage(currentPageNum);
    if (!_renderGenerations.isCurrent(generation)) {
      await _destroyPdf(pdf);
      return false;
    }
    const base = page.getViewport({ scale: 1 });
    const padding = 32;
    const scrollbar = 14;
    const availW = Math.max(100, viewer.clientWidth - padding - scrollbar);
    if (scaleMode === "fit-width") {
      scale = availW / base.width;
    } else {
      const availH = Math.max(100, viewer.clientHeight - padding);
      scale = Math.min(availW / base.width, availH / base.height);
    }
    scale = Math.max(0.25, Math.min(4.0, scale));
    _updateZoomUI();
  }

  const previousDoc = currentDoc;
  const committed = await renderAllPages(pdf, manifestPages, generation);
  if (!committed) {
    await _destroyPdf(pdf);
    return false;
  }
  if (previousDoc && previousDoc !== pdf) {
    try {
      await previousDoc.destroy();
    } catch {
      // The new document is already committed; stale cleanup is best-effort.
    }
  }
  if (renderOutline) {
    _renderOutline(pdf).catch((err) => {
      if (currentDoc === pdf) console.warn("outline render failed:", err);
    });
  }
  return true;
}

function _rerenderCurrentDocument() {
  if (!currentDoc) return Promise.resolve(false);
  const generation = _beginRenderGeneration();
  return _renderLoadedDocument(
    currentDoc,
    _manifestPages,
    generation,
    false,
  );
}

// Per-page reconciliation state (option B): reuse a page's already-built
// .page-wrap (and its painted canvas) across reloads when its content +
// geometry are unchanged at the same zoom. _manifestPages is the latest
// manifest's page index; _renderedPages is what the live DOM was built
// from; _renderedScale guards against reusing canvases after a zoom.
let _manifestPages = null;
let _renderedPages = null;
let _renderedScale = null;

async function renderAllPages(pdf, manifestPages, generation) {
  // Build changed page nodes while the previous document remains mounted.
  // Reused nodes are collected by reference but are not moved until the
  // synchronous commit below, so awaits cannot partially empty the viewer.
  const prevScroll = { top: viewer.scrollTop, left: viewer.scrollLeft };
  const nextNodes = [];
  const reusePlan = scale === _renderedScale
    ? planPageReconciliation(_renderedPages, manifestPages)
    : [];
  const oldWraps = new Map();
  if (reusePlan.length) {
    for (const wrap of viewer.querySelectorAll(".page-wrap")) {
      oldWraps.set(parseInt(wrap.dataset.pageNumber, 10), wrap);
    }
  }

  for (let i = 1; i <= pdf.numPages; i++) {
    if (!_renderGenerations.isCurrent(generation)) return false;
    if (reusePlan[i - 1] === "reuse" && oldWraps.has(i)) {
      nextNodes.push(oldWraps.get(i));
      continue;
    }

    const page = await pdf.getPage(i);
    if (!_renderGenerations.isCurrent(generation)) return false;
    const viewport = page.getViewport({ scale });

    const wrap = document.createElement("div");
    wrap.className = "page-wrap";
    wrap.style.width = `${viewport.width}px`;
    wrap.style.height = `${viewport.height}px`;
    wrap.dataset.pageNumber = String(i);

    const canvas = document.createElement("canvas");
    // CSS dimensions preserve layout. The intrinsic backing store stays at
    // zero until this page enters the observer retention margin.
    canvas.width = 0;
    canvas.height = 0;
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    canvas.dataset.pageNumber = String(i);
    canvasViewports.set(canvas, viewport);
    if (SYNCTEX_ENABLED) {
      canvas.addEventListener("click", onCanvasClick);
    }
    wrap.appendChild(canvas);

    // Commit an empty overlay now; nearby pages hydrate it through the
    // render observer, and search hydrates the remaining pages on demand.
    const textLayerEl = document.createElement("div");
    textLayerEl.className = "text-layer";
    textLayerEl.style.setProperty("--scale-factor", String(viewport.scale));
    wrap.appendChild(textLayerEl);
    wrap.dataset.textRendered = "";
    nextNodes.push(wrap);
  }

  if (!_renderGenerations.isCurrent(generation)) return false;

  // No awaits between stopping the old work and replacing its nodes. Moving
  // reused nodes and installing changed nodes therefore happens in one task.
  _stopLiveRenderWork();
  _displayGeneration = generation;
  viewer.replaceChildren(...nextNodes);
  viewer.scrollTo(prevScroll);

  currentDoc = pdf;
  _manifestPages = manifestPages;
  _renderedPages = manifestPages;
  _renderedScale = scale;
  _setTotalPages(pdf.numPages);
  if (_renderStats.current?.generation === generation) {
    Object.assign(_renderStats.current, {
      pages: pdf.numPages,
      reusedPages: reusePlan.filter((action) => action === "reuse").length,
      scale,
      committedMs: performance.now() - _renderStats.current.startedAt,
      textLayers: viewer.querySelectorAll(
        '.page-wrap[data-text-rendered="1"]',
      ).length,
    });
  }
  _updateCanvasStats(generation);

  _attachPageObserver();
  _attachRenderObserver(pdf, generation);
  const currentWrap = viewer.querySelector(
    `.page-wrap[data-page-number="${currentPageNum}"]`,
  );
  if (currentWrap) {
    _hydrateTextLayer(pdf, currentWrap, generation);
    _queuePaint(pdf, currentWrap, generation, -1);
  }
  if (_searchQuery) _runSearch();

  refreshStatus();
  return true;
}

async function _hydrateTextLayer(pdf, wrap, generation) {
  if (wrap.dataset.textRendered === "1") return true;
  if (wrap._textPromise) return wrap._textPromise;

  let promise;
  promise = (async () => {
    const startedAt = performance.now();
    let textLayer = null;
    try {
      const pageNum = parseInt(wrap.dataset.pageNumber, 10);
      const page = await pdf.getPage(pageNum);
      if (!_isDisplayGeneration(generation)) return false;

      const viewport = page.getViewport({ scale });
      const canvas = wrap.querySelector("canvas");
      canvasViewports.set(canvas, viewport);
      const textLayerEl = wrap.querySelector(".text-layer");
      textLayerEl.replaceChildren();
      textLayerEl.style.setProperty("--scale-factor", String(viewport.scale));

      textLayer = new pdfjsLib.TextLayer({
        textContentSource: page.streamTextContent(),
        container: textLayerEl,
        viewport,
      });
      wrap._textLayer = textLayer;
      await textLayer.render();
      if (!_isDisplayGeneration(generation)) return false;

      wrap.dataset.textRendered = "1";
      const current = _renderStats.current;
      if (current?.generation === generation) {
        current.textLayers += 1;
        current.textLayerMs += performance.now() - startedAt;
        if (current.firstTextMs === null) {
          current.firstTextMs = performance.now() - current.startedAt;
        }
      }
      return true;
    } catch (err) {
      if (
        _isDisplayGeneration(generation) &&
        err?.name !== "AbortException" &&
        err?.name !== "RenderingCancelledException"
      ) {
        console.warn(
          "text layer render failed for page " +
            wrap.dataset.pageNumber + ":",
          err,
        );
      }
      return false;
    } finally {
      if (wrap._textPromise === promise) wrap._textPromise = null;
      if (wrap._textLayer === textLayer) wrap._textLayer = null;
    }
  })();
  wrap._textPromise = promise;
  return promise;
}

// Rasterize one page's canvas into its placeholder. Idempotent: a page
// already painted (or mid-paint) is skipped, so the eager current-page
// paint and the observer can't double-render. The RenderTask is stashed
// on the wrap so _renderObserver can cancel it if the page scrolls out
// before its raster starts.
function _updateCanvasStats(generation) {
  const current = _renderStats.current;
  if (!current || current.generation !== generation) return;
  const canvases = [...viewer.querySelectorAll(".page-wrap canvas")];
  current.canvasBytes = canvases.reduce(
    (bytes, canvas) => bytes + canvas.width * canvas.height * 4,
    0,
  );
  current.renderedCanvases = viewer.querySelectorAll(
    '.page-wrap[data-rendered="1"]',
  ).length;
}

function _releaseCanvas(wrap) {
  const canvas = wrap.querySelector("canvas");
  if (!canvas) return;
  // Assigning either intrinsic dimension resets the context and releases its
  // bitmap. Keep CSS width/height and the stored viewport for stable layout.
  const hadBacking = canvas.width > 0 && canvas.height > 0;
  canvas.width = 0;
  canvas.height = 0;
  wrap.dataset.rendered = "";
  const current = _renderStats.current;
  if (hadBacking && current?.generation === _displayGeneration) {
    current.releasedCanvases += 1;
  }
  _updateCanvasStats(_displayGeneration);
}

function _cancelledRenderError() {
  const err = new Error("render generation superseded");
  err.name = "RenderingCancelledException";
  return err;
}

function paintPage(pdf, wrap, generation) {
  return paintPageImpl(
    wrap,
    async (currentWrap) => {
      if (!_isDisplayGeneration(generation)) {
        throw _cancelledRenderError();
      }
      const pageNum = parseInt(currentWrap.dataset.pageNumber, 10);
      const canvas = currentWrap.querySelector("canvas");
      const page = await pdf.getPage(pageNum);
      if (!_isDisplayGeneration(generation)) {
        throw _cancelledRenderError();
      }
      const viewport = page.getViewport({ scale });
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      const ctx = canvas.getContext("2d");
      const transform = dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null;
      const startedAt = performance.now();
      const task = page.render({ canvasContext: ctx, viewport, transform });
      task.promise.then(
        () => {
          if (!_isDisplayGeneration(generation)) return;
          const ms = performance.now() - startedAt;
          recordRenderTiming(_renderStats, pageNum, ms);
          if (window.__SERVE_DEBUG__) {
            console.debug(`page ${pageNum} rasterized in ${ms.toFixed(1)}ms`);
          }
        },
        () => {},
      );
      return task;
    },
    () => _isDisplayGeneration(generation),
  ).finally(() => {
    if (wrap.dataset.rendered !== "1") {
      _releaseCanvas(wrap);
      return;
    }
    const current = _renderStats.current;
    if (
      current?.generation === generation &&
      current.firstPaintMs === null
    ) {
      current.firstPaintMs = performance.now() - current.startedAt;
    }
    _updateCanvasStats(generation);
  });
}

let _renderObserver = null;
const MAX_CONCURRENT_PAINTS = 2;
let _paintQueue = [];
let _activePaints = 0;

function _clearPaintQueue() {
  for (const item of _paintQueue) item.wrap._paintQueued = false;
  _paintQueue = [];
}

function _cancelQueuedPaint(wrap) {
  if (!wrap._paintQueued) return;
  wrap._paintQueued = false;
  _paintQueue = _paintQueue.filter((item) => item.wrap !== wrap);
}

function _queuePaint(pdf, wrap, generation, priority = null) {
  if (
    !_isDisplayGeneration(generation) ||
    wrap.dataset.rendered === "1" ||
    wrap.dataset.rendering === "1" ||
    wrap._paintQueued
  ) {
    return;
  }
  const pageNum = parseInt(wrap.dataset.pageNumber, 10);
  wrap._paintQueued = true;
  _paintQueue.push({
    pdf,
    wrap,
    generation,
    priority: priority ?? Math.abs(pageNum - currentPageNum),
  });
  _paintQueue.sort((a, b) => a.priority - b.priority);
  _drainPaintQueue();
}

function _drainPaintQueue() {
  while (_activePaints < MAX_CONCURRENT_PAINTS && _paintQueue.length) {
    const item = _paintQueue.shift();
    if (!item.wrap._paintQueued) continue;
    item.wrap._paintQueued = false;
    if (
      !_isDisplayGeneration(item.generation) ||
      item.wrap.dataset.rendered === "1"
    ) {
      continue;
    }

    _activePaints += 1;
    const current = _renderStats.current;
    if (current?.generation === item.generation) {
      current.paintQueueMax = Math.max(
        current.paintQueueMax,
        _activePaints,
      );
    }
    paintPage(item.pdf, item.wrap, item.generation).finally(() => {
      _activePaints -= 1;
      _drainPaintQueue();
    });
  }
}

// How long a page must stay in (or near) the viewport before we enqueue its
// raster. The current page bypasses this delay but still uses the same
// concurrency-limited queue.
const RENDER_SETTLE_MS = 80;

function _attachRenderObserver(pdf, generation) {
  if (_renderObserver) _renderObserver.disconnect();
  _renderObserver = new IntersectionObserver((entries) => {
    if (!_isDisplayGeneration(generation)) return;
    for (const entry of entries) {
      const wrap = entry.target;
      const action = renderObserverAction(entry);
      if (action === "paint") {
        _hydrateTextLayer(pdf, wrap, generation);
        if (wrap.dataset.rendered !== "1" && !wrap._paintTimer) {
          wrap._paintTimer = setTimeout(() => {
            wrap._paintTimer = null;
            _queuePaint(pdf, wrap, generation);
          }, RENDER_SETTLE_MS);
        }
      } else {
        _cancelQueuedPaint(wrap);
        if (wrap._paintTimer) {
          clearTimeout(wrap._paintTimer);
          wrap._paintTimer = null;
        }
        if (action === "cancel") {
          try { wrap._renderTask.cancel(); } catch { /* best-effort */ }
        } else if (action === "release") {
          _releaseCanvas(wrap);
        }
      }
    }
  }, { root: viewer, rootMargin: "100% 0px" });
  for (const wrap of viewer.querySelectorAll(".page-wrap")) {
    _renderObserver.observe(wrap);
  }
}

// Cached latest /status response. The status pill ticks every
// second so the "Xs ago" suffix stays current without re-fetching;
// re-fetch happens on build events (via SSE/WS reload).
let _lastStatus = null;

async function refreshStatus() {
  try {
    const r = await fetch("/status");
    const s = await r.json();
    _lastStatus = s;
    _renderStatus();
    _updateGitBadge(s);
  } catch (e) {
    setStatus("fail", "server unreachable");
  }
}

function _formatAge(sec) {
  if (sec < 5) return "just now";
  if (sec < 60) return `${Math.floor(sec)} s ago`;
  if (sec < 3600) {
    const m = Math.floor(sec / 60);
    return m === 1 ? "1 min ago" : `${m} min ago`;
  }
  const h = Math.floor(sec / 3600);
  return h === 1 ? "1 h ago" : `${h} h ago`;
}

function _renderStatus() {
  if (!_lastStatus) return;
  const s = _lastStatus;
  if (s.last_success) {
    const elapsed = (s.last_elapsed_seconds || 0).toFixed(2);
    const ageSec = Math.max(0, (Date.now() / 1000) - s.last_finished_at);
    setStatus(
      "ok",
      `✓ ${elapsed} s · build #${s.build_count} · ${_formatAge(ageSec)}`,
    );
  } else {
    setStatus("fail", `${s.last_message}`);
  }
}

// Tick the "Xs ago" suffix every second without polling /status.
setInterval(_renderStatus, 1000);

function _updateGitBadge(s) {
  const badge = document.getElementById("git-badge");
  const branchEl = document.getElementById("git-branch");
  const dirtyEl = document.getElementById("git-dirty");
  if (!badge) return;
  if (s.git_branch) {
    branchEl.textContent = s.git_branch;
    if (s.git_short_sha) {
      branchEl.title = `at ${s.git_short_sha}`;
    }
    dirtyEl.hidden = !s.git_dirty;
    badge.hidden = false;
  } else if (s.git_short_sha) {
    // Detached HEAD: show short sha + dirty marker.
    branchEl.textContent = s.git_short_sha;
    branchEl.title = "detached HEAD";
    dirtyEl.hidden = !s.git_dirty;
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
}

async function onCanvasClick(event) {
  const canvas = event.currentTarget;
  const viewport = canvasViewports.get(canvas);
  if (!viewport) return;
  // Map through the CSS-pixel viewport rather than the optional HiDPI
  // backing store, which may still be zero for an unpainted placeholder.
  const rect = canvas.getBoundingClientRect();
  const [pdfX, pdfY] = clientPointToPdfPoint(
    viewport,
    rect,
    event.clientX,
    event.clientY,
  );
  const pageNumber = parseInt(canvas.dataset.pageNumber, 10);

  syncResultEl.textContent = "looking up source location…";
  try {
    const r = await fetch("/sync/reverse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ page: pageNumber, x: pdfX, y: pdfY }),
    });
    const body = await r.json();
    if (body.ok) {
      // Render as <file>:<line>, copy to clipboard immediately
      // (most users want to paste it into their editor anyway),
      // and surface a subtle "copied" badge so the user knows
      // the click had a side effect beyond rendering text. The
      // browser can't drive the editor; the clipboard handoff is
      // as close as we get.
      const locText = `${body.file}:${body.line}`;
      const copied = await _copyToClipboard(locText);
      const status = copied ? "copied" : "click to copy";
      syncResultEl.innerHTML =
        `→ <span class="sync-loc" tabindex="0" role="button" ` +
        `title="click to copy ${escapeHtml(locText)}">` +
        `<strong>${escapeHtml(body.file)}</strong>:` +
        `<strong>${body.line}</strong></span> ` +
        `<span class="sync-copied">${status}</span>`;
      // Wire the click-again-to-copy fallback (handles the
      // clipboard-write-blocked case + any later re-click).
      const span = syncResultEl.querySelector(".sync-loc");
      if (span) {
        const recopy = async (e) => {
          if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          if (await _copyToClipboard(locText)) {
            const badge = syncResultEl.querySelector(".sync-copied");
            if (badge) {
              badge.textContent = "copied";
              setTimeout(() => {
                if (badge.textContent === "copied") badge.textContent = "click to copy";
              }, 1500);
            }
          }
        };
        span.addEventListener("click", recopy);
        span.addEventListener("keydown", recopy);
      }
    } else {
      syncResultEl.textContent = `synctex: ${body.error || "no match"}`;
    }
  } catch (err) {
    syncResultEl.textContent = `synctex request failed: ${err.message || err}`;
  }
}

async function _copyToClipboard(text) {
  // navigator.clipboard.writeText is the modern path but requires
  // a secure context (http://127.0.0.1 counts) AND, on some
  // browsers, a transient user activation. The PDF-canvas click
  // satisfies the activation requirement; the secure-context one
  // we trust the localhost binding. Fall back to a textarea +
  // execCommand for hostile environments (file:// previews,
  // ancient browsers). Returns true on success.
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "absolute";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

// -----------------------------------------------------------------------
// Live-update transport
// -----------------------------------------------------------------------
//
// Prefer a WebSocket to /ws — the server pushes the manifest plus
// any chunks we don't already have in a single duplex burst,
// saving the manifest-fetch + chunk-fetch round-trips the SSE
// flow needs. If WS fails to open (older server, proxy that
// doesn't speak Upgrade, CORS, etc.) we fall back transparently
// to SSE + /chunk/<hash> over HTTP.
//
// The first page-load always uses the HTTP path (renderDocument()
// at the bottom of this file); the WS connection inherits whatever
// chunkCache state that left behind by sending its "hello" with
// the keys we already have. The server uses that to skip chunks
// the browser doesn't need.

let _sseSource = null;  // EventSource | null
let _wsConn = null;     // WebSocket | null
// Pending state for one WS push: we get the manifest first, then
// zero or more binary frames, then we render when all expected
// chunks have arrived. If the manifest changes mid-batch we
// just overwrite — the latest manifest wins.
let _wsPendingManifest = null;
let _wsPendingHashes = new Set();

function _hexFromBytes(bytes) {
  // Hot path during chunk delivery — avoid the array+join allocation
  // in favour of direct string concatenation against a lookup table.
  let s = "";
  for (let i = 0; i < bytes.length; i++) {
    const b = bytes[i];
    s += (b < 0x10 ? "0" : "") + b.toString(16);
  }
  return s;
}

function _handleWsMessage(ev) {
  if (typeof ev.data === "string") {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (!msg || !msg.type) return;
    if (msg.type === "manifest") {
      _wsPendingManifest = msg;
      _wsPendingHashes = new Set();
      for (const r of msg.ranges) {
        if (!chunkCache.has(r.hash)) _wsPendingHashes.add(r.hash);
      }
      if (_wsPendingHashes.size === 0) {
        _flushWsRender();
      }
    } else if (msg.type === "build-failed") {
      // The status banner will flash red via refreshStatus.
      // Keep the existing rendered PDF in place — it's still
      // the last good version.
      refreshStatus();
    } else if (msg.type === "log-update") {
      _handleLogUpdate(msg);
    } else if (msg.type === "jump") {
      jumpToPdfLocation(msg);
    }
    return;
  }
  // Binary frame: <32 bytes raw sha256><payload>.
  const bytes = new Uint8Array(ev.data);
  if (bytes.length < 32) return;
  const hash = _hexFromBytes(bytes.subarray(0, 32));
  const payload = bytes.subarray(32);
  // Copy out of the underlying ArrayBuffer so the cache entry
  // isn't tied to whatever view PDF.js may have on the next
  // message. Allocates, but chunks are bounded in size.
  rememberChunk(hash, new Uint8Array(payload));
  _wsPendingHashes.delete(hash);
  if (_wsPendingHashes.size === 0 && _wsPendingManifest) {
    _flushWsRender();
  }
}

async function _flushWsRender() {
  const manifest = _wsPendingManifest;
  _wsPendingManifest = null;
  _wsPendingHashes = new Set();
  if (!manifest) return;

  const generation = _beginRenderGeneration();
  setStatus("building", "rendering…");
  let loadingTask = null;
  let pdf = null;
  try {
    const transport = new ChunkedTransport(manifest);
    loadingTask = pdfjsLib.getDocument({ range: transport });
    _activeLoadingTask = loadingTask;
    pdf = await loadingTask.promise;
    if (_activeLoadingTask === loadingTask) _activeLoadingTask = null;
    if (!_renderGenerations.isCurrent(generation)) {
      await _destroyPdf(pdf);
      return;
    }
    await _renderLoadedDocument(
      pdf,
      manifest.pages || null,
      generation,
      true,
    );
  } catch (err) {
    if (_activeLoadingTask === loadingTask) _activeLoadingTask = null;
    if (!_renderGenerations.isCurrent(generation)) {
      await _destroyPdf(pdf);
      return;
    }
    setStatus("fail", `render error: ${err.message || err}`);
    console.error(err);
  }
}

function _startWebSocket() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${proto}//${location.host}/ws`;
  let ws;
  try {
    ws = new WebSocket(url);
  } catch (err) {
    _startSSE();
    return;
  }
  ws.binaryType = "arraybuffer";

  let opened = false;
  ws.addEventListener("open", () => {
    opened = true;
    // Announce our chunk-cache state so the server only pushes
    // chunks we don't already have. After a hard reload this set
    // is empty; after a soft (in-page) state change it carries
    // over from whatever renderDocument() populated on first load.
    try {
      ws.send(JSON.stringify({
        type: "hello",
        have: Array.from(chunkCache.keys()),
      }));
    } catch {
      // Send failed; the onclose handler will fall back.
    }
  });

  ws.addEventListener("message", _handleWsMessage);

  ws.addEventListener("close", () => {
    _wsConn = null;
    if (!opened) {
      // Never connected — fall back to SSE so live-reload still
      // works. Status banner only flips on if SSE also fails.
      _startSSE();
    } else if (!_sseSource) {
      // Mid-session disconnect; bring up SSE as a safety net.
      // The user will get a chance to reconnect WS on next page
      // refresh.
      _startSSE();
      setStatus("fail", "live-reload reconnecting via SSE…");
    }
  });

  ws.addEventListener("error", () => {
    // The browser fires 'error' before 'close' for connection-
    // refused style failures. We do all the fallback work in
    // 'close' so nothing here.
  });

  _wsConn = ws;
}

function _startSSE() {
  if (_sseSource) return;
  const evtSrc = new EventSource("/events");
  evtSrc.onmessage = (e) => {
    if (e.data === "reload") {
      renderDocument();
      // SSE doesn't carry log-update events; pull on each reload
      // so the drawer still tracks build output for fallback
      // clients (assume success — the drawer's auto-expand only
      // fires for failed builds, which SSE delivers as the
      // "build-failed" branch below).
      _handleLogUpdate({ success: true });
    } else if (e.data === "build-failed" || e.data === "hello") {
      refreshStatus();
      if (e.data === "build-failed") {
        _handleLogUpdate({ success: false });
      }
    } else if (e.data.startsWith("{")) {
      let msg;
      try {
        msg = JSON.parse(e.data);
      } catch {
        return;
      }
      if (msg && msg.type === "jump") {
        jumpToPdfLocation(msg);
      }
    }
  };
  evtSrc.onerror = () => {
    setStatus("fail", "lost connection to server");
  };
  _sseSource = evtSrc;
}

// Forward-sync target: scroll the named page into view, then briefly
// flash a highlight overlay at the PDF-coordinate box. The overlay
// is a transient absolutely-positioned div that fades out after
// ~1.5s; it's positioned with the same PDF.js viewport math the
// reverse-click handler uses in the opposite direction.
function jumpToPdfLocation(msg) {
  const canvas = viewer.querySelector(
    `canvas[data-page-number="${msg.page}"]`,
  );
  if (!canvas) {
    syncResultEl.textContent =
      `forward-sync: page ${msg.page} not yet rendered`;
    return;
  }
  const viewport = canvasViewports.get(canvas);
  if (!viewport) return;
  // PDF box origin is the bottom-left in PDF-point coords; convert it
  // to a CSS-pixel rectangle in the page viewport (see serve_web_synctex).
  const { left, top, width, height } =
    pdfBoxToViewportRect(viewport, msg.x, msg.y, msg.w, msg.h);

  const wrap = canvas.parentElement;
  // The canvas itself isn't a positioned container, so we add a
  // wrapper if needed and position the overlay relative to the
  // canvas's offsetParent. Easiest: position the overlay relative
  // to the viewer and offset by the canvas's position in the
  // viewer's coordinate space.
  const canvasRect = canvas.getBoundingClientRect();
  const viewerRect = viewer.getBoundingClientRect();
  const overlay = document.createElement("div");
  overlay.className = "synctex-flash";
  overlay.style.left = `${
    canvasRect.left - viewerRect.left + viewer.scrollLeft + left
  }px`;
  overlay.style.top = `${
    canvasRect.top - viewerRect.top + viewer.scrollTop + top
  }px`;
  overlay.style.width = `${Math.max(width, 4)}px`;
  overlay.style.height = `${Math.max(height, 4)}px`;
  viewer.appendChild(overlay);
  // Scroll the overlay into view (smooth on supporting browsers,
  // graceful fallback otherwise).
  overlay.scrollIntoView({ behavior: "smooth", block: "center" });
  // Fade + remove after the CSS animation settles. The animation
  // is 1.5s in `.synctex-flash`; we wait a little longer before
  // removing the node to avoid a sub-pixel flash at the end.
  setTimeout(() => { overlay.remove(); }, 1800);
  syncResultEl.innerHTML =
    `← <strong>${escapeHtml(msg.file)}</strong>:` +
    `<strong>${msg.line}</strong> ` +
    `(page ${msg.page})`;
}

// -----------------------------------------------------------------------
// Build-log drawer
// -----------------------------------------------------------------------
//
// /log returns the latest build's combined stdout+stderr keyed by
// an integer id that the server bumps per build. The WS push
// transport notifies us with {type: "log-update", logId, success}
// so we can refetch without polling. Auto-expands on the first
// failed build of a session; honours the user's manual collapse
// after that (persisted to localStorage).

const logDrawer = document.getElementById("log-drawer");
const logHeader = document.getElementById("log-header");
const logBody = document.getElementById("log-body");
const logSummary = document.getElementById("log-summary");
const logToggleBtn = document.getElementById("log-toggle");
const logCopyBtn = document.getElementById("log-copy");

let _logFetchedId = -1;
// User-driven open state: null = "follow auto rules", true/false =
// pinned by the user. Persisted to localStorage.
let _logUserOpen = null;
try {
  const v = localStorage.getItem("rules_latex_log_open");
  if (v === "true") _logUserOpen = true;
  else if (v === "false") _logUserOpen = false;
} catch {
  /* localStorage may be blocked; ignore */
}

function _setLogOpen(open) {
  logDrawer.classList.toggle("collapsed", !open);
  logHeader.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) {
    // Tail-scroll: most users want the latest output visible.
    logBody.scrollTop = logBody.scrollHeight;
  }
}

function _persistLogOpen(open) {
  _logUserOpen = open;
  try {
    localStorage.setItem("rules_latex_log_open", open ? "true" : "false");
  } catch {
    /* best-effort */
  }
}

async function _fetchLog() {
  try {
    const r = await fetch("/log");
    if (!r.ok) return;
    const data = await r.json();
    if (data.id === _logFetchedId) return;
    _logFetchedId = data.id;
    logBody.textContent = data.text || "";
    _renderLogSummary(data.text);
    // Auto-scroll to tail when refreshed and visible.
    if (!logDrawer.classList.contains("collapsed")) {
      logBody.scrollTop = logBody.scrollHeight;
    }
  } catch {
    /* /log failures aren't fatal */
  }
}

function _renderLogSummary(text) {
  // Show the last non-empty line in the summary — usually that's
  // either "Build completed successfully" or the actual error
  // line, which is exactly what the user wants to see at a glance
  // without expanding the drawer.
  if (!text) {
    logSummary.textContent = "(no output)";
    return;
  }
  const lines = text.split(/\r?\n/).filter(s => s.trim() !== "");
  logSummary.textContent = lines.length ? lines[lines.length - 1] : "";
}

function _handleLogUpdate(msg) {
  logDrawer.setAttribute(
    "data-build-success", msg.success === false ? "false" : "true",
  );
  _fetchLog();
  // Auto-expand on failure if the user hasn't explicitly closed
  // the drawer this session. If they have an explicit user
  // preference, honour it (so a user who closed it stays closed,
  // even on subsequent failures — they can re-open manually).
  if (msg.success === false && _logUserOpen !== false) {
    _setLogOpen(true);
  } else if (_logUserOpen !== null) {
    _setLogOpen(_logUserOpen);
  }
}

function _toggleLog() {
  const open = logDrawer.classList.contains("collapsed");
  _setLogOpen(open);
  _persistLogOpen(open);
}

logHeader.addEventListener("click", _toggleLog);
logHeader.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    _toggleLog();
  }
});
logToggleBtn.addEventListener("click", _toggleLog);
logCopyBtn.addEventListener("click", async (e) => {
  // Stop the click from bubbling to logHeader which would toggle
  // open/closed when the user only meant to copy.
  e.stopPropagation();
  try {
    await navigator.clipboard.writeText(logBody.textContent);
    logCopyBtn.textContent = "✓";
    setTimeout(() => { logCopyBtn.textContent = "⎘"; }, 1200);
  } catch {
    logCopyBtn.textContent = "✗";
    setTimeout(() => { logCopyBtn.textContent = "⎘"; }, 1200);
  }
});

// Apply the persisted user preference on first paint. If unset,
// stay collapsed — the drawer will auto-expand on the first
// failed build via _handleLogUpdate.
if (_logUserOpen !== null) {
  _setLogOpen(_logUserOpen);
}

// -----------------------------------------------------------------------
// TOC sidebar
// -----------------------------------------------------------------------
//
// Driven by pdf.getOutline(), which surfaces the bookmarks that
// hyperref emits for sectioning commands (section / subsection /
// chapter / etc.). Each entry has a title plus a `dest` reference
// (string for named dests, array for explicit). We resolve the
// dest to a page number via pdf.getDestination + pdf.getPageIndex
// and feed that to jumpToPage.

const sidebar = document.getElementById("sidebar");
const tocEl = document.getElementById("toc");
const sidebarToggleBtn = document.getElementById("sidebar-toggle");
let _outlineEntries = [];  // flat list of {pageNum, link} for current-section tracking

async function _renderOutline(pdf) {
  let outline = null;
  try {
    outline = await pdf.getOutline();
  } catch (err) {
    if (currentDoc === pdf) console.warn("getOutline failed:", err);
  }
  if (currentDoc !== pdf) return;
  if (!outline || outline.length === 0) {
    _outlineEntries = [];
    tocEl.replaceChildren();
    sidebar.hidden = true;
    sidebarToggleBtn.hidden = true;
    return;
  }

  const entries = [];
  const list = await _buildOutlineList(pdf, outline, entries);
  if (currentDoc !== pdf) return;

  _outlineEntries = entries;
  tocEl.replaceChildren(list);
  sidebarToggleBtn.hidden = false;
  // Auto-show sidebar on first render that produces an outline,
  // unless the user has explicitly hidden it (persisted state).
  let userPref;
  try {
    userPref = localStorage.getItem("rules_latex_sidebar");
  } catch {
    userPref = null;
  }
  sidebar.hidden = userPref === "hidden";
  _updateCurrentOutlineEntry();
}

async function _buildOutlineList(pdf, items, entries) {
  const ol = document.createElement("ol");
  for (const item of items) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.textContent = item.title;
    a.href = "#";
    a.addEventListener("click", async (event) => {
      event.preventDefault();
      const pageNum = await _resolveDestPage(pdf, item.dest);
      if (pageNum && currentDoc === pdf) jumpToPage(pageNum);
    });
    // Pre-resolve page numbers without delaying the detached outline tree.
    _resolveDestPage(pdf, item.dest).then((pageNum) => {
      if (!pageNum || currentDoc !== pdf) return;
      a.dataset.pageNumber = String(pageNum);
      entries.push({ pageNum, link: a });
      entries.sort((x, y) => x.pageNum - y.pageNum);
      if (_outlineEntries === entries) _updateCurrentOutlineEntry();
    }).catch(() => {});
    li.appendChild(a);
    if (item.items && item.items.length) {
      li.appendChild(await _buildOutlineList(pdf, item.items, entries));
      if (currentDoc !== pdf) return ol;
    }
    ol.appendChild(li);
  }
  return ol;
}

async function _resolveDestPage(pdf, dest) {
  if (!dest) return null;
  try {
    let explicit = dest;
    if (typeof dest === "string") {
      explicit = await pdf.getDestination(dest);
    }
    if (!explicit || !explicit.length) return null;
    const ref = explicit[0];
    const idx = await pdf.getPageIndex(ref);
    return idx + 1;
  } catch (err) {
    return null;
  }
}

function _updateCurrentOutlineEntry() {
  // Highlight the deepest outline entry whose page <= currentPageNum.
  if (_outlineEntries.length === 0) return;
  let best = null;
  for (const entry of _outlineEntries) {
    if (entry.pageNum <= currentPageNum) best = entry;
    else break;  // sorted, can stop
  }
  for (const e of _outlineEntries) e.link.classList.remove("current");
  if (best) {
    best.link.classList.add("current");
    // Keep the highlighted entry visible in the sidebar if the
    // sidebar's overflowing.
    best.link.scrollIntoView({ block: "nearest" });
  }
}

sidebarToggleBtn.addEventListener("click", () => {
  sidebar.hidden = !sidebar.hidden;
  try {
    localStorage.setItem(
      "rules_latex_sidebar", sidebar.hidden ? "hidden" : "shown",
    );
  } catch {
    /* persistence best-effort */
  }
});

// -----------------------------------------------------------------------
// In-document search
// -----------------------------------------------------------------------
//
// Drives the find bar shown above the viewer (Ctrl+F to open). We
// hydrate missing text layers with bounded concurrency, then walk their
// spans and highlight whole spans whose text contains the query
// case-insensitively. The "current" match
// gets a stronger background and is scrolled into view. Substring-
// level highlighting is a possible polish; whole-span is enough to
// see context.
//
// State survives across renderAllPages: when a new build lands, the
// text layers are recreated and _runSearch re-applies the highlight
// to the new spans so the search experience doesn't reset on
// rebuild.

const searchBar = document.getElementById("search-bar");
const searchInput = document.getElementById("search-input");
const searchCountEl = document.getElementById("search-count");
const searchPrevBtn = document.getElementById("search-prev");
const searchNextBtn = document.getElementById("search-next");
const searchCloseBtn = document.getElementById("search-close");
const searchToggleBtn = document.getElementById("search-toggle");

let _searchQuery = "";
let _searchMatches = [];  // [{ pageNum, span }]
let _searchCurrent = -1;
let _searchRun = 0;

function _openSearch() {
  searchBar.hidden = false;
  searchInput.focus();
  searchInput.select();
}

function _closeSearch() {
  _searchRun += 1;
  searchBar.hidden = true;
  _searchQuery = "";
  for (const m of _searchMatches) {
    m.span.classList.remove("find-match", "find-match-current");
  }
  _searchMatches = [];
  _searchCurrent = -1;
  _updateSearchCount();
}

function _updateSearchCount() {
  if (_searchMatches.length === 0) {
    searchCountEl.textContent = _searchQuery ? "0 / 0" : "";
    searchInput.classList.toggle(
      "no-results", _searchQuery !== "" && _searchMatches.length === 0,
    );
  } else {
    searchCountEl.textContent =
      `${_searchCurrent + 1} / ${_searchMatches.length}`;
    searchInput.classList.remove("no-results");
  }
  searchPrevBtn.disabled = _searchMatches.length === 0;
  searchNextBtn.disabled = _searchMatches.length === 0;
}

function _setSearchCurrent(idx) {
  if (_searchMatches.length === 0) {
    _searchCurrent = -1;
    return;
  }
  // Wrap around so prev from match 0 goes to last, next from last
  // wraps to 0 — matches the UX most viewers ship.
  idx = ((idx % _searchMatches.length) + _searchMatches.length)
        % _searchMatches.length;
  if (_searchCurrent >= 0 && _searchMatches[_searchCurrent]) {
    _searchMatches[_searchCurrent].span.classList.remove("find-match-current");
  }
  _searchCurrent = idx;
  const m = _searchMatches[idx];
  m.span.classList.add("find-match-current");
  m.span.scrollIntoView({ block: "center", behavior: "smooth" });
  _updateSearchCount();
}

async function _hydrateAllTextLayers(pdf, generation, searchRun) {
  const startedAt = performance.now();
  const wraps = [...viewer.querySelectorAll(".page-wrap")];
  wraps.sort((a, b) => {
    const aPage = parseInt(a.dataset.pageNumber, 10);
    const bPage = parseInt(b.dataset.pageNumber, 10);
    return Math.abs(aPage - currentPageNum) -
      Math.abs(bPage - currentPageNum);
  });

  let cursor = 0;
  let completed = 0;
  async function worker() {
    while (cursor < wraps.length) {
      if (
        searchRun !== _searchRun ||
        generation !== _displayGeneration
      ) {
        return;
      }
      const wrap = wraps[cursor];
      cursor += 1;
      await _hydrateTextLayer(pdf, wrap, generation);
      if (
        searchRun !== _searchRun ||
        generation !== _displayGeneration
      ) {
        return;
      }
      completed += 1;
      searchCountEl.textContent =
        "indexing " + completed + "/" + wraps.length;
    }
  }

  const workers = Math.min(2, wraps.length);
  await Promise.all(Array.from({ length: workers }, () => worker()));
  if (
    searchRun !== _searchRun ||
    generation !== _displayGeneration
  ) {
    return false;
  }
  const current = _renderStats.current;
  if (current?.generation === generation) {
    current.searchIndexMs = performance.now() - startedAt;
  }
  return true;
}

async function _runSearch() {
  const searchRun = ++_searchRun;
  // Clear previous highlights, then hydrate and search the current document.
  for (const m of _searchMatches) {
    m.span.classList.remove("find-match", "find-match-current");
  }
  _searchMatches = [];
  _searchCurrent = -1;

  const query = _searchQuery.toLowerCase();
  if (!query) {
    _updateSearchCount();
    return;
  }

  const pdf = currentDoc;
  const generation = _displayGeneration;
  const totalPages = viewer.querySelectorAll(".page-wrap").length;
  searchCountEl.textContent = "indexing 0/" + totalPages;
  searchInput.classList.remove("no-results");
  searchPrevBtn.disabled = true;
  searchNextBtn.disabled = true;

  if (
    !pdf ||
    !await _hydrateAllTextLayers(pdf, generation, searchRun) ||
    searchRun !== _searchRun ||
    query !== _searchQuery.toLowerCase()
  ) {
    return;
  }

  // Walk page-wraps in document order so match indices match visual order.
  const wraps = viewer.querySelectorAll(".page-wrap");
  for (const wrap of wraps) {
    const pageNum = parseInt(wrap.dataset.pageNumber, 10);
    const spans = wrap.querySelectorAll(".text-layer > span");
    for (const span of spans) {
      if (span.textContent.toLowerCase().includes(query)) {
        span.classList.add("find-match");
        _searchMatches.push({ pageNum, span });
      }
    }
  }

  if (_searchMatches.length > 0) {
    _setSearchCurrent(0);
  } else {
    _updateSearchCount();
  }
}

// Debounce typing so we don't run a full DOM walk on every
// keystroke; 80 ms is below the perception threshold but coalesces
// fast bursts.
let _searchDebounce = null;
searchInput.addEventListener("input", () => {
  _searchRun += 1;
  _searchQuery = searchInput.value;
  if (_searchDebounce) clearTimeout(_searchDebounce);
  _searchDebounce = setTimeout(() => { _runSearch(); }, 80);
});

searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    if (e.shiftKey) _setSearchCurrent(_searchCurrent - 1);
    else _setSearchCurrent(_searchCurrent + 1);
  } else if (e.key === "Escape") {
    e.preventDefault();
    _closeSearch();
  }
});

searchPrevBtn.addEventListener("click",
  () => _setSearchCurrent(_searchCurrent - 1));
searchNextBtn.addEventListener("click",
  () => _setSearchCurrent(_searchCurrent + 1));
searchCloseBtn.addEventListener("click", _closeSearch);
searchToggleBtn.addEventListener("click", () => {
  if (searchBar.hidden) _openSearch();
  else _closeSearch();
});

// Ctrl+F / Cmd+F intercept. Captured at the document level so it
// works regardless of focus. We don't preventDefault unless we
// actually open the bar, so users can still use the browser's
// own find if they prefer it from somewhere we don't capture.
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "f") {
    e.preventDefault();
    _openSearch();
  }
});

// -----------------------------------------------------------------------
// Page navigation + zoom controls
// -----------------------------------------------------------------------
//
// Current-page tracking uses IntersectionObserver to find the canvas
// whose intersection with the viewer is largest; we update the
// header counter from that. Jump-to-page is a scrollIntoView on
// the matching canvas (each is tagged with data-page-number).
// Fit-to-width / fit-to-page modes recompute scale from the
// viewer's clientWidth/Height on resize; manual mode (set by
// +/-/0) ignores resize.

let _pageObserver = null;

function _attachPageObserver() {
  if (_pageObserver) _pageObserver.disconnect();
  _pageObserver = new IntersectionObserver((entries) => {
    // Pick the page-wrap with the highest intersectionRatio.
    let bestRatio = 0;
    let bestPage = currentPageNum;
    for (const entry of entries) {
      if (entry.intersectionRatio > bestRatio) {
        bestRatio = entry.intersectionRatio;
        const n = parseInt(entry.target.dataset.pageNumber, 10);
        if (!isNaN(n)) bestPage = n;
      }
    }
    if (bestPage !== currentPageNum) {
      currentPageNum = bestPage;
      _updatePageCounter();
      _updateCurrentOutlineEntry();
    }
  }, { root: viewer, threshold: [0.1, 0.5, 0.9] });
  for (const wrap of viewer.querySelectorAll(".page-wrap")) {
    _pageObserver.observe(wrap);
  }
}

function _updatePageCounter() {
  // Don't overwrite the input while the user is typing in it.
  if (document.activeElement !== pageInput) {
    pageInput.value = String(currentPageNum);
  }
  pagePrevBtn.disabled = currentPageNum <= 1;
  pageNextBtn.disabled =
    currentDoc !== null && currentPageNum >= currentDoc.numPages;
}

function _setTotalPages(n) {
  pageTotalEl.textContent = String(n);
  pageInput.max = String(n);
  _updatePageCounter();
}

function jumpToPage(n) {
  if (!currentDoc) return;
  n = Math.max(1, Math.min(currentDoc.numPages, n | 0));
  const wrap = viewer.querySelector(`.page-wrap[data-page-number="${n}"]`);
  if (wrap) {
    wrap.scrollIntoView({ behavior: "smooth", block: "start" });
    currentPageNum = n;
    _updatePageCounter();
  }
}

pageInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    const n = parseInt(pageInput.value, 10);
    if (!isNaN(n)) jumpToPage(n);
    pageInput.blur();
  } else if (e.key === "Escape") {
    pageInput.blur();
  }
});
pageInput.addEventListener("blur", _updatePageCounter);
pagePrevBtn.addEventListener("click", () => jumpToPage(currentPageNum - 1));
pageNextBtn.addEventListener("click", () => jumpToPage(currentPageNum + 1));

// -- Zoom --

function _updateZoomUI() {
  zoomDisplayEl.textContent = `${Math.round(scale * 100)}%`;
  fitWidthBtn.classList.toggle("active", scaleMode === "fit-width");
  fitPageBtn.classList.toggle("active", scaleMode === "fit-page");
}

function _setManualScale(s) {
  scaleMode = "manual";
  scale = Math.max(0.25, Math.min(4.0, s));
  _updateZoomUI();
  _rerenderCurrentDocument();
}

async function _applyFitMode() {
  await _rerenderCurrentDocument();
}

function _setFitMode(mode) {
  scaleMode = mode;
  _applyFitMode();
}

document.getElementById("zoom-in").addEventListener("click",
  () => _setManualScale(scale * 1.2));
document.getElementById("zoom-out").addEventListener("click",
  () => _setManualScale(scale / 1.2));
document.getElementById("zoom-display").addEventListener("click",
  () => _setManualScale(1.5));
fitWidthBtn.addEventListener("click", () => _setFitMode("fit-width"));
fitPageBtn.addEventListener("click", () => _setFitMode("fit-page"));

// Resize: only re-fit when in a fit mode; manual scale shouldn't
// change underneath the user.
let _resizeTimer = null;
window.addEventListener("resize", () => {
  if (scaleMode === "manual") return;
  // Debounce so we don't thrash renderAllPages during a drag.
  if (_resizeTimer) clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(() => { _applyFitMode(); }, 150);
});

// -- Download --
// The download attribute on the anchor handles most of the work;
// we just keep the filename in sync with the document name.
// (Already set via the template substitution; nothing dynamic
// needed here.)

// -- Fullscreen --
fullscreenBtn.addEventListener("click", () => {
  if (document.fullscreenElement) {
    document.exitFullscreen();
  } else {
    viewer.requestFullscreen().catch(() => {});
  }
});

// -- Keyboard shortcuts --
//
// Skipped entirely if the user is typing in an input/textarea.
// All shortcuts use unmodified keys so they don't conflict with
// browser chrome (Ctrl+F for find lands in PR 2).
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
  switch (e.key) {
    case "PageDown":
      e.preventDefault();
      jumpToPage(currentPageNum + 1);
      break;
    case "PageUp":
      e.preventDefault();
      jumpToPage(currentPageNum - 1);
      break;
    case "Home":
      e.preventDefault();
      jumpToPage(1);
      break;
    case "End":
      e.preventDefault();
      if (currentDoc) jumpToPage(currentDoc.numPages);
      break;
    case "+":
    case "=":
      _setManualScale(scale * 1.2);
      break;
    case "-":
    case "_":
      _setManualScale(scale / 1.2);
      break;
    case "0":
      _setManualScale(1.5);
      break;
    case "w":
      _setFitMode("fit-width");
      break;
    case "p":
      _setFitMode("fit-page");
      break;
    case "f":
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else {
        viewer.requestFullscreen().catch(() => {});
      }
      break;
    case "g":
      e.preventDefault();
      pageInput.focus();
      pageInput.select();
      break;
    case "t":
      _cycleTheme();
      break;
    case "s":
      // Toggle the outline sidebar (when one is available).
      if (!sidebarToggleBtn.hidden) sidebarToggleBtn.click();
      break;
    case "l":
      _toggleLog();
      break;
  }
});

// -- Theme toggle --
//
// Three states: auto (follows system prefers-color-scheme), dark
// (forced dark), light (forced light). Persisted in localStorage so
// the choice survives reloads. `t` cycles between them.
const THEMES = ["auto", "dark", "light"];
const THEME_ICONS = { auto: "⊙", dark: "☾", light: "☀" };
const themeBtn = document.getElementById("theme-toggle");
let _theme;
try {
  _theme = localStorage.getItem("rules_latex_theme") || "auto";
  if (!THEMES.includes(_theme)) _theme = "auto";
} catch {
  // localStorage can throw under restrictive sandbox policies
  // (e.g. data: URLs, file:// with --disable-storage). Fall back
  // to auto and accept that the choice won't persist.
  _theme = "auto";
}

function _applyTheme() {
  if (_theme === "auto") {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", _theme);
  }
  themeBtn.textContent = THEME_ICONS[_theme];
  themeBtn.title = `Theme: ${_theme} (t to cycle)`;
}

function _cycleTheme() {
  const i = THEMES.indexOf(_theme);
  _theme = THEMES[(i + 1) % THEMES.length];
  try {
    localStorage.setItem("rules_latex_theme", _theme);
  } catch {
    // Best-effort persistence; ignore.
  }
  _applyTheme();
}

themeBtn.addEventListener("click", _cycleTheme);
_applyTheme();

renderDocument();
refreshStatus();
_fetchLog();  // populate the drawer with whatever the server has on first paint
// Live-update transport: try WS first; falls back to SSE on
// connection failure. Existing SSE listeners (build-failed,
// reload, jump events) get the same payloads via the WS path
// when it's up.
_startWebSocket();

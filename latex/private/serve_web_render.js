// serve_web_render.js — lazy-paint orchestration for the live-preview
// client, factored out of serve_web.js so the render state machine and
// observer decision can be unit-tested without a real DOM or PDF.js.
// The DOM / PDF.js specifics are injected by serve_web.js.

// A monotonically increasing render generation. Every document load, reload,
// or zoom starts a generation; async work checks its captured token before
// committing so a slower, superseded render cannot replace newer output.
export function newRenderGeneration() {
  let current = 0;
  return {
    begin() {
      current += 1;
      return current;
    },
    isCurrent(generation) {
      return generation === current;
    },
  };
}

// Rasterize one page into its `.page-wrap`, idempotently.
//
// `renderPage(wrap)` performs the actual PDF.js raster and resolves to a
// RenderTask (`{ promise, cancel() }`); it's injected so this logic is
// testable with fakes. `isCurrent()` guards the async gap before PDF.js
// returns its RenderTask as well as task completion.
export async function paintPage(
  wrap,
  renderPage,
  isCurrent = () => true,
) {
  if (wrap.dataset.rendered === "1" || wrap.dataset.rendering === "1") return;
  wrap.dataset.rendering = "1";
  try {
    const task = await renderPage(wrap);
    if (!isCurrent()) {
      try { task.cancel(); } catch { /* best-effort */ }
      return;
    }
    wrap._renderTask = task;
    await task.promise;
    if (isCurrent()) wrap.dataset.rendered = "1";
  } catch (err) {
    // RenderingCancelledException is the expected outcome when a page
    // scrolls out or its render generation is superseded.
    if (!(err && err.name === "RenderingCancelledException")) {
      console.warn("page render failed:", err);
    }
  } finally {
    wrap.dataset.rendering = "";
    wrap._renderTask = null;
  }
}

// Decide what the render observer should do for one IntersectionObserver
// entry: paint a nearby page, cancel work that left the retention margin,
// release a completed far-away backing store, or skip an idle placeholder.
// Pure — the caller performs the side effects.
export function renderObserverAction(entry) {
  if (entry.isIntersecting) return "paint";
  const wrap = entry.target;
  if (wrap.dataset.rendered === "1") return "release";
  if (wrap._renderTask) return "cancel";
  return "skip";
}

// Decide, per page, whether a reload can reuse the previous render's
// `.page-wrap` (and its painted canvas) instead of rebuilding it
// (option B, DESIGN.md §5 #13). Index-based: a page is reused only when
// the same index exists in the previous manifest with an identical
// content hash and geometry. Page insertions/removals shift indices, so
// affected pages re-render — correct, just not optimal. Returns an array
// the length of `newPages` of "reuse" | "render". Pure; the caller
// applies the DOM moves and gates on zoom (scale) separately.
export function planPageReconciliation(oldPages, newPages) {
  if (!Array.isArray(newPages)) return [];
  if (!Array.isArray(oldPages)) return newPages.map(() => "render");
  return newPages.map((np, i) => {
    const op = oldPages[i];
    return (
      op &&
      op.contentHash === np.contentHash &&
      op.width === np.width &&
      op.height === np.height
    )
      ? "reuse"
      : "render";
  });
}

// --- render timing (option 0: measure before going off-thread) ---

// A fresh render-timing aggregate. serve_web.js exposes it on
// window.__serveWebRenderStats so the maintainer can inspect real-document
// raster cost (avg / max / slow-count) and decide whether off-main-thread
// rendering (option 2, DESIGN.md §5 #13) is worth its complexity.
export function newRenderStats() {
  return {
    count: 0,
    totalMs: 0,
    avgMs: 0,
    maxMs: 0,
    slowestPage: null,
    slowCount: 0,
    generations: 0,
    current: null,
  };
}

// Fold one page's raster duration into the aggregate. Pure (mutates and
// returns `stats`); `slowMs` is the per-frame jank threshold above which a
// render is counted as "slow".
export function recordRenderTiming(stats, pageNum, ms, slowMs = 50) {
  stats.count += 1;
  stats.totalMs += ms;
  stats.avgMs = stats.totalMs / stats.count;
  if (ms > stats.maxMs) {
    stats.maxMs = ms;
    stats.slowestPage = pageNum;
  }
  if (ms > slowMs) stats.slowCount += 1;
  return stats;
}

// Fold a PerformanceObserver long-task entry into the active generation.
// Unsupported browsers simply never call this function.
export function recordLongTask(stats, ms) {
  if (!stats.current) return stats;
  stats.current.longTaskCount += 1;
  stats.current.longTaskTotalMs += ms;
  stats.current.longTaskMaxMs = Math.max(stats.current.longTaskMaxMs, ms);
  return stats;
}

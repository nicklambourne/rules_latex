// serve_web_render.js — lazy-paint orchestration for the live-preview
// client, factored out of serve_web.js so the render state machine and
// the observer decision can be unit-tested without a real DOM or PDF.js.
// The DOM / PDF.js specifics are injected by serve_web.js.

// Rasterize one page into its `.page-wrap`, idempotently.
//
// `renderPage(wrap)` performs the actual PDF.js raster and resolves to a
// RenderTask (`{ promise, cancel() }`); it's injected so this logic is
// testable with fakes. A page already painted (`rendered`) or mid-paint
// (`rendering`) is skipped, so an eager current-page paint and the
// IntersectionObserver can't double-render. The in-flight task is stashed
// on the wrap as `_renderTask` so the observer can cancel it if the page
// scrolls out before its raster starts; a cancelled raster leaves the
// page unpainted so it repaints when scrolled to again.
export async function paintPage(wrap, renderPage) {
  if (wrap.dataset.rendered === "1" || wrap.dataset.rendering === "1") return;
  wrap.dataset.rendering = "1";
  try {
    const task = await renderPage(wrap);
    wrap._renderTask = task;
    await task.promise;
    wrap.dataset.rendered = "1";
  } catch (err) {
    // RenderingCancelledException is the expected outcome when a page
    // scrolls out before its paint starts; anything else is a real
    // failure worth surfacing.
    if (!(err && err.name === "RenderingCancelledException")) {
      console.warn("page render failed:", err);
    }
  } finally {
    wrap.dataset.rendering = "";
    wrap._renderTask = null;
  }
}

// Decide what the render observer should do for one IntersectionObserver
// entry: "paint" a page that has come into (or near) view, "cancel" an
// in-flight raster for a page that scrolled out before it started, or
// "skip". Pure — the caller performs the side effects.
export function renderObserverAction(entry) {
  if (entry.isIntersecting) return "paint";
  const wrap = entry.target;
  if (wrap.dataset.rendered !== "1" && wrap._renderTask) return "cancel";
  return "skip";
}

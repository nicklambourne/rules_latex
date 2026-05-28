// serve_web_synctex.js — pure SyncTeX coordinate math for the
// live-preview client.
//
// No DOM or PDF.js imports: the viewport object is passed in, so these
// helpers are unit-tested directly under tests/js/ with `node --test`.
// serve_web.js imports them; the browser resolves the relative path to
// /_assets/serve_web_synctex.js.

// Convert a PDF-point box (origin bottom-left, as SyncTeX reports it)
// into a CSS-pixel rectangle in the page's rendered viewport. Both
// opposite corners are converted independently and normalised, so the
// result is correct regardless of zoom, devicePixelRatio, or the
// PDF→viewport axis flip.
export function pdfBoxToViewportRect(viewport, x, y, w, h) {
  const [vx1, vy1] = viewport.convertToViewportPoint(x, y);
  const [vx2, vy2] = viewport.convertToViewportPoint(x + w, y + h);
  return {
    left: Math.min(vx1, vx2),
    top: Math.min(vy1, vy2),
    width: Math.abs(vx2 - vx1),
    height: Math.abs(vy2 - vy1),
  };
}

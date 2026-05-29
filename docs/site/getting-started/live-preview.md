# Live preview

`rules_latex` ships `latex_live` for the
"edit-the-source-watch-the-PDF-update" workflow. It watches the
document's transitive sources via `LatexInfo` and rebuilds via
`bazel build` on every save, pushing the result to a localhost
HTTP page rendered with PDF.js.

!!! info "`latex_serve` was removed in v0.6.0"
    Earlier releases also exposed a `latex_serve` rule that
    opened the document in the system PDF viewer (macOS
    Preview, Linux Evince/Okular, Adobe Reader, etc.) and
    relied on the viewer's own auto-reload-on-disk-change
    behaviour to refresh the preview after each rebuild. That
    contract eroded — macOS Preview's auto-reload became
    unreliable after the Sonoma sandbox changes, Adobe never
    watched the file on macOS — and v0.6 removed the rule
    rather than ship a viewer-specific workaround. If you
    prefer a native PDF viewer, point a reload-aware one (Skim,
    Sioyek, Zathura, PDF Expert) at
    `bazel-bin/.../<doc>.pdf` directly — `bazel build` keeps
    that path fresh on every save.

## `latex_live` — in-browser preview

Declare a `latex_live` target alongside your `latex_document`:

```python
load("@rules_latex//latex:defs.bzl", "latex_document", "latex_live")

latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    synctex = True,   # PDF clicks copy <file>:<line> to clipboard
)

latex_live(
    name = "cv_live",
    document = ":cv",
)
```

```bash
bazel run //:cv_live
# serving live preview at http://127.0.0.1:8765/
```

Open the URL in your browser. The page:

- Renders the PDF with [PDF.js](https://mozilla.github.io/pdf.js/)
  (vendored, no CDN dependency).
- Listens for "reload" events over Server-Sent Events.
- Preserves scroll position across reloads.
- When `synctex = True` is set on the document, clicking anywhere in
  the rendered PDF resolves to a source `file:line`, displays it in
  the footer bar, and copies it to the clipboard (**reverse-sync
  lookup** — the browser can't drive your editor to that location;
  you paste the location into whatever opens files for you). The
  footer text is itself clickable to recopy.
- Editors can push the other direction (**forward-sync**) by
  POSTing to `/sync/forward`; the browser scrolls the page into view
  and flashes a yellow overlay at the matching PDF location. This
  direction *does* jump because the editor — not the browser — is
  the one driving the navigation. See
  [SyncTeX forward-sync](#synctex-forward-sync) below.

## How fast is the loop?

For a small document (single-page CV, hello-world) paired with a
cache snapshot, steady-state rebuilds complete in **200–400 ms**.
First build is slower (the online prime takes ~30 s) but happens
exactly once per content-hash of the inputs — Bazel's action cache
handles the rest.

For larger documents (multi-chapter thesis, paper with figures), the
TeX compile itself dominates and rebuilds run in 2–5 s.

## What gets watched?

The watcher monitors every `.tex`, `.bib`, image, and other file in
the document's `srcs` plus transitively via `deps`. Edits to the
toolchain binary or the cache snapshot are picked up by Bazel's
analysis layer, so they trigger correct rebuilds too.

External-repo files (e.g. from a `latex_library` published in another
Bazel module) are not watched. Edit those and re-run `bazel run
//:cv_live` to pick up the change.

## SyncTeX forward-sync

When `latex_document(synctex = True)` is set, `latex_live`
exposes a `POST /sync/forward` endpoint that maps a source
`(file, line)` tuple to a PDF location and flashes a highlight in
every open browser tab. The complement to the click-on-PDF
reverse-sync *lookup* the same documents already support — except
this direction *does* jump (the editor, not the browser, drives
the navigation).

The endpoint is the integration point — your editor (or a small CLI
shim) is responsible for invoking it. The minimum viable wrapper is
two lines of `curl`:

```bash
# Jump every connected browser to cv.tex line 42.
curl -sf -X POST http://127.0.0.1:8765/sync/forward \
    -H "Content-Type: application/json" \
    -d '{"file":"cv.tex","line":42}' | jq .
```

Successful response:

```json
{"ok": true, "page": 3, "x": 121.5, "y": 614.2, "w": 156.7, "h": 11.0}
```

The HTTP response is for the caller's logging; the actual UX (scroll
+ flash) happens in the browser tab via an SSE event.

### Editor integrations

Any editor that can shell out on save can wire this up. Examples:

=== "Neovim (Lua)"

    ```lua
    vim.api.nvim_create_autocmd("BufWritePost", {
      pattern = "*.tex",
      callback = function()
        local line = vim.api.nvim_win_get_cursor(0)[1]
        local file = vim.fn.expand("%:t")
        vim.fn.jobstart({
          "curl", "-sf", "-X", "POST",
          "http://127.0.0.1:8765/sync/forward",
          "-H", "Content-Type: application/json",
          "-d", vim.fn.json_encode({file = file, line = line}),
        }, {detach = true})
      end,
    })
    ```

=== "VS Code (tasks.json)"

    Bind a keybinding to a task that runs `curl` with
    `${file}` / `${lineNumber}` substituted.

=== "Emacs (auctex)"

    Hook `LaTeX-after-write-hook` to call
    `(shell-command (format "curl ... '{\"file\":\"%s\",\"line\":%d}'" ...))`.

### Response semantics

| Case | HTTP | Body |
|---|---|---|
| Line has output in the PDF | 200 | `{"ok": true, "page": ..., "x": ..., "y": ..., "w": ..., "h": ...}` |
| Line is recorded in SyncTeX but produced no boxes (e.g. a comment-only line) | 200 | `{"ok": false, "error": "no PDF location found for that source line"}` |
| File isn't in the document's input set | 200 | Same as above |
| SyncTeX file isn't produced yet (the build is still in progress) | 404 | `{"ok": false, "error": "synctex file not produced yet"}` |
| Document was built without `synctex = True` | 404 | `{"ok": false, "error": "synctex not enabled for this document"}` |

When multiple boxes match the same source line (a line that produces
output on more than one PDF page — uncommon, but possible with
two-column layouts and `\twocolumn` breaks), the **first** match is
returned. That's the earliest occurrence in document order, which is
what most editors and users expect for a "jump-to" action.

## Architecture

`latex_live` synthesises a small launcher script that:

1. Polls the watched paths every 250 ms via `os.stat`.
2. Shells out to `bazel build <document_label>` on change.
3. Keeps a tiny HTTP server alive and pushes updates to
   connected browser tabs over WebSocket (see below) with an
   SSE fallback.

The same `bazel build` invocation as a normal build, which means
**live-mode behaviour is identical to CI** — no "works locally,
fails in CI" drift. See
[DESIGN.md §4.7](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md#47-live-preview)
for the rationale.

### WebSocket push transport

`latex_live` exposes `/ws` for live updates. After each
successful rebuild the server pushes the chunk manifest plus any
PDF chunks the connected client doesn't already have. The browser
applies them to its in-memory chunk cache and re-renders.

Wire format:

| Direction | Frame | Payload |
|---|---|---|
| server → client | text | `{"type":"manifest","pdfSize":N,"ranges":[{objectId,start,end,hash},...],"skeletonRanges":[[s,e],...]}` |
| server → client | text | `{"type":"build-failed","message":"…"}` |
| server → client | text | `{"type":"jump",...}` (forward-sync from `POST /sync/forward`) |
| server → client | binary | `<32-byte raw SHA-256><chunk bytes>` (one per missing chunk) |
| client → server | text | `{"type":"hello","have":[<hex sha256>,...]}` (declares cache state on connect) |

Compared to the SSE path (reload event → `/pdf-manifest` fetch →
`/chunk/<hash>` fetch per missing chunk), the WS push saves the
two pull round-trips: the manifest arrives as part of the same
push burst as the chunk bytes.

**Fallback.** If `/ws` can't connect (WS upgrade refused, no
`ws_server` module on the server side, proxy in the way, etc.)
the browser falls back transparently to the SSE flow at
`/events`. The user experience is unchanged; only the
build-to-render latency differs.

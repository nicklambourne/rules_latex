# Design rationale

This page summarises the architectural choices behind `rules_latex`.
The canonical source for design discussion is
[`DESIGN.md`](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md)
in the repo; this page is a friendlier overview.

## Goals

`rules_latex` exists to make Bazel-based LaTeX builds painless. Three
specific goals motivated the design:

1. **Zero per-document boilerplate.** A user dropping a
   `latex_document` into a `BUILD.bazel` shouldn't need to enumerate
   every package their document uses. Existing rule sets that wrap
   TeX Live require this and it's the single biggest source of
   friction.
2. **Modern Bazel hygiene.** Bzlmod from day one; toolchain-based;
   platform constraints via `@platforms`; no legacy WORKSPACE entry
   point.
3. **Hermeticity without misery.** Content-addressed binaries,
   sandboxed actions, repeatable cache snapshots — but with sensible
   defaults so the common case is fast and the hermetic case is just
   one attribute flip away.

## Why Tectonic?

[Tectonic](https://tectonic-typesetting.github.io/) is a modern
TeX/LaTeX engine derived from XeTeX. The key property we care about:
**it resolves `\usepackage` from an external bundle at compile
time**. We don't need to ship a TeX Live distribution; we just need
to ship Tectonic + a content-addressed pin of the bundle.

Compare:

| | bazel_latex (TeX Live) | rules_latex (Tectonic) |
|---|---|---|
| Toolchain artefact | TeX Live distribution (many MB) | Single binary (~20 MB) |
| Package resolution | Per-package Bazel targets | Resolved at compile time |
| First-build cost | TeX Live as needed | ~20 MB tectonic + ~10–100 MB cache |
| Maintenance | Patches against rule internals | Single dependency: tectonic |

## Why the implicit cache pipeline?

The natural first design was just "set the `cache` attribute to a
`latex_cache_snapshot` tarball and commit the tarball". But this
forced users into a four-step workflow (declare snapshot target, run
it, commit, reference it) for every document.

The implicit pipeline removes all four steps. The rule synthesises a
two-action build internally: one online prime, one hermetic compile.
Bazel's action cache makes the prime a one-time cost. Users with
zero awareness of caching just write `latex_document(...)` and get
fast warm builds anyway.

The opt-in `cache = "foo.tar.gz"` path is still there for air-gapped
scenarios.

## Why a vendored biber?

Tectonic's `\addbibresource{...}` directive resolves bibliographies
by shelling out to an external `biber` binary at compile time. Bazel
sandboxes scrub PATH, so a system-installed biber isn't visible.
Three options:

1. Propagate the host PATH into the sandbox (less hermetic).
2. Vendor biber the same way we vendor tectonic.
3. Document the limitation and tell users to install biber themselves.

We picked (2). The biber binary is fetched from a `rules_latex`-owned
GitHub release mirror (because SourceForge only serves predictable
URLs for the `current` release, which makes content-addressed
pinning fragile across upstream bumps).

biber 2.21 is vendored for every supported platform, including
linux/aarch64 (a prebuilt binary from CTAN's `biber-linux-aarch64`
package). The escape hatch (`biber_strategy = "system"`) remains as
option (1) for any platform we don't ship a binary for.

## Why WebSocket *and* Server-Sent Events?

The live-preview server speaks two transports for the "rebuild →
reload" channel:

1. **WebSocket (`/ws`, preferred).** Server pushes the chunk
   manifest plus the chunks the client doesn't already have, in
   a single duplex burst. Saves the round-trip-per-chunk-fetch
   the pull-based flow needs. See
   [Live preview → WebSocket push transport](../getting-started/live-preview.md#websocket-push-transport)
   for the wire format.

2. **Server-Sent Events (`/events`, fallback).** Server emits
   `data: reload\n\n` after each successful build; browser
   fetches `/pdf-manifest` then any missing `/chunk/<hash>` via
   HTTP. Simpler protocol, works through every HTTP proxy that
   doesn't speak `Upgrade`, and stays as a transparent fallback
   if WS can't connect (or if the deployed server doesn't ship
   the WS module — `/ws` then returns 503).

WS is hand-rolled on top of Python stdlib's `http.server`
(`tools/ws_server.py`, ~430 LOC including docstrings). We
specifically don't take a `websockets` PyPI dependency for this
small implementation surface. The hand-roll is the slice of
RFC 6455 the push transport actually uses: handshake, frame
parse/write, ping/pong, fragmentation, close. Things like
`permessage-deflate` and subprotocols are deliberately out of
scope (the chunks we push are already FlateDecode'd PDF object
streams; recompressing them would cost CPU for no win).

See [DESIGN.md §5.7](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md)
for the historical context — this section originally argued
*against* WebSockets, and the threshold for moving was "we'd
actually save round-trips on the hot path." Server-pushed PDF
deltas hit that bar.

## Why self-hosted PDF.js?

`latex_live` v0.1.x fetched PDF.js from cdn.jsdelivr.net at
page-load. v0.2.0 vendors it from a Bazel repository rule fetching
the pinned npm tarball. The motivations:

- Air-gapped live preview works.
- The PDF.js version is content-addressed at build time, matching
  every other dependency.
- No third-party CDN in the critical path.

## Bundle freshness

The upstream `tectonic-texlive-bundles` project was archived in
October 2024, freezing Tectonic's default bundle at TeX Live 2022.
Rather than stay stuck there, `rules_latex` now **rebuilds the bundle
itself** — a TeX Live 2026 `.ttb` self-hosted on Cloudflare R2
(`rules-latex.ndl.au`), pinned by SHA. The whole distribution is
current (biblatex 3.21, biber 2.21, tikz, …), so the package-staleness
and biblatex/biber version-coupling problems are resolved at the root.
The full rationale and the graded options considered are in
[`DESIGN.md` §4.10](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md#410-biberbiblatex-version-coupling-and-the-upstream-bundle-staleness);
tracked in [issue #1](https://github.com/nicklambourne/rules_latex/issues/1).

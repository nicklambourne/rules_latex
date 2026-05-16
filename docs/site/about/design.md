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

The escape hatch (`biber_strategy = "system"`) covers (1) for
platforms where we can't ship a binary (currently linux/aarch64).

## Why Server-Sent Events, not WebSockets?

`latex_serve_web`'s "rebuild → reload page" channel is one-way
(server → browser) and uses
[Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events).
SSE is a simpler protocol than WebSocket — it's just a regular HTTP
response that stays open and streams `data: …\n\n` lines. Python's
stdlib `http.server` handles it trivially.

WebSockets would let the browser talk back (e.g. "I scrolled, please
debounce", or "I clicked at coords X,Y, please resolve via
SyncTeX"). The one duplex feature we wanted (SyncTeX reverse-sync)
is solvable with a `POST /sync/reverse` endpoint that piggybacks on
the existing SSE channel. So WebSockets stay on the open-questions
list, not in v0.2.

See [DESIGN.md §5.7](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md)
for the full discussion.

## Why self-hosted PDF.js?

`latex_serve_web` v0.1.x fetched PDF.js from cdn.jsdelivr.net at
page-load. v0.2.0 vendors it from a Bazel repository rule fetching
the pinned npm tarball. The motivations:

- Air-gapped live preview works.
- The PDF.js version is content-addressed at build time, matching
  every other dependency.
- No third-party CDN in the critical path.

## Open questions

The pinned Tectonic bundle dates from 2022 — the upstream
`tectonic-texlive-bundles` project was archived in October 2024.
Documents using packages or features added after that are stuck.
The five solution options are documented in
[`DESIGN.md` §4.10](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md);
tracked in [issue #1](https://github.com/nicklambourne/rules_latex/issues/1).
For v0.2 we ship with the 2022 stack and document the limitation
clearly.

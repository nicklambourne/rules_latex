# rules_latex — Design

This document captures the goals, non-goals, key design decisions, and
known open questions for `rules_latex`. It is meant to be read alongside the
[README](./README.md), which focuses on user-facing behaviour.

## 1. Goals

1. **Make Bazel-based LaTeX builds painless.** Users should be able to drop a
   `latex_document(...)` into a `BUILD.bazel` and have it Just Work, without
   enumerating which LaTeX packages their document uses or maintaining patches
   against an underlying ruleset.
2. **Modern Bazel hygiene.** Bzlmod from day one; toolchain-based; platform
   constraints handled via `@platforms`; no legacy WORKSPACE entry point.
3. **Hermeticity.** Pinned, content-addressed Tectonic binaries; an offline
   package bundle path for environments that disallow network at build time.
4. **Composability.** Documents can `dep` on libraries (shared preambles,
   class files) and resource packages (images, `.bib` files) without weird
   workarounds.
5. **Small, legible codebase.** Easy to read end-to-end; easy to contribute
   to; easy to fork if Tectonic ever stops being the right backend.

## 2. Non-goals (for v0.1)

- **Wrapping pdfTeX/XeTeX/LuaTeX directly.** Tectonic is the only backend.
  Multi-engine support is a possible future direction but multiplies the
  toolchain surface area.
- **Windows support.** Easy enough to add later — the version pin and
  toolchain extension are structured for it — but not in v0.1.
- **Building Tectonic from source via `rules_rust`.** The official prebuilt
  binaries are sufficient for the 99% case.
- **A `latex_test` rule.** Useful, but out of scope for the first release.
- **Per-document package isolation.** Tectonic resolves packages from a single
  shared bundle; we don't try to virtualise that further.

## 3. Comparison with alternatives

| Aspect                      | `bazel_latex`                              | `rules_latex` (this repo)              |
|-----------------------------|--------------------------------------------|----------------------------------------|
| Backend                     | TeX Live (full distribution)               | Tectonic (XeTeX + custom driver)       |
| Package management          | Explicit Bazel labels per `.sty`           | Implicit, by Tectonic at compile time  |
| Hermeticity                 | Strong (sandboxed TeX Live)                | Strong with offline bundle; opt-in     |
| WORKSPACE / Bzlmod          | Both, leans WORKSPACE                      | Bzlmod-only                            |
| First build cost            | Many MB of TeX Live fetched as needed      | ~20 MB tectonic binary, ~few MB bundle |
| Maintenance burden          | Patches needed against rule internals      | Single dependency: tectonic            |

## 4. Architecture

### 4.1 Public API surface

Loaded from `@rules_latex//latex:defs.bzl`:

- `latex_document(name, main, srcs, deps = [], format = "pdf", tectonic_args = [])`
- `latex_library(name, srcs, deps = [])`
- `latex_pkg(name, srcs)`
- `LatexInfo` provider (for users authoring their own rules)

The toolchain type is exported at `@rules_latex//latex:toolchain_type` for
custom toolchain registrations.

### 4.2 Toolchain model

A single `toolchain_type` (`//latex/toolchain:toolchain_type`) is consumed by
every rule that invokes Tectonic.

A `latex_toolchain` rule packages two attributes:

| Field    | Description                                                           |
|----------|-----------------------------------------------------------------------|
| `tectonic` | The Tectonic executable for the target platform.                    |
| `bundle`   | Optional offline package bundle (`.tar`). If set, the rule passes `--bundle <path>` and Tectonic runs with no network access. |

A `tectonic` module extension (`@rules_latex//latex/toolchain:extensions.bzl`)
materialises one `tectonic_repository` per supported platform and a single
`rules_latex_tectonic_toolchains` "hub" repository that registers a
`toolchain(...)` for each one, gated by `exec_compatible_with` / `target_compatible_with`.

The pinned Tectonic version and per-platform SHA256 hashes live in
[`latex/private/versions.bzl`](./latex/private/versions.bzl).

### 4.3 Action model

`latex_document` produces one output file (the PDF, by default).
Internally it invokes:

    tectonic -X compile \
        --outfmt <pdf|html|xdv|aux> \
        --outdir <bazel-out-dir> \
        [--bundle <bundle.tar> --only-cached] \
        --keep-logs \
        [user-supplied tectonic_args ...] \
        <main.tex>

Sources are gathered transitively from `srcs` plus every `LatexInfo` provider
exposed by `deps`. The bundle, if present, is an action input so it
participates in Bazel's content-based caching. When a bundle is supplied we
also pass `--only-cached`, which causes Tectonic to refuse any network
access.

### 4.4 Network policy

By default, Tectonic fetches its package bundle on first run from
`relay.fullyjustified.net`. This is convenient but non-hermetic and a single
point of failure.

`rules_latex` plans two modes:

1. **Online mode (default in v0.1).** No `bundle` attribute on the
   toolchain; Tectonic reaches out to fetch packages, caching them in
   `XDG_CACHE_HOME`. Documented as "fine for local dev, not for CI".
2. **Offline mode (preferred for CI / publishing).** A separate repository
   rule (`tectonic_bundle`) http_archives a pinned bundle tarball and feeds
   it to the toolchain's `bundle` attribute. The `TECTONIC_OFFLINE=1`
   environment variable in the compile action enforces no-network behaviour.

Wiring up the canonical pinned bundle URL + SHA in `versions.bzl` is tracked
as a v0.1 release blocker.

## 5. Open questions / future work

These are deliberately out of scope for v0.1 but worth flagging.

1. **Bundle pinning.** We need a stable, versioned bundle URL with a known
   SHA256. The upstream Tectonic project moved bundle hosting around in
   2022–2023; the current canonical entrypoint is
   `relay.fullyjustified.net/default_bundle.tar`, which transparently
   redirects to a versioned object. We should either:
   - Mirror a known-good bundle into a `rules_latex_bundles` GitHub release
     and pin from there, or
   - Compute the SHA from a fetched copy and pin the redirect target URL
     directly.
   Decision needed before v0.1 ships.
2. **Tectonic v2 workspace mode.** Tectonic v2 introduced a project format
   with `Tectonic.toml`. Worth supporting eventually, but the simpler
   `-X compile <main.tex>` invocation is enough for v0.1.
3. **`biber` / `bibtex` / `makeindex` toolchain attrs.** Tectonic vendors
   these internally, but advanced workflows may want to swap them. Add as
   optional fields on `latex_toolchain` later if there's demand.
4. **`latex_test`.** A rule that compiles a document and asserts on the
   log/aux for warnings, missing references, overfull boxes, etc. Useful for
   thesis-style documents.
5. **`latex_lint`.** Wraps `chktex` / `lacheck`. Could ship as an optional
   toolchain.
6. **Windows support.** Add `windows_x86_64` to the platform list; arrange
   for `tectonic.exe` extraction from the `.zip` artifact.
7. **Caching of intermediate aux files.** Tectonic is fast and Bazel caches
   the action output, so this is probably never worth doing — but worth
   benchmarking on multi-pass documents (e.g. with biblatex).
8. **Reproducibility of PDF output.** PDFs embed timestamps by default. We
   should consider passing `SOURCE_DATE_EPOCH=0` or `--reproducible` flags
   to Tectonic where supported, so identical inputs produce byte-identical
   PDFs.

## 6. Versioning

`rules_latex` will follow semver post-1.0. Pre-1.0 releases (v0.x) can break
API freely; expect every rule to potentially change shape.

## 7. Release process (target state)

- Tag `vX.Y.Z` on `master`.
- A GitHub Action creates a release archive and computes its integrity hash.
- A second action opens a PR against
  [`bazelbuild/bazel-central-registry`](https://github.com/bazelbuild/bazel-central-registry)
  to publish the new version.

Not implemented yet — to be set up before v0.1.0.

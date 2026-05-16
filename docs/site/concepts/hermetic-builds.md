# Hermetic builds

`rules_latex` supports four offline-mode strategies. The rule chooses
automatically based on what you've configured, in priority order:

## 1. Per-document checked-in cache snapshot

**When to use:** air-gapped CI, byte-reproducible release builds.

```python
load("@rules_latex//latex:defs.bzl", "latex_cache_snapshot", "latex_document")

latex_cache_snapshot(
    name = "cv_snapshot",
    main = "cv.tex",
    srcs = ["cv.tex"],
    output = "cv_cache.tar.gz",
)

latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
    cache = "cv_cache.tar.gz",
)
```

Run once with internet to populate:

```bash
bazel run //:cv_snapshot
```

This produces a ~10–100 MB tarball that you check into the
repository. Every subsequent build (local, CI, anywhere) extracts the
snapshot, runs `tectonic --only-cached`, and never touches the
network.

Re-run the snapshot only when you add new `\usepackage` lines.

## 2. Full bundle

**When to use:** monorepos with many documents sharing most packages;
when you'd rather pay a one-time 3 GB fetch than per-document primes.

```python
# MODULE.bazel
tectonic.bundle()
```

`tectonic_bundle_repository` http-fetches the pinned 2.88 GB bundle.
Every `latex_document` runs `tectonic --bundle <path> --only-cached`.

## 3. Implicit cache pipeline (default)

**When to use:** every other situation. This is what you get if you
don't configure (1) or (2).

```python
latex_document(
    name = "cv",
    main = "cv.tex",
    srcs = ["cv.tex"],
)
```

The rule synthesises two actions:

1. **`TectonicPopulateCache`** runs tectonic once in online mode
   against the document's sources, captures the resulting tectonic
   cache directory as a deterministic `tar.gz`, and emits it as a
   Bazel-declared output. The action is marked
   `requires-network = "1"` and content-addressed by `.tex` sources
   × tectonic toolchain version × bundle URL.
2. **`TectonicCompile`** consumes that tarball as an action input,
   extracts it into `$TECTONIC_CACHE_DIR`, and runs tectonic with
   `--only-cached`.

Because `PopulateCache` is content-addressed, Bazel's action cache
makes it a **one-time cost per (sources × tectonic × bundle URL)
tuple**. Subsequent builds:

- with unchanged sources → both actions hit the cache → ~120ms
- with changed `.tex` content → `TectonicCompile` re-runs → ~3-5s
- with a new `\usepackage` → both actions re-run → ~30-80s once

CI runs share warm caches via the remote cache, so a typical CI run
spends near-zero time on the prime.

## 4. Pure online (legacy)

Not currently exposed. The implicit pipeline (3) is strictly better
unless you have a niche reason to want to skip the action-cache
layer.

## Priority

When multiple modes are configured, precedence is:

```
cache attribute (1) > tectonic.bundle() (2) > implicit pipeline (3)
```

You can mix freely across a workspace: some documents with checked-in
snapshots, others using the implicit pipeline, others using the full
bundle. Each `latex_document` resolves independently.

## Hermeticity claims

In all four modes:

- The Tectonic binary, biber binary, and (when used) bundle are
  content-addressed by SHA-256 and fetched via Bazel repository
  rules.
- Compile actions run in Bazel's sandbox with a scrubbed environment.
- The action's only inputs are declared in the build graph.

The only operation that touches the network is the `PopulateCache`
action in mode (3), and that's marked `requires-network = "1"` so a
sandbox configured to refuse network access will refuse it correctly.
After that one prime, modes (1) and (3) are indistinguishable from
the engine's perspective.

See [DESIGN.md §4.4](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md#44-network-policy)
for the architectural rationale.

# Caching

`rules_latex` has two distinct caching layers, easily confused. This
page is the canonical reference for both.

## Tectonic's internal cache

When you compile a LaTeX document, Tectonic resolves
`\usepackage{...}` directives from a **bundle** — a single tar
archive containing a curated subset of TeX Live (~3 GB). On first
use of any given package, Tectonic copies its files from the bundle
into a **per-user cache directory** so subsequent compiles can
proceed offline.

That cache is what `rules_latex` plumbs around. Most of the
[hermetic build modes](hermetic-builds.md) are different strategies
for pre-populating that cache.

The default location is platform-specific (`~/.cache/Tectonic` on
Linux, `~/Library/Caches/Tectonic` on macOS). Inside a Bazel sandbox,
where `$HOME` is unset, we override it to a per-action `mktemp -d`
scratch directory and pre-populate it with the bundle contents
specific to this build.

## Bazel's action cache

Bazel itself caches the outputs of every action by content-hashing
the action's inputs. This is the layer that makes the
**implicit cache pipeline** (mode 3 in [hermetic
builds](hermetic-builds.md)) practical: the online prime action's
content-hash is stable as long as the `.tex` sources, the tectonic
binary, and the bundle URL don't change.

When you run `bazel build //:cv` for the second time with unchanged
sources, neither the prime action nor the compile action actually
runs — Bazel notices the inputs are identical to a previous build and
just copies the cached outputs out.

When you share a remote cache (e.g. across a CI fleet), every machine
gets the same benefit. The 30-second online prime happens once across
your entire team.

## How the two interact

A typical "compile a document" build executes two actions, in order:

```
TectonicPopulateCache  (online, content-addressed)
    │  inputs:  .tex sources × tectonic binary × bundle URL
    │  output:  _<name>_implicit_cache.tar.gz  (~10-100 MB)
    ▼
TectonicCompile        (offline, --only-cached)
    │  inputs:  .tex sources × tectonic binary × implicit_cache.tar.gz
    │  outputs: <name>.pdf, optionally <name>.synctex.gz
```

Both are Bazel actions; both are cached.

The implicit cache *content* lives inside Bazel's action cache. The
tectonic cache *layout* (the directory structure inside the tarball)
is what's checked into your repo when you opt into a manual
`latex_cache_snapshot`.

## When to invalidate

| Change                          | What re-runs |
|---------------------------------|---------------------|
| Edit a sentence in `cv.tex`     | `TectonicCompile` only (~3-5s) |
| Add a new `\usepackage` line    | Both actions (one-time prime, ~30-80s) |
| Bump rules_latex version        | Both actions (different tectonic binary) |
| Move the document to a new dir  | Both actions (paths feed into the action key) |

For the manual snapshot path (mode 1 in [hermetic
builds](hermetic-builds.md)), the same trigger ("new `\usepackage`")
means you need to re-run `bazel run //:cv_snapshot` and commit the
new tarball. Otherwise the document will fail to compile with a
missing-file error.

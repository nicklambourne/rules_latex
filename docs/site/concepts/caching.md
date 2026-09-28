# Caching

`rules_latex` has two distinct caching layers, easily confused. This
page is the canonical reference for both.

## Tectonic's internal cache

When you compile a LaTeX document, Tectonic resolves
`\usepackage{...}` directives from a **bundle** — a single tar
archive containing a curated subset of TeX Live (~1.78 GiB). On first
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
action key includes the source contents, toolchain, bundle URL, compile
arguments, CTAN package list, and optional CTAN lock file. Unchanged declared
inputs can reuse a prior result; changed bytes at an unlocked upstream URL
are not themselves part of that key.

When you run `bazel build //:cv` for the second time with unchanged
sources, neither the prime action nor the compile action actually
runs — Bazel notices the inputs are identical to a previous build and
just copies the cached outputs out.

Normal builds can share these outputs through a remote cache when their
action keys and execution configuration match. Live preview's persistent
cache override is different: its compile actions use a host-local directory
and are excluded from remote execution and remote cache reuse.

## How the two interact

A typical "compile a document" build executes two actions, in order:

```
TectonicPopulateCache  (online, content-addressed)
    │  inputs:  sources × toolchain × bundle URL × arguments × CTAN list/lock
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

For a normal build using the implicit pipeline (assuming no matching action
cache entry already exists):

| Change                          | What is invalidated |
|---------------------------------|---------------------|
| Edit a sentence in `cv.tex`     | The prime; compilation also depends on the edited source |
| Add a new `\usepackage` line    | The prime and compilation |
| Add/remove a `ctan_packages` entry | Both actions (re-fetches CTAN packages during prime) |
| Change `ctan_lock` contents     | The prime; compilation consumes the resulting snapshot |
| Update rules_latex or a toolchain | Actions whose tools, inputs, or command lines change; a version bump alone need not invalidate everything |
| Move the document to a new dir  | Both actions (paths feed into the action key) |

The prime uses the full source contents, not just package directives. Live
preview avoids repeating that prime for ordinary edits by reusing a persistent
cache. A missing cached resource triggers re-priming; toolchain, package-list,
or lock changes require restarting preview and select a new configuration key.
Re-priming publishes an immutable extraction and changes the compile action's
generation key. Old extractions remain available to in-flight compiles; they
are distinct from the bounded PDF history described in
[Live preview](../getting-started/live-preview.md#websocket-push-transport).

For the manual snapshot path (mode 1 in [hermetic
builds](hermetic-builds.md)), the same trigger ("new `\usepackage`")
means you need to re-run `bazel run //:cv_snapshot` and commit the
new tarball. Otherwise the document will fail to compile with a
missing-file error.

## Snapshot tarball structure

A `latex_cache_snapshot` tarball has one of two layouts depending on
whether the document declares
[`ctan_packages`](../getting-started/ctan-packages.md):

=== "Without `ctan_packages` (legacy / common)"

    The tarball is a flat dump of the tectonic cache directory:

    ```
    cv_cache.tar.gz
    └── (tectonic-cache files at root: hash-named files, manifests, ...)
    ```

    `TectonicCompile` extracts straight into `$TECTONIC_CACHE_DIR`
    and runs `tectonic --only-cached`.

=== "With `ctan_packages` (structured)"

    The tarball wraps two parallel trees:

    ```
    thesis_cache.tar.gz
    ├── cache/        ← tectonic's bundle cache (what flat-format used to be)
    └── ctan_pkgs/    ← extracted TDS overlay (tex/latex/biblatex/, etc.)
    ```

    `TectonicCompile` detects this structure, extracts each subtree,
    sets `TECTONIC_CACHE_DIR` to `cache/`, and passes one
    `-Z search-path=<directory>` argument for every directory under
    `ctan_pkgs/` that contains package files. Tectonic does not use
    kpathsea or honour `TEXMFHOME`; the explicit search paths make the
    CTAN overlay available ahead of bundle resolution.

The compile-time format detection is purely structural (does the
tarball have `cache/` and `ctan_pkgs/` at the root?), so legacy
snapshots from older `rules_latex` versions continue to work
unchanged — there's no on-disk migration.

# CTAN packages

The bundle ships a curated subset of **TeX Live 2026** (self-hosted;
see [DESIGN.md §4.10][design-410]). It covers ~95% of real-world LaTeX
documents, but some packages — bleeding-edge releases, niche contrib
or font packages — aren't there.

[design-410]: https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md#410-biberbiblatex-version-coupling-and-the-upstream-bundle-staleness

The `ctan_packages` attribute lets you fetch missing packages
directly from CTAN at build time, in [TDS][tds] format, with no extra
target boilerplate.

```python
load("@rules_latex//latex:defs.bzl", "latex_document")

latex_document(
    name = "thesis",
    main = "thesis.tex",
    srcs = ["thesis.tex", "references.bib"],
    ctan_packages = ["biblatex-apa"],   # extension style, not in the bundle
    biber = True,
)
```

That's the entire API surface: list package names, get the packages.

[tds]: https://tug.org/tds/

## When you need this

Reach for `ctan_packages` when a build fails with a missing-file
error like:

```
! LaTeX Error: File `tcolorbox.sty' not found.
```

and the missing file is a self-contained CTAN package that isn't in
the bundle.

The realistic use cases:

- **Niche contrib packages** that the curated bundle excluded —
  utility macros, specialised drawing tools, domain-specific styles.
- **Active-development packages** like recent `tcolorbox` or
  `pgfplots` releases — as long as the new version doesn't depend
  on engine or core-package features the bundle lacks.

For limitations (biblatex extension styles in particular), see the
[Limitations](#limitations) section below.

## When you *don't* need this

For most documents, you don't need `ctan_packages` at all. Tectonic's
bundle already contains:

- The LaTeX core and standard `article` / `report` / `book` classes.
- `amsmath`, `amssymb`, `amsthm`, `mathtools`.
- `graphicx`, `xcolor`, `hyperref`, `geometry`, `fancyhdr`, `tikz`.
- `biblatex` 3.21, `biber` 2.21, and the standard citation styles
  (numeric, alphabetic, authoryear, authortitle, verbose).
- `babel`, `polyglossia`, `csquotes`, `microtype`.
- The `lipsum`, `blindtext`, `booktabs`, `tabularx` ecosystem.
- All standard fonts (Computer Modern, Latin Modern, TeX Gyre).

If your `\usepackage{...}` lines all resolve cleanly without
`ctan_packages`, **leave it empty**. Adding packages you don't need
just makes your first build slower and your cache snapshots larger.

## Example

Pick any leaf CTAN package the bundle doesn't ship; the API is the
same regardless of which one. Suppose your document needs the
`my-niche-pkg` styling package (substitute the real one you need):

=== "BUILD.bazel"

    ```python
    load("@rules_latex//latex:defs.bzl", "latex_document")

    latex_document(
        name = "thesis",
        main = "thesis.tex",
        srcs = ["thesis.tex"],
        ctan_packages = ["my-niche-pkg"],
    )
    ```

=== "thesis.tex"

    ```latex
    \documentclass{article}
    \usepackage{my-niche-pkg}

    \begin{document}
    ...
    \end{document}
    ```

```bash
bazel build //:thesis
```

The first build downloads `my-niche-pkg.zip` from CTAN, extracts it
into a TDS overlay, and runs tectonic with `-Z search-path` flags
pointing at the overlay so the fetched `.sty` files are discoverable.
Subsequent builds skip the download (Bazel action cache).

## How it works

`ctan_packages` plugs into the existing
[implicit cache pipeline](../concepts/hermetic-builds.md#3-implicit-cache-pipeline-default).
The pipeline already runs `tectonic` once online to populate a
per-document cache; we use that same online step to pull in the CTAN
packages, then bundle them alongside the tectonic cache for offline
reuse.

```
TectonicPopulateCache  (online, network OK)
  1. Download each ctan_packages entry from mirrors.ctan.org
     (TDS .zip first, then raw .zip fallback). Walk the scanned
     dep graph to fetch transitive deps too.
  2. Normalise into a TDS overlay under ctan_pkgs/.
  3. Invoke tectonic with one `-Z search-path=<dir>` flag per
     directory in the overlay that holds package files. Tectonic
     prefers cwd > search-path > bundle, so fetched packages
     overlay the bundle without shadowing it for documents that
     don't list them.
  4. Emit a structured tarball:
        cache.tar.gz
        ├── cache/        ← tectonic's bundle cache
        └── ctan_pkgs/    ← extracted TDS overlay
        ▼
TectonicCompile        (offline, --only-cached)
  1. Extract cache.tar.gz.
  2. Run `tectonic --only-cached` with the same `-Z search-path`
     flags so the offline compile sees the same overlay.
```

Tectonic's resolver does **not** use kpathsea or honour `TEXMFHOME`.
Search paths come through `-Z search-path=<dir>` flags, which are
*flat* (no recursive descent) — so we pass one flag per overlay
directory holding `.sty` / `.cls` / `.bbx` / `.cbx` / `.lbx` / `.dbx`
files. The ordering is cwd → each search-path → bundle; the first
match wins.

## Source resolution

CTAN package names map to a few different URL patterns. The fetcher
tries them in order:

1. `mirrors.ctan.org/install/macros/latex/contrib/<pkg>.tds.zip`
   — when CTAN ships a pre-built TDS zip.
2. `mirrors.ctan.org/macros/latex/contrib/<pkg>.zip`
   — the source zip.
3. `mirrors.ctan.org/macros/latex/contrib/biblatex-contrib/<pkg>.zip`
   — biblatex extension styles live in a sub-directory.

If none of those resolve, the build fails with a clear message
listing what was tried. Most users won't have to think about this:
CTAN package names are stable, and the fallbacks cover the common
naming conventions.

### Retries and mirror overrides

CTAN's mirror network is best-effort, and individual mirrors
occasionally time out or 5xx. The fetcher retries each URL up to
three times with exponential backoff (1 s, 2 s, 4 s) on transient
errors (connection timeouts, DNS hiccups, 5xx responses). 4xx
responses propagate immediately — those are "the file isn't there",
and the next URL in the fallback list is tried instead.

If you're behind a corporate firewall, on an air-gapped network, or
want to pin against a specific mirror for reproducibility, set
`RULES_LATEX_CTAN_MIRROR`:

```bash
bazel build //:thesis \
    --action_env=RULES_LATEX_CTAN_MIRROR=https://mirror.your-org.com/CTAN
```

The value is used as a URL prefix in place of `https://mirrors.ctan.org`.
The same env var is what CI uses to point at a local fixture server
(see `tests/ctan/fixtures/`) and avoid depending on real CTAN
availability for the integration tests.

## Transitive dependencies

You only need to list the **entry-point** packages your document
actually `\usepackage{}`s. If a fetched package transitively requires
another package outside the bundle, the populate step walks the
dependency graph and auto-fetches what's needed:

```python
latex_document(
    name = "thesis",
    main = "thesis.tex",
    srcs = [...],
    ctan_packages = ["biblatex-apa"],   # that's it
)
```

Even if `biblatex-apa` pulls in further packages outside the bundle,
you don't list them. The populate step scans each fetched package's
source files for `\RequirePackage` / `\usepackage` / `\LoadClass`
references, filters out anything already in Tectonic's bundle (using
the shipped [bundle
manifest](https://github.com/nicklambourne/rules_latex/blob/master/latex/toolchain/bundle_manifest.txt)),
HEAD-probes CTAN for the rest, and fetches the closure. A single
compile pass; no manual iteration loop.

**You'll see the dep map at build time** so the auto-resolution is
transparent:

```
ctan_packages dep map:
  my-niche-pkg -> etoolbox, xcolor
```

(`etoolbox` and `xcolor` are in the bundle, so they're listed but
not fetched. Anything *not* in the bundle gets HEAD-probed against
CTAN and pulled in if found.)

**When auto-resolution can't help.** If the missing file isn't
referenced by any fetched package's source, or isn't actually on
CTAN, the existing failure-path hint kicks in with the same three
cases as before (already-listed, referenced-but-missing, or
unknown — possibly a typo in `.tex`). See "Failure hints" below.

**Why the bundle manifest matters.** The bundle ships specific pinned
versions of common packages (`biblatex` 3.21, paired with `biber` 2.21
— see [DESIGN.md §4.10][design-410]). If the auto-resolver fetched
newer CTAN versions of these and shadowed the bundle, the version
coupling would break (biber 2.21 can't read a newer biblatex's control
files). The manifest filter is how we avoid that.

The manifest is generated by `tools/extract_bundle_manifest.py` from
the bundle's `.ttb.index.gz` file listing, and refreshed when the
pinned bundle version (`DEFAULT_BUNDLE`) changes — a maintainer-only
step.

### Failure hints

When the populate step fails despite auto-resolution (which means
the auto-resolver couldn't find a referenced name on CTAN, or there
was a name typo in `.tex`), you'll see a targeted hint:

```
! LaTeX Error: File `foo.sty' not found.
tectonic exited with code 1; see log in /sandbox/.../ for details.

hint: 'foo' isn't in the bundle and isn't referenced
by any of your ctan_packages. If 'foo' is a CTAN package, add it
to ctan_packages on this target. Otherwise check for a typo in
your .tex sources.
```

If `foo` is referenced by one of your fetched packages but the
HEAD-probe couldn't reach CTAN (transient network), the hint
names the requiring package — adding `foo` to `ctan_packages`
explicitly bypasses the probe filter on the next run.

## Modern biblatex extension styles

`ctan_packages = ["biblatex-apa"]` (or `biblatex-chicago`,
`biblatex-ieee`, `biblatex-nature`, …) **just works** — list the
style package, set `biber = True`, done:

```python
latex_document(
    name = "thesis",
    main = "thesis.tex",
    srcs = ["thesis.tex", "references.bib"],
    ctan_packages = ["biblatex-apa"],
    biber = True,
)
```

The version-coupling chain (DESIGN.md §4.10) that used to require an
opt-in is gone: modern extension styles need `biblatex 3.18+` /
`biber 2.18+`, and the self-hosted TeX Live 2026 bundle ships
**biblatex 3.21** + **biber 2.21**. The style files load against the
bundle's own biblatex; no overlay, no toolchain flag.

!!! note "Upgrading from ≤ v0.5"
    These styles previously required
    `tectonic.toolchain(modern_biblatex = True)`, which overlaid a
    CTAN biblatex + a vendored biber on top of the frozen 2022 bundle
    via `-Z search-path`. That opt-in was **removed in v0.6.0** when
    the bundle was rebuilt on TeX Live 2026. Delete the argument from
    your `MODULE.bazel`.

### Shadowing risk for bundle packages

The auto-resolver filters transitive references against the [bundle
manifest](https://github.com/nicklambourne/rules_latex/blob/master/latex/toolchain/bundle_manifest.txt)
so it never re-fetches a bundle-resident package and shadows the
bundle's version via `-Z search-path`. But if you explicitly list a
bundle-resident package in `ctan_packages` (a *seed*), the resolver
fetches it anyway — and that newer CTAN copy then shadows the bundle,
which can reintroduce a version-coupling break (e.g. a CTAN biblatex
newer than the bundle's 3.21 that biber 2.21 can't read). Don't list
bundle-resident packages unless you've verified the CTAN version is
compatible.

## Hermeticity and reproducibility

CTAN is a mutable mirror network. The Bazel action cache key for
`TectonicPopulateCache` includes the `ctan_packages` list (as
strings), but **not the content of the downloaded packages**. If
upstream updates a package, you might keep getting the old version
from your action cache until you `bazel clean`.

For development this is usually what you want — fast, "good enough"
builds. For production (CI, paper submissions, archival) you have
two options:

### Option 1: Pin via cache snapshot (recommended)

Once the document compiles cleanly, capture a snapshot:

```python
load("@rules_latex//latex:defs.bzl", "latex_cache_snapshot")

latex_cache_snapshot(
    name = "thesis_cache",
    main = "thesis.tex",
    srcs = ["thesis.tex", "references.bib"],
    ctan_packages = ["biblatex-apa"],
    output = "thesis_cache.tar.gz",
    biber = True,
)
```

```bash
bazel run //:thesis_cache
```

Commit `thesis_cache.tar.gz`, then point your document at it:

```python
latex_document(
    name = "thesis",
    main = "thesis.tex",
    srcs = ["thesis.tex", "references.bib"],
    ctan_packages = ["biblatex-apa"],   # documentation; the snapshot is what matters
    cache = "thesis_cache.tar.gz",
    biber = True,
)
```

The snapshot bundles both the tectonic cache *and* the extracted
CTAN packages, so subsequent builds are fully offline and frozen at
the package versions captured when you ran the snapshot.

### Option 2: Tolerate drift

If you don't need bit-for-bit reproducibility, just don't add `cache`
and let CTAN updates flow through whenever you `bazel clean`. This
matches how most package managers (pip, npm) treat unpinned
dependencies.

## Bundle mode is incompatible

`ctan_packages` works with all offline modes **except** the
toolchain-level `tectonic.bundle()`. Bundle mode skips the
`PopulateCache` action entirely and runs tectonic with `--bundle
<path>`, so there's no online step in which to fetch CTAN packages.
The rule fails at analysis time with:

```
latex_document(ctan_packages = ...) on //:thesis is incompatible with
the toolchain-level bundle. ctan_packages requires the implicit cache
pipeline (default) or a cache snapshot generated with matching
ctan_packages. See DESIGN.md for details.
```

If you need both, generate a per-document snapshot via Option 1
above. Snapshots work everywhere bundle mode does and don't share
the limitation.

## Comparison with alternatives

| Approach | What you write | When to use |
|---|---|---|
| **`ctan_packages`** (this) | `ctan_packages = ["foo"]` | The package is on CTAN and you want it. |
| **Vendor `.sty` files** | `latex_library(srcs = [".../foo.sty"])` | The package isn't on CTAN, or you've patched it. |
| **`tectonic.bundle()`** | Module extension | You want the whole bundle downloaded once for fully-offline builds. |
| **Wait for upstream** | Nothing | You don't need newer packages right now. |

`ctan_packages` is purely additive: documents without it keep
working unchanged, the bundle-only mode keeps working unchanged, and
adding it to one document doesn't affect any other document in the
workspace.

## See also

- [Bibliography](bibliography.md) — for the biber wiring that
  `biblatex-apa` and friends depend on.
- [Hermetic builds](../concepts/hermetic-builds.md) — for how
  `ctan_packages` interacts with each offline-mode strategy.
- [`examples/ctan_paper/`][ex] — a complete worked example.

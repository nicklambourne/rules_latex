# CTAN packages

Tectonic's bundle ships a curated subset of TeX Live frozen at
**TeX Live 2022**. It covers ~95% of real-world LaTeX documents, but
some packages — newer biblatex citation styles, recently-published
contrib packages, niche font packages — aren't there.

The `ctan_packages` attribute lets you fetch missing packages
directly from CTAN at build time, in [TDS][tds] format, with no extra
target boilerplate.

```python
load("@rules_latex//latex:defs.bzl", "latex_document")

latex_document(
    name = "thesis",
    main = "thesis.tex",
    srcs = ["thesis.tex", "references.bib"],
    ctan_packages = ["biblatex-apa"],   # not in the 2022 bundle
    biber = True,
)
```

That's the entire API surface: list package names, get the packages.

[tds]: https://tug.org/tds/

## When you need this

Reach for `ctan_packages` when a build fails with a missing-file
error like:

```
error: biblatex.sty:15894: Package biblatex Error: Style 'apa' not found.
```

or

```
! LaTeX Error: File `tcolorbox.sty' not found.
```

and the missing file is a CTAN package newer than 2022.

The most common cases:

- **Modern biblatex citation styles** — `biblatex-apa`, `biblatex-chicago`,
  `biblatex-ieee`, `biblatex-nature`, …
- **Active-development contrib packages** — `tcolorbox`, `pgfplots`,
  `siunitx` releases newer than 2022.
- **Niche or domain-specific packages** that the curated 2022 bundle
  excluded.

## When you *don't* need this

For most documents, you don't need `ctan_packages` at all. Tectonic's
bundle already contains:

- The LaTeX core and standard `article` / `report` / `book` classes.
- `amsmath`, `amssymb`, `amsthm`, `mathtools`.
- `graphicx`, `xcolor`, `hyperref`, `geometry`, `fancyhdr`, `tikz`.
- `biblatex` 3.17, `biber` 2.17, and the big-five citation styles
  (numeric, alphabetic, authoryear, authortitle, verbose).
- `babel`, `polyglossia`, `csquotes`, `microtype`.
- The `lipsum`, `blindtext`, `booktabs`, `tabularx` ecosystem.
- All standard fonts (Computer Modern, Latin Modern, TeX Gyre).

If your `\usepackage{...}` lines all resolve cleanly without
`ctan_packages`, **leave it empty**. Adding packages you don't need
just makes your first build slower and your cache snapshots larger.

## Example: APA-style bibliography

`biblatex-apa` is a popular APA citation style that's actively
maintained outside the bundle. The bundle's biblatex 3.17 ships only
the five core styles; APA, Chicago, IEEE, and the rest are separate
CTAN packages.

=== "BUILD.bazel"

    ```python
    load("@rules_latex//latex:defs.bzl", "latex_document")

    latex_document(
        name = "thesis",
        main = "thesis.tex",
        srcs = ["thesis.tex", "references.bib"],
        ctan_packages = ["biblatex-apa"],
        biber = True,
    )
    ```

=== "thesis.tex"

    ```latex
    \documentclass{article}
    \usepackage[american]{babel}
    \usepackage{csquotes}
    \usepackage[style=apa]{biblatex}
    \addbibresource{references.bib}

    \begin{document}
    Wittgenstein's account of language games \cite{wittgenstein1953}
    bears on this directly.
    \printbibliography
    \end{document}
    ```

=== "references.bib"

    ```bibtex
    @book{wittgenstein1953,
      author = {Ludwig Wittgenstein},
      title  = {Philosophical Investigations},
      year   = {1953},
      publisher = {Blackwell},
    }
    ```

```bash
bazel build //:thesis
```

The first build downloads `biblatex-apa.zip` from CTAN (~430 KB),
extracts it into a TDS overlay, sets `TEXMFHOME` so tectonic finds
`apa.bbx`/`apa.cbx`/`american-apa.lbx`, and runs through tectonic +
biber + tectonic. Subsequent builds skip the download and the prime
entirely (Bazel action cache).

The full working example lives at [`examples/ctan_paper/`][ex] in the
repo.

[ex]: https://github.com/nicklambourne/rules_latex/tree/master/examples/ctan_paper

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
     (TDS .zip first, then raw .zip fallback)
  2. Normalise into a TDS overlay (tex/latex/, tex/latex/biblatex/...)
  3. Set TEXMFHOME=<overlay> and run tectonic to prime the cache
  4. Emit a structured tarball:
        cache.tar.gz
        ├── cache/        ← tectonic's bundle cache
        └── ctan_pkgs/    ← extracted TDS overlay
        ▼
TectonicCompile        (offline, --only-cached)
  1. Extract cache.tar.gz
  2. Set TECTONIC_CACHE_DIR=cache/, TEXMFHOME=ctan_pkgs/
  3. Run tectonic --only-cached
```

Tectonic resolves `\usepackage{foo}` against the overlay first
(via kpathsea / `TEXMFHOME`) and falls back to the bundle for
everything else. Modern packages override stale bundle versions
transparently; old packages still come from the cached bundle.

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
another post-2022 package, the populate step walks the dependency
graph and auto-fetches what's needed:

```python
latex_document(
    name = "thesis",
    main = "thesis.tex",
    srcs = [...],
    ctan_packages = ["biblatex-apa"],   # that's it
)
```

Even if `biblatex-apa` pulls in further post-2022 packages, you
don't list them. The populate step scans each fetched package's
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
  biblatex-apa -> apa, apa-american, biblatex, csquotes, etoolbox
```

**When auto-resolution can't help.** If the missing file isn't
referenced by any fetched package's source, or isn't actually on
CTAN, the existing failure-path hint kicks in with the same three
cases as before (already-listed, referenced-but-missing, or
unknown — possibly a typo in `.tex`). See "Failure hints" below.

**Why the bundle manifest matters.** Tectonic's 2022 bundle ships
specific pinned versions of common packages (`biblatex` 3.17, paired
with `biber` 2.17 — see [DESIGN.md
§4.10](https://github.com/nicklambourne/rules_latex/blob/master/DESIGN.md)).
If the auto-resolver fetched newer CTAN versions of these and
shadowed the bundle, the version coupling would break (biber 2.17
can't read biblatex 3.18+ control files). The manifest filter is
how we avoid that.

The manifest is generated by
`tools/extract_bundle_manifest.py` from the tectonic bundle and
refreshed when the pinned bundle version changes — a
[maintainer-only step](https://github.com/nicklambourne/rules_latex/blob/master/.github/MAINTAINER_TODO.md).

### Failure hints

When the populate step fails despite auto-resolution (which means
the auto-resolver couldn't find a referenced name on CTAN, or there
was a name typo in `.tex`), you'll see a targeted hint:

```
! LaTeX Error: File `foo.sty' not found.
tectonic exited with code 1; see log in /sandbox/.../ for details.

hint: 'foo' isn't in Tectonic's 2022 bundle and isn't referenced
by any of your ctan_packages. If 'foo' is a CTAN package, add it
to ctan_packages on this target. Otherwise check for a typo in
your .tex sources.
```

If `foo` is referenced by one of your fetched packages but the
HEAD-probe couldn't reach CTAN (transient network), the hint
names the requiring package — adding `foo` to `ctan_packages`
explicitly bypasses the probe filter on the next run.

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
| **`tectonic.bundle()`** | Module extension | You're fine with the 2022 frozen versions of everything. |
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

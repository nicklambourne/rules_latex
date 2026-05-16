<!-- Generated with Stardoc: http://skydoc.bazel.build -->

The `latex_toolchain` rule.

A `latex_toolchain` packages everything an action needs to invoke tectonic:
the binary itself, optionally a pre-fetched package bundle, and optionally
a biber binary for bibliography processing.

Toolchains of this type are registered automatically by the `tectonic` module
extension defined in `//latex/toolchain:extensions.bzl`.


## Rules

- [latex_toolchain](#latex_toolchain)

## Providers

- [LatexToolchainInfo](#LatexToolchainInfo)


<a id="latex_toolchain"></a>

## latex_toolchain

<pre>
load("@rules_latex//latex/toolchain:toolchain.bzl", "latex_toolchain")

latex_toolchain(<a href="#latex_toolchain-name">name</a>, <a href="#latex_toolchain-biber">biber</a>, <a href="#latex_toolchain-bundle">bundle</a>, <a href="#latex_toolchain-tectonic">tectonic</a>)
</pre>

Defines a tectonic-based LaTeX toolchain.

**ATTRIBUTES**


| Name  | Description | Type | Mandatory | Default |
| :------------- | :------------- | :------------- | :------------- | :------------- |
| <a id="latex_toolchain-name"></a>name |  A unique name for this target.   | <a href="https://bazel.build/concepts/labels#target-names">Name</a> | required |  |
| <a id="latex_toolchain-biber"></a>biber |  Optional biber executable. When set, latex_document actions invoked with `biber = True` make this binary available on PATH so tectonic can shell out to it for bibliography processing. Absent on platforms without an upstream biber build (currently linux/aarch64).   | <a href="https://bazel.build/concepts/labels">Label</a> | optional |  `None`  |
| <a id="latex_toolchain-bundle"></a>bundle |  Optional offline package bundle (.tar). When set, the toolchain runs tectonic with `--bundle` pointed at this file, making compilation fully hermetic.   | <a href="https://bazel.build/concepts/labels">Label</a> | optional |  `None`  |
| <a id="latex_toolchain-tectonic"></a>tectonic |  The tectonic executable.   | <a href="https://bazel.build/concepts/labels">Label</a> | required |  |


<a id="LatexToolchainInfo"></a>

## LatexToolchainInfo

<pre>
load("@rules_latex//latex/toolchain:toolchain.bzl", "LatexToolchainInfo")

LatexToolchainInfo(<a href="#LatexToolchainInfo-tectonic">tectonic</a>, <a href="#LatexToolchainInfo-bundle">bundle</a>, <a href="#LatexToolchainInfo-biber">biber</a>)
</pre>

Resolved tectonic toolchain.

**FIELDS**

| Name  | Description |
| :------------- | :------------- |
| <a id="LatexToolchainInfo-tectonic"></a>tectonic |  File: the tectonic executable.    |
| <a id="LatexToolchainInfo-bundle"></a>bundle |  File\|None: an offline package bundle, or None for online (default) operation.    |
| <a id="LatexToolchainInfo-biber"></a>biber |  File\|None: a biber executable for bibliography processing, or None if biber isn't available for this platform.    |



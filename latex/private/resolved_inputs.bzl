"""The common source and staging inputs of a LaTeX invocation."""

load("//latex:providers.bzl", "LatexInfo")

def resolve_inputs(ctx):
    """Resolve inputs once for document, test, and snapshot consumers.

    Each consumer still chooses its own cache, biber, output, and execution
    policy.

    Args:
      ctx: the rule context with main, srcs, deps, and pkg_files attributes.

    Returns:
      A struct with `main`, a transitive `srcs` depset, and
      `(File, staged_path)` `pkg_files` pairs.
    """
    main = ctx.file.main
    if main not in ctx.files.srcs:
        fail("`main` ({}) must also appear in `srcs`.".format(main.short_path))

    pkg_files = []
    for label, rel in ctx.attr.pkg_files.items():
        files = label.files.to_list()
        if len(files) != 1:
            fail(
                "pkg_files key {} expands to {} files; expected exactly one."
                    .format(label, len(files)),
            )
        pkg_files.append((files[0], rel))

    return struct(
        main = main,
        srcs = depset(
            direct = ctx.files.srcs,
            transitive = [
                dep[LatexInfo].srcs
                for dep in ctx.attr.deps
                if LatexInfo in dep
            ],
        ),
        pkg_files = pkg_files,
    )

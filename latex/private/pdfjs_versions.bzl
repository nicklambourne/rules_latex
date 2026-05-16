"""Pinned PDF.js distribution.

PDF.js is the JavaScript runtime that `latex_serve_web` uses to render
the preview PDF in the browser. We vendor it via a repository rule
(rather than letting the browser hit cdn.jsdelivr.net at page-load
time) so the live-preview flow works air-gapped and so the version is
content-addressed at build time, matching the rest of the rule set.

To update: bump `PDFJS_VERSION`, download the new
`pdfjs-dist-<version>.tgz` from registry.npmjs.org, compute its
sha256, and bump the hash below.
"""

PDFJS_VERSION = "5.4.149"

# Tarball published on the npm registry. The url here points at the
# canonical CDN-fronted endpoint; the sha256 was computed locally from
# a fresh download.
PDFJS_TARBALL = struct(
    url = "https://registry.npmjs.org/pdfjs-dist/-/pdfjs-dist-{version}.tgz".format(
        version = PDFJS_VERSION,
    ),
    sha256 = "0f002cf949f1ba0c7a4192184616856d7f9561b7e8d08d42ef069690b90e3d23",
)

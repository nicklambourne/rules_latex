# Preparing the macOS Biber mirror

The macOS toolchains use the two native slices of the existing **Biber 2.21**
universal binary. Biber's universal PAR launcher calls `/usr/bin/lipo
-extract_family`, an option rejected by Xcode 27. Pre-extracting the slices
removes that startup dependency. It does not change the Perl application or
the biblatex version pairing. See [DESIGN.md §4.9](../DESIGN.md#49-biber).

## Provenance and acceptance

Source: [the original mirrored universal archive](https://github.com/nicklambourne/rules_latex/releases/download/biber-mirror-v2.21/biber-darwin_universal.tar.gz),
SHA-256 `8c895defed5e69b7a824cb7b7947e8bbfa3f3b17ffb8a1d493e982b679e6633c`.
The upstream application is [Biber](https://github.com/plk/biber); its
[Artistic License 2.0](https://github.com/plk/biber/blob/dev/LICENSE) remains
unchanged. This is a repack of that binary, not a new build.

| Asset | SHA-256 | Size (bytes) |
| --- | --- | ---: |
| `biber-darwin_aarch64.tar.gz` | `d643669ed51179fcd9791fe739029a90ac4ef3c5a97bdb8ef2cc74025b3b7baf` | 37118978 |
| `biber-darwin_x86_64.tar.gz` | `9638e94f569ad6da8e62e9ac63c20649e9a32a9fb53089b139b236665c042e73` | 34999516 |

Both retain the upstream **Developer ID Application: Philip Kime
(45MA3H23TG)** signature and notarization. Do not ad-hoc sign, strip
signatures, or disable Gatekeeper. A standalone CLI is not an application
bundle; `spctl --assess --type execute` is not the acceptance test here.
Use Apple's [explicit notarization check for command-line tools](https://developer.apple.com/forums/thread/130560).
Trust checks must run in an ordinary macOS process with access to the system
trust services, not inside a restricted agent/build sandbox.

## Reproduce and publish

On a Mac with `lipo` and `codesign`, download the source archive and run:

```sh
python3 tools/prepare_biber_macos.py /absolute/path/to/biber-darwin_universal.tar.gz /absolute/path/to/new-output-directory
```

Use a host-local scratch volume for the output. The script refuses a wrong
source checksum or an existing output directory. It checks both source
architectures and each extracted slice against the Apple trust anchor,
upstream team ID, and notarization before packaging. It never signs or
uploads files. The single `biber` archive entry is USTAR, mode 0755, with
zero UID/GID/mtime and empty owner names; gzip uses level 9, zero mtime, and
no filename. Compare the resulting hashes with the table above before
publishing (compression-library changes can affect archive bytes).

Upload the two archives and `biber-macos-provenance.json` to the existing
`biber-mirror-v2.21` release **without `--clobber`**. Keep every existing
asset unchanged, including the universal archive used by older consumers.
Append the source digest and new asset digests to the release notes. The
provenance file records the input, signer requirement, output archive
digests, and extracted executable digests.

Download the published assets again into a fresh directory. Check each
archive digest, extract it, and verify each executable:

```sh
codesign --verify --strict --all-architectures --verbose=2 \
  -R='anchor apple generic and certificate leaf[subject.OU] = "45MA3H23TG" and notarized' \
  --check-notarization /absolute/path/to/extracted/biber
```

Validate `//tests/biber:biber_test` and the `examples` workspace's
`//paper:paper` on native ARM64 and Intel macOS runners. Check the PDF for
the resolved Morgenthaler citation. The test covers concurrent startup in
a fresh PAR cache with an unusable Xcode developer directory, so a working
host `lipo` cannot conceal a regression to the universal launcher. Consumer
validation must use the published repository downloads, not a local Biber
repository override. Linux and Windows pins are unaffected.

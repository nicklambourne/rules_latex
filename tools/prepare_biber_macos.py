#!/usr/bin/env python3
"""Repack the pinned Biber 2.21 universal archive into signed macOS slices.

Maintainer-only: requires macOS, lipo, codesign and online notarization checks.
Takes an existing archive and a NEW output directory; never downloads, signs,
or uploads anything. See docs/biber-macos-mirror.md for the publication checks.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile


SOURCE_URL = (
    "https://github.com/nicklambourne/rules_latex/releases/download/"
    "biber-mirror-v2.21/biber-darwin_universal.tar.gz"
)
SOURCE_SHA256 = "8c895defed5e69b7a824cb7b7947e8bbfa3f3b17ffb8a1d493e982b679e6633c"
SIGNER_REQUIREMENT = 'anchor apple generic and certificate leaf[subject.OU] = "45MA3H23TG" and notarized'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(binary):
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", "--all-architectures",
         "--verbose=2", "-R=" + SIGNER_REQUIREMENT, "--check-notarization", str(binary)],
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if sha256(args.archive) != SOURCE_SHA256:
        parser.error("input does not match the pinned Biber 2.21 archive SHA-256")
    args.output.mkdir()  # Refuse to overwrite an earlier artifact preparation.
    artifacts = []
    with tempfile.TemporaryDirectory(dir=args.output) as tmp:
        tmp = Path(tmp)
        universal = tmp / "universal"
        with tarfile.open(args.archive, "r:gz") as archive:
            members = archive.getmembers()
            if len(members) != 1 or members[0].name not in ("biber", "./biber") or not members[0].isfile():
                parser.error("expected exactly one regular biber file")
            with archive.extractfile(members[0]) as source:
                universal.write_bytes(source.read())
        universal.chmod(0o755)
        verify(universal)
        for cpu, arch in [("aarch64", "arm64"), ("x86_64", "x86_64")]:
            binary = tmp / "biber"
            subprocess.run(["/usr/bin/lipo", str(universal), "-thin", arch, "-output", str(binary)], check=True)
            binary.chmod(0o755)
            actual_arch = subprocess.check_output(["/usr/bin/lipo", "-archs", str(binary)], text=True).strip()
            if actual_arch != arch:
                raise RuntimeError(f"expected {arch}, got {actual_arch}")
            verify(binary)
            output = args.output / f"biber-darwin_{cpu}.tar.gz"
            with output.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=9) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                    entry = tarfile.TarInfo("biber")
                    entry.size = binary.stat().st_size
                    entry.mode = 0o755
                    # TarInfo defaults uid, gid and mtime to zero, and names to empty.
                    with binary.open("rb") as source:
                        archive.addfile(entry, source)
            artifacts.append({"asset": output.name, "architecture": arch,
                              "sha256": sha256(output), "bytes": output.stat().st_size,
                              "binary_sha256": sha256(binary)})
    provenance = {"source_url": SOURCE_URL, "source_sha256": SOURCE_SHA256,
                  "signer_requirement": SIGNER_REQUIREMENT, "artifacts": artifacts}
    (args.output / "biber-macos-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(json.dumps(provenance, indent=2))


if __name__ == "__main__":
    main()

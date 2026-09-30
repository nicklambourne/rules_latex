"""Initialize the bundled Biber's PAR cache before concurrent document builds.

PAR's extraction lock does not recheck completion after acquiring the lock;
another cold starter can overwrite modules an earlier process is loading.
A successful serial --version extracts the complete payload before we allow
document processing. Only initialization is locked, not bibliography work.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile


def prepare_biber_cache(biber: Path, env: dict[str, str]) -> None:
    """Set the child environment to a fully initialized, per-user PAR cache.

    The cache lives under the host temporary directory, outside document
    outputs and snapshots. Binary content (not its sandbox-dependent path)
    identifies it. Failed/interrupted initialization is never marked ready.
    Call only for toolchain Biber; system Biber retains its own environment.
    """
    binary = biber.resolve()
    with binary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    root = Path(tempfile.gettempdir()) / f"rules_latex_biber_cache_v1_{os.getuid()}"
    root.mkdir(mode=0o700, exist_ok=True)
    info = root.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise SystemExit(f"Biber cache must be a private directory owned by this user: {root}")
    cache = root / digest
    cache.mkdir(mode=0o700, exist_ok=True)
    par = cache / "par"
    ready = cache / "ready"

    # The toolchain pin must not inherit PAR overrides that load a different
    # payload, redirect extraction, or delete a cache another build is using.
    for key in list(env):
        if key.startswith("PAR_"):
            del env[key]
    env["PAR_GLOBAL_TEMP"] = str(par)

    with (cache / "init.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if ready.is_file() and par.is_dir():
            return
        ready.unlink(missing_ok=True)
        if par.exists():
            shutil.rmtree(par)
        par.mkdir(mode=0o700)
        result = subprocess.run(
            [str(binary), "--version"], env=env, capture_output=True,
            # If this Python process is killed, its child must retain the lock
            # until extraction exits, before a later initializer can clean up.
            pass_fds=(lock.fileno(),), check=False,
        )
        if result.returncode:
            diagnostics = (result.stdout + result.stderr).decode("utf-8", errors="replace")
            raise SystemExit(f"Biber cache initialization failed ({result.returncode}):\n{diagnostics}")
        ready.touch()

#!/usr/bin/env python3
"""The builder's ENTRYPOINT runs, not just its functions.

On 2026-09-02 a refactor deleted a constant that only main() reads; every
suite stayed green because none of them calls main(), and production failed
on NameError for 2.5 hours. This test runs scripts/build_site_data.py as a
process with the build stamp pre-set to the current inputs fingerprint, so
main() takes its "inputs unchanged" early return: the whole module-level and
main()-prelude path executes, in seconds, without building anything.
"""
import os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_site_data as B  # noqa: E402

stamp = B.STAMP_FILE
prev = stamp.read_text() if stamp.exists() else None
try:
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(B.inputs_fingerprint())
    r = subprocess.run([sys.executable, "-u", str(ROOT / "scripts" / "build_site_data.py")],
                       cwd=ROOT, capture_output=True, text=True, timeout=600,
                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-2000:]
    assert "inputs unchanged" in out, out[-2000:]
    print("build entry : main() prelude runs and returns early on a matching stamp")
finally:
    if prev is None:
        stamp.unlink(missing_ok=True)
    else:
        stamp.write_text(prev)
print("\nPASS")

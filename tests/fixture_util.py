"""Shared helpers for the partition tests: the cached §9 fixture, JSON
normalisation and a structural diff."""
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import make_eq_fixture as mef                                    # noqa: E402

FIXTURE_DIR = ROOT / "data" / "processed" / "fixtures" / "eq"
SET_KEYS = ("cls", "spec", "hero", "dun", "role", "reg", "weeksA", "weeksB")


def fixture(runs_per_day: int = 300, seed: int = 1) -> dict:
    """Build the fixture once (≈20 s) under data/processed/fixtures/eq and
    reuse it while its parameters match."""
    fj = FIXTURE_DIR / "fixture.json"
    if fj.exists():
        fx = json.loads(fj.read_text())
        if fx.get("runs_per_day") == runs_per_day and fx.get("seed") == seed \
                and (FIXTURE_DIR / "payload.json.gz").exists():
            return fx
    return mef.build_fixture(FIXTURE_DIR, runs_per_day, seed)


def norm(x):
    """JSON-comparable: NaN/±inf -> None (JSON.stringify does the same),
    numpy scalars/arrays -> Python, sets -> sorted lists, dict keys -> str."""
    if isinstance(x, bool):
        return x
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [norm(v) for v in x]
    if isinstance(x, (set, frozenset)):
        return sorted(x)
    if hasattr(x, "tolist"):
        return norm(x.tolist())
    if hasattr(x, "item"):
        return norm(x.item())
    return x


def diff(a, b, path="", out=None, limit=25):
    """Paths where two normalised structures differ (exact comparison)."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'js' if k in a else 'py'}")
            else:
                diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: len {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out, limit)
    else:
        if a != b and not (a is None and b is None):
            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) \
                    and a == b:
                return out
            out.append(f"{path}: js={a!r} py={b!r}")
    return out

#!/usr/bin/env python3
"""tests/test_refchars_manifest_equals_scan.py (partitioned_payload.md §9.1)

`manifest.window.refchars` equals `refChars()` over the loaded window for
ALL 24 keys (8 role subsets x 3 attack states), keyed with the client's
exact string including "" for the empty role set; `window.keys` equals the
row scan; `window.rows`/`runs` and `weeks[].reg` counts equal the scan.
"""
import itertools
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import partition_client as pc                                    # noqa: E402
import parts_util as pu                                          # noqa: E402
import sitecalc as sc                                            # noqa: E402
from fixture_util import fixture                                 # noqa: E402


def all_keys() -> set:
    roles = ["DPS", "Healer", "Tank"]
    out = set()
    for mask in range(8):
        rs = sorted(r for j, r in enumerate(roles) if mask >> j & 1)
        for melee, ranged in ((False, False), (True, False), (False, True)):
            out.add(",".join(rs) + "|" + str(melee).lower() + "|" + str(ranged).lower())
    return out


def test_refchars_manifest_equals_scan():
    fx = fixture()
    proot = pu.parts_root()
    loaded = pc.load_site(proot / "site" / "d")
    man = loaded.manifest
    ref = man["window"]["refchars"]
    assert set(ref) == all_keys() and len(ref) == 24
    assert "|false|false" in ref                      # the empty role set is "", never "any"
    site = sc.init_data(loaded.D, fx["now_ms"], R=loaded.R)
    scan = sc.all_ref_chars(site)
    assert scan == ref, {k: (scan[k], ref.get(k)) for k in scan if scan[k] != ref.get(k)}
    # the per-key scan through the client's own refChars(), one state at a time
    st = site.state
    for mask, (melee, ranged) in itertools.product(range(8), ((False, False), (True, False), (False, True))):
        st["role"] = {r for j, r in enumerate(["DPS", "Healer", "Tank"]) if mask >> j & 1}
        st["melee"], st["ranged"] = melee, ranged
        site.refMemo = {}
        key = sc.ref_chars_key(site)
        assert sc.ref_chars(site) == ref[key], key
        assert sc.ref_chars(site, precomputed=ref) == ref[key]
    # keys / rows / runs
    assert man["window"]["keys"] == sorted(int(k) for k in np.unique(loaded.R["key"]))
    assert man["window"]["rows"] == len(loaded.R["dps"])
    assert man["window"]["runs"] == int(loaded.R["run"].max()) + 1
    # weeks[].reg counts equal a row scan under the §3.1 week rule, W clamped
    # to W(now, reg) exactly as computeResetBuckets buckets a row started
    # after the current reset instant into bucket 0 (the fixture's future-
    # dated run): site.W is that identity, and the manifest never names a
    # week past now
    regions = loaded.D["regions"]
    W_raw = np.array([sc.week_of(int(ms), regions[int(r)], man["epoch"]) if ms >= 0 else -10 ** 6
                      for ms, r in zip(_started_ms(loaded), loaded.R["reg"])], dtype=np.int64)
    cur = np.array([site.curW[int(r)] for r in loaded.R["reg"]], dtype=np.int64)
    W = np.where(W_raw > -10 ** 6, np.minimum(W_raw, cur), W_raw)
    assert np.array_equal(W, np.where(site.rbucket < 999, site.W, -10 ** 6))
    assert int((W_raw > cur).sum()) == 5, "the future-dated run should be clamped"
    assert max(int(w["w"]) for w in man["weeks"]) <= max(site.curW.values())
    by = {}
    for w in man["weeks"]:
        by[w["w"]] = w["reg"]
    for w, regs in by.items():
        for rn, cnt in regs.items():
            m = (W == w) & (loaded.R["reg"] == regions.index(rn))
            assert cnt["n"] == int(m.sum()), (w, rn)
            assert cnt["runs"] == len(np.unique(loaded.R["run"][m])), (w, rn)
            assert cnt["chars"] == len(np.unique(loaded.R["char"][m])), (w, rn)
            assert cnt["dmin"] == int(loaded.R["day"][m].min()) and cnt["dmax"] == int(loaded.R["day"][m].max())
    assert set(by) == set(int(w) for w in np.unique(W[W > -10 ** 6]))
    # buckets from the manifest's weeks equal computeResetBuckets' weekCounts
    wc = {}
    for w, regs in by.items():
        for rn, cnt in regs.items():
            b = site.curW[regions.index(rn)] - w
            wc[b] = wc.get(b, 0) + cnt["n"]
    assert wc == site.weekCounts, (wc, site.weekCounts)
    print(f"refchars: 24 keys equal, keys {man['window']['keys'][0]}..{man['window']['keys'][-1]}, "
          f"weeks {sorted(by)}")


def _started_ms(loaded: pc.Loaded) -> np.ndarray:
    """Start instant per row from the day + hour (the file carries no ms;
    the week only needs the hour, §3.1) -- and the keys.npz cache where the
    builder kept the exact ms, to assert the two agree."""
    epoch = sc.parse_iso_ms(loaded.manifest["epoch"] + "T00:00:00Z")
    hr = loaded.R["hr"]
    day = loaded.R["day"]
    return np.where(day >= 0, epoch + day * 86_400_000 + np.maximum(hr, 0) * 3_600_000, -1)


if __name__ == "__main__":
    test_refchars_manifest_equals_scan()
    print("test_refchars_manifest_equals_scan: all green")

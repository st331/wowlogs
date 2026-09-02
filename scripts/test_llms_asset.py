"""The LLM export's Release-asset path (partitioned_payload.md section 5,
section 10 PR-1 stage A): scripts/llms_asset.sh, the step body refresh.yml
runs after the build and llms.yml's Pack step.

The property under test is the one the first review found missing: NO DEPLOY
CAN DROP A TREE THE PREVIOUS DEPLOY CARRIED. The step is driven exactly as
the workflow drives it (bash, environment overrides, a file:// URL standing in
for the Release, a fake build command that records each call), through every
state it can be in:

  built   no Release, no cached tarball  -> builds inline ONCE, caches it
  stale   no Release, cached tarball     -> unpacks the cache, never builds
  fresh   Release reachable              -> downloads, replaces the cache
  cached  `fresh` drain run + cache      -> no download, cache as is
  stale   corrupt download               -> cache kept
  built   corrupt cached tarball         -> discarded, rebuilt
  none    no Release, no cache, build fails -> exit 0, warning, no tree
  none    no Release, no cache, build exceeds LLMS_BUILD_MAX_S -> stopped by
          `timeout`, exit 0, warning, no half tree, no tarball
  age     an old stamp -> llms.age_h and a ::warning:: past the threshold
  pack    the tarball carries exactly the served set plus the stamp

Every case asserts the exit code is 0 and that both site dirs end in the
same state, because refresh.yml publishes site/ and mirrors docs/.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
SCRIPT = HERE / "llms_asset.sh"


def sh(root: pathlib.Path, *args, env=None, mode="", url=None):
    e = {**os.environ,
         "LLMS_TAR": str(root / "cache" / "llms.tar.gz"),
         "LLMS_URL": url or "file:///nonexistent/llms.tar.gz",
         "LLMS_MODE": mode,
         "LLMS_SITE_DIRS": "site docs",
         "LLMS_BUILD_CMD": f"bash {root / 'fakebuild.sh'}",
         "LLMS_CURL_MAX_S": "5",
         **(env or {})}
    p = subprocess.run(["bash", str(SCRIPT), *args], cwd=root, env=e,
                       capture_output=True, text=True)
    return p


def health(root: pathlib.Path, d="site") -> dict:
    out = {}
    for line in (root / d / "build_health.txt").read_text().splitlines():
        k, _, v = line.partition("=")
        out[k] = v
    return out


def reset_health(root):
    for d in ("site", "docs"):
        (root / d).mkdir(exist_ok=True)
        (root / d / "build_health.txt").write_text("built=x\n")


def tree(root, d="site"):
    p = root / d
    return sorted(str(f.relative_to(p)) for f in p.rglob("*")
                  if f.is_file() and f.name != "build_health.txt")


def make_release(root: pathlib.Path, marker: str, built: str | None = None) -> str:
    """A tarball as llms.yml would publish it, with a marker so downloads are
    distinguishable from the cache and the inline build."""
    src = root / f"rel_{marker}"
    shutil.rmtree(src, ignore_errors=True)
    (src / "llms").mkdir(parents=True)
    (src / "llms.txt").write_text(f"release {marker}\n")
    (src / "llms" / f"{marker}.csv").write_text("a,b\n1,2\n")
    (src / "llms" / "index.html").write_text("<p>x</p>")
    (src / "robots.txt").write_text("User-agent: *\n")
    (src / "sitemap.xml").write_text("<urlset/>\n")
    out = root / f"release_{marker}.tar.gz"
    p = subprocess.run(["bash", str(SCRIPT), "pack", str(out)], cwd=root,
                       env={**os.environ, "LLMS_SITE_DIRS": str(src)},
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    if built:                       # rewrite the stamp to an arbitrary age
        with tarfile.open(out) as t:
            names = t.getnames()
            tmp = root / f"restamp_{marker}"
            shutil.rmtree(tmp, ignore_errors=True)
            t.extractall(tmp)
        (tmp / "llms.built").write_text(built + "\n")
        with tarfile.open(out, "w:gz") as t:
            for n in names:
                t.add(tmp / n, arcname=n)
    return f"file://{out}"


with tempfile.TemporaryDirectory() as td:
    root = pathlib.Path(td)
    (root / "cache").mkdir()
    calls = root / "build_calls"
    # the fake --llms-only: writes what build_llms writes (both dirs, like the
    # real one), and counts its invocations; FAKE_FAIL makes it fail
    (root / "fakebuild.sh").write_text(f"""#!/usr/bin/env bash
echo call >> "{calls}"
[ -n "${{FAKE_FAIL:-}}" ] && {{ echo "fake build failing"; exit 1; }}
[ -n "${{FAKE_SLOW:-}}" ] && {{ mkdir -p site/llms; echo half > site/llms/partial.csv; sleep 20; }}
for d in site docs; do
  mkdir -p "$d/llms"
  echo "inline build" > "$d/llms.txt"
  echo "a,b" > "$d/llms/inline.csv"
  echo "<p>i</p>" > "$d/llms/index.html"
  echo "User-agent: *" > "$d/robots.txt"
  echo "<urlset/>" > "$d/sitemap.xml"
done
""")
    ncalls = lambda: len(calls.read_text().splitlines()) if calls.exists() else 0  # noqa: E731

    # ---- 1. built: no Release, no cache -> inline build, once, cached
    reset_health(root)
    p = sh(root)
    assert p.returncode == 0, p.stdout + p.stderr
    h = health(root)
    assert h["llms.unpack"] == "built", (h, p.stdout)
    assert ncalls() == 1
    assert (root / "cache" / "llms.tar.gz").exists(), "inline build must be cached"
    assert (root / "site" / "llms.txt").read_text().startswith("inline build")
    assert tree(root, "site") == tree(root, "docs") and "llms/inline.csv" in tree(root)
    assert h["llms.age_h"] == "0" and h["llms.built"] != "unknown" and h["llms.files"] == "2"
    assert "::warning::no LLM export available" in p.stdout
    assert h == health(root, "docs")
    print("built   : no Release + no cache -> built inline once, cached, both dirs populated, "
          f"health {h['llms.unpack']}/{h['llms.age_h']}h/{h['llms.files']} files")

    # ---- 2. stale: no Release, cache present -> unpack, NO build
    reset_health(root)
    for d in ("site", "docs"):
        shutil.rmtree(root / d / "llms"); (root / d / "llms.txt").unlink()
    p = sh(root)
    assert p.returncode == 0
    h = health(root)
    assert h["llms.unpack"] == "stale" and ncalls() == 1, (h, ncalls())
    assert "llms/inline.csv" in tree(root) and tree(root) == tree(root, "docs")
    assert "::warning::" not in p.stdout, p.stdout
    print("stale   : no Release + cache -> unpacked from the cache, build NOT run, no warning")

    # ---- 3. fresh: Release reachable -> downloaded, cache replaced
    reset_health(root)
    url_a = make_release(root, "relA")
    p = sh(root, url=url_a)
    h = health(root)
    assert p.returncode == 0 and h["llms.unpack"] == "fresh", (h, p.stdout)
    assert "llms/relA.csv" in tree(root) and "llms/inline.csv" not in tree(root)
    assert tree(root) == tree(root, "docs")
    with tarfile.open(root / "cache" / "llms.tar.gz") as t:
        assert "llms/relA.csv" in t.getnames(), "cache must hold the downloaded asset"
    assert ncalls() == 1
    print("fresh   : Release reachable -> downloaded, previous tree replaced in both dirs, cache updated")

    # ---- 4. cached: a `fresh` drain run with a cache never downloads
    reset_health(root)
    url_b = make_release(root, "relB")
    p = sh(root, url=url_b, mode="fresh")
    h = health(root)
    assert p.returncode == 0 and h["llms.unpack"] == "cached", (h, p.stdout)
    assert "llms/relA.csv" in tree(root) and "llms/relB.csv" not in tree(root)
    print("cached  : mode=fresh + cache -> no download, cached relA served although relB is published")

    # ---- 4b. a `fresh` run WITHOUT a cache still downloads (never builds needlessly)
    reset_health(root)
    (root / "cache" / "llms.tar.gz").unlink()
    p = sh(root, url=url_b, mode="fresh")
    h = health(root)
    assert h["llms.unpack"] == "fresh" and "llms/relB.csv" in tree(root) and ncalls() == 1
    print("cached  : mode=fresh + NO cache -> downloads (relB), build not run")

    # ---- 5. corrupt download -> cache kept (stale)
    reset_health(root)
    bad = root / "bad.tar.gz"; bad.write_text("not a tarball")
    p = sh(root, url=f"file://{bad}")
    h = health(root)
    assert h["llms.unpack"] == "stale" and "llms/relB.csv" in tree(root), (h, p.stdout)
    assert not (root / "cache" / "llms.tar.gz.tmp").exists()
    print("stale   : corrupt download -> discarded, cached relB kept")

    # ---- 6. corrupt cached tarball, no Release -> discarded, rebuilt inline
    reset_health(root)
    (root / "cache" / "llms.tar.gz").write_text("garbage")
    p = sh(root)
    h = health(root)
    assert h["llms.unpack"] == "built" and ncalls() == 2, (h, ncalls(), p.stdout)
    assert "llms/inline.csv" in tree(root) and tree(root) == tree(root, "docs")
    with tarfile.open(root / "cache" / "llms.tar.gz") as t:
        assert "llms/inline.csv" in t.getnames()
    print("built   : corrupt cache + no Release -> discarded, rebuilt inline, re-cached")

    # ---- 7. none: nothing works -> exit 0, warning, no tree, no half tarball
    reset_health(root)
    (root / "cache" / "llms.tar.gz").unlink()
    p = sh(root, env={"FAKE_FAIL": "1"})
    h = health(root)
    assert p.returncode == 0, "the step must never fail the deploy"
    assert h["llms.unpack"] == "none" and h["llms.built"] == "unknown", h
    assert ncalls() == 3 and "::warning::inline LLM build failed" in p.stdout
    assert not (root / "cache" / "llms.tar.gz").exists()
    assert h == health(root, "docs")
    print("none    : no Release + no cache + build fails -> exit 0, two warnings, health none/unknown")

    # ---- 7b. none: the inline build is O(season); past the cap it is stopped
    reset_health(root)
    t0 = time.time()
    p = sh(root, env={"FAKE_SLOW": "1", "LLMS_BUILD_MAX_S": "2"})
    took = time.time() - t0
    h = health(root)
    assert p.returncode == 0, "a timed-out inline build must never fail the deploy"
    assert took < 10, f"timeout did not stop the build ({took:.1f}s)"
    assert h["llms.unpack"] == "none" and h["llms.files"] == "0", (h, p.stdout)
    assert "::warning::inline LLM build exceeded 2 s" in p.stdout, p.stdout
    assert not (root / "site" / "llms").exists() and not (root / "docs" / "llms").exists(), \
        "a half-written tree must not ship"
    assert not (root / "cache" / "llms.tar.gz").exists()
    assert h == health(root, "docs")
    print(f"none    : inline build over LLMS_BUILD_MAX_S -> stopped in {took:.1f}s, exit 0, warning, "
          "half tree removed, nothing cached")

    # ---- 8. age: an old stamp is reported and warned about past the threshold
    reset_health(root)
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 50 * 3600))
    url_old = make_release(root, "relOld", built=old)
    p = sh(root, url=url_old)
    h = health(root)
    assert h["llms.unpack"] == "fresh" and h["llms.built"] == old, h
    assert 49 <= int(h["llms.age_h"]) <= 51, h
    assert "::warning::LLM export is" in p.stdout and " h old" in p.stdout, p.stdout
    reset_health(root)
    p = sh(root, url=url_old, env={"LLMS_STALE_WARN_H": "72"})
    assert "::warning::" not in p.stdout
    print(f"age     : fresh download of a {h['llms.age_h']} h old build -> llms.built carried, "
          "warned at 36 h, quiet at 72 h")

    # ---- 8b. a tarball from before the stamp existed -> age from the file clock
    reset_health(root)
    legacy = root / "legacy.tar.gz"
    src = root / "rel_relA"
    with tarfile.open(legacy, "w:gz") as t:
        for n in ("llms.txt", "llms", "robots.txt", "sitemap.xml"):
            t.add(src / n, arcname=n)
    p = sh(root, url=f"file://{legacy}")
    h = health(root)
    assert h["llms.unpack"] == "fresh" and h["llms.built"] != "unknown" and h["llms.age_h"] == "0", h
    assert not (root / "site" / "llms.built").exists()
    print("age     : stamp-less tarball -> built taken from the file clock, no stale llms.built left behind")

    # ---- 9. pack: exactly the served set plus the stamp; refuses an empty tree
    with tarfile.open(root / "release_relA.tar.gz") as t:
        names = set(t.getnames())
    assert {"llms.txt", "llms", "llms/relA.csv", "llms/index.html", "robots.txt",
            "sitemap.xml", "llms.built"} == names, names
    empty = root / "empty"; empty.mkdir()
    p = subprocess.run(["bash", str(SCRIPT), "pack", str(root / "never.tar.gz")], cwd=root,
                       env={**os.environ, "LLMS_SITE_DIRS": str(empty)}, capture_output=True, text=True)
    assert p.returncode != 0 and not (root / "never.tar.gz").exists()
    print("pack    : tarball = llms.txt + llms/ + robots.txt + sitemap.xml + llms.built; "
          "an empty tree is refused (llms.yml's Pack step goes red, not the asset)")

    # ---- 10. the property: across every case a tree existed before, it exists after
    # (cases 2-6, 8 all began with a tree or a cache and ended with llms.txt)
    assert (root / "site" / "llms.txt").exists() and (root / "docs" / "llms.txt").exists()

print("PASS")

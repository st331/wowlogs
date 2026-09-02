#!/usr/bin/env python3
"""tests/test_build_step_exit.py (partitioned_payload.md §9.1)

A shell test of the §6 Build step (scripts/build_step.sh, what refresh.yml
runs):

  * a partition failure -> the step exits with the LEGACY rc, prints the
    ::warning::, and the parts.* lines are in site/build_health.txt after
    both builders exit (parts.status, parts.rc, build.step_wall_s);
  * a partition builder that stalls (sleeps) does not extend the step past
    the legacy builder's exit + PARTS_DEADLINE_S grace, and
    parts.deadline_hit=1 is written -- both for a builder that cannot
    react (a plain sleep, killed by timeout) and for the real builder
    stalled between days (PARTS_TEST_STALL_S), which stops at its
    checkpoint, writes the manifest with the completed days, and whose next
    run continues without rebuilding them;
  * a builder run with the network namespace disabled (unshare -rn) and a
    socket guard (every socket() raises) completes an ordinary run: no
    socket is ever opened.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import parts_util as pu                                          # noqa: E402
from fixture_util import FIXTURE_DIR, fixture                    # noqa: E402

STEP = ROOT / "scripts" / "build_step.sh"
SCRATCH = FIXTURE_DIR / "build_step"


def _fresh(name: str) -> pathlib.Path:
    d = SCRATCH / name
    if d.exists():
        shutil.rmtree(d)
    (d / "site").mkdir(parents=True)
    (d / "data" / "processed" / "parts").mkdir(parents=True)
    return d


def _step(cwd: pathlib.Path, env: dict, timeout: int = 300) -> tuple[subprocess.CompletedProcess, float, dict]:
    e = dict(os.environ, PYTHON=sys.executable, HEALTH="site/build_health.txt",
             PARTS_HEALTH="data/processed/parts/health.txt", PARTS_LOG="parts.log")
    e.update(env)
    t0 = time.monotonic()
    r = subprocess.run(["bash", str(STEP)], cwd=str(cwd), env=e, capture_output=True, text=True, timeout=timeout)
    wall = time.monotonic() - t0
    health = {}
    hp = cwd / "site" / "build_health.txt"
    if hp.exists():
        for line in hp.read_text().splitlines():
            k, _, v = line.partition("=")
            health.setdefault(k, []).append(v)
    return r, wall, health


def test_partition_failure_never_reds_the_run():
    d = _fresh("failure")
    (d / "site" / "build_health.txt").write_text("built=legacy\n")
    r, wall, h = _step(d, {
        "LEGACY_CMD": "echo legacy-ok; echo 'build.wall_s=1.0' > site/build_health.txt",
        "PARTS_CMD": "sh -c 'printf \"parts.status=failed\\nparts.error=boom\\n\" > data/processed/parts/health.txt; exit 1'",
        "PARTS_DEADLINE_S": "30"})
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "::warning::partition build failed" in r.stdout
    assert h["parts.status"] == ["failed"] and h["parts.rc"] == ["1"]
    assert "build.step_wall_s" in h and h["build.wall_s"] == ["1.0"]
    # the append comes AFTER legacy's truncating write_health()
    text = (d / "site" / "build_health.txt").read_text()
    assert text.index("build.wall_s=") < text.index("parts.status=")
    # the legacy rc alone decides the job
    d = _fresh("failure2")
    r2, _, h2 = _step(d, {"LEGACY_CMD": "exit 3", "PARTS_CMD": "sh -c 'printf \"parts.status=ok\\n\" > data/processed/parts/health.txt'",
                          "PARTS_DEADLINE_S": "30"})
    assert r2.returncode == 3 and h2["parts.status"] == ["ok"] and h2["parts.rc"] == ["0"]
    assert "::warning::" not in r2.stdout
    print("failure: legacy rc decides, ::warning::, parts.* appended after legacy")


def test_stalled_builder_does_not_extend_the_step():
    d = _fresh("stall")
    deadline = 4
    r, wall, h = _step(d, {"LEGACY_CMD": "sleep 1; echo legacy", "PARTS_CMD": "sleep 600",
                           "PARTS_DEADLINE_S": str(deadline)})
    assert r.returncode == 0
    # legacy exit (1 s) + the deadline (4 s) + SIGKILL grace (30 s) is the
    # bound; a sleep dies at the TERM, so the step ends at the deadline
    assert wall <= 1 + deadline + 30 + 5, wall
    assert wall < 20, wall
    assert h["parts.deadline_hit"] == ["1"] and h["parts.deadline_s"] == [str(deadline)]
    assert h["parts.status"] == ["killed"] and h["parts.rc"] == ["124"]
    assert "::warning::" in r.stdout
    print(f"stall (sleep): step ended in {wall:.1f} s, deadline_hit written by the step")


def test_real_builder_stalled_between_days_checkpoints():
    fx = fixture()
    src = pu.parts_root()
    d = _fresh("stall_real")
    shutil.rmtree(d / "data")
    shutil.copytree(src / "data", d / "data")
    shutil.rmtree(d / "site")
    shutil.copytree(src / "site", d / "site")
    # every day dirty (a format-level rebuild), so there is a queue to stall in
    st_path = d / "data" / "processed" / "parts" / "s2" / "state.json"
    st = json.loads(st_path.read_text())
    st["static_inputs"] = {"all": "changed"}
    st_path.write_text(json.dumps(st))
    # the builder stops at the first boundary past deadline - 30 s (§6), so
    # a deadline under the margin builds nothing: 45 s = 15 s of days
    deadline = 45
    cmd = (f"{sys.executable} -u {ROOT / 'scripts' / 'partition_build.py'} --data-root data --site-dir site "
           f"--now {fx['now']} --deadline {deadline} --max-days 400 --withhold-cubes '*'")
    r, wall, h = _step(d, {"LEGACY_CMD": "sleep 1", "PARTS_CMD": cmd, "PARTS_DEADLINE_S": str(deadline),
                           "PARTS_TEST_STALL_S": "600"}, timeout=600)
    assert r.returncode == 0
    assert wall <= 1 + deadline + 30 + 10, wall
    assert h["parts.deadline_hit"] == ["1"], h
    assert h["parts.status"] == ["ok"], h.get("parts.status")          # a clean stop, not a kill
    all_days = sorted((int(k) for k, e in st["days"].items() if e.get("n")), reverse=True)
    done = [int(x) for x in h["parts.rebuilt_order"][0].split(",") if x]
    assert 1 <= len(done) < len(all_days) and done == sorted(done, reverse=True), done
    assert int(h["parts.days_left"][0]) == len(all_days) - len(done)
    man = json.loads((d / "site" / "d" / "s2" / "manifest.json").read_text())
    assert man["seq"] >= 1 and len([e for e in man["days"] if e.get("f")]) == len(all_days)
    # the next run continues from the checkpoint without rebuilding the completed days
    r2 = pu.run_parts(d, fx["now"], max_days=400, extra=["--withhold-cubes", "*"])
    h2 = pu.parts_health(d)
    done2 = [int(x) for x in h2["parts.rebuilt_order"][0].split(",") if x]
    assert not (set(done) & set(done2)) and sorted(set(done) | set(done2)) == sorted(all_days), (done, done2)
    assert h2["parts.days_left"] == ["0"]
    print(f"stall (real builder): {len(done)} days before the deadline, {len(done2)} after, none twice")


def test_no_socket_is_ever_opened():
    fx = fixture()
    src = pu.parts_root()
    d = _fresh("nonet")
    shutil.rmtree(d / "data")
    shutil.copytree(src / "data", d / "data")
    shutil.rmtree(d / "site")
    shutil.copytree(src / "site", d / "site")
    # append the last chunk again as a "new" tail so the run does real work
    # (a resend: dedup keep=last, so nothing changes but every stage runs)
    for name, rel in (("players.jsonl", "data/processed/players.jsonl"), ("gear.jsonl", "data/processed/gear.jsonl"),
                      ("abilities.jsonl", "data/raw/abilities.jsonl")):
        with open(d / rel, "ab") as fh:
            fh.write((FIXTURE_DIR / "chunks" / "07" / name).read_bytes())
    guard = d / "guard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(
        "import socket\n"
        "def _deny(*a, **k):\n"
        "    raise RuntimeError('partition_build opened a socket')\n"
        "socket.socket.__init__ = _deny\n"
        "socket.create_connection = _deny\n"
        "socket.getaddrinfo = _deny\n")
    env = dict(os.environ, PYTHONPATH=str(guard) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    env.pop("WOWLOGS_PINS", None)
    args = [sys.executable, str(ROOT / "scripts" / "partition_build.py"), "--data-root", str(d / "data"),
            "--site-dir", str(d / "site"), "--now", fx["now"], "--max-days", "400", "--withhold-cubes", "*"]
    ns = subprocess.run(["unshare", "-rn", "true"], capture_output=True).returncode == 0
    if ns:
        args = ["unshare", "-rn"] + args
    r = subprocess.run(args, env=env, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-2000:])
    h = pu.parts_health(d)
    assert h["parts.status"] == ["ok"] and int(h["parts.tail.players"][0]) > 0
    assert int(h["parts.rebuilt_days"][0]) >= 1
    assert "socket" not in r.stderr
    print(f"no network: ordinary run ok under {'unshare -rn + ' if ns else ''}socket guard, "
          f"{h['parts.rebuilt_days'][0]} days rebuilt")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("test_build_step_exit: all green")

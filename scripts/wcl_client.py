#!/usr/bin/env python3
"""Warcraft Logs v2 (GraphQL) client with quota management.

Design goals:
  * Never spend more than a fixed share of the hourly budget: WCL_QUOTA_FRACTION
    when the environment sets it, else data/cadence.json's quota_fraction
    (0.70 -- the owner's cap since 2026-09-30, "never more than 70% of my API
    quota"; it was 0.85 from 2026-09-08 and 0.70 before that), else
    DEFAULT_QUOTA_FRACTION. The ceiling is measured against the ACCOUNT's live
    spend, so the rest of the hour belongs to whatever else the account is
    doing -- the owner's own lookups included.
  * Within that ceiling, spend without artificial pacing, then stop cleanly
    and sleep until the window resets.
  * Every query piggybacks `rateLimitData` so we always know the live spend
    without extra requests.
  * Survive 429s, transient network errors and GraphQL quota errors.

Credentials are resolved in this order:
  1. WCL_TOKEN env var
  2. .secrets/wcl_token file
  3. client-credentials OAuth flow using WCL_CLIENT_ID/WCL_CLIENT_SECRET
     (or .secrets/wcl_client_id / .secrets/wcl_client_secret), cached to
     .secrets/wcl_token_auto
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECRETS = ROOT / ".secrets"
# how long a 429 may put the client to sleep before it asks again (see the
# 429 branch in query()); a 429 costs no points
RETRY_429_STEP_S = float(os.environ.get("WCL_RETRY_429_STEP_S", 60) or 60)


class QuotaDeadline(Exception):
    """Raised instead of sleeping past a caller-supplied deadline.

    Deliberately NOT a RuntimeError: callers catch RuntimeError to mean "this
    batch failed, requeue it", and a deadline is the opposite -- it means stop
    asking. Subclassing RuntimeError would have it swallowed and retried.
    """


API_URL = "https://www.warcraftlogs.com/api/v2/client"
TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"

RATE_FIELD = "rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }"

# Hard ceiling on the share of the hourly budget this process may spend, as a
# fraction of whatever limitPerHour the API reports. Resolved by
# quota_fraction(): WCL_QUOTA_FRACTION when the environment sets it (a
# deliberate one-off dispatch, a test), else the owner's standing knob in
# data/cadence.json (scripts/cadence.py; 0.70 since 2026-09-30), else this
# constant -- which therefore only ever applies when the cadence file cannot
# be read. Clamped to (0, 1] everywhere so nothing can authorise more than the
# account actually has. Every process on the account (the sweep, the trinket
# and keystone collectors, a hand-run diagnostic) resolves it the same way and
# the governor measures it against the account's LIVE spend, which is what
# makes one fraction a cap on the TOTAL.
DEFAULT_QUOTA_FRACTION = 0.70


def cadence_quota_fraction() -> float:
    """data/cadence.json's quota_fraction, else DEFAULT_QUOTA_FRACTION."""
    here = str(pathlib.Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    try:
        from cadence import read_cadence
    except ImportError:
        return DEFAULT_QUOTA_FRACTION
    c = read_cadence(log=lambda m: print(f"[wcl] {m}", flush=True))
    f = c.get("quota_fraction", DEFAULT_QUOTA_FRACTION)
    return f if 0 < f <= 1 else DEFAULT_QUOTA_FRACTION


def quota_fraction() -> float:
    raw = os.environ.get("WCL_QUOTA_FRACTION", "").strip()
    if not raw:
        return cadence_quota_fraction()
    try:
        f = float(raw)
    except ValueError:
        f = cadence_quota_fraction()
        print(f"[wcl] ignoring unparseable WCL_QUOTA_FRACTION={raw!r}; "
              f"using the cadence cap {f}", flush=True)
        return f
    if not 0 < f <= 1:
        f = cadence_quota_fraction()
        print(f"[wcl] WCL_QUOTA_FRACTION={raw} out of range (0, 1]; "
              f"using the cadence cap {f}", flush=True)
        return f
    return f


class _Quota:
    """Account-wide hourly budget, shared by every client in the process.

    The budget is per ACCOUNT -- not per API client and not per thread -- so
    the fourteen summary workers are all drawing on one pot. Holding this state
    per instance let each worker believe the whole hour was its own, and
    fourteen such beliefs overshoot any ceiling by fourteen requests.

    `inflight` is the estimated cost of requests admitted but not yet returned.
    Without it a burst of workers all pass the same check against the same
    stale `spent` and go through together.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.limit = 18000.0
        self.spent = 0.0
        self.reset_in = 3600.0
        self.inflight = 0.0
        self.requests_made = 0
        self.fraction = quota_fraction()
        # A wall-clock deadline (epoch seconds) set by a caller that runs on a
        # clock of its own -- backfill mode (scripts/backfill.py) -- and, while
        # set, REPLACES WCL_MAX_SLEEP_S as the cap on sleeping for the reset:
        # the process sleeps to the window reset whenever the reset comes
        # before the deadline, and stops cleanly (QuotaDeadline) when it does
        # not. None = the environment's cap, as always.
        self.deadline: float | None = None
        self.probe_lock = threading.Lock()
        self.probed = False
        # until a real reading lands we are guessing; the first response fixes
        # both the true limit and what the rest of the account already spent
        self.observed = False

    @property
    def ceiling(self) -> float:
        return self.limit * self.fraction

    def admit(self, est_cost: float, margin: float) -> bool:
        """Reserve est_cost against the ceiling, or refuse."""
        with self.lock:
            if self.spent + self.inflight + est_cost + margin >= self.ceiling:
                return False
            self.inflight += est_cost
            return True

    def release(self, est_cost: float) -> None:
        with self.lock:
            self.inflight = max(0.0, self.inflight - est_cost)

    def observe(self, rl: dict) -> None:
        with self.lock:
            self.limit = float(rl["limitPerHour"])
            self.spent = float(rl["pointsSpentThisHour"])
            self.reset_in = float(rl["pointsResetIn"])
            self.observed = True

    def count_request(self) -> None:
        with self.lock:
            self.requests_made += 1

    def rolled_over(self) -> None:
        with self.lock:
            self.spent = 0.0
            self.reset_in = 3600.0
            self.inflight = 0.0


QUOTA = _Quota()


def _read_secret(name: str) -> str | None:
    p = SECRETS / name
    if p.exists():
        return p.read_text().strip() or None
    return None


def _client_credentials_token(session: requests.Session) -> str:
    cid = os.environ.get("WCL_CLIENT_ID") or _read_secret("wcl_client_id")
    csec = os.environ.get("WCL_CLIENT_SECRET") or _read_secret("wcl_client_secret")
    if not (cid and csec):
        sys.exit("no WCL credentials: set WCL_TOKEN or WCL_CLIENT_ID/WCL_CLIENT_SECRET")
    r = session.post(TOKEN_URL, data={"grant_type": "client_credentials"},
                     auth=(cid, csec), timeout=60)
    r.raise_for_status()
    token = r.json()["access_token"]
    SECRETS.mkdir(exist_ok=True)
    (SECRETS / "wcl_token_auto").write_text(token)
    return token


def get_token(session: requests.Session) -> tuple[str, str]:
    """Returns (token, source) where source is 'static' or 'auto'."""
    token = os.environ.get("WCL_TOKEN") or _read_secret("wcl_token")
    if token:
        return token, "static"
    cached = _read_secret("wcl_token_auto")
    if cached:
        return cached, "auto"
    return _client_credentials_token(session), "auto"


class QuotaExceeded(Exception):
    """Raised internally when the hourly point budget is exhausted."""


class WCLClient:
    def __init__(self, budget_margin: float | None = None, verbose: bool = True):
        if budget_margin is None:
            try:
                budget_margin = float(os.environ.get("WCL_BUDGET_MARGIN", "") or 400.0)
            except ValueError:
                budget_margin = 400.0
        self.session = requests.Session()
        self.token, self.token_source = get_token(self.session)
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        self.budget_margin = budget_margin
        self.verbose = verbose
        self._probe_quota()

    def _probe_quota(self) -> None:
        """Learn what the account has already spent, before spending anything.

        The governor starts at zero spent because it has not seen a reading
        yet. Without this, the first request of a process goes out no matter
        what the rest of the account did in this hour -- which is precisely the
        case the ceiling exists to catch. One cheap query (rateLimitData alone)
        closes it. Deliberately bypasses the budget guard: it is the call that
        makes the guard meaningful, and refusing it would deadlock the start.
        """
        with QUOTA.probe_lock:
            if QUOTA.probed:
                return
            QUOTA.probed = True
        try:
            r = self.session.post(API_URL, json={"query": "{ " + RATE_FIELD + " }"},
                                  timeout=60)
            rl = ((r.json() or {}).get("data") or {}).get("rateLimitData")
        except (requests.RequestException, ValueError, AttributeError) as e:
            self._log(f"could not read starting quota ({e}); "
                      f"the first response will set it")
            return
        if not rl:
            return
        QUOTA.observe(rl)
        self._log(f"quota at start: {QUOTA.spent:.0f}/{QUOTA.limit:.0f} pts spent "
                  f"this hour; ceiling {QUOTA.ceiling:.0f} "
                  f"({QUOTA.fraction:.0%}), {QUOTA.ceiling - QUOTA.spent:.0f} available")
        if QUOTA.spent >= QUOTA.ceiling:
            self._log("already at or over the ceiling; this run will not fetch")

    # Quota lives in the process-wide governor, not on the instance: callers
    # build one client per thread, and a per-instance view of a per-account
    # budget is exactly the bug this avoids. Exposed as properties so existing
    # readers of client.spent / client.limit keep working unchanged.
    @property
    def limit(self) -> float:
        return QUOTA.limit

    @property
    def spent(self) -> float:
        return QUOTA.spent

    @property
    def reset_in(self) -> float:
        return QUOTA.reset_in

    @property
    def ceiling(self) -> float:
        return QUOTA.ceiling

    @property
    def requests_made(self) -> int:
        return QUOTA.requests_made

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[wcl {time.strftime('%H:%M:%S')}] {msg}", flush=True)

    @staticmethod
    def _sleep_cap() -> float:
        """Seconds we may sleep waiting for the window: to QUOTA.deadline when
        a caller set one (it overrides the environment), else WCL_MAX_SLEEP_S
        (0 = no cap). Never 0 with a deadline set, so a passed deadline reads
        as "stop now" rather than "sleep without limit"."""
        if QUOTA.deadline is not None:
            return max(1.0, QUOTA.deadline - time.time())
        return float(os.environ.get("WCL_MAX_SLEEP_S", 0) or 0)

    def _sleep_for_reset(self) -> None:
        wait = max(self.reset_in, 30) + 20  # small cushion past the reset
        # A caller on a clock (a CI job with a timeout) must not burn its whole
        # slot asleep. WCL_MAX_SLEEP_S caps how long we are willing to wait;
        # past that we stop cleanly and the next run picks up from the journal
        # with a fresh budget, instead of being killed having fetched nothing.
        cap = self._sleep_cap()
        if cap and wait > cap:
            self._log(f"budget ceiling reached ({self.spent:.0f}/{self.ceiling:.0f} "
                      f"pts, {QUOTA.fraction:.0%} of {self.limit:.0f}) and the reset "
                      f"is {wait:.0f}s away, over the {cap:.0f}s cap; stopping so the "
                      f"next run can use a fresh window")
            raise QuotaDeadline(f"quota reset {wait:.0f}s away, cap {cap:.0f}s")
        self._log(f"budget ceiling reached ({self.spent:.0f}/{self.ceiling:.0f} pts, "
                  f"{QUOTA.fraction:.0%} of {self.limit:.0f}); sleeping {wait:.0f}s "
                  f"until the window resets")
        time.sleep(wait)
        QUOTA.rolled_over()

    def _update_rate(self, data: dict) -> None:
        rl = data.get("rateLimitData")
        if rl:
            QUOTA.observe(rl)

    def query(self, gql: str, variables: dict | None = None,
              est_cost: float = 15.0) -> dict:
        """Run a GraphQL query; returns the `data` dict.

        Automatically appends rateLimitData, enforces the point budget, and
        retries on 429 / quota errors / transient failures.  GraphQL errors
        that only affect some aliases are tolerated (partial data returned).
        """
        if "rateLimitData" not in gql:
            gql = gql.rstrip()
            assert gql.endswith("}")
            gql = gql[:-1] + f" {RATE_FIELD} }}"

        backoff = 2
        auth_retried = False
        gql_failures = 0
        while True:
            # Budget guard. Reserves this request's estimated cost against the
            # ceiling before sending, so concurrent workers cannot all clear the
            # same check on the same stale reading. Refused means the ceiling is
            # reached: sleep to the window reset (or stop, under a deadline).
            if not QUOTA.admit(est_cost, self.budget_margin):
                self._sleep_for_reset()
                continue
            # The reservation is released on every way out of this iteration --
            # retry, raise or return. Leaking one would permanently shrink the
            # headroom the ceiling is computed against; on a success the real
            # cost has already replaced it via _update_rate.
            try:
                try:
                    r = self.session.post(
                        API_URL, json={"query": gql, "variables": variables or {}},
                        timeout=120)
                except requests.RequestException as e:
                    self._log(f"network error: {e}; retrying in {backoff}s")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 120)
                    continue

                if r.status_code == 429:
                    retry_after = float(r.headers.get("Retry-After", 0) or 0)
                    wait = retry_after if retry_after > 0 else max(self.reset_in, 60) + 20
                    # A 429 costs nothing, and its Retry-After is not to be
                    # trusted for long sleeps: run 4275 (2026-09-28 00:59:27Z)
                    # woke 20 s early for the window, drew a 429 with
                    # Retry-After 3620 s, slept the hour and lost the whole
                    # window. So a 429 is polled: sleep at most RETRY_429_STEP_S
                    # and re-send, until the window opens or the deadline says
                    # stop -- the same clock the ceiling path honours (a caller
                    # on a CI slot must not sleep past its deadline).
                    step = min(wait, RETRY_429_STEP_S)
                    cap = self._sleep_cap()
                    if cap and step > cap:
                        self._log(f"HTTP 429 (Retry-After {wait:.0f}s) with {cap:.0f}s left "
                                  f"before the deadline; stopping so the next run can use "
                                  f"a fresh window")
                        raise QuotaDeadline(f"HTTP 429, reset {wait:.0f}s away, cap {cap:.0f}s")
                    self._log(f"HTTP 429 (Retry-After {wait:.0f}s); re-trying in {step:.0f}s")
                    time.sleep(step)
                    continue
                if r.status_code >= 500:
                    self._log(f"HTTP {r.status_code}; retrying in {backoff}s")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 120)
                    continue
                if r.status_code in (401, 403):
                    # a cached client-credentials token may simply have expired
                    if self.token_source == "auto" and not auth_retried:
                        auth_retried = True
                        self._log("HTTP 401/403: refreshing client-credentials token")
                        (SECRETS / "wcl_token_auto").unlink(missing_ok=True)
                        self.token = _client_credentials_token(self.session)
                        self.session.headers["Authorization"] = f"Bearer {self.token}"
                        continue
                    sys.exit(f"auth failure (HTTP {r.status_code}): check WCL token")
                r.raise_for_status()

                try:
                    payload = r.json()
                except ValueError:
                    self._log(f"non-JSON 200 response; retrying in {backoff}s")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 120)
                    continue
                data = payload.get("data") or {}
                self._update_rate(data)
                QUOTA.count_request()

                errors = payload.get("errors") or []
                quota_err = [e for e in errors
                             if "quota" in e.get("message", "").lower()
                             or "rate limit" in e.get("message", "").lower()]
                if quota_err and not data.get("reportData") and not data.get("worldData"):
                    self._log(f"GraphQL quota error: {quota_err[0]['message']}")
                    self._sleep_for_reset()
                    continue
                if errors and not (data.get("reportData") or data.get("worldData")):
                    # whole-query GraphQL failure with no usable payload: transient
                    # server hiccups land here too, so retry before giving up
                    gql_failures += 1
                    if gql_failures <= 3:
                        self._log(f"GraphQL error ({gql_failures}/3): "
                                  f"{errors[0].get('message', '?')[:120]}; retrying")
                        time.sleep(backoff)
                        backoff = min(backoff * 2, 120)
                        continue
                    raise RuntimeError(f"GraphQL error: {errors[:3]}")
                # partial errors (individual aliases) are the caller's business
                data["_errors"] = errors
                return data
            finally:
                QUOTA.release(est_cost)

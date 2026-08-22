#!/usr/bin/env python3
"""Warcraft Logs v2 (GraphQL) client with quota management.

Design goals:
  * Spend the 18,000 points/hour budget aggressively (no artificial pacing),
    but stop cleanly before exhausting it and sleep until the window resets.
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
import time

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECRETS = ROOT / ".secrets"
class QuotaDeadline(Exception):
    """Raised instead of sleeping past a caller-supplied deadline.

    Deliberately NOT a RuntimeError: callers catch RuntimeError to mean "this
    batch failed, requeue it", and a deadline is the opposite -- it means stop
    asking. Subclassing RuntimeError would have it swallowed and retried.
    """


API_URL = "https://www.warcraftlogs.com/api/v2/client"
TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"

RATE_FIELD = "rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }"


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
    def __init__(self, budget_margin: float = 400.0, verbose: bool = True):
        self.session = requests.Session()
        self.token, self.token_source = get_token(self.session)
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        self.budget_margin = budget_margin
        self.verbose = verbose
        # live quota state, refreshed on every response
        self.limit = 18000.0
        self.spent = 0.0
        self.reset_in = 3600.0
        self.requests_made = 0

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[wcl {time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def _sleep_for_reset(self) -> None:
        wait = max(self.reset_in, 30) + 20  # small cushion past the reset
        # A caller on a clock (a CI job with a timeout) must not burn its whole
        # slot asleep. WCL_MAX_SLEEP_S caps how long we are willing to wait;
        # past that we stop cleanly and the next run picks up from the journal
        # with a fresh budget, instead of being killed having fetched nothing.
        cap = float(os.environ.get("WCL_MAX_SLEEP_S", 0) or 0)
        if cap and wait > cap:
            self._log(f"quota exhausted ({self.spent:.0f}/{self.limit:.0f} pts) and "
                      f"the reset is {wait:.0f}s away, over the {cap:.0f}s cap; "
                      f"stopping so the next run can use a fresh window")
            raise QuotaDeadline(f"quota reset {wait:.0f}s away, cap {cap:.0f}s")
        self._log(f"quota nearly exhausted ({self.spent:.0f}/{self.limit:.0f} pts); "
                  f"sleeping {wait:.0f}s until the window resets")
        time.sleep(wait)
        self.spent = 0.0
        self.reset_in = 3600.0

    def _update_rate(self, data: dict) -> None:
        rl = data.get("rateLimitData")
        if rl:
            self.limit = float(rl["limitPerHour"])
            self.spent = float(rl["pointsSpentThisHour"])
            self.reset_in = float(rl["pointsResetIn"])

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
            # budget guard: leave headroom for this request's estimated cost
            if self.spent + est_cost + self.budget_margin >= self.limit:
                self._sleep_for_reset()
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
                self._log(f"HTTP 429; sleeping {wait:.0f}s")
                time.sleep(wait)
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
            self.requests_made += 1

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

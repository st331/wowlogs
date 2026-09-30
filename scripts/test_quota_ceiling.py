"""Drive the real WCLClient against a fake API; assert the standing ceiling holds.

Five scenarios, each on a fresh governor (W.QUOTA) with 14 worker clients, the
way Fetch's summary stage runs:
  1. an empty hour (or START_SPENT=<pts> from the environment): the workers spend
     to the ceiling and stop cleanly, never past it
  2. the hour already over the ceiling (the owner's own lookups), probe working:
     nothing is sent
  3. the window opens LATE seconds after pointsResetIn said it would, as WCL does
     (run 4275): no request is charged to the old hour after the reported reset,
     the workers proceed only once WCL reports the new window, and the peak stays
     under the ceiling in both hours
  4. the hour already over the ceiling and the probe endpoint failing: the probe
     is retried, then at most ONE request goes out (the blind one whose response
     brings the reading) before everything stops
  5. the late window AND the post-reset probe failing: at most one request is
     charged to the old hour (the blind one), never a wave

Time is scaled: the client's time.sleep runs 1/SCALE times faster and the fake
server's clock runs on the same scale, so a scheduled Fetch step's real knobs
(WCL_MAX_SLEEP_S=480, margin 400) drive scenario 3 in a couple of seconds.
"""
import os, sys, threading, time
sys.path.insert(0, 'scripts')
os.environ['WCL_TOKEN'] = 'fake'
os.environ['WCL_MAX_SLEEP_S'] = '1'      # refuse to sleep; stop instead
import cadence
# no WCL_QUOTA_FRACTION in the environment = the standing cap in data/cadence.json
# (0.70), exactly the path a scheduled run's collectors take; START_SPENT=<pts>
# simulates an hour the owner's own lookups already used, and WCL_QUOTA_FRACTION
# a deliberate override
FRACTION = float(os.environ.get('WCL_QUOTA_FRACTION') or cadence.read_cadence()['quota_fraction'])
import wcl_client as W
assert abs(W.quota_fraction() - FRACTION) < 1e-9, f'client resolves {W.quota_fraction()} != {FRACTION}'

LIMIT, COST, WORKERS = 18000.0, 40.0, 14
CEILING = LIMIT * FRACTION
SCALE = 0.02                    # real seconds per simulated second
LATE = 60                       # how many seconds after the reported reset WCL still bills the old hour
RUNAWAY = 5000                  # more requests than any scenario can admit under the ceiling
_real_sleep = time.sleep
W.time.sleep = lambda s: _real_sleep(max(s, 0) * SCALE)
W.get_token = lambda s: ('fake', 'env')


class FakeResp:
    status_code, headers = 200, {}
    def __init__(self, b): self._b = b
    def json(self): return self._b
    def raise_for_status(self): pass


class FakeWCL:
    """One account hour on the simulated clock. `reported` is when (simulated
    seconds from construction) pointsResetIn counts down to; `actual` is when
    the spend counter really rolls (None = never within the test)."""
    def __init__(self, start_spent=0.0, reported=None, actual=None, probe_fails=False):
        self.lock = threading.Lock()
        self.t0 = time.monotonic()
        self.spent = float(start_spent)
        self.reported, self.actual, self.probe_fails = reported, actual, probe_fails
        self.rolled = False
        self.n = self.probes = self.n_old_after_reported = 0
        self.old_peak = self.new_peak = 0.0

    def now(self):
        return (time.monotonic() - self.t0) / SCALE

    def post(self, url, json=None, timeout=None, **kw):
        q = (json or {}).get('query', '')
        probe = 'rateLimitData' in q and 'worldData' not in q
        with self.lock:
            t = self.now()
            if self.actual is not None and not self.rolled and t >= self.actual:
                self.rolled, self.spent = True, 0.0
            if probe:                              # the probe query itself is ~free
                self.probes += 1
                if self.probe_fails:
                    raise W.requests.RequestException('probe endpoint down')
            else:
                self.spent += COST
                self.n += 1
                if self.n > RUNAWAY:            # a governor that never stops: fail fast
                    raise RuntimeError(f'runaway: {self.n} requests served')
                if self.rolled:
                    self.new_peak = max(self.new_peak, self.spent)
                else:
                    self.old_peak = max(self.old_peak, self.spent)
                    if self.reported is not None and t >= self.reported:
                        self.n_old_after_reported += 1
            if self.reported is None:
                reset_in = 1800.0
            elif self.rolled:
                reset_in = self.reported + 3600 - t
            else:
                reset_in = max(0.0, self.reported - t)   # WCL: "reset now", counter says otherwise
            sp = self.spent
        return FakeResp({'data': {'worldData': {}, 'rateLimitData': {
            'limitPerHour': LIMIT, 'pointsSpentThisHour': sp, 'pointsResetIn': reset_in}}})


def run(server, max_sleep):
    """14 clients hammer the fake until they stop; returns how many stopped cleanly."""
    os.environ['WCL_MAX_SLEEP_S'] = str(max_sleep)
    W.QUOTA = W._Quota()
    W.requests.Session.post = staticmethod(server.post)
    clients = [W.WCLClient(verbose=False) for _ in range(WORKERS)]
    stopped = []
    def worker(c):
        try:
            while True:                          # until the governor stops us
                c.query('{ worldData { x } }', est_cost=COST)
        except W.QuotaDeadline:
            stopped.append(1)
    ts = [threading.Thread(target=worker, args=(c,)) for c in clients]
    [t.start() for t in ts]; [t.join() for t in ts]
    return len(stopped)


def report(name, srv, stopped):
    print(f'[{name}] requests {srv.n}, probes {srv.probes}, old-hour peak {srv.old_peak:.0f} '
          f'({srv.old_peak / LIMIT:.1%}), new-hour peak {srv.new_peak:.0f}, '
          f'billed to the old hour after its reported reset {srv.n_old_after_reported}, '
          f'stopped cleanly {stopped}/{WORKERS}')


# 1. the standing scenario: spend to the ceiling, stop cleanly
start = float(os.environ.get('START_SPENT', 0))
srv = FakeWCL(start_spent=start)
stopped = run(srv, max_sleep=1)
report(f'1 from {start:.0f}', srv, stopped)
assert srv.old_peak <= CEILING, f'CEILING BREACHED {srv.old_peak} > {CEILING}'
assert stopped == WORKERS and (srv.n > 0 or start >= CEILING)

# 2. the hour already over the ceiling, probe working: nothing goes out
srv = FakeWCL(start_spent=13000)
stopped = run(srv, max_sleep=1)
report('2 over the ceiling at start', srv, stopped)
assert srv.n == 0 and stopped == WORKERS, 'a run that starts over the ceiling sent requests'

# 3. the reset comes LATE seconds after WCL said it would (the Fetch step's knobs:
#    an 8 min cap, so the workers do sleep for the reset)
srv = FakeWCL(reported=60, actual=60 + LATE)
stopped = run(srv, max_sleep=480)
report(f'3 window {LATE}s late', srv, stopped)
assert srv.n_old_after_reported == 0, \
    f'{srv.n_old_after_reported} requests billed to the old hour after its reported reset'
assert srv.old_peak <= CEILING and srv.new_peak <= CEILING, \
    f'CEILING BREACHED old {srv.old_peak} / new {srv.new_peak} > {CEILING}'
assert srv.rolled and srv.new_peak > 0, 'the workers never reached the new window'
assert stopped == WORKERS

# 4. over the ceiling at start and the probe endpoint down: retried, then blind --
#    at most one request before the reading lands, then everything stops
srv = FakeWCL(start_spent=13000, probe_fails=True)
stopped = run(srv, max_sleep=1)
report('4 over the ceiling, probe down', srv, stopped)
assert srv.probes == W.PROBE_ATTEMPTS, f'probe attempted {srv.probes} times, not {W.PROBE_ATTEMPTS}'
assert srv.n <= 1, f'a blind start sent {srv.n} requests before the first reading'
assert stopped == WORKERS

# 5. the late window with the post-reset probe failing too: the blind fallback
#    bills at most one request to the old hour, never a wave
srv = FakeWCL(reported=60, actual=60 + LATE, probe_fails=True)
stopped = run(srv, max_sleep=480)
report(f'5 window {LATE}s late, probe down', srv, stopped)
assert srv.n_old_after_reported <= 1, \
    f'{srv.n_old_after_reported} requests billed to the old hour after its reported reset'
assert srv.old_peak <= CEILING and srv.new_peak <= CEILING
assert srv.rolled and srv.new_peak > 0 and stopped == WORKERS

W.time.sleep = _real_sleep
print(f'PASS  never exceeded {FRACTION:.0%}; a late window and a failed probe cost at most one request\n')

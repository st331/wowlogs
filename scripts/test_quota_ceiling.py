"""Drive the real WCLClient against a fake API; assert the 70% ceiling holds."""
import os, sys, threading
sys.path.insert(0, 'scripts')
os.environ['WCL_TOKEN'] = 'fake'
os.environ['WCL_MAX_SLEEP_S'] = '1'      # refuse to sleep; stop instead
FRACTION = float(os.environ.setdefault('WCL_QUOTA_FRACTION', '0.70'))
import wcl_client as W

LIMIT, COST = 18000.0, 40.0
st = {'spent': float(os.environ.get('START_SPENT', 0)), 'peak': 0.0, 'n': 0}
lock = threading.Lock()

class FakeResp:
    status_code, headers = 200, {}
    def __init__(self, b): self._b = b
    def json(self): return self._b
    def raise_for_status(self): pass

def fake_post(url, json=None, timeout=None, **kw):
    probe = 'rateLimitData' in (json or {}).get('query', '') and 'worldData' not in (json or {}).get('query', '')
    with lock:
        if not probe:                      # the probe query itself is ~free
            st['spent'] += COST
            st['peak'] = max(st['peak'], st['spent']); st['n'] += 1
        sp = st['spent']
    return FakeResp({'data': {'worldData': {}, 'rateLimitData': {
        'limitPerHour': LIMIT, 'pointsSpentThisHour': sp, 'pointsResetIn': 1800}}})

W.requests.Session.post = staticmethod(fake_post)
W.get_token = lambda s: ('fake', 'env')

clients = [W.WCLClient(verbose=False) for _ in range(14)]
stopped = []
def worker(c):
    try:
        for _ in range(400):
            c.query('{ worldData { x } }', est_cost=COST)
    except W.QuotaDeadline:
        stopped.append(1)
ts = [threading.Thread(target=worker, args=(c,)) for c in clients]
[t.start() for t in ts]; [t.join() for t in ts]

ceiling = LIMIT * FRACTION
print(f'start spend   : {os.environ.get("START_SPENT","0")}')
print(f'requests sent : {st["n"]}')
print(f'peak spend    : {st["peak"]:.0f}  ({st["peak"]/LIMIT:.1%} of account limit)')
print(f'ceiling       : {ceiling:.0f}  ({FRACTION:.0%})')
print(f'workers stopped cleanly: {len(stopped)}/14')
assert st['peak'] <= ceiling, f'CEILING BREACHED {st["peak"]} > {ceiling}'
print(f'PASS  never exceeded {FRACTION:.0%}\n')

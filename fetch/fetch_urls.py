"""Fetch every URL in fetch/queue/*.txt -> fetch/raw/<name>/<sha1>.txt + index.json. Runs on GitHub Actions.
One worker per host, 1 request/second per host, retries on 429/5xx, stops before the time budget so the commit step always runs.
Exit code 3 means work remains (the workflow re-dispatches itself)."""
import hashlib, json, os, sys, threading, time, urllib.request, urllib.error
from urllib.parse import urlsplit
Q, R = 'fetch/queue', 'fetch/raw'
UA = 'upsc-study-bot/1.0 (personal study; contact via github dileep143-droid)'
BUDGET = int(os.environ.get('FETCH_BUDGET_SEC', '1200'))
RETRY = {0, 429, 500, 502, 503, 504}
t0 = time.time()
lock = threading.Lock()
jobs = {}      # name -> (out_dir, idx dict, urls list)
byhost = {}    # host -> [(name, url)]
for name in sorted(os.listdir(Q)) if os.path.isdir(Q) else []:
    if not name.endswith('.txt'): continue
    out = os.path.join(R, name[:-4]); idx_p = os.path.join(out, 'index.json')
    idx = json.load(open(idx_p, encoding='utf-8')) if os.path.exists(idx_p) else {}
    urls = [u.strip() for u in open(os.path.join(Q, name), encoding='utf-8') if u.strip().startswith('http')]
    todo = [u for u in urls if u not in idx or (idx[u].get('status') in RETRY and idx[u].get('tries', 1) < 3)]
    jobs[name] = (out, idx, urls)
    for u in todo: byhost.setdefault(urlsplit(u).netloc, []).append((name, u))
total = sum(len(v) for v in byhost.values()); done = 0
def fetch(u):
    try:
        req = urllib.request.Request(u, headers={'User-Agent': UA, 'Accept-Language': 'ja,en;q=0.8'})
        with urllib.request.urlopen(req, timeout=30) as r: return r.status, r.read(), r.headers.get('Retry-After')
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if hasattr(e, 'read') else b''), e.headers.get('Retry-After')
    except Exception as e:
        return 0, str(e).encode(), None
def worker(host, items):
    global done
    for name, u in items:
        if time.time() - t0 > BUDGET: return
        status, body, ra = fetch(u)
        if status == 429 and ra and ra.isdigit() and int(ra) <= 60:
            time.sleep(int(ra)); status, body, ra = fetch(u)
        out, idx, _ = jobs[name]
        h = hashlib.sha1(u.encode()).hexdigest()
        with lock:
            os.makedirs(out, exist_ok=True)
            open(os.path.join(out, h + '.txt'), 'wb').write(body)
            prev = idx.get(u, {})
            idx[u] = {'file': h + '.txt', 'status': status, 'tries': prev.get('tries', 0) + 1}
            done += 1
        time.sleep(2.0 if status == 429 else 1.0)
threads = [threading.Thread(target=worker, args=(h, items), daemon=True) for h, items in byhost.items()]
for t in threads: t.start()
for t in threads: t.join(BUDGET + 60)
for name, (out, idx, urls) in jobs.items():
    if idx:
        os.makedirs(out, exist_ok=True)
        json.dump(idx, open(os.path.join(out, 'index.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    ok = sum(1 for u in urls if idx.get(u, {}).get('status') == 200)
    print(f'{name}: {ok} ok / {len(idx)} tried / {len(urls)} queued')
remaining = total - done
print(f'fetched {done} of {total} pending this run across {len(byhost)} hosts in {int(time.time() - t0)} s; remaining {remaining}')
sys.exit(3 if remaining > 0 else 0)

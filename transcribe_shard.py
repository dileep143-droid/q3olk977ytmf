r"""One shard of the Telugu-cartoon transcription, for GitHub Actions (public repo = free minutes).
env: GEMINI_API_KEYS (comma-separated, the owner's own keys), REPO_TOKEN (PAT with repo scope for the PRIVATE repo),
     SHARD (0-based), SHARDS (count), PRIVATE_REPO (owner/name), BUDGET_SEC (default 5h15m).
Reads video indexes + progress from the private repo, downloads audio (Android client), transcribes with Gemini
(same prompt as transcribe_batch.py), writes transcripts/<Show>/<id>.md + transcripts/progress_shard_<n>.json into the
PRIVATE repo and pushes after every episode. Keys are never printed. Nothing is written to this public repo."""
import base64, glob, hashlib, json, os, re, subprocess, sys, time, urllib.error, urllib.request
sys.stdout.reconfigure(encoding="utf-8")
KEYS = [k.strip() for k in os.environ.get("GEMINI_API_KEYS", "").split(",") if k.strip()]
TOK = os.environ["REPO_TOKEN"]; SHARD = int(os.environ.get("SHARD", "0")); SHARDS = int(os.environ.get("SHARDS", "6"))
PRIV = os.environ.get("PRIVATE_REPO", "dileep143-droid/upsc-cloud"); BUDGET = int(os.environ.get("BUDGET_SEC", str(5 * 3600 + 900)))
if not KEYS: raise SystemExit("GEMINI_API_KEYS secret is empty")
KEYS = KEYS[SHARD % len(KEYS):] + KEYS[:SHARD % len(KEYS)]   # each shard prefers its own key, so the daily quota spreads over all keys
t0 = time.time()
W = os.path.abspath("private")
def sh(*a, **k): return subprocess.run(a, capture_output=True, text=True, encoding="utf-8", **k)
def clean(s): return (s or "").replace(TOK, "***")
if not os.path.exists(W):
    # the token goes into the git credential helper, never into a URL or a log
    r = sh("git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", "-c", "credential.helper=",
           "-c", "http.extraheader=AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{TOK}".encode()).decode(),
           f"https://github.com/{PRIV}.git", W)
    print("clone", r.returncode, clean(r.stderr)[-200:], flush=True)
    sh("git", "-C", W, "config", "http.extraheader", "AUTHORIZATION: basic " + base64.b64encode(f"x-access-token:{TOK}".encode()).decode())
    sh("git", "-C", W, "sparse-checkout", "set", "upsc/plan/doraemon", "--no-cone")
    sh("git", "-C", W, "config", "user.name", "transcribe-bot"); sh("git", "-C", W, "config", "user.email", "transcribe-bot@users.noreply.github.com")
H = os.path.join(W, "upsc", "plan", "doraemon"); OUT = os.path.join(H, "transcripts"); os.makedirs(OUT, exist_ok=True)
TMP = os.path.abspath("audio_tmp"); os.makedirs(TMP, exist_ok=True)
src = open(os.path.join(H, "transcribe_batch.py"), encoding="utf-8").read()
SHOWS = eval(src[src.index("SHOWS = ["):src.index("]\nFAN")].replace("SHOWS = ", "", 1) + "]")
FAN = re.compile(r"gta|franklin|minecraft|gameplay|free ?fire|real life|thug life|roblox|spider|lord ganesha|avengers|pubg|\bgame\b|horror|untold|status|whatsapp|reaction|drawing|toy|unboxing|live\b|- live", re.I)
TEL = re.compile(r"telugu|[ఀ-౿]", re.I)
BLOCK = re.compile(r"not a bot|Sign in to confirm|This video is not available|HTTP Error 429|timed out|Requested format|ffmpeg", re.I)
CLIENT = ["--extractor-args", "youtube:player_client=android,web"]
def all_progress():
    p = {}
    for f in glob.glob(os.path.join(OUT, "progress*.json")):
        try: p.update(json.load(open(f, encoding="utf-8")))
        except Exception: pass
    return p
MYP = os.path.join(OUT, f"progress_shard_{SHARD}.json")
mine = json.load(open(MYP, encoding="utf-8")) if os.path.exists(MYP) else {}
def save_mine(): json.dump(mine, open(MYP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
def push(msg):
    for i in range(20):
        sh("git", "-C", W, "add", "-A", "upsc/plan/doraemon/transcripts")
        sh("git", "-C", W, "commit", "-qm", msg)
        sh("git", "-C", W, "pull", "--rebase", "-q", "origin", "main")
        r = sh("git", "-C", W, "push", "-q", "origin", "HEAD:main")
        if r.returncode == 0: return True
        sh("git", "-C", W, "rebase", "--abort"); time.sleep(3 + i)
    print("push failed 20x:", clean(r.stderr)[-200:], flush=True); return False
cands = []
for d in ("yt_subs_other", "yt_subs_kick", "yt_subs"):
    p = os.path.join(H, d, "index.json")
    if not os.path.exists(p): continue
    for vid, v in json.load(open(p, encoding="utf-8")).items():
        t = v.get("title", ""); dur = v.get("duration") or 0
        if not TEL.search(t) or FAN.search(t) or not (300 <= dur <= 2100): continue
        if d == "yt_subs" and v.get("subs"): continue
        show = next((s for s in SHOWS if re.search(s[1], t, re.I)), None)
        if show and int(hashlib.sha1(vid.encode()).hexdigest(), 16) % SHARDS == SHARD: cands.append((show, vid, t, dur))
prog = all_progress()
def skip(v):
    if v.get("status") in ("done", "partial", "working"): return True
    return v.get("status") == "unavailable" and not BLOCK.search(v.get("err", ""))
queue = [c for c in cands if not skip(prog.get(c[1], {}))]
print(f"shard {SHARD}/{SHARDS}: {len(queue)} videos to do, {len(KEYS)} keys", flush=True)
EXH = set()
def gemini(path, show, guide, offset):
    prompt = f"""Telugu dub of the cartoon "{show}". Voice guide: {guide}
This audio piece starts at {offset//60:02d}:{offset%60:02d} of the episode. Transcribe it VERBATIM, every utterance, in order.
FORMAT: one utterance per line, each on its OWN line:
[mm:ss] Speaker (tone in 2 words): exact words
- mm:ss = time in the whole episode (add the start offset). Telugu in Telugu script; English words as spoken.
- Decide the speaker from the voice and the voice guide. If not sure, write "Unknown (male/female, adult/child)" — never guess.
- Keep nicknames, insults, catchphrases, exclamations exactly. Sound effects as [laugh] [crash] on their own line only if important.
Transcript only, no summary."""
    body = {"contents": [{"parts": [{"text": prompt}, {"inlineData": {"mimeType": "audio/mp3", "data": base64.b64encode(open(path, "rb").read()).decode()}}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 16000}}
    combos = [(k, m) for m in ("gemini-3.8-flash", "gemini-3.5-flash-lite") for k in KEYS if (k, m) not in EXH]
    if not combos: raise SystemExit("QUOTA")
    for key, model in combos:
        for attempt in range(3):
            req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                         data=json.dumps(body).encode(), headers={"x-goog-api-key": key, "Content-Type": "application/json"})
            try:
                j = json.load(urllib.request.urlopen(req, timeout=600))
                return model, "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    if "PerDay" in e.read().decode(): EXH.add((key, model)); print(f"  daily limit: {model} key {KEYS.index(key)+1}", flush=True); break
                    time.sleep(30); continue
                if e.code in (500, 503): time.sleep(20 * (attempt + 1)); continue
                if e.code in (400, 403): EXH.add((key, model)); print(f"  key {KEYS.index(key)+1} rejected ({e.code})", flush=True); break
                break
            except Exception: time.sleep(10)
    return None, ""
done_n = 0
for (show, rx, guide), vid, title, dur in queue:
    if time.time() - t0 > BUDGET: print("time budget reached", flush=True); break
    mine[vid] = {"status": "working", "show": show, "title": title, "shard": SHARD}; save_mine()
    base = os.path.join(TMP, vid)
    r = sh(sys.executable, "-m", "yt_dlp", "--no-warnings", *CLIENT, "-f", "bestaudio/best", "-x", "--audio-format", "mp3",
           "--postprocessor-args", "ffmpeg:-ac 1 -b:a 32k", "-o", base + ".%(ext)s", "https://www.youtube.com/watch?v=" + vid)
    if not os.path.exists(base + ".mp3"):
        if BLOCK.search(r.stderr or ""):
            print("YouTube blocks this runner:", (r.stderr or "").strip()[-100:], flush=True); mine.pop(vid, None); save_mine(); push(f"transcribe shard {SHARD}: blocked"); sys.exit(0)
        mine[vid] = {"status": "unavailable", "show": show, "title": title, "err": (r.stderr or "")[-160:]}; save_mine(); continue
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", base + ".mp3", "-f", "segment", "-segment_time", "300", "-c", "copy", base + "_part%02d.mp3"])
    parts = sorted(glob.glob(base + "_part*.mp3")); lines = []; used = set(); quota = False
    for i, pth in enumerate(parts):
        try:
            model, txt = gemini(pth, show, guide, i * 300); used.add(model)
            lines.append(txt.strip() if txt else f"[piece {i} failed]"); time.sleep(4)
        except SystemExit:
            quota = True; break
    if quota:
        print("all keys exhausted for today", flush=True); mine.pop(vid, None); save_mine(); push(f"transcribe shard {SHARD}: quota"); break
    sdir = os.path.join(OUT, re.sub(r"\W+", "_", show)); os.makedirs(sdir, exist_ok=True)
    md = "# %s — %s\nhttps://www.youtube.com/watch?v=%s · %d min · models: %s\n\n%s\n" % (show, title, vid, dur // 60, ", ".join(sorted(m for m in used if m)), "\n".join(lines))
    open(os.path.join(sdir, vid + ".md"), "w", encoding="utf-8").write(md)
    ok = sum(1 for l in lines if not l.startswith("[piece"))
    mine[vid] = {"status": "done" if ok == len(parts) else "partial", "show": show, "title": title, "pieces": len(parts), "ok": ok, "shard": SHARD}
    save_mine(); done_n += 1
    for f in [base + ".mp3"] + parts:
        try: os.remove(f)
        except OSError: pass
    print("  %-22s %-50s | %d/%d pieces" % (show, title[:50], ok, len(parts)), flush=True)
    push(f"transcribe shard {SHARD}: {show} {vid}")
push(f"transcribe shard {SHARD}: end of run, {done_n} episodes")
print(f"FINISHED shard {SHARD}: {done_n} episodes this run", flush=True)

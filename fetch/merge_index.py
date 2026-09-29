"""Resolve rebase conflicts in fetch/raw/*/index.json by taking the union of both sides, then continue the rebase."""
import json, subprocess, sys
def sh(*a): return subprocess.run(a, capture_output=True, text=True)
conflicted = sh('git', 'diff', '--name-only', '--diff-filter=U').stdout.split()
for p in conflicted:
    if not p.endswith('index.json'):
        print('cannot auto-merge', p); sys.exit(1)
    upstream = json.loads(sh('git', 'show', ':2:' + p).stdout or '{}')
    mine = json.loads(sh('git', 'show', ':3:' + p).stdout or '{}')
    merged = {**upstream, **mine}
    json.dump(merged, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    sh('git', 'add', p)
    print('merged', p, len(upstream), '+', len(mine), '->', len(merged))
r = sh('git', '-c', 'core.editor=true', 'rebase', '--continue')
print(r.stdout[-300:], r.stderr[-300:])
sys.exit(r.returncode)

import json, os, re, sys, collections
R='/srv/jarvis/home/hq-neko/jarvis/evals/neko95'
MOCKED = re.compile(r"(?i)\b(mock(?:ed|s)?|fake[sd]?|faked|hand-written|recorded|recorder|stand-?in|stands in|scripted|stub(?:bed)?)\b")
cases={}
for n in sorted(os.listdir(R+'/bench/cases') if os.path.isdir(R+'/bench/cases') else []):
    if n.endswith('.json'):
        for c in json.load(open(R+'/bench/cases/'+n)):
            if isinstance(c,dict) and c.get('id'): cases[c['id']]=c
print('case files cases', len(cases))
def ev(c):
    if not c: return 'integration'
    e=c.get('evidence')
    if e in ('live','integration','mocked'): return e
    return 'mocked' if MOCKED.search(str(c.get('scenario',''))) else 'integration'
f=sys.argv[1]
d=json.load(open(R+'/results/'+f))
rows=d['cases']
tab=collections.defaultdict(collections.Counter)
evc=collections.Counter(); explicit=collections.Counter(); missing=0
for r in rows:
    c=cases.get(r['id'])
    if c is None: missing+=1
    if c and c.get('evidence'): explicit[c['evidence']]+=1
    e=ev(c); evc[e]+=1
    tab[r['area']][r['status']]+=1; tab[r['area']]['ev_'+e]+=1
print(f, d['counts'], 'rows',len(rows),'missing case defs',missing)
print('evidence:',dict(evc),'explicit field:',dict(explicit))
print('area | n | pass | fail | error | deferred | integration | mocked | live')
for a in sorted(tab):
    t=tab[a]; n=sum(v for k,v in t.items() if not k.startswith('ev_'))
    print(a,'|',n,'|',t['pass'],'|',t['fail'],'|',t['error'],'|',t['deferred'],'|',t['ev_integration'],'|',t['ev_mocked'],'|',t['ev_live'])
# evidence by status
st=collections.Counter((r['status'],ev(cases.get(r['id']))) for r in rows); print(dict(st))
if '-v' in sys.argv:
    for r in rows:
        if r['status']!='pass': print('NONPASS',r['id'],r['area'],r['status'],r.get('risk'),'|',(cases.get(r['id']) or {}).get('platform','any'),'|',str(r.get('observed'))[:220].replace('\n',' '))

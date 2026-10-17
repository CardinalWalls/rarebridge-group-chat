"""Symbolic transition relations; lazy or exhaustive querying uses identical constraints."""
import json
import z3 as z
from model import World, RULES, stable, tri, enrich_pathology, snapshot_relation

KINDS=['measurement_change','clinical_change','eligibility_change','evidence_completion','correction','representation_change','no_evidence_change']

def relation(a,b,kind):
    eq_lab=[];narrow=[];different_lab=[]
    for k,x in a.labs.items():
        y=b.labs[k]
        equal=z.And(x.lo==y.lo,x.hi==y.hi,x.inf==y.inf,x.lopen==y.lopen,x.hopen==y.hopen)
        eq_lab.append(equal);different_lab.append(z.Not(equal))
        # New interval is contained in old interval, including open endpoints.
        narrow.append(z.And(y.lo>=x.lo,z.Implies(z.And(y.lo==x.lo,x.lopen),y.lopen),
            z.Or(x.inf,z.And(z.Not(y.inf),y.hi<=x.hi,z.Implies(z.And(y.hi==x.hi,x.hopen),y.hopen)))))
    eq_clin=[a.clin[k]==b.clin[k] for k in a.clin]
    all_eq=z.And(*eq_lab,*eq_clin,a.tg_mmol==b.tg_mmol)
    raw_diff=z.Not(all_eq)
    if kind=='measurement_change':return z.And(*eq_clin,a.tg_mmol==b.tg_mmol,z.Or(*different_lab))
    if kind=='clinical_change':
        return z.And(*eq_lab,a.tg_mmol==b.tg_mmol,*[a.clin[k]==b.clin[k] for k in ['sjia','fever']],z.Or(*[a.clin[k]!=b.clin[k] for k in a.clin if k not in ['sjia','fever']]))
    if kind=='eligibility_change':
        return z.And(*eq_lab,a.tg_mmol==b.tg_mmol,*[a.clin[k]==b.clin[k] for k in a.clin if k not in ['sjia','fever']],z.Or(a.clin['fever']!=b.clin['fever'],a.clin['sjia']!=b.clin['sjia']))
    if kind=='evidence_completion':return z.And(*narrow,*[z.Or(a.clin[k]==-1,a.clin[k]==b.clin[k]) for k in a.clin],a.tg_mmol==b.tg_mmol,raw_diff)
    if kind=='correction':return raw_diff
    # Unit spelling and source metadata are outside canonical symbolic state; canonical content identical.
    if kind in ['representation_change','no_evidence_change']:return all_eq
    raise ValueError(kind)

def check_pair(sig_a,sig_b,kind,timeout=10000):
    raise RuntimeError('Retired terminal-label projection; use 路径研究/generate.py and source-mapped node traces.')

def describe_space(catalog):
    raise RuntimeError('Retired terminal-label projection; use 路径研究/generate.py and source-mapped node traces.')

def generate_completion_catalog(timeout=3000):
    raise RuntimeError('Retired terminal-label projection; use 路径研究/generate.py and source-mapped node traces.')

def local_implications():
    """Prove impossible local combinations hidden by coarser final results."""
    import itertools
    rows=[]
    for name,k,aop,ac,bop,bc in [('ferritin_thresholds','ferritin','>',684,'>=',500),('platelet_thresholds','platelets','<',100,'<=',181),('fibrinogen_thresholds','fibrinogen','<=',150,'<=',360)]:
        w=World();s=w.solver()
        for aa,bb in itertools.product([-1,0,1],repeat=2):
            s.push();s.add(tri(w.labs[k].compare(aop,ac))==aa,tri(w.labs[k].compare(bop,bc))==bb);st=s.check()
            row={'predicate_pair':name,'states':[aa,bb],'satisfiability':str(st)}
            if st==z.sat:row['witness']=enrich_pathology(w.witness(s.model()))
            else:row['reason']='Shared numeric interval cannot satisfy this pair of threshold relations.'
            rows.append(row);s.pop()
    return rows

def enumerate_relation(kind,timeout=10000):
    raise RuntimeError('Retired terminal-label projection; use 路径研究/generate.py and source-mapped node traces.')

"""Exact rule abstraction. Solver witnesses describe evidence, not clinical patients."""
from __future__ import annotations
import argparse, copy, hashlib, itertools, json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import z3 as z
import jsonschema

HERE = Path(__file__).resolve().parent
RULES = json.loads((HERE / 'rules.json').read_text())
VERSION = RULES['version']
LABELS = {-1:'unknown', 0:'not_met', 1:'met'}
BOOL_VALUES = {'unknown':-1,'absent':0,'present':1}

def Q(v): return v if isinstance(v,z.ArithRef) else z.RealVal(str(v))
def stable(kind, value):
    raw=json.dumps([VERSION,kind,value],sort_keys=True,separators=(',',':'))
    return kind+'-'+hashlib.sha256(raw.encode()).hexdigest()[:16]
def jnum(v):
    v=z.simplify(v)
    if z.is_rational_value(v):
        n,d=v.numerator_as_long(),v.denominator_as_long()
        if d==1:return str(n)
        remain=d
        for factor in [2,5]:
            while remain%factor==0:remain//=factor
        if remain==1:
            from decimal import localcontext
            with localcontext() as ctx:
                ctx.prec=max(len(str(abs(n)))+len(str(d))+10,50)
                return format(Decimal(n)/Decimal(d),'f')
        return f'{n}/{d}'
    raise ValueError('non-rational model value')
def dec(v):
    if isinstance(v,bool): raise ValueError('boolean is not a number')
    s=str(v)
    if '/' in s:
        n,d=s.split('/'); return Decimal(n)/Decimal(d)
    r=Decimal(s)
    if not r.is_finite():raise ValueError('nonfinite value')
    return r

def both(xs): return z.And(*xs) if xs else z.BoolVal(True)
def either(xs): return z.Or(*xs) if xs else z.BoolVal(False)
def count(xs,k): return z.Sum([z.If(x,1,0) for x in xs])>=k

def tand(*ps): return (both([p[0] for p in ps]),both([p[1] for p in ps]))
def tor(*ps): return (either([p[0] for p in ps]),either([p[1] for p in ps]))
def atleast(ps,k):return (count([p[0] for p in ps],k),count([p[1] for p in ps],k))
def tri(p):return z.If(p[0],1,z.If(p[1],-1,0))

class Interval:
    def __init__(self,name):
        self.lo=z.Real(name+'_lo'); self.hi=z.Real(name+'_hi')
        self.lopen=z.Bool(name+'_lopen'); self.hopen=z.Bool(name+'_hopen');self.inf=z.Bool(name+'_inf')
    def constraints(self):
        return [self.lo>=0,z.Implies(z.Not(self.inf),z.Or(self.hi>self.lo,z.And(self.hi==self.lo,z.Not(self.lopen),z.Not(self.hopen)))),z.Implies(self.inf,z.And(self.hi==self.lo,z.Not(self.hopen)))]
    def compare(self,op,c):
        c=Q(c) if not isinstance(c,z.ExprRef) else c
        if op=='>':return (z.Or(self.lo>c,z.And(self.lo==c,self.lopen)),z.Or(self.inf,self.hi>c))
        if op=='>=':return (self.lo>=c,z.Or(self.inf,self.hi>c,z.And(self.hi==c,z.Not(self.hopen))))
        if op=='<':return (z.And(z.Not(self.inf),z.Or(self.hi<c,z.And(self.hi==c,self.hopen))),self.lo<c)
        if op=='<=':return (z.And(z.Not(self.inf),self.hi<=c),z.Or(self.lo<c,z.And(self.lo==c,z.Not(self.lopen))))
        raise ValueError(op)

class World:
    def __init__(self,prefix=''):
        self.labs={k:Interval(prefix+k) for k in RULES['labs']}
        self.clin={k:z.Int(prefix+k) for k in RULES['clinical']}
        self.tg_mmol=z.Bool(prefix+'tg_reported_mmol')
        self.constraints=[c for x in self.labs.values() for c in x.constraints()]+[z.And(v>=-1,v<=1) for v in self.clin.values()]
        self.clinical_pairs={k:(v==1,v!=0) for k,v in self.clin.items()}
        self.eligibility=tri(tand(*(self.clinical_pairs[k] for k in RULES['mas_2016']['eligibility'])))
        self.review=self.clin['alternative_causes_excluded']
        self.mas_items=[self.labs[k].compare(op,c) for k,op,c in RULES['mas_2016']['additional']]
        gate=RULES['mas_2016']['required'];self.mas=tri(tand(self.labs[gate[0]].compare(gate[1],gate[2]),atleast(self.mas_items,RULES['mas_2016']['at_least'])))
        lower=[];upper=[];lower_inf=[];upper_inf=[];lo_open=[];hi_open=[];complete=[]
        for k,c in RULES['ms_2019']['coefficients'].items():
            coef=Q(c); positive=Decimal(c)>0
            if k in self.labs:
                x=self.labs[k]
                lower.append(coef*(x.lo if positive else x.hi));upper.append(coef*(x.hi if positive else x.lo))
                lower_inf.append(z.And(x.inf,z.BoolVal(not positive)));upper_inf.append(z.And(x.inf,z.BoolVal(positive)))
                lo_open.append(x.lopen if positive else x.hopen);hi_open.append(x.hopen if positive else x.lopen)
                complete.append(z.And(z.Not(x.inf),x.lo==x.hi,z.Not(x.lopen),z.Not(x.hopen)))
            else:
                v=self.clin[k];vmin=z.If(v==-1,0,v);vmax=z.If(v==-1,1,v)
                lower.append(coef*(vmin if positive else vmax));upper.append(coef*(vmax if positive else vmin));complete.append(v!=-1)
        self.ms_lo=z.Sum(lower);self.ms_hi=z.Sum(upper)
        self.ms_lo_inf=either(lower_inf);self.ms_hi_inf=either(upper_inf)
        self.ms_lo_open=either(lo_open);self.ms_hi_open=either(hi_open)
        self.ms_complete=both(complete)
        cutoff=Q(RULES['ms_2019']['cutoff'])
        ms_all=z.And(z.Not(self.ms_lo_inf),self.ms_lo>=cutoff)
        ms_possible=z.Or(self.ms_hi_inf,self.ms_hi>cutoff,z.And(self.ms_hi==cutoff,z.Not(self.ms_hi_open)))
        self.ms=tri((ms_all,ms_possible))
        infant=self.clin['infant_under_4_weeks']
        h=RULES['hlh_2004'];hb=self.labs['hemoglobin']; hb90=hb.compare('<',h['hemoglobin_cutoff']);hb100=hb.compare('<',h['infant_hemoglobin_cutoff'])
        hbp=(z.If(infant==1,hb100[0],hb90[0]),z.If(infant==0,hb90[1],hb100[1]))
        cell=atleast([hbp,self.labs['platelets'].compare('<',h['platelets_cutoff']),self.labs['anc'].compare('<',h['anc_cutoff'])],2)
        tg=self.labs['triglycerides'].compare('>=',z.If(self.tg_mmol,Q(h['triglycerides_mmol_l_cutoff'])*Q(RULES['labs']['triglycerides']['aliases']['mmol/L']),Q(h['triglycerides_mg_dl_cutoff'])))
        fasting=self.clin['fasting']
        usable_tg=(z.And(fasting==1,tg[0]),z.Or(fasting!=1,tg[1]))
        lipid=tor(usable_tg,self.labs['fibrinogen'].compare('<=',h['fibrinogen_cutoff']))
        cp=self.clinical_pairs
        self.hlh_hb=hbp;self.hlh_tg=usable_tg;self.hlh_lipid=lipid
        self.hlh_items=[cp['fever'],cp['splenomegaly'],cell,lipid,cp['hemophagocytosis'],cp['nk_activity_low'],self.labs['ferritin'].compare('>=',h['ferritin_cutoff']),self.labs['scd25'].compare('>=',h['scd25_cutoff'])]
        self.hlh_five=tri(atleast(self.hlh_items,h['at_least']))
        self.hlh=tri(tor(cp['molecular_hlh'],atleast(self.hlh_items,h['at_least'])))
    def solver(self,timeout=10000):
        s=z.Solver();s.set(timeout=timeout);s.add(*self.constraints);return s
    def witness(self,m):
        labs={}
        for k,x in self.labs.items():
            ev=lambda q:m.eval(q,model_completion=True)
            lo,hi=jnum(ev(x.lo)),jnum(ev(x.hi));inf=z.is_true(ev(x.inf));lop=z.is_true(ev(x.lopen));hop=z.is_true(ev(x.hopen))
            unit=RULES['labs'][k]['unit']
            if k=='triglycerides' and z.is_true(ev(self.tg_mmol)):
                lo=jnum(ev(x.lo)/Q('88.57'));hi=jnum(ev(x.hi)/Q('88.57'));unit='mmol/L'
            labs[k]={'lower':lo,'upper':None if inf else hi,'lower_open':lop,'upper_open':hop,'unit':unit}
        clinical={k:LABELS[int(str(m.eval(v,model_completion=True)))] for k,v in self.clin.items()}
        clinical={k:{'met':'present','not_met':'absent','unknown':'unknown'}[v] for k,v in clinical.items()}
        return {'patient_id':'synthetic','observed_at':'2026-01-19T10:00:00+00:00','recorded_at':'2026-01-19T10:00:00+00:00','labs':labs,'clinical':clinical,'identity':'synthetic mathematical evidence witness', 'source':'solver-generated'}

def normalize(item,k):
    if item is None or item.get('state')=='unknown':return ('0',None,False,False,RULES['labs'][k]['unit'])
    if not ('value' in item or 'lower' in item or 'upper' in item):raise ValueError('numeric value or explicit interval required')
    unit=item.get('unit'); aliases=RULES['labs'][k]['aliases']
    if unit not in aliases:raise ValueError(f'{k}: unsupported unit {unit}')
    f=Q(aliases[unit])
    if 'value' in item:
        v=Q(item['value'])*f;op=item.get('operator','=')
        if op=='=':return (v,v,False,False,unit)
        if op in ['>','>=']:return (v,None,op=='>',False,unit)
        if op in ['<','<=']:return (Q(0),v,False,op=='<',unit)
        raise ValueError('unknown numeric operator')
    lo=Q(item.get('lower','0'))*f;hi=None if item.get('upper') is None else Q(item['upper'])*f
    return lo,hi,item.get('lower_open',False),item.get('upper_open',False),unit

def bind(w,snapshot):
    constraints=[];issues=[]
    for k,x in w.labs.items():
        item=snapshot.get('labs',{}).get(k)
        if isinstance(item,dict) and item.get('state')=='conflict':issues.append({'field':k,'reason':'conflict'});continue
        try:
            lo,hi,lop,hop,unit=normalize(item,k)
            constraints.extend([x.lo==lo,x.hi==(lo if hi is None else hi),x.inf==(hi is None),x.lopen==lop,x.hopen==hop])
            if k=='triglycerides':constraints.append(w.tg_mmol==(unit=='mmol/L'))
        except (ValueError,z.Z3Exception,KeyError) as e:issues.append({'field':k,'reason':str(e)})
    for k,v in w.clin.items():
        item=snapshot.get('clinical',{}).get(k,'unknown')
        state=item.get('state','unknown') if isinstance(item,dict) else item
        if state not in BOOL_VALUES:issues.append({'field':k,'reason':'invalid_or_conflicting_clinical_state'});continue
        if k=='hemophagocytosis' and state=='present':
            site=item.get('site') if isinstance(item,dict) else None
            if site not in RULES['hlh_2004']['pathology_sites']:issues.append({'field':k,'reason':'confirmed_pathology_site_required'})
        constraints.append(v==BOOL_VALUES[state])
    return constraints,issues

def enrich_pathology(snapshot):
    # Generated witnesses explicitly include the required site; real inputs must provide it.
    d=copy.deepcopy(snapshot)
    if d.get('identity','').startswith('synthetic') and d['clinical'].get('hemophagocytosis')=='present':d['clinical']['hemophagocytosis']={'state':'present','site':'bone_marrow'}
    return d

def evaluate(snapshot):
    try:
        jsonschema.validate(snapshot,json.loads((HERE/'input.schema.json').read_text()),format_checker=jsonschema.FormatChecker())
    except jsonschema.ValidationError as e:
        return {'status':'invalid_evidence','issues':[{'reason':e.message,'path':list(e.absolute_path)}],'rule_version':VERSION}
    for key in ['patient_id','source']:
        if not snapshot.get(key):raise ValueError(f'{key} is required')
    temporal_checks=[]
    def check_time(left,right,field,reason,raise_future=False):
        relation=_evidence_order(left,right)
        temporal_checks.append({'field':field,'status':'not_asserted' if relation is None else 'checked'})
        if relation is not None and relation>0:
            if raise_future:raise ValueError(reason)
            return {'status':'invalid_evidence','issues':[{'field':field,'reason':reason}],'rule_version':VERSION}
    # Validate all declared time values, even when the comparison is indeterminate.
    for key in ('observed_at','recorded_at'):_evidence_time(snapshot[key])
    check_time(snapshot['observed_at'],snapshot['recorded_at'],'snapshot','observation after recorded time',True)
    for group in ['labs','clinical']:
        for name,item in snapshot.get(group,{}).items():
            if isinstance(item,dict):
                for key,reason in [('recorded_at','fact not known by snapshot knowledge time'),('observed_at','future fact cannot fill earlier assessment')]:
                    if key in item:
                        failure=check_time(item[key],snapshot[key],f'{group}.{name}.{key}',reason)
                        if failure:return failure
    temporal_validation='not_asserted' if any(c['status']=='not_asserted' for c in temporal_checks) else 'checked'
    temporal_note=('部分时间缺少年份、时区或足够精度，未断言这些时间的先后顺序；数学规则仍按原始值计算。' if temporal_validation=='not_asserted' else '可比较的时间已核对，未发现未来事实。')
    w=World();s=w.solver();cons,issues=bind(w,snapshot)
    if issues:return {'status':'invalid_evidence','issues':issues,'rule_version':VERSION}
    s.add(*cons);status=s.check()
    if status!=z.sat:return {'status':'invalid_evidence' if status==z.unsat else 'solver_unresolved','reason':str(status),'rule_version':VERSION}
    m=s.model();ival=lambda e:int(str(m.eval(e,model_completion=True)));label=lambda e:LABELS[ival(e)]
    clinical=snapshot.get('clinical',{})
    expressions={
        'eligible':{k:w.clin[k] for k in ['fever','sjia']},
        'mas_numeric':{'ferritin':tri(w.labs['ferritin'].compare(*RULES['mas_2016']['required'][1:])),**{k:tri(v) for (k,_,_),v in zip(RULES['mas_2016']['additional'],w.mas_items)}},
        'mas_review':{'alternative_causes_excluded':w.review},
        'hlh_cells':{'hemoglobin':tri(w.hlh_hb),'platelets':tri(w.labs['platelets'].compare('<',RULES['hlh_2004']['platelets_cutoff'])),'anc':tri(w.labs['anc'].compare('<',RULES['hlh_2004']['anc_cutoff']))},
        'hlh_lipid':{'usable_tg':tri(w.hlh_tg),'low_fibrinogen':tri(w.labs['fibrinogen'].compare('<=',RULES['hlh_2004']['fibrinogen_cutoff']))},
        'hlh_total':dict(zip(['molecular','fever','splenomegaly','cytopenias','lipid','hemophagocytosis','nk','ferritin','scd25'],[w.clin['molecular_hlh']]+[tri(v) for v in w.hlh_items]))}
    nodes={g:{k:label(v) for k,v in atoms.items()} for g,atoms in expressions.items()}
    nodes['ms_clinical']={k:label(w.clin[k]) for k in ['cns','hemorrhage','active_arthritis']}
    numeric_nodes={}
    for key,coef in RULES['ms_2019']['coefficients'].items():
        c=Q(coef)
        if key in w.labs:
            x=w.labs[key];positive=Decimal(coef)>0
            lower=x.lo if positive else x.hi;upper=x.hi if positive else x.lo
            lo_inf=z.is_true(m.eval(x.inf)) and not positive
            hi_inf=z.is_true(m.eval(x.inf)) and positive
        else:
            x=w.clin[key];positive=Decimal(coef)>0
            lower=z.If(x==-1,0,x) if positive else z.If(x==-1,1,x)
            upper=z.If(x==-1,1,x) if positive else z.If(x==-1,0,x)
            lo_inf=hi_inf=False
        numeric_nodes['ms.'+key]={'lower':None if lo_inf else jnum(m.eval(c*lower)),'upper':None if hi_inf else jnum(m.eval(c*upper))}
    result={'temporal_validation':temporal_validation,'temporal_note':temporal_note,'status':'evaluated','rule_version':VERSION,'case_id':stable('E',snapshot),'nodes':nodes,'numeric_nodes':numeric_nodes,'patient_id':snapshot['patient_id'],'observed_at':snapshot['observed_at'],'recorded_at':snapshot['recorded_at'],'evidence_snapshot':copy.deepcopy(snapshot),
        'mas_2016':{'eligibility':label(w.eligibility),'mathematical':label(w.mas),'clinical_review':label(w.review),'items':[label(tri(x)) for x in w.mas_items]},
        'ms_2019':{'eligibility':label(w.eligibility),'mathematical':label(w.ms),'score_complete':z.is_true(m.eval(w.ms_complete)),'score':jnum(m.eval(w.ms_lo)) if z.is_true(m.eval(w.ms_complete)) else None,'bounds':{'lower':None if z.is_true(m.eval(w.ms_lo_inf)) else jnum(m.eval(w.ms_lo)),'upper':None if z.is_true(m.eval(w.ms_hi_inf)) else jnum(m.eval(w.ms_hi)),'lower_open':z.is_true(m.eval(w.ms_lo_open)),'upper_open':z.is_true(m.eval(w.ms_hi_open))},'bound_interpretation':'mathematical-completion analysis, not validated missing-data MS method'},
        'hlh_2004':{'mathematical':label(w.hlh),'five_of_eight':label(w.hlh_five),'molecular':label(w.clin['molecular_hlh']),'items':[label(tri(x)) for x in w.hlh_items],'no_malignancy_background':label(w.clin['no_malignancy'])}}
    result['hlh_2004']['confirmed_count']=sum(v=='met' for v in result['hlh_2004']['items'])
    result['hlh_2004']['unknown_count']=sum(v=='unknown' for v in result['hlh_2004']['items'])
    return result

def make_catalog(*args,**kwargs):
    raise RuntimeError('The terminal-label catalog is retired. Use 路径研究/generate.py for source-mapped internal rule paths.')


def _evidence_time(value):
    """Validate source precision without manufacturing an instant."""
    if value is None:return ('unknown',None)
    if value.startswith('--'):
        datetime.strptime('2000'+value[1:],'%Y-%m-%d')
        return ('partial',value)
    if len(value)==10:
        return ('date',datetime.strptime(value,'%Y-%m-%d').date())
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    return ('aware' if parsed.tzinfo else 'local',parsed)

def _evidence_order(left,right):
    kind,a=_evidence_time(left);other,b=_evidence_time(right)
    if kind!=other or kind in ('unknown','partial'):return None
    if kind in ('aware','local'):
        # A minute-only value denotes the whole minute, not second zero.
        import re
        minute=lambda x:bool(re.fullmatch(r'.*[T ]\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})?',x))
        from datetime import timedelta
        a_end=a+timedelta(minutes=1) if minute(left) else a
        b_end=b+timedelta(minutes=1) if minute(right) else b
        if a>b_end or a==b_end and minute(right):return 1
        if b>a_end or b==a_end and minute(left):return -1
        return 0 if not minute(left) and not minute(right) and a==b else None
    return (a>b)-(a<b) if a!=b else None

def _time(s):
    if not isinstance(s,str):raise ValueError('comparable timestamp required')
    t=datetime.fromisoformat(s.replace('Z','+00:00'))
    if not t.tzinfo:raise ValueError('timezone required')
    return t

def refines(a,b,key=None):
    if key is None:
        unknown=lambda v:v is None or v=='unknown' or isinstance(v,dict) and v.get('state')=='unknown'
        return unknown(a) or a==b
    try:
        al,ah,alo,aho,_=normalize(a,key);bl,bh,blo,bho,_=normalize(b,key)
        truth=lambda x:z.is_true(z.simplify(x))
        return truth(Q(bl)>=Q(al)) and (not truth(Q(bl)==Q(al)) or not alo or blo) and (ah is None or bh is not None and truth(Q(bh)<=Q(ah)) and (not truth(Q(bh)==Q(ah)) or not aho or bho))
    except (ValueError,z.Z3Exception):return False

def require_orderable_snapshot(snapshot):
    for key in ('observed_at','recorded_at'):_time(snapshot[key])
    for group in ('labs','clinical'):
        for item in snapshot.get(group,{}).values():
            if isinstance(item,dict):
                for key in ('observed_at','recorded_at'):
                    if key in item:_time(item[key])

def snapshot_relation(old,new):
    for snapshot in (old,new):require_orderable_snapshot(snapshot)
    if old['patient_id']!=new['patient_id']:raise ValueError('different patients cannot form a transition')
    if _time(new['observed_at'])<_time(old['observed_at']):raise ValueError('comparison must follow observation time')
    if _time(new['observed_at'])==_time(old['observed_at']) and _time(new['recorded_at'])<_time(old['recorded_at']):raise ValueError('revision must follow knowledge time')
    kinds=set();changes=[]
    same=_time(old['observed_at'])==_time(new['observed_at'])
    for group in ['labs','clinical']:
        for key in sorted(set(old.get(group,{}))|set(new.get(group,{}))):
            a=old.get(group,{}).get(key);b=new.get(group,{}).get(key)
            if a==b:continue
            unknown=lambda x:x is None or x=='unknown' or isinstance(x,dict) and x.get('state')=='unknown'
            kind='evidence_completion' if same and refines(a,b,key if group=='labs' else None) else 'correction' if same else 'eligibility_change' if key in ['fever','sjia'] else 'clinical_change' if group=='clinical' else 'measurement_change'
            # Equivalent units are representational changes, never a false clinical trend.
            equivalent=False
            if group=='labs' and a is not None and b is not None:
                try:
                    aa=normalize(a,key);bb=normalize(b,key)
                    equivalent=all(str(z.simplify(Q(x)-Q(y)))=='0' if x is not None and y is not None else x==y for x,y in zip(aa[:2],bb[:2])) and aa[2:4]==bb[2:4]
                except Exception:pass
                if equivalent:kind='representation_change'
            if group=='clinical':
                state=lambda v:(v.get('state') if isinstance(v,dict) else v)
                site=lambda v:(v.get('site') if isinstance(v,dict) else None)
                if state(a)==state(b) and site(a)==site(b):kind='representation_change'
            kinds.add(kind);entry={'field':group+'.'+key,'kind':kind,'before':a,'after':b}
            if group=='labs' and not same and not equivalent:
                try:
                    aa=normalize(a,key);bb=normalize(b,key)
                    if aa[1] is not None and bb[1] is not None and z.is_true(z.simplify(Q(aa[0])==Q(aa[1]))) and z.is_true(z.simplify(Q(bb[0])==Q(bb[1]))):
                        av=z.simplify(Q(aa[0]));bv=z.simplify(Q(bb[0]));entry['delta']=jnum(bv-av);entry['percent']=None if z.is_true(av==0) else jnum((bv-av)*100/av)
                    else:entry['delta_status']='interval_evidence_preserved'
                except Exception:entry['delta_status']='not_comparable'
            changes.append(entry)
    if not changes:kinds.add('no_evidence_change')
    a,b=evaluate(old),evaluate(new)
    # A changed internal condition is retained even when all terminal outcomes agree.
    node_changes=[]
    for node in sorted(set(a.get('numeric_nodes',{}))|set(b.get('numeric_nodes',{}))):
        before=a.get('numeric_nodes',{}).get(node);after=b.get('numeric_nodes',{}).get(node)
        if before!=after:node_changes.append({'tool':'ms_2019','node':node,'before':before,'after':after})
    for group in sorted(set(a.get('nodes',{}))|set(b.get('nodes',{}))):
        before=a.get('nodes',{}).get(group,{})
        after=b.get('nodes',{}).get(group,{})
        for key in sorted(set(before)|set(after)):
            if before.get(key)!=after.get(key):
                node_changes.append({'tool':'ms_2019' if group.startswith('ms_') else 'hlh_2004' if group.startswith('hlh_') else 'mas_2016','node':group+'.'+key,'before':before.get(key),'after':after.get(key)})
    for tool in ['mas_2016','ms_2019','hlh_2004']:
        before=a.get(tool,{})
        after=b.get(tool,{})
        for key in sorted(set(before)|set(after)):
            if before.get(key)!=after.get(key):
                node_changes.append({'tool':tool,'node':key,'before':before.get(key),'after':after.get(key)})
    if any(n['node']=='hlh_lipid.usable_tg' for n in node_changes):
        for item in changes:
            if item['kind']=='representation_change' and item['field']=='labs.triglycerides':
                item['kind']='rule_context_change'
        kinds={item['kind'] for item in changes} or kinds
    changed_result=any(n['node'] in ['mathematical','eligibility','clinical_review','score','score_complete'] for n in node_changes)
    return {'transition_id':stable('T',[old,new]),'from_case':a.get('case_id'),'to_case':b.get('case_id'),'kinds':sorted(kinds),'changes':changes,'result_changed':changed_result,'internal_changed':bool(node_changes),'node_changes':node_changes,'from_result':a,'to_result':b}


def replay(events,as_of=None):
    current={};versions=[];patient=None;ordered=sorted(enumerate(events),key=lambda it:(_time(it[1]['recorded_at']),it[0]));seen=set()
    for _,event in ordered:
        if as_of and _time(event['recorded_at'])>_time(as_of):continue
        if event['event_id'] in seen:raise ValueError('duplicate event_id')
        seen.add(event['event_id'])
        if event['kind'] not in ['observation','completion','correction']:raise ValueError('invalid event kind')
        snap=copy.deepcopy(event['snapshot']);pid=snap['patient_id']
        require_orderable_snapshot(snap)
        if _time(snap['observed_at'])>_time(event['recorded_at']):raise ValueError('future observation cannot be known')
        snap['recorded_at']=event['recorded_at']
        valid=evaluate(snap)
        if valid.get('status')!='evaluated':raise ValueError('invalid observation evidence: '+json.dumps(valid,ensure_ascii=False))
        if patient and patient!=pid:raise ValueError('mixed patient stream')
        patient=pid;oid=event['observation_id']
        if oid in current:
            if event['kind'] not in ['completion','correction']:raise ValueError('existing observation needs completion/correction')
            if _time(snap['observed_at'])!=_time(current[oid]['observed_at']):raise ValueError('revision cannot move observation time')
            change=snapshot_relation(current[oid],snap)
            if event['kind']=='completion' and any(x['kind'] not in ['evidence_completion','representation_change'] for x in change['changes']):raise ValueError('completion cannot replace known evidence')
        elif event['kind']!='observation':raise ValueError('revision target missing')
        current[oid]=snap
        history=sorted(current.items(),key=lambda it:(_time(it[1]['observed_at']),it[0]))
        results=[{'observation_id':i,**evaluate(s)} for i,s in history]
        transitions=[snapshot_relation(history[i-1][1],history[i][1]) for i in range(1,len(history))]
        versions.append({'event_id':event['event_id'],'known_at':event['recorded_at'],'history':results,'transitions':transitions})
    return {'rule_version':VERSION,'patient_id':patient,'versions':versions,'current':versions[-1] if versions else None}

class Stream:
    def __init__(self):self.events=[]
    def append(self,event):
        result=replay(self.events+[event]);self.events.append(copy.deepcopy(event));return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['evaluate','replay','catalog']);p.add_argument('--input');p.add_argument('--output');a=p.parse_args()
    r=make_catalog() if a.mode=='catalog' else (evaluate if a.mode=='evaluate' else replay)(json.loads(Path(a.input).read_text()))
    text=json.dumps(r,ensure_ascii=False,indent=2)
    if a.output:Path(a.output).write_text(text)
    else:print(text)

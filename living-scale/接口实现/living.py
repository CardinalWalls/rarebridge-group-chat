"""Versioned configurable living-scale service; no consent/agreement workflow.

The engine executes declared research rules. It does not validate their medical
usefulness. Original scales retain scale-api-2.0 semantics and thresholds.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from fractions import Fraction

import jsonschema
import z3 as z
import api
from model import Q, normalize, jnum, _evidence_order

VERSION='living-service-1.1'
NUMBER={'oneOf':[{'type':'number'},{'type':'string','pattern':r'^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$'}]}
RULE_SCHEMA={'type':'object','additionalProperties':False,'required':['id','field','basis','measure','direction','operator','threshold','group','weight','source'], 'properties':{
 'id':{'type':'string','minLength':1},'field':{'enum':list(api.service.RULES['labs'])},
 'basis':{'enum':['current','previous','first']},'measure':{'enum':['level','absolute','percent']},
 'direction':{'enum':['rise','fall']},'operator':{'enum':['>','>=','<','<=']},
 'threshold':NUMBER,'threshold_unit':{'type':'string'},'weight':NUMBER,'group':{'enum':['core','general']},
 'source':{'type':'string','minLength':1}}}
STAGE_SCHEMA={'type':'object','additionalProperties':False,'required':['rules','min_weight','min_core_hits','message','next_observation'], 'properties':{
 'rules':{'type':'array','minItems':1,'maxItems':20,'items':RULE_SCHEMA},'min_weight':NUMBER,
 'min_core_hits':{'type':'integer','minimum':0},'message':{'type':'string','minLength':1},
 'next_observation':{'type':'array','items':{'type':'object','additionalProperties':False,'required':['field','purpose','source'],'properties':{'field':{'type':'string'},'purpose':{'type':'string'},'source':{'type':'string'}}}}}}
CONFIG_SCHEMA={'type':'object','additionalProperties':False,'required':['id','version','identity','required_context','max_interval_hours','stages'],'properties':{
 'id':{'type':'string','minLength':1},'version':{'type':'string','minLength':1},'identity':{'const':'research_only'},
 'required_context':{'type':'array','uniqueItems':True,'items':{'enum':api.service.RULES['clinical']}},
 'max_interval_hours':NUMBER,'stages':{'type':'object','additionalProperties':False,'required':['observation','mas_review'],'properties':{'observation':STAGE_SCHEMA,'mas_review':STAGE_SCHEMA}}}}
REQUEST_SCHEMA={'$schema':'https://json-schema.org/draft/2020-12/schema','type':'object','additionalProperties':False,'required':['scale','observations'],
 'properties':{'scale':{'enum':list(api.service.TOOLS)},'observations':{'type':'array','minItems':1,'items':{'type':'object','additionalProperties':False,'required':['observation_id','snapshot'],'properties':{'observation_id':{'type':'string','minLength':1},'snapshot':{'$ref':'#/$defs/snapshot'}}}},'config':CONFIG_SCHEMA},'$defs':api.DEFS}
RESPONSE_SCHEMA={'type':'object','additionalProperties':False,'required':['contract_version','execution','input','config_digest','history','current','wide_rows'], 'properties':{
 'contract_version':{'const':VERSION},'execution':{'type':'object','required':['status','error'],'properties':{'status':{'enum':['ok','error']},'error':{'type':['object','null']}}},
 'input':{'type':'object'},'config_digest':{'type':['string','null']},'history':{'type':'array','items':{'type':'object','required':['observation_id','original_scale','dynamic','comparison_facts']}},
 'current':{'type':['object','null']},'wide_rows':{'type':'array','items':{'type':'object'}}}}

_BOOL={'type':['boolean','null']}
_SIGNAL={'enum':['no_rule_signal','observation_signal','mas_review_signal',None]}
_RULE_RESULT={'type':'object','required':['rule_id','field','source','basis','measure','direction','operator','threshold','threshold_unit','weight','group','current_input','matched'],
 'properties':{'rule_id':{'type':'string'},'field':{'type':'string'},'source':{'type':'string'},'basis':{'enum':['current','previous','first']},
 'measure':{'enum':['level','absolute','percent']},'direction':{'enum':['rise','fall']},'operator':{'enum':['>','>=','<','<=']},
 'threshold':{'type':'string'},'threshold_unit':{'type':'string'},'weight':{'type':'string'},'group':{'enum':['core','general']},
 'current_input':{'type':['object','null']},'reference_input':{'type':['object','null']},'reference_observation_id':{'type':'string'},
 'elapsed_hours_bounds':{'type':['array','null'],'minItems':2,'maxItems':2,'items':{'type':'number'}},
 'calculation':{'type':'object','required':['status','unit'],'properties':{'status':{'enum':['bounded','unbounded','undefined_reference']},'unit':{'type':'string'},'lower':{'type':['string','null']},'upper':{'type':['string','null']},'lower_open':{'type':'boolean'},'upper_open':{'type':'boolean'}}},'unresolved_reason':{'type':'string'},'matched':_BOOL}}
_STAGE_RESULT={'type':'object','required':['matched','rules','weight_bounds','weight_bounds_kind','min_weight','min_core_hits','message','next_observation'],
 'properties':{'matched':_BOOL,'rules':{'type':'array','items':_RULE_RESULT},'weight_bounds':{'type':'array','minItems':2,'maxItems':2,'items':{'type':'string'}},
 'weight_bounds_kind':{'const':'conservative_per_rule'},'min_weight':{'type':'string'},'min_core_hits':{'type':'integer','minimum':0},'message':{'type':'string'},'next_observation':STAGE_SCHEMA['properties']['next_observation']}}
_DYNAMIC_RESULT={'type':'object','required':['status','signal','confirmed_minimum_signal','stages','transition','peak_signal'],
 'properties':{'status':{'enum':['unconfigured','not_applicable','needs_context','undetermined','evaluated']},
 'signal':_SIGNAL,'confirmed_minimum_signal':_SIGNAL,'candidate_signal':_SIGNAL,'peak_signal':_SIGNAL,
 'identity':{'const':'research_rule_output_not_clinical_validation'},
 'transition':{'enum':['unconfigured','entry_changed','evidence_insufficient','initial','escalated','conditions_no_longer_met','maintained']},
 'stages':{'type':'object','additionalProperties':False,'properties':{'observation':_STAGE_RESULT,'mas_review':_STAGE_RESULT}}}}
_HISTORY_ROW={'type':'object','additionalProperties':False,'required':['observation_id','original_scale','dynamic','comparison_facts'],
 'properties':{'observation_id':{'type':'string'},'original_scale':copy.deepcopy(api.RESPONSE_SCHEMA),'dynamic':_DYNAMIC_RESULT,'comparison_facts':{'type':'array','items':{'type':'object','required':['field','basis','status']}}}}
RESPONSE_SCHEMA['properties']['history']['items']=_HISTORY_ROW
RESPONSE_SCHEMA['properties']['current']={'anyOf':[_HISTORY_ROW,{'type':'null'}]}

def rational(v):
    if isinstance(v,bool):raise ValueError('boolean is not numeric')
    return Fraction(str(v))

def time_range(value):
    """Bounds and timezone kind, preserving date-only precision."""
    if not isinstance(value,str):return None
    try:
        if len(value)==10:
            a=datetime.fromisoformat(value)
            return a,a+timedelta(days=1),False
        a=datetime.fromisoformat(value.replace('Z','+00:00'))
        end=a+timedelta(minutes=1) if re.fullmatch(r'.*[T ]\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})?',value) else a
        return a,end,a.tzinfo is not None
    except ValueError:return None

def elapsed(before,after):
    a,b=time_range(before),time_range(after)
    if a is None or b is None or a[2]!=b[2]:return None
    if a[2]:
        a=(a[0].astimezone(timezone.utc),a[1].astimezone(timezone.utc),True)
        b=(b[0].astimezone(timezone.utc),b[1].astimezone(timezone.utc),True)
    lo=(b[0]-a[1]).total_seconds()/3600
    hi=(b[1]-a[0]).total_seconds()/3600
    return [lo,hi]

def decide(solver,expr):
    solver.push();solver.add(expr);yes=solver.check();solver.pop()
    solver.push();solver.add(z.Not(expr));no=solver.check();solver.pop()
    if yes==z.unsat and no==z.unsat:raise ValueError('inconsistent evidence')
    if no==z.unsat and yes==z.sat:return True
    if yes==z.unsat and no==z.sat:return False
    return None

def validate_config(c):
    if rational(c['max_interval_hours'])<=0:raise ValueError('positive comparison window required')
    if not any(r['basis']!='current' for r in c['stages']['mas_review']['rules']):
        raise ValueError('second-stage warning requires at least one temporal condition')
    ids=set()
    for stage in c['stages'].values():
        if rational(stage['min_weight'])<=0:raise ValueError('positive aggregation weight required')
        for rule in stage['rules']:
            if rule['id'] in ids:raise ValueError('rule IDs must be unique across stages')
            ids.add(rule['id'])
            if rational(rule['weight'])<0:raise ValueError('negative weight')
            if rational(rule['threshold'])<0:raise ValueError('negative threshold')
            if (rule['measure']=='level') != (rule['basis']=='current'):raise ValueError('level requires current basis; changes require a prior basis')
            if rule['measure']=='percent' and rule.get('threshold_unit','%')!='%':
                raise ValueError('percentage threshold_unit must be % or omitted')
            if rule['measure']!='percent':
                if rule.get('threshold_unit') not in api.service.RULES['labs'][rule['field']]['aliases']:raise ValueError('explicit threshold unit required')

def validate_dynamic_evidence(observations,config):
    fields={r['field'] for stage in config['stages'].values() for r in stage['rules']}
    for observation in observations:
        snapshot=observation['snapshot']
        for field in fields:
            raw=snapshot['labs'].get(field)
            if not isinstance(raw,dict):continue
            if raw.get('state')=='conflict':raise ValueError('conflicting dynamic evidence: '+field)
            for key in ('observed_at','recorded_at'):
                if key in raw and _evidence_order(raw[key],snapshot[key])==1:
                    raise ValueError('future dynamic evidence: '+field+'.'+key)
            lo,hi,lop,hip,_=normalize(raw,field)
            if z.is_true(z.simplify(Q(lo)<0)) or hi is not None and z.is_true(z.simplify(z.Or(Q(hi)<Q(lo),z.And(Q(hi)==Q(lo),z.BoolVal(lop or hip))))):
                raise ValueError('invalid dynamic interval: '+field)

def calculation_interval(current,reference,rule):
    """Exact rational bounds in normalized units; None is an unbounded endpoint."""
    def bounds(item):
        lo,hi,lop,hip,_=normalize(item,rule['field'])
        return rational(jnum(Q(lo))),None if hi is None else rational(jnum(Q(hi))),lop,hip
    cl,ch,clo,cho=bounds(current)
    unit=api.service.RULES['labs'][rule['field']]['unit']
    if rule['measure']=='level':lo,hi,lop,hip=cl,ch,clo,cho
    else:
        bl,bh,blo,bho=bounds(reference)
        if rule['measure']=='absolute':
            lo=None if bh is None else cl-bh
            hi=None if ch is None else ch-bl
            lop=clo or bho or bh is None
            hip=cho or blo or ch is None
        else:
            unit='%'
            if bl==0 and not blo:return {'status':'undefined_reference','unit':unit}
            if ch==0:
                lo=hi=Fraction(-100);lop=hip=False
            else:
                lo=Fraction(-100) if bh is None else (cl/bh-1)*100
                hi=None if ch is None or bl==0 else (ch/bl-1)*100
                lop=(clo if cl==0 else clo or bho or bh is None)
                hip=cho or blo or ch is None or bl==0
        if rule['direction']=='fall':
            lo,hi,lop,hip=None if hi is None else -hi,None if lo is None else -lo,hip,lop
    return {'status':'bounded' if lo is not None and hi is not None else 'unbounded',
            'lower':None if lo is None else str(lo),'upper':None if hi is None else str(hi),
            'lower_open':lop,'upper_open':hip,'unit':unit}

def compute_stages(observations,index,config):
    solver=z.Solver();solver.set(timeout=3000)
    variables={};rule_rows={};stage_exprs={}
    def variable(i,field):
        key=(i,field)
        if key in variables:return variables[key]
        raw=observations[i]['snapshot']['labs'].get(field)
        v=z.Real(f'v_{i}_{field}');solver.add(v>=0)
        if isinstance(raw,dict) and raw.get('state')=='conflict':raise ValueError('conflicting laboratory evidence')
        lo,hi,lop,hip,_=normalize(raw,field)
        solver.add(v>Q(lo) if lop else v>=Q(lo))
        if hi is not None:solver.add(v<Q(hi) if hip else v<=Q(hi))
        variables[key]=(v,raw);return v,raw
    for name,stage in config['stages'].items():
        weights=[];core=[];temporal=[];rows=[]
        for rule in stage['rules']:
            f=rule['field'];current,raw=variable(index,f)
            row={'rule_id':rule['id'],'field':f,'source':rule['source'],'basis':rule['basis'],
                 'measure':rule['measure'],'direction':rule['direction'],'operator':rule['operator'],
                 'threshold':str(rule['threshold']),'threshold_unit':rule.get('threshold_unit','%'),
                 'weight':str(rule['weight']),'group':rule['group'],'current_input':copy.deepcopy(raw)}
            threshold=Q(rational(rule['threshold']));expr_value=current;reference=None;reason=None;reference_raw=None
            if rule['measure']!='percent':
                threshold*=Q(api.service.RULES['labs'][f]['aliases'][rule['threshold_unit']])
            if rule['basis']!='current':
                ri=0 if rule['basis']=='first' else index-1
                if ri<0 or ri==index:
                    reason='no_prior_observation'
                else:
                    reference,reference_raw=variable(ri,f)
                    row.update(reference_observation_id=observations[ri]['observation_id'],reference_input=copy.deepcopy(reference_raw))
                    after=raw.get('observed_at') if isinstance(raw,dict) and raw.get('observed_at') else observations[index]['snapshot']['observed_at']
                    before=reference_raw.get('observed_at') if isinstance(reference_raw,dict) and reference_raw.get('observed_at') else observations[ri]['snapshot']['observed_at']
                    hours=elapsed(before,after);row['elapsed_hours_bounds']=hours
                    if hours is None or hours[0]<0 or hours[1]<=0 or hours[1]>float(rational(config['max_interval_hours'])):reason='time_window_not_established'
                    sign=1 if rule['direction']=='rise' else -1
                    expr_value=sign*(current-reference)
                    if rule['measure']=='percent':
                        # Keep unknown or zero reference as unknown; never silently assume a positive baseline.
                        if decide(solver,reference>0) is not True:reason='positive_reference_not_established'
                        expr_value*=100;threshold*=reference
            if not reason:
                row['calculation']=calculation_interval(raw,reference_raw,rule)
            if reason:
                # Identical unknown predicates must not become independent facts.
                signature=json.dumps([index,f,rule['basis'],rule['measure'],rule['direction'],rule['operator'],str(rule['threshold']),rule.get('threshold_unit'),reason])
                expr=z.Bool('unknown_'+hashlib.sha256(signature.encode()).hexdigest());row['unresolved_reason']=reason
            else:
                expr={'>' : lambda:expr_value>threshold,'>=':lambda:expr_value>=threshold,'<':lambda:expr_value<threshold,'<=':lambda:expr_value<=threshold}[rule['operator']]()
            weights.append(z.If(expr,Q(rational(rule['weight'])),Q(0)))
            if rule['group']=='core':core.append(z.If(expr,1,0))
            if rule['basis']!='current':temporal.append(expr)
            rows.append((row,expr))
        total=z.Sum(weights)
        count=z.Sum(core) if core else z.IntVal(0)
        stage_exprs[name]=z.And(total>=Q(rational(stage['min_weight'])),count>=stage['min_core_hits'],
                               z.Or(*temporal) if name=='mas_review' else z.BoolVal(True))
        rule_rows[name]=rows
    output={}
    for name,stage in config['stages'].items():
        result_rows=[]
        for row,expr in rule_rows[name]:
            hit=decide(solver,expr);row['matched']=hit
            if hit is None and 'unresolved_reason' not in row:
                row['unresolved_reason']='evidence_range_does_not_decide_or_solver_unresolved'
            result_rows.append(row)
        minimum=sum(rational(r['weight']) for r in result_rows if r['matched'] is True)
        maximum=minimum+sum(rational(r['weight']) for r in result_rows if r['matched'] is None)
        output[name]={'matched':decide(solver,stage_exprs[name]),'rules':result_rows,
                      'weight_bounds':[str(minimum),str(maximum)],'weight_bounds_kind':'conservative_per_rule','min_weight':str(stage['min_weight']),
                      'min_core_hits':stage['min_core_hits'],'message':stage['message'],
                      'next_observation':copy.deepcopy(stage['next_observation'])}
    return output

def comparison_facts(observations,index,scale):
    """Threshold-free evidence for the usage Skill, independent of alarm configuration."""
    current=observations[index]['snapshot']
    fields=sorted(set(api.service.FIELDS[scale]['labs']) | set(current['labs']))
    rows=[]
    for basis,ri in [('previous',index-1),('first',0)]:
        for field in fields:
            row={'field':field,'basis':basis,'status':'needs_reference'}
            rows.append(row)
            if ri<0 or ri==index:continue
            previous=observations[ri]['snapshot']
            a=previous['labs'].get(field);b=current['labs'].get(field)
            row.update(reference_observation_id=observations[ri]['observation_id'],
                       reference_input=copy.deepcopy(a),current_input=copy.deepcopy(b))
            if not a or not b or a.get('state')=='unknown' or b.get('state')=='unknown':
                row['status']='needs_data';continue
            try:
                if a.get('state')=='conflict' or b.get('state')=='conflict':raise ValueError('conflicting evidence')
                for raw,snap in [(a,previous),(b,current)]:
                    for key in ('observed_at','recorded_at'):
                        if key in raw and _evidence_order(raw[key],snap[key])==1:raise ValueError('future evidence')
                    lo,hi,lop,hip,_=normalize(raw,field)
                    if z.is_true(z.simplify(Q(lo)<0)) or hi is not None and z.is_true(z.simplify(z.Or(Q(hi)<Q(lo),z.And(Q(hi)==Q(lo),z.BoolVal(lop or hip))))):raise ValueError('invalid interval')
                hours=elapsed(a.get('observed_at') or previous['observed_at'],b.get('observed_at') or current['observed_at'])
                row['elapsed_hours_bounds']=hours
                if hours is None or hours[0]<0 or hours[1]<=0:
                    row['status']='time_uncertain';continue
                rule={'field':field,'measure':'absolute','direction':'rise'}
                row['absolute_change']=calculation_interval(b,a,rule)
                rule['measure']='percent'
                row['percent_change']=calculation_interval(b,a,rule)
                row['status']='calculated'
            except (ValueError,ZeroDivisionError,z.Z3Exception) as exc:
                row.update(status='invalid_evidence',reason=str(exc))
    return rows

def run(request):
    response={'contract_version':VERSION,'input':copy.deepcopy(request) if isinstance(request,dict) else {'invalid_request':copy.deepcopy(request)},'config_digest':None,
              'execution':{'status':'ok','error':None},'history':[],'current':None,'wide_rows':[]}
    try:
        jsonschema.validate(request,REQUEST_SCHEMA)
        observations=request['observations'];config=request.get('config');scale=request['scale']
        if len({o['observation_id'] for o in observations})!=len(observations):raise ValueError('duplicate observation IDs; replace corrections instead of appending')
        if len({o['snapshot']['patient_id'] for o in observations})!=1:raise ValueError('mixed patients')
        for a,b in zip(observations,observations[1:]):
            d=elapsed(a['snapshot']['observed_at'],b['snapshot']['observed_at'])
            if d is None or d[0]<0 or d[1]<=0:raise ValueError('observation order not established')
        if config:
            validate_config(config)
            validate_dynamic_evidence(observations,config)
            response['config_digest']=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        peak=0;previous=None
        for i,obs in enumerate(observations):
            raw=api.evaluate({'tool':scale,'snapshot':obs['snapshot']})
            if raw['execution']['status']=='error':raise ValueError(raw['execution']['error']['message'])
            if not config:
                dynamic={'status':'unconfigured','signal':None,'confirmed_minimum_signal':None,'stages':{},'transition':'unconfigured','peak_signal':None}
            else:
                stages=compute_stages(observations,i,config)
                states=[]
                for f in config['required_context']:
                    v=obs['snapshot']['clinical'].get(f,'unknown')
                    states.append(v.get('state') if isinstance(v,dict) else v)
                context='not_applicable' if 'absent' in states else 'needs_context' if any(v!='present' for v in states) else 'applicable'
                a,b=stages['observation']['matched'],stages['mas_review']['matched']
                confirmed=2 if b is True else 1 if a is True else 0
                exact=None if b is None else 2 if b is True else None if a is None else 1 if a else 0
                effective=exact if context=='applicable' else None
                if context!='applicable':transition='entry_changed'
                elif effective is None:transition='evidence_insufficient'
                elif previous is None:transition='initial'
                elif effective>previous:transition='escalated'
                elif effective<previous:transition='conditions_no_longer_met'
                else:transition='maintained'
                if context=='applicable':peak=max(peak,confirmed)
                labels={0:'no_rule_signal',1:'observation_signal',2:'mas_review_signal',None:None}
                dynamic={'status':context if context!='applicable' else 'undetermined' if exact is None else 'evaluated',
                         'signal':labels[effective],'confirmed_minimum_signal':labels[confirmed] if context=='applicable' else None,
                         'candidate_signal':labels[exact],'stages':stages,'transition':transition,'peak_signal':labels[peak],
                         'identity':'research_rule_output_not_clinical_validation'}
                if context=='applicable':previous=effective if effective is not None else confirmed
            facts=comparison_facts(observations,i,scale)
            row={'observation_id':obs['observation_id'],'original_scale':raw,'dynamic':dynamic,'comparison_facts':facts}
            response['history'].append(row)
            response['wide_rows'].append(api.flatten({'observation_id':obs['observation_id'],'input':obs['snapshot'],'original_scale':raw,'dynamic':dynamic,'comparison_facts':facts,'config_digest':response['config_digest']}))
        response['current']=response['history'][-1]
    except Exception as exc:
        response.update(execution={'status':'error','error':{'type':type(exc).__name__,'message':str(exc)}},history=[],current=None,wide_rows=[])
    return response

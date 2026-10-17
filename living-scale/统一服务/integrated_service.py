"""Persistent facade over the existing three-scale calculator. No new medical formulas."""
from __future__ import annotations
import copy,hashlib,json,os,sqlite3,sys,threading,uuid,math
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime,timezone
import jsonschema
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'接口实现'))
import api,living
from unified_contract import *

def now():return datetime.now(timezone.utc).isoformat()
def dump(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(dump(v).encode()).hexdigest()
def ident(prefix):return prefix+'_'+uuid.uuid4().hex[:16]
def visible(t,cutoff):
    if not t or not cutoff:return False
    a,b=living.time_range(t),living.time_range(cutoff)
    if not a or not b:return False
    if len(t)==10 and len(cutoff)==10:return t<=cutoff
    if a[2]!=b[2]:
        # A date cutoff denotes the entire named calendar day; do not invent a timezone.
        if len(cutoff)==10:return t[:10]<cutoff
        return t[:10]<cutoff[:10]
    return a[1]<=b[1]

def time_order(a,b):
    if a==b:return 0
    x,y=living.time_range(a),living.time_range(b)
    if not x or not y:return None
    if x[2]!=y[2]:
        return -1 if a[:10]<b[:10] else 1 if a[:10]>b[:10] else None
    if x[1]<=y[0]:return -1
    if x[0]>=y[1]:return 1
    return None

def same_evaluation_day(event,evaluation):
    a,b=living.time_range(event),living.time_range(evaluation)
    if not a or not b:return False
    if a[2] and b[2]:return a[0].astimezone(b[0].tzinfo).date()==b[0].date()
    if a[2]!=b[2]:return False
    return a[0].date()==b[0].date()

def time_sort(value):
    t=living.time_range(value)[0]
    return t.replace(tzinfo=timezone.utc).timestamp() if t.tzinfo is None else t.timestamp()

def public_fact(row):
    v=json.loads(row['payload']);v.update(fact_ref=row['fact_ref'],fact_revision=row['revision'],source_system=row['source_system'],source_id=row['external_id'],patient_id=row['patient_id'])
    return v

ALIASES={'known_or_suspected_sjia':'sjia','age_days':'infant_under_4_weeks','absolute_neutrophils':'anc','soluble_cd25':'scd25'}
DERIVED={'scale_assessment','course_interpretation','monitoring_alert','system_inference'}
class Conflict(ValueError):pass
class IntegratedService:
 def __init__(self,db_path=None):
    self.db_path=str(db_path or os.environ.get('LIVING_SCALE_DB',str(HERE/'.runtime/service.sqlite')))
    Path(self.db_path).parent.mkdir(parents=True,exist_ok=True)
    self.lock=threading.RLock()
    with self.connect() as c:
     c.executescript('''
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS identities(source_system TEXT,external_id TEXT,patient_id TEXT,PRIMARY KEY(source_system,external_id));
      CREATE TABLE IF NOT EXISTS facts(fact_ref TEXT PRIMARY KEY,patient_id TEXT,source_system TEXT,external_id TEXT,revision INTEGER,payload TEXT,UNIQUE(patient_id,source_system,external_id,revision));
      CREATE TABLE IF NOT EXISTS profiles(profile_id TEXT,revision INTEGER,patient_id TEXT,payload TEXT,created_at TEXT,reason TEXT,PRIMARY KEY(profile_id,revision));
      CREATE TABLE IF NOT EXISTS assessments(assessment_id TEXT,revision INTEGER,patient_id TEXT,fingerprint TEXT,payload TEXT,stale INTEGER DEFAULT 0,PRIMARY KEY(assessment_id,revision));
      CREATE INDEX IF NOT EXISTS assessment_fingerprint ON assessments(fingerprint);
      CREATE TABLE IF NOT EXISTS changes(cursor INTEGER PRIMARY KEY AUTOINCREMENT,patient_id TEXT,event_type TEXT,payload TEXT,created_at TEXT);
     ''')
 @contextmanager
 def connect(self):
    c=sqlite3.connect(self.db_path,timeout=30);c.row_factory=sqlite3.Row
    try:
     c.execute("BEGIN IMMEDIATE")
     yield c
     c.commit()
    except Exception:
     c.rollback();raise
    finally:c.close()
 def emit(self,c,patient,kind,payload):c.execute('INSERT INTO changes(patient_id,event_type,payload,created_at) VALUES(?,?,?,?)',(patient,kind,dump(payload),now()))
 def dispatch(self,tool,args):
    try:
     if tool not in TOOL_SCHEMAS:raise ValueError('unknown integrated tool')
     jsonschema.validate(args,TOOL_SCHEMAS[tool])
     with self.lock:
      result=getattr(self,tool)(**args)
     result={'ok':True,**result};jsonschema.validate(result,RESULT_SCHEMA);return result
    except Exception as exc:
     return {'ok':False,'error':{'type':type(exc).__name__,'message':str(exc),'operation':tool}}
 def _facts(self,c,patient,asof=None,evaluation=None):
    rows=[public_fact(x) for x in c.execute('SELECT * FROM facts WHERE patient_id=? ORDER BY revision',(patient,))]
    latest={}
    for f in rows:
     if asof and not visible(f.get('recorded_time'),asof):continue
     if evaluation and not visible(f.get('event_time'),evaluation):continue
     key=(f['source_system'],f['source_id'])
     if key not in latest or f['fact_revision']>latest[key]['fact_revision']:latest[key]=f
    return list(latest.values())
 def import_evidence(self,source_system,external_patient_id,patient_id,envelope):
    if envelope['subject_id']!=external_patient_id:raise ValueError('subject_id does not match explicit identity mapping')
    adopted=[];issues=[]
    with self.connect() as c:
     prior=c.execute('SELECT patient_id FROM identities WHERE source_system=? AND external_id=?',(source_system,external_patient_id)).fetchone()
     if prior and prior[0]!=patient_id:raise Conflict('external identity already maps to another patient')
     c.execute('INSERT OR IGNORE INTO identities VALUES(?,?,?)',(source_system,external_patient_id,patient_id))
     for original in envelope['facts']:
      f=copy.deepcopy(original)
      if f.get('kind') in DERIVED or f.get('inference_status') in ('inferred','derived','system_generated') or isinstance(f.get('source'),dict) and f['source'].get('kind') in DERIVED|{'system_generated'}:
       issues.append({'id':f['id'],'reason':'derived evaluation cannot be imported as independent source fact'});continue
      if any(f.get(t) is not None and living.time_range(f[t]) is None for t in ('event_time','recorded_time')):
       issues.append({'id':f['id'],'reason':'invalid time; record not adopted'});continue
      previous=c.execute('SELECT * FROM facts WHERE patient_id=? AND source_system=? AND external_id=? ORDER BY revision DESC LIMIT 1',(patient_id,source_system,f['id'])).fetchone()
      original_revision=f.pop('fact_revision',None)
      if previous and json.loads(previous['payload'])==f:
       adopted.append({'fact_ref':previous['fact_ref'],'status':'unchanged'});continue
      revision=previous['revision']+1 if previous else 1
      if original_revision is not None and original_revision!=revision:raise Conflict('unexpected fact revision for '+f['id'])
      ref=source_system+':'+external_patient_id+':'+f['id']+'@'+str(revision)
      c.execute('INSERT INTO facts VALUES(?,?,?,?,?,?)',(ref,patient_id,source_system,f['id'],revision,dump(f)))
      adopted.append({'fact_ref':ref,'status':'revised' if previous else 'adopted','concept':ALIASES.get(f['concept'],f['concept'])})
      for row in c.execute('SELECT * FROM assessments WHERE patient_id=? AND stale=0',(patient_id,)).fetchall():
       b=json.loads(row['payload']);t=b['assessment']['time']
       old_used=previous and previous['fact_ref'] in [z['fact_ref'] for z in b['facts']]
       can_affect=visible(f.get('event_time'),t['evaluation_at']) and visible(f.get('recorded_time'),t['knowledge_as_of'])
       if can_affect or old_used and visible(f.get('recorded_time'),t['knowledge_as_of']):
        c.execute('UPDATE assessments SET stale=1 WHERE assessment_id=?',(row['assessment_id'],))
        self.emit(c,patient_id,'assessment_invalidated',{'assessment_id':row['assessment_id'],'cause':ref,'producer':'integrated_scale'})
     self.emit(c,patient_id,'evidence_imported',{'adopted':adopted,'issues':issues})
    return {'patient_id':patient_id,'mapping_version':MAPPING_VERSION,'adopted':adopted,'issues':issues}
 def get_patient_context(self,patient_id,knowledge_as_of=None,evaluation_at=None):
    with self.connect() as c:
     facts=self._facts(c,patient_id,knowledge_as_of,evaluation_at)
     profiles=[{'profile_id':x['profile_id'],'revision':x['revision'],'profile':json.loads(x['payload']),'created_at':x['created_at'],'change_reason':x['reason']} for x in c.execute('SELECT * FROM profiles WHERE patient_id=? ORDER BY created_at,revision',(patient_id,))]
     summaries=[]
     for x in c.execute('SELECT * FROM assessments WHERE patient_id=? ORDER BY rowid',(patient_id,)):
      b=json.loads(x['payload']);summaries.append({'assessment_id':x['assessment_id'],'revision':x['revision'],'stale':bool(x['stale']),'time':b['assessment']['time'],'warning':b['assessment']['warning']})
     aliases=[dict(x) for x in c.execute('SELECT * FROM identities WHERE patient_id=?',(patient_id,))]
     fact_versions=[public_fact(x) for x in c.execute('SELECT * FROM facts WHERE patient_id=? ORDER BY rowid',(patient_id,))]
     if knowledge_as_of:fact_versions=[f for f in fact_versions if visible(f.get('recorded_time'),knowledge_as_of)]
    return {'patient_id':patient_id,'facts':facts,'profiles':profiles,'assessments':summaries,'identities':aliases,'fact_versions':fact_versions}
 def save_monitoring_profile(self,patient_id,profile,profile_id=None,expected_revision=None,change_reason=''):
    with self.connect() as c:
     prev=c.execute('SELECT * FROM profiles WHERE profile_id=? ORDER BY revision DESC LIMIT 1',(profile_id,)).fetchone() if profile_id else None
     if profile_id and not prev:raise ValueError('profile not found')
     if prev and (prev['patient_id']!=patient_id or expected_revision!=prev['revision']):raise Conflict('profile owner or revision mismatch')
     if not prev and expected_revision is not None:raise Conflict('new profile cannot have expected_revision')
     profile_id=profile_id or ident('profile');rev=prev['revision']+1 if prev else 1
     profile=copy.deepcopy(profile);profile.setdefault('purpose','');profile.setdefault('team_requirements',[])
     if prev and json.loads(prev['payload'])==profile:return {'profile_id':profile_id,'revision':prev['revision'],'profile':profile,'unchanged':True}
     p=copy.deepcopy(profile);p.setdefault('purpose','');p.setdefault('team_requirements',[])
     c.execute('INSERT INTO profiles VALUES(?,?,?,?,?,?)',(profile_id,rev,patient_id,dump(p),now(),change_reason))
     self.emit(c,patient_id,'profile_saved',{'profile_id':profile_id,'revision':rev,'reason':change_reason})
    return {'patient_id':patient_id,'profile_id':profile_id,'revision':rev,'profile':p}
 def _profile(self,c,patient,profile_id,revision):
    if not profile_id:return None
    row=c.execute('SELECT * FROM profiles WHERE profile_id=? '+('AND revision=? ' if revision else '')+'ORDER BY revision DESC LIMIT 1',((profile_id,revision) if revision else (profile_id,))).fetchone()
    if not row or row['patient_id']!=patient:raise ValueError('profile not found for patient')
    return {'profile_id':row['profile_id'],'revision':row['revision'],'profile':json.loads(row['payload']),'created_at':row['created_at'],'change_reason':row['reason']}
 def _select(self,facts,date):
    chosen={};mapped=[]
    for f in facts:
     concept=ALIASES.get(f['concept'],f['concept'])
     if concept not in LABS and concept not in CLINICAL:continue
     if not visible(f.get('event_time'),date):continue
     if concept in LABS and not same_evaluation_day(f['event_time'],date):continue
     v=copy.deepcopy(f);v['mapped_concept']=concept;v['mapping_reason']=f['concept']+' → '+concept
     if f['concept']=='age_days':
      try:
       age=float(f['value'])
       if isinstance(f['value'],bool) or not math.isfinite(age) or age<0:raise ValueError()
       # Age-at-event is not current age; compute only date-precise elapsed days.
       days=(datetime.fromisoformat(date[:10])-datetime.fromisoformat(f['event_time'][:10])).days
       v['state']='present' if age+days<28 else 'absent';v['mapping_reason']+='；原年龄加事件到评价日天数'
      except (TypeError,ValueError):v['state']='unknown';v['mapping_reason']+='；无法确认年龄'
     if concept not in chosen or time_order(v['event_time'],chosen[concept]['event_time'])==1:chosen[concept]=v
     elif time_order(v['event_time'],chosen[concept]['event_time']) in (0,None):
      old=chosen[concept]
      if (old.get('state'),old.get('value'),old.get('unit'),old.get('operator'))!=(v.get('state'),v.get('value'),v.get('unit'),v.get('operator')):
       old['state']='conflict';old['conflicting_refs']=list(set(old.get('conflicting_refs',[old['fact_ref']])+[v['fact_ref']]))
    return chosen
 def _snapshot(self,patient,date,asof,chosen,mode):
    s={'patient_id':patient,'observed_at':date,'recorded_at':asof,'source':{'kind':'evidence_projection','mapping_version':MAPPING_VERSION},'identity':mode,'labs':{},'clinical':{}}
    for k,f in chosen.items():
     base={'state':f['state'],'source':{'fact_ref':f['fact_ref']}}
     if f.get('event_time'):base['observed_at']=f['event_time']
     if f.get('recorded_time'):base['recorded_at']=f['recorded_time']
     if k in LABS:
      if f['state']=='present':
       if f.get('lower') is not None:base.update(lower=str(f['lower']),upper=None if f.get('upper') is None else str(f['upper']),lower_open=f.get('lower_open',False),upper_open=f.get('upper_open',False),unit=f.get('unit'))
       elif f.get('value') is not None:base.update(value=str(f['value']),operator=f.get('operator') or '=',unit=f.get('unit'))
       else:base['state']='unknown'
      elif f['state']=='absent':base['state']='unknown'
      s['labs'][k]=base
     else:
      if f.get('site'):base['site']=f['site']
      s['clinical'][k]=base
    return s
 def _fill_scale(self,target,tool,raw,chosen,reason):
    entry=target['entry'];process=target['process'];result=target['result']
    entry.update(selection_status='requested',selection_reason=reason,evidence_refs=[f['fact_ref'] for k,f in chosen.items() if k in api.service.FIELDS[tool]['clinical']])
    a=raw['assessment'];i=raw['intermediate'];c=i.get('calculation',{})
    result.update(status=a['status'],usable_conclusion=a['conclusion'],statement=a['statement'],reason=a['reason'],pending_requirements=a['pending_requirements'])
    entry.update(eligibility=c.get('eligibility','not_applicable'),clinical_review=c.get('clinical_review','not_applicable'),execution_mode='clinical' if a['status']=='evaluated' else 'research_only',reason=a['reason'])
    failed=raw['execution']['status']=='error'
    process.update(calculation_status='failed' if failed else 'partial' if c.get('mathematical')=='unknown' or c.get('score_complete') is False else 'complete',mathematical_result=c.get('mathematical'),source=i.get('source'))
    for k,n in i.get('normalized_labs',{}).items():
     process['normalized'][k].update({key:str(v) if v is not None and key in ('lower','upper') else v for key,v in n.items()})
    if tool=='mas_2016' and not failed:
     for row in i['additional_conditions']:
      process['conditions'][row['field']].update(operator=row['operator'],threshold=str(row['threshold']),state=row['state'])
     required=i['required'];cond=process['conditions'][required['field']]
     cond.update(operator=required['operator'],threshold=str(required['threshold']))
     # Use the original fixed rule and interval comparator, not an invented clinical threshold.
     try:
      from model import normalize,Q
      lo,hi,lop,hip,_=normalize(chosen.get('ferritin') and self._snapshot('', '', '', {'ferritin':chosen['ferritin']},'')['labs']['ferritin'],'ferritin')
      threshold=Q(required['threshold']);import z3
      cond['state']='met' if z3.is_true(z3.simplify(lo>threshold)) or z3.is_true(z3.simplify(lo==threshold)) and lop else 'not_met' if hi is not None and z3.is_true(z3.simplify(hi<=threshold)) else 'unknown'
     except Exception:cond['state']='unknown'
     for key in ('confirmed_count','unknown_count','required_additional_count'):process[key]=i[key]
    if tool=='ms_2019' and not failed:
     for row in i['contributions']:
      process['contributions'][row['field']].update({k:str(v) if v is not None else None for k,v in row.items() if k!='field'})
     process.update(score=c.get('score'),score_complete=c.get('score_complete'),lower=c['bounds']['lower'],upper=c['bounds']['upper'],lower_open=c['bounds']['lower_open'],upper_open=c['bounds']['upper_open'],bound_interpretation=c.get('bound_interpretation'),cutoff_operator=i['cutoff']['operator'],cutoff_value=str(i['cutoff']['value']))
    if tool=='hlh_2004' and not failed:
     process['criteria'].update({x['criterion']:x['state'] for x in i['criteria']})
     process.update(molecular=c['molecular'],five_of_eight=c['five_of_eight'],no_malignancy_background=c.get('no_malignancy_background'),confirmed_count=sum(x=='met' for x in c['items']),unknown_count=sum(x=='unknown' for x in c['items']))
 def prepare_monitoring_review(self,patient_id,evaluation_at,knowledge_as_of,requested_tools,question,selection_reason='',selected_by='user',profile_id=None,profile_revision=None,mode='retrospective',episode_id=None):
    if not living.time_range(evaluation_at) or not living.time_range(knowledge_as_of):raise ValueError('valid evaluation and knowledge times required')
    if not visible(evaluation_at,knowledge_as_of):raise ValueError('evaluation cannot be after knowledge cutoff')
    with self.connect() as c:
     facts=self._facts(c,patient_id,knowledge_as_of,evaluation_at)
     if not facts:raise ValueError('no visible source facts for this patient/time')
     profile=self._profile(c,patient_id,profile_id,profile_revision)
     fp=digest({'facts':sorted(f['fact_ref'] for f in facts),'patient':patient_id,'evaluation':evaluation_at,'asof':knowledge_as_of,'tools':sorted(requested_tools),'question':question,'profile':profile,'reason':selection_reason,'selected_by':selected_by,'mode':mode,'episode_id':episode_id})
     prev=c.execute('SELECT * FROM assessments WHERE fingerprint=? AND stale=0 ORDER BY revision DESC LIMIT 1',(fp,)).fetchone()
     if prev:
      b=json.loads(prev['payload']);return {'assessment_id':prev['assessment_id'],'revision':prev['revision'],'assessment':b['assessment'],'wide_row':wide_row(b['assessment']),'reused':True}
     chosen=self._select(facts,evaluation_at);snapshot=self._snapshot(patient_id,evaluation_at,knowledge_as_of,chosen,mode)
     a=blank_assessment();aid=ident('assessment')
     a['identity'].update(patient_id=patient_id,source_system=facts[0]['source_system'] if len(set(f['source_system'] for f in facts))==1 else 'multiple',external_patient_id=None,episode_id=episode_id,assessment_id=aid,revision=1)
     alias=c.execute('SELECT external_id FROM identities WHERE patient_id=? AND source_system=?',(patient_id,a['identity']['source_system'])).fetchone()
     if alias:a['identity']['external_patient_id']=alias[0]
     a['time'].update(evaluation_at=evaluation_at,knowledge_as_of=knowledge_as_of,generated_at=now(),precision='date' if len(evaluation_at)==10 else 'datetime',mode=mode)
     a['version'].update(contract=VERSION,evidence=digest(sorted(f['fact_ref'] for f in facts)),mapping=MAPPING_VERSION,rule=api.service.VERSION,profile=str(profile['revision']) if profile else None)
     a['purpose'].update(question=question,requested_tools=requested_tools,selection_reason=selection_reason,selected_by=selected_by)
     for k,target in list(a['input']['lab'].items())+list(a['input']['clinical'].items()):
      f=chosen.get(k);target['state']='unknown'
      if not f:continue
      for attr in target:
       if attr in ('source_ref','verification'):target[attr]=f['source'] if attr=='source_ref' and isinstance(f['source'],dict) else {'locator':f['source']} if attr=='source_ref' else f.get('verification',{}) if isinstance(f.get('verification',{}),dict) else {'status':f['verification']}
       elif attr=='fact_id':target[attr]=f['fact_ref']
       elif attr=='fact_revision':target[attr]=f['fact_revision']
       elif attr=='observed_at':target[attr]=f.get('event_time')
       elif attr=='recorded_at':target[attr]=f.get('recorded_time')
       elif attr=='mapping_reason':target[attr]=f['mapping_reason']
       elif attr in ('lower_open','upper_open'):target[attr]=f.get(attr,False)
       elif attr in ('value','lower','upper'):target[attr]=str(f[attr]) if f.get(attr) is not None else None
       else:target[attr]=f.get(attr)
     for f in facts:
      cat='diagnosis_refs' if f['concept'] in ('prior_diagnosis','diagnosis') or f.get('kind')=='diagnosis' else 'administration_refs' if f.get('kind')=='administration' or f.get('action') in ('administered','retrospective_administered') else 'treatment_order_refs' if f.get('kind')=='order' or f['concept'] in ('plan','treatment','medication') else 'other_fact_refs'
      a['context'][cat].append(f['fact_ref']);a['context']['history_refs'].append(f['fact_ref'])
     if profile:a['monitoring'].update(profile_id=profile['profile_id'],profile_revision=profile['revision'],**{k:v for k,v in profile['profile'].items() if k in a['monitoring']})
     raw_results={};errors=[]
     for tool in TOOLS:
      target=a['scale'][tool]
      if tool not in requested_tools:
       target['entry'].update(selection_status='not_requested',eligibility='not_applicable',clinical_review='not_applicable',execution_mode='skipped',reason='本次未选择该工具')
       target['process']['calculation_status']='not_run';target['result']['status']='not_requested';continue
      raw=api.evaluate({'tool':tool,'snapshot':snapshot});raw_results[tool]=raw
      self._fill_scale(target,tool,raw,chosen,selection_reason)
      if raw['execution']['status']=='error':errors.append({'module':tool,'error':raw['execution']['error']})
     references=profile['profile']['comparison_policy']['references'] if profile else ['previous','first']
     for lab in LABS:
      current_time=chosen.get(lab,{}).get('event_time')
      previous=[f for f in facts if ALIASES.get(f['concept'],f['concept'])==lab and f.get('event_time') and current_time and time_order(f['event_time'],current_time)==-1]
      times=sorted(set(f['event_time'] for f in previous),key=time_sort)
      for basis in ('previous','first'):
       t=a['trend'][lab][basis];t['status']='no_reference'
       if basis not in references:t['status']='not_requested';continue
       if not times or lab not in chosen:continue
       refdate=times[-1] if basis=='previous' else times[0]
       refchosen=self._select(facts,refdate);ref=refchosen.get(lab)
       if not ref:continue
       before=self._snapshot(patient_id,refdate,knowledge_as_of,{lab:ref},mode);after=self._snapshot(patient_id,evaluation_at,knowledge_as_of,{lab:chosen[lab]},mode)
       row=next(x for x in living.comparison_facts([{'observation_id':ref['fact_ref'],'snapshot':before},{'observation_id':aid,'snapshot':after}],1,'mas_2016') if x['field']==lab and x['basis']=='previous')
       t.update(status=row['status'],reference_fact_id=ref['fact_ref'],current_fact_id=chosen[lab]['fact_ref'],reference_time=refdate,current_time=chosen[lab]['event_time'],reason=row.get('reason'),reference_value=before['labs'][lab],current_value=after['labs'][lab],absolute_change=row.get('absolute_change',{}),percent_change=row.get('percent_change',{}),elapsed_hours_bounds=row.get('elapsed_hours_bounds') or [])
     a['interpretation']['clinical_question']=question
     a['warning'].update(status='not_reviewed',kind=None,message=None,observation_purpose=None,delivery_status='not_generated')
     older=c.execute('SELECT payload,stale FROM assessments WHERE patient_id=? ORDER BY rowid DESC',(patient_id,)).fetchall()
     for old in older:
      prior=json.loads(old['payload'])['assessment']
      if prior['time']['evaluation_at']==evaluation_at and prior['purpose']['requested_tools']==requested_tools and prior['monitoring']['profile_id']==(profile['profile_id'] if profile else None):
       a['execution']['supersedes']=prior['identity']['assessment_id'];break
     a['execution'].update(status='partial' if errors else 'calculated',module_statuses={**{k:v['execution']['status'] for k,v in raw_results.items()},'interpretation':'pending'},errors=errors,stale=False)
     jsonschema.validate(a,ASSESSMENT_SCHEMA)
     previous_assessments=[];seen=set()
     for old in older:
      if old['stale']:continue
      prior=json.loads(old['payload'])['assessment'];pid=prior['identity']['assessment_id']
      if pid in seen:continue
      seen.add(pid)
      if visible(prior['time']['evaluation_at'],evaluation_at) and visible(prior['time']['knowledge_as_of'],knowledge_as_of) and prior['execution']['module_statuses'].get('interpretation')=='complete':
       previous_assessments.append({k:prior[k] for k in ('identity','time','monitoring','interpretation','warning')})
      if len(previous_assessments)>=3:break
     b={'assessment':a,'facts':facts,'profile':profile,'raw_results':raw_results,'snapshot':snapshot,'clues':[],'previous_assessments':previous_assessments}
     c.execute('INSERT INTO assessments VALUES(?,?,?,?,?,0)',(aid,1,patient_id,fp,dump(b)))
     self.emit(c,patient_id,'scale_assessment',{'assessment_id':aid,'revision':1,'producer':'integrated_scale','evidence_refs':a['context']['history_refs'],'valid':True})
    return {'assessment_id':aid,'revision':1,'assessment':a,'wide_row':wide_row(a),'reused':False}
 def record_monitoring_review(self,assessment_id,expected_revision,review,skill_version,model_version,run_metadata=None):
    with self.connect() as c:
     row=c.execute('SELECT * FROM assessments WHERE assessment_id=? ORDER BY revision DESC LIMIT 1',(assessment_id,)).fetchone()
     if not row:raise ValueError('assessment not found')
     if row['stale']:raise Conflict('assessment source snapshot is stale; prepare again')
     if row['revision']!=expected_revision:raise Conflict('assessment revision mismatch')
     b=json.loads(row['payload']);refs=set(f['fact_ref'] for f in b['facts'])
     used=review['supporting_refs']+review['opposing_refs']+review['warning']['evidence_refs']+review['warning']['action_refs']+[r for clue in review['clues'] for r in clue['evidence_refs']]
     allowed_actions=set(b['assessment']['context']['treatment_order_refs'])
     if not set(review['warning']['action_refs'])<=allowed_actions:raise ValueError('action reference must identify a documented order/plan')
     if not set(used)<=refs:raise ValueError('review references unknown source facts: '+str(set(used)-refs))
     if not set(review['source_refs'])<={'D','P1.1','P1.2','P1.3','P1.4','P6.1','R10','C2016','M2019','H2004'}:raise ValueError('unknown literature reference')
     w=review['warning']
     if w['status']!='none' and (not w['evidence_refs'] or not review['source_refs']):raise ValueError('warning requires evidence and literature references')
     if (w['status']=='none')!=(w['kind']=='none'):raise ValueError('warning status/kind mismatch')
     if w['kind']=='mas_possible' and w['status']!='review':raise ValueError('MAS possibility requires review status')
     if not skill_version or not model_version:raise ValueError('method versions required')
     a=b['assessment'];a['identity']['revision']=expected_revision+1
     a['version'].update(skill=skill_version,model=model_version)
     a['interpretation'].update({k:copy.deepcopy(review[k]) for k in a['interpretation']})
     notification=b['profile']['profile']['notification_policy']['repeat'] if b['profile'] else 'changes_only'
     # Deduplicate semantic alert identity; wording alone must not create a new alert.
     delivery='not_required' if w['status']=='none' else 'available'
     fingerprint=digest({'kind':w['kind'],'status':w['status'],'evidence_refs':sorted(w['evidence_refs']),'action_refs':sorted(w['action_refs']),'evidence':a['version']['evidence'],'profile':b['profile']})
     if notification=='changes_only':
      old=c.execute("SELECT payload FROM changes WHERE patient_id=? AND event_type='monitoring_alert'",(row['patient_id'],)).fetchall()
      if any(json.loads(x[0]).get('fingerprint')==fingerprint for x in old):delivery='duplicate_suppressed'
     a['warning'].update(**w,delivery_status=delivery);a['execution']['module_statuses']['interpretation']='complete'
     a['execution']['errors']=[error for error in a['execution']['errors'] if error.get('module')!='interpretation']
     a['execution']['status']='partial' if a['execution']['errors'] else 'complete'
     b.update(clues=review['clues'],literature_refs=review['source_refs'],run_metadata=run_metadata or {})
     jsonschema.validate(a,ASSESSMENT_SCHEMA)
     c.execute('INSERT INTO assessments VALUES(?,?,?,?,?,0)',(assessment_id,expected_revision+1,row['patient_id'],row['fingerprint'],dump(b)))
     for kind in ('course_interpretation','monitoring_alert'):
      if kind=='monitoring_alert' and delivery in ('not_required','duplicate_suppressed'):continue
      self.emit(c,row['patient_id'],kind,{'assessment_id':assessment_id,'revision':expected_revision+1,'producer':'integrated_scale','skill_version':skill_version,'model_version':model_version,'evidence_refs':sorted(set(used)),'valid':True,'fingerprint':fingerprint})
    return {'assessment_id':assessment_id,'revision':expected_revision+1,'assessment':a,'wide_row':wide_row(a)}
 def record_review_failure(self,assessment_id,expected_revision,message,model_version='unknown'):
    """Persist a failed model attempt without replacing medical interpretation."""
    with self.lock, self.connect() as c:
     row=c.execute('SELECT * FROM assessments WHERE assessment_id=? ORDER BY revision DESC LIMIT 1',(assessment_id,)).fetchone()
     if not row or row['revision']!=expected_revision:return
     b=json.loads(row['payload']);a=b['assessment'];revision=expected_revision+1
     a['identity']['revision']=revision
     a['execution']['status']='partial';a['execution']['module_statuses']['interpretation']='failed'
     a['execution']['errors'].append({'module':'interpretation','message':message,'model':model_version})
     a['execution']['stale']=bool(row['stale'])
     c.execute('INSERT INTO assessments VALUES(?,?,?,?,?,?)',(assessment_id,revision,row['patient_id'],row['fingerprint'],dump(b),row['stale']))
     self.emit(c,row['patient_id'],'review_failed',{'assessment_id':assessment_id,'revision':revision,'message':message,'producer':'integrated_scale'})

 def record_monitoring_failure(self,assessment_id,expected_revision,message,model_version):
    with self.connect() as c:
     row=c.execute('SELECT revision FROM assessments WHERE assessment_id=? ORDER BY revision DESC LIMIT 1',(assessment_id,)).fetchone()
     if not row or row['revision']!=expected_revision:raise Conflict('assessment revision mismatch')
    self.record_review_failure(assessment_id,expected_revision,message,model_version)
    return self.export_assessment_bundle(assessment_id)

 def export_assessment_bundle(self,assessment_id,revision=None):
    with self.connect() as c:
     row=c.execute('SELECT * FROM assessments WHERE assessment_id=? '+('AND revision=? ' if revision else '')+'ORDER BY revision DESC LIMIT 1',((assessment_id,revision) if revision else (assessment_id,))).fetchone()
     if not row:raise ValueError('assessment not found')
     b=json.loads(row['payload']);b['assessment']['execution']['stale']=bool(row['stale'])
    b.update(assessment_id=assessment_id,revision=row['revision'],wide_row=wide_row(b['assessment']),field_dictionary=FIELD_DICTIONARY,contract_version=VERSION)
    return b
 def list_assessment_changes(self,patient_id,cursor=0):
    with self.connect() as c:
     rows=[{'cursor':r['cursor'],'event_type':r['event_type'],'payload':json.loads(r['payload']),'created_at':r['created_at']} for r in c.execute('SELECT * FROM changes WHERE patient_id=? AND cursor>? ORDER BY cursor',(patient_id,cursor))]
    return {'patient_id':patient_id,'changes':rows,'cursor':rows[-1]['cursor'] if rows else cursor}

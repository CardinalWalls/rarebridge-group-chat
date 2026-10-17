"""Deterministic same-source chart projection; no new medical judgement."""
import copy,json,gzip,base64
VERSION='chat-scale-card-1.0'
def clean(v):
 if isinstance(v,dict):return {k:clean(x) for k,x in v.items() if x is not None and x!={} and x!=[]}
 if isinstance(v,list):return [clean(x) for x in v]
 return v

def chart_payload(bundle,kind='new_observation',changed=(),history=()):
 a=bundle['assessment'];patient=a['identity']['patient_id'];facts=copy.deepcopy(bundle['facts']);refs={f['fact_ref'] for f in facts}
 if any(f.get('patient_id',patient)!=patient for f in facts):raise ValueError('cross-patient facts')
 if not set(changed)<=refs:raise ValueError('changed facts not in bundle')
 for k in ['supporting_refs','opposing_refs']:
  if not set(a['interpretation'].get(k,[]))<=refs:raise ValueError('invalid interpretation reference')
 if not set(a['warning']['evidence_refs'])<=refs:raise ValueError('invalid warning reference')
 for tool in a['scale'].values():
  if not set(tool['entry'].get('evidence_refs',[]))<=refs:raise ValueError('invalid scale reference')
 for values in a['trend'].values():
  for t in values.values():
   for k in ('reference_fact_id','current_fact_id'):
    if t.get(k) and t[k] not in refs:raise ValueError('invalid trend reference')
 def validate_refs(value):
  if isinstance(value,dict):
   for k,v in value.items():
    if k.endswith('_refs') and k!='source_refs' and isinstance(v,list) and all(isinstance(x,str) for x in v):
     if not set(v)<=refs:raise ValueError('unresolved source references: '+k)
    elif isinstance(v,(dict,list)):validate_refs(v)
  elif isinstance(value,list):
   for v in value:validate_refs(v)
 validate_refs(a.get('context',{}));validate_refs(a['scale'])
 context={k:{'value':'unknown','basis_refs':[]} for k in ['care_phase','care_setting','diagnosis_record','evidence_identity']}
 diagnoses=[];messages={}
 for f in facts:
  if f['concept'] in ('care_phase','care_setting') and f['state']=='present':context[f['concept']]={'value':f['value'],'basis_refs':[f['fact_ref']]}
  if f['fact_ref'] in a['context']['diagnosis_refs']:diagnoses.append(f['fact_ref'])
  source=f.get('source',{})
  if isinstance(source,dict) and source.get('message_id'):
   messages[source['message_id']]={'message_id':source['message_id'],'channel_id':source['channel_id'],'attachment_ids':source.get('attachment_ids',[])}
 if diagnoses:
  diagnostic_facts=[f for f in facts if f['fact_ref'] in diagnoses]
  direct=any(isinstance(f.get('source'),dict) and f['source'].get('kind') in ('physician_record','clinical_document','source_report') for f in diagnostic_facts)
  label='附件或诊疗文书记载诊断/病史，提取内容需结合原件核对' if direct else '家长转述诊断/既往病史；诊断原件未附或未核对'
  context['diagnosis_record']={'value':label,'basis_refs':diagnoses}
 else:context['diagnosis_record']={'value':'本次资料未包含可引用的诊断记录','basis_refs':[]}
 context['evidence_identity']={'value':'逐项保留原始来源与记录时间','basis_refs':list(refs)}
 source={**a['identity'],**a['time'],'assessment_revision':a['identity']['revision'],'stale':a['execution']['stale'],'supersedes':a['execution']['supersedes']}
 scales={}
 for tool,parts in a['scale'].items():scales[tool]={k:copy.deepcopy(parts[k]) for k in ['entry','process','result']}
 payload={'presentation_version':VERSION,'message_type':'scale_chart_update','source_assessment':source,'source_profile':{'profile_id':a['monitoring']['profile_id'],'profile_revision':a['monitoring']['profile_revision']},'facts':facts,'scales':scales,'trend':clean(a['trend']),'interpretation':a['interpretation'],'warning':a['warning'],'display_context':context,'execution':copy.deepcopy(a['execution']),'update':{'kind':kind,'changed_fact_refs':list(changed)},'history':list(history),'source_messages':list(messages.values())}
 return payload

def message_content(payload):
 raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
 text='```scale_chart_update\n'+raw.decode()+'\n```'
 if len(text)>30000:
  if len(raw)>2_000_000:raise ValueError('chart payload exceeds 2MB safety bound')
  wrapper={'encoding':'gzip-base64','uncompressed_bytes':len(raw),'data':base64.b64encode(gzip.compress(raw,mtime=0)).decode()}
  text='```scale_chart_update\n'+json.dumps(wrapper,separators=(',',':'))+'\n```'
 if len(text)>31000:raise ValueError('compressed chart exceeds Raft message limit')
 return text

def decode_message(text):
 obj=json.loads(text.split('\n',1)[1].rsplit('\n',1)[0])
 if obj.get('encoding')=='gzip-base64':return json.loads(gzip.decompress(base64.b64decode(obj['data'])))
 return obj

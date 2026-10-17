"""Scoped channel-message orchestration. Native Grok calls this through MCP."""
import asyncio,copy,hashlib,json,os,sqlite3,subprocess,sys,threading,tempfile,re,fcntl
from contextlib import closing
from pathlib import Path
from datetime import datetime,timezone
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
sys.path.insert(0,str(ROOT/'统一服务'))
from unified_contract import FACT_SCHEMA,PROFILE_SCHEMA,enum,obj,S,A,N
import agent_runner
from raft_client import RaftClient
from presentation import chart_payload,message_content,decode_message
EXTRACTION_SCHEMA=obj({'operation':enum('new_observation','completion','correction','context_update','profile_change','assessment_request','explain','clarify'),'evaluation_at':N,'question':S,'requested_tools':{'type':'array','items':enum('mas_2016','ms_2019','hlh_2004')},'facts':{'type':'array','items':obj({'target_fact_ref':N,'concept':S,'state':enum('present','absent','unknown','conflict'),'value':{'type':['number','string','boolean','null']},'unit':N,'operator':enum('=','>','>=','<','<=',None),'event_time':N,'document_time':N,'kind':S,'quote':S})},'sensitivity':enum('early_attention','corroborated_attention',None),'clarification':S})
EXTRACTION_SCHEMA['properties']['profile_changes']={'type':'object','properties':{'focus':A,'priorities':{'type':'object','additionalProperties':{'type':['string','number']}},'comparison_policy':PROFILE_SCHEMA['properties']['comparison_policy'],'notification_policy':PROFILE_SCHEMA['properties']['notification_policy']},'additionalProperties':False}
LOCK=threading.RLock()
def sha(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
class RemoteService:
 def __init__(self,binding,audit):self.b=binding;self.audit=audit
 def dispatch(self,name,args):
  async def call():
   async with stdio_client(StdioServerParameters(command=str(ROOT/'.venv/bin/python'),args=[str(ROOT/'接口实现/mcp_server.py')],env={**os.environ,'LIVING_SCALE_DB':self.b['database']})) as streams:
    async with ClientSession(*streams) as c:
     await c.initialize();r=await c.call_tool(name,args);return r.structuredContent or json.loads(next(x.text for x in r.content if hasattr(x,'text')))
  result=asyncio.run(call());self.audit.append({'tool':name,'request':args,'response':result})
  return result
 def record_review_failure(self,assessment_id,expected_revision,message,model_version):
  result=self.dispatch('record_monitoring_failure',dict(assessment_id=assessment_id,expected_revision=expected_revision,message=message,model_version=model_version))
  if not result['ok']:raise ValueError(result['error'])

class Processor:
 def __init__(self,binding):
  self.b=binding;self.root=Path(binding['state_dir']);self.root.mkdir(parents=True,exist_ok=True)
  self.db=self.root/'messages.sqlite';self.raft=RaftClient(binding)
  with closing(sqlite3.connect(self.db)) as c, c:c.execute('CREATE TABLE IF NOT EXISTS processed(message_id TEXT PRIMARY KEY,status TEXT,result TEXT)')
 def _wrapper(self):
  roots=list(Path(self.b['transport_root']).glob('*/raft'))
  if not roots:raise ValueError('Raft agent transport not ready')
  return str(max(roots,key=lambda p:p.stat().st_mtime))
 def _cli(self,args,text=None):
  r=subprocess.run([self._wrapper(),*args],input=text,text=True,capture_output=True,timeout=40)
  if r.returncode:raise RuntimeError('Raft authorized CLI failed: '+r.stderr[-700:])
  return r.stdout
 def _post(self,text):
  # Respect the native freshness guard: read again after a hold, never --anyway.
  attempts=[]
  for attempt in range(3):
   matches=[m for m in self.raft.messages() if m.get('content')==text and m.get('senderType')=='agent' and m.get('senderId')==self.b['agent_id']]
   if matches:return {'state':'sent','messageId':matches[-1]['id'],'reconciled':True}
   self._cli(['message','read','--target',self.b['channel_target'],'--limit','10'])
   r=subprocess.run([self._wrapper(),'message','send','--target',self.b['channel_target'],'--target-confirmed','--json'],input=text,text=True,capture_output=True,timeout=40)
   attempts.append({'attempt':attempt+1,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
   (self.root/'last-send-attempts.json').write_text(json.dumps(attempts,ensure_ascii=False,indent=2))
   if r.returncode==0:
    data=json.loads(r.stdout)
    if data.get('state')=='sent':return data
   detail=r.stdout+r.stderr
   if not any(k in detail.lower() for k in ('freshness','held','newmessagecount','unread')):raise RuntimeError('Raft send failed: '+detail[:900])
  raise RuntimeError('Raft freshness guard still holds after 3 read/send attempts; pending outbox retained')
 def context(self):
  self.raft.authorize()
  audit=[];r=RemoteService(self.b,audit).dispatch('get_patient_context',{'patient_id':self.b['patient_id']})
  return {'patient_id':self.b['patient_id'],'facts':r.get('facts',[]),'profiles':r.get('profiles',[]),'assessments':r.get('assessments',[])}
 def _single_attachment_sources(self,message):
  sources=[]
  for a in message.get('attachments',[]):
   aid=a['id'];blob=self.raft.request('attachments/'+aid,raw=True)
   if len(blob)>12_000_000:raise ValueError('attachment exceeds scoped extraction limit')
   name=a.get('filename',a.get('name','attachment'));mime=a.get('mimeType','')
   if mime.startswith('text/') or name.lower().endswith(('.txt','.csv','.json','.md')):
    sources.append({'attachment_id':aid,'text':blob.decode('utf-8-sig'),'method':'text'})
    continue
   with tempfile.TemporaryDirectory() as tmp:
    file=Path(tmp)/('report.pdf' if 'pdf' in mime or name.lower().endswith('.pdf') else 'image');file.write_bytes(blob)
    images=[]
    if file.suffix=='.pdf':
     r=subprocess.run(['pdftotext','-layout',str(file),'-'],text=True,capture_output=True,timeout=25)
     if r.returncode==0 and r.stdout.strip():
      sources.append({'attachment_id':aid,'text':r.stdout,'method':'pdftotext'});continue
     subprocess.run(['pdftoppm','-png','-scale-to','2200',str(file),str(Path(tmp)/'page')],check=True,capture_output=True,timeout=60)
     images=sorted(Path(tmp).glob('page-*.png'))
    elif mime.startswith('image/'):images=[file]
    else:
     sources.append({'attachment_id':aid,'text':'','method':'unsupported','issue':'无法提取，保留原附件'});continue
    for page,img in enumerate(images,1):
     r=subprocess.run(['swift',str(HERE/'ocr.swift'),str(img)],text=True,capture_output=True,timeout=60)
     if r.returncode:raise ValueError('image OCR failed; original attachment retained')
     rows=json.loads(r.stdout)
     sources.append({'attachment_id':aid,'page':page,'text':'\n'.join(x['text'] for x in rows),'method':'apple_vision_ocr','ocr_rows':rows,'issue':'OCR内容需结合版面核对；不得猜测串列项目'})
  return sources
 def _attachments(self,message):
  sources=[]
  for attachment in message.get('attachments',[]):
   try:sources.extend(self._single_attachment_sources({'attachments':[attachment]}))
   except (ValueError,RuntimeError,subprocess.SubprocessError,UnicodeError) as exc:
    sources.append({'attachment_id':attachment.get('id'),'text':'','method':'unreadable','issue':str(exc)})
  return sources
 def process(self,message_id):
  with (self.root/'processing.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX)
   try:return self._process(message_id)
   finally:fcntl.flock(lock,fcntl.LOCK_UN)
 def _process(self,message_id):
  self.raft.authorize()
  if not re.fullmatch(r'(?:[0-9a-f]{8}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',message_id):raise ValueError('invalid message UUID')
  with LOCK:
   with closing(sqlite3.connect(self.db)) as c, c:
    row=c.execute('SELECT status,result FROM processed WHERE message_id=?',(message_id,)).fetchone()
    if row and row[0]=='complete':return {'status':'already_processed',**json.loads(row[1])}
   audit=[];svc=RemoteService(self.b,audit);folder=self.root/message_id;folder.mkdir(exist_ok=True)
   try:
    msg=self.raft.source_message(message_id);message_id=msg['id']
    if not re.fullmatch(r'[0-9a-f-]{36}',message_id):raise ValueError('invalid source message identity')
    folder=self.root/message_id;folder.mkdir(exist_ok=True)
    with closing(sqlite3.connect(self.db)) as c, c:
     row=c.execute('SELECT status,result FROM processed WHERE message_id=?',(message_id,)).fetchone()
     if row and row[0]=='complete':return {'status':'already_processed',**json.loads(row[1])}
     c.execute('INSERT OR REPLACE INTO processed VALUES(?,?,?)',(message_id,'processing','{}'))
    outbox=folder/'outbox.json'
    if outbox.exists():
     pending=json.loads(outbox.read_text());matches=[m for m in self.raft.messages() if m.get('content')==pending['content'] and m.get('senderType')=='agent' and m.get('senderId')==self.b['agent_id']]
     posted={'state':'sent','messageId':matches[-1]['id']} if matches else self._post(pending['content'])
     card=decode_message(pending['content'])
     result={'source_message_id':message_id,'assessment_id':pending['assessment_id'],'assessment_revision':card['source_assessment']['assessment_revision'],'operation':card['update']['kind'],'card_message_id':posted.get('message',{}).get('id') or posted.get('messageId'),'posted':posted,'recovered_outbox':True}
     return self._finish(message_id,result,audit,folder)
    ctxt=svc.dispatch('get_patient_context',{'patient_id':self.b['patient_id']})
    if not ctxt['ok']:raise ValueError(ctxt['error'])
    attachments=self._attachments(msg)
    fulltext=msg['content']+'\n'+'\n'.join(x['text'] for x in attachments)
    payload={'message':{'id':message_id,'content':msg['content'],'created_at':msg.get('createdAt')},'attachments':attachments,'patient_id':self.b['patient_id'],'context':ctxt}
    task='''将本条真实人类消息及附件转换为既有量表事实。上下文只用于定位已有事实/方案，不把旧消息重复提取。未知不补阴性。每条新事实quote必须是本条消息或附件中的连续原文。数字、单位、比较符保持原样。既往诊断、计划、医嘱和实际用药分别记录，kind用diagnosis/order/administration/observation。普通聊天问答operation=explain且facts=[]。若明确要求改用或增加量表重新评估，operation=assessment_request且不新增事实；按明确请求选工具。明确纠正用correction并target_fact_ref引用精确旧事实；若无法唯一对应则clarify，不猜。用户说补同报告则completion。event_time是诊疗事件发生时间，document_time是文书形成或记载日期，不能混用：例如2月12日出院总结记载1月4日考虑MAS，event_time必须为1月4日，document_time为2月12日，system received/recorded_time由程序处理。日期未知填null；不要补当前日期。只有明确已知/疑似sJIA才能写known_or_suspected_sjia；不能用普通关节炎代替。evalution_at用本次明确日期，不确定可null。请求使用哪个表就选哪个；否则沿用最近所选表，无依据不自动三表全选。仅从消息确实要求调整时填sensitivity，early_attention=更早关注弱线索，corroborated_attention=多证据印证。没有明确调整填null。若用户明确修改关注指标、优先级、比较参照或重复提醒方式，在profile_changes里仅填明确要求的字段，其他不写；focus使用指标英文名。新输入不给药物建议。facts中value为临床原意，state为存在/明确否定/未知/冲突。不要从用药推断诊断。kind=diagnosis时concept=prior_diagnosis，value保留诊断原文及确定程度。其他测量concept使用ferritin/platelets/ast/triglycerides/fibrinogen/ldh/hemoglobin/anc/scd25；临床前提用known_or_suspected_sjia/fever/alternative_causes_excluded/cns/hemorrhage/active_arthritis/splenomegaly/hemophagocytosis/nk_activity_low/fasting/molecular_hlh/infant_under_4_weeks/no_malignancy。不要把指令中的假设和示例当成真实病情。'''
    saved_extraction=folder/'extraction.json'
    cached=json.loads(saved_extraction.read_text()) if saved_extraction.exists() else None
    if cached and cached['input']['message']==payload['message'] and cached['input']['attachments']==payload['attachments']:
     extraction,skill=cached['output'],cached['skill']
    else:
     extraction,skill=agent_runner._invoke(task,payload,EXTRACTION_SCHEMA)
     saved_extraction.write_text(json.dumps({'input':payload,'output':extraction,'skill':skill},ensure_ascii=False,indent=2))
    if extraction['operation']=='explain' and (extraction['facts'] or extraction['sensitivity'] or extraction.get('profile_changes')):raise ValueError('read-only explanation cannot mutate facts or monitoring profile')
    if extraction['operation']=='new_observation' and extraction['facts'] and all(f['kind'] in ('diagnosis','order','administration') for f in extraction['facts']):extraction['operation']='context_update'
    facts=[];changed=[];known={f['fact_ref']:f for f in ctxt['facts']}
    recorded=msg.get('createdAt') or datetime.now(timezone.utc).isoformat()
    for n,f in enumerate(extraction['facts']):
     if not f['quote'] or f['quote'] not in fulltext:raise ValueError('extraction quote not in current human message/attachment')
     target=f['target_fact_ref']
     if extraction['operation']=='correction' and not target:raise ValueError('correction must identify original fact')
     if target:
      if target not in known:raise ValueError('target fact not in this patient/channel')
      old=known[target]
      if old['source_system']!='raft':raise ValueError('correction of external imported source requires original source owner')
      fid=old['source_id'];event=f['event_time'] or old.get('event_time')
     else:fid=message_id+':'+str(n);event=f['event_time']
     source={'kind':'source_report' if any(f['quote'] in item['text'] for item in attachments) else 'caregiver_report','message_id':message_id,'channel_id':self.b['channel_id'],'quote':f['quote'],'document_time':f.get('document_time'),'attachment_ids':[x['attachment_id'] for x in attachments],'speaker_role':'user','extraction_model':agent_runner.MODEL}
     if target:source['corrects']=target
     original_unit=f['unit']
     unit={'×10^9/L':'10^9/L','×10⁹/L':'10^9/L','10⁹/L':'10^9/L','mg/dl':'mg/dL','ng/ml':'ng/mL'}.get(original_unit,original_unit)
     source['reported_unit']=original_unit
     source['unit_mapping']='typographic normalization only'
     facts.append({'id':fid,'concept':f['concept'],'state':f['state'],'value':f['value'],'unit':unit,'operator':f['operator'],'event_time':event,'recorded_time':recorded,'source':source,'kind':f['kind'],'verification':{'status':'extracted_from_user_message','quote_checked':True}})
    if extraction['operation']=='clarify':
     posted=self._post(extraction['clarification'] or '请明确需要更正的日期和指标。')
     result={'source_message_id':message_id,'operation':'clarify','posted':posted};return self._finish(message_id,result,audit,folder)
    if facts:
     imported=svc.dispatch('import_evidence',{'source_system':'raft','external_patient_id':self.b['patient_id'],'patient_id':self.b['patient_id'],'envelope':{'subject_id':self.b['patient_id'],'facts':facts}})
     if not imported['ok']:raise ValueError(imported['error'])
     changed=[x['fact_ref'] for x in imported['adopted'] if x['status']!='unchanged']
     if imported['issues']:raise ValueError('facts not all adopted: '+str(imported['issues']))
    # Use persisted profile, changing only when the human explicitly requests it.
    profiles=ctxt.get('profiles',[]);profile=profiles[-1] if profiles else None
    if extraction['sensitivity'] or extraction.get('profile_changes'):
     base=copy.deepcopy(profile['profile']) if profile else {'sensitivity':extraction['sensitivity'] or 'early_attention','focus':['ferritin','platelets','ast'],'priorities':{},'comparison_policy':{'references':['previous','first']},'notification_policy':{'repeat':'changes_only'}}
     if extraction['sensitivity']:base['sensitivity']=extraction['sensitivity']
     base.update(extraction.get('profile_changes',{}))
     args={'patient_id':self.b['patient_id'],'profile':base,'change_reason':msg['content']}
     if profile:args.update(profile_id=profile['profile_id'],expected_revision=profile['revision'])
     profile=svc.dispatch('save_monitoring_profile',args)
     if not profile['ok']:raise ValueError(profile['error'])
    previous=ctxt.get('assessments',[]);latest=previous[-1] if previous else None
    dates=[f['event_time'] for f in facts if f.get('event_time')]
    evaluation=extraction['evaluation_at'] or (max(dates) if dates else latest['time']['evaluation_at'] if latest else None)
    if not evaluation:
     result={'source_message_id':message_id,'operation':'clarify','posted':self._post('资料已保存。请说明这份检查的日期，才能把它放在正确的病程时间点。')};return self._finish(message_id,result,audit,folder)
    if extraction['operation']=='explain' and latest and not facts:
     prepared={'ok':True,'assessment_id':latest['assessment_id']}
    else:
     requested_tools=extraction['requested_tools']
     if not requested_tools and latest:
      latest_bundle=svc.dispatch('export_assessment_bundle',{'assessment_id':latest['assessment_id']})
      if latest_bundle['ok']:requested_tools=latest_bundle['assessment']['purpose']['requested_tools']
     args={'patient_id':self.b['patient_id'],'evaluation_at':evaluation,'knowledge_as_of':recorded,'requested_tools':requested_tools,'question':extraction['question'],'selection_reason':'依据本条用户问题与已有用途选择','selected_by':'raft-agent','mode':self.b.get('mode','retrospective')}
     if profile:args.update(profile_id=profile['profile_id'],profile_revision=profile['revision'])
     prepared=svc.dispatch('prepare_monitoring_review',args)
    if not prepared['ok']:raise ValueError(prepared['error'])
    aid=prepared['assessment_id']
    reviewed=None;review_error=None
    try:reviewed=agent_runner.review(svc,aid)
    except Exception as exc:review_error=str(exc)
    bundle=svc.dispatch('export_assessment_bundle',{'assessment_id':aid})
    if not bundle['ok']:raise ValueError(bundle['error'])
    history=[]
    with closing(sqlite3.connect(self.db)) as c, c:
     for row in c.execute("SELECT result FROM processed WHERE status='complete'"):
      old=json.loads(row[0])
      if old.get('assessment_id'):history.append({'assessment_id':old['assessment_id'],'revision':old.get('assessment_revision'),'source_message_id':old['source_message_id'],'card_message_id':old.get('card_message_id'),'channel_id':self.b['channel_id'],'stale':next((x.get('stale',False) for x in reversed(ctxt.get('assessments',[])) if x['assessment_id']==old['assessment_id']),False)})
    card=chart_payload(bundle,extraction['operation'],[x for x in changed if x in {f['fact_ref'] for f in bundle['facts']}],history)
    (folder/'card.json').write_text(json.dumps(card,ensure_ascii=False,indent=2))
    content=message_content(card)
    (folder/'card.json').write_text(json.dumps(card,ensure_ascii=False,indent=2))
    outbox=folder/'outbox.json'
    outbox.write_text(json.dumps({'content':content,'assessment_id':aid},ensure_ascii=False))
    matches=[m for m in self.raft.messages() if m.get('content')==content and m.get('senderType')=='agent' and m.get('senderId')==self.b['agent_id']]
    posted={'state':'sent','messageId':matches[-1]['id']} if matches else self._post(content)
    cardid=posted.get('message',{}).get('id') or posted.get('messageId') or posted.get('id')
    result={'source_message_id':message_id,'assessment_id':aid,'assessment_revision':bundle['assessment']['identity']['revision'],'operation':extraction['operation'],'card_message_id':cardid,'posted':posted,'review_error':review_error}
    return self._finish(message_id,result,audit,folder)
   except Exception as exc:
    (folder/'failure.json').write_text(json.dumps({'error':str(exc),'audit':audit},ensure_ascii=False,indent=2))
    with closing(sqlite3.connect(self.db)) as c, c:c.execute('INSERT OR REPLACE INTO processed VALUES(?,?,?)',(message_id,'failed',json.dumps({'error':str(exc)})))
    raise
 def _finish(self,mid,result,audit,folder):
  (folder/'mcp-calls.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2));(folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
  with closing(sqlite3.connect(self.db)) as c, c:c.execute('INSERT OR REPLACE INTO processed VALUES(?,?,?)',(mid,'complete',json.dumps(result)))
  return {'status':'complete',**result}

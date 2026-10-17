"""Single source for integrated tools, stable wide columns and field metadata."""
import copy
VERSION='integrated-scale-1.0'
MAPPING_VERSION='evidence-to-local-1.0'
LABS={'ferritin':'铁蛋白','platelets':'血小板','ast':'AST','triglycerides':'甘油三酯','fibrinogen':'纤维蛋白原','ldh':'LDH','hemoglobin':'血红蛋白','anc':'中性粒细胞绝对计数','scd25':'可溶性CD25'}
CLINICAL={'fever':'发热','sjia':'已知或疑似sJIA','alternative_causes_excluded':'其他原因解释已核查','cns':'中枢神经受累','hemorrhage':'出血','active_arthritis':'活动性关节炎','splenomegaly':'脾大','hemophagocytosis':'组织噬血','nk_activity_low':'NK活性低','fasting':'采血空腹','molecular_hlh':'符合HLH的分子诊断','infant_under_4_weeks':'不足四周','no_malignancy':'无恶性肿瘤证据'}
TOOLS=('mas_2016','ms_2019','hlh_2004')
S={'type':'string'}; N={'type':['string','null']}; A={'type':'array','items':S}; O={'type':'object'}
def obj(props,required=None):return {'type':'object','properties':props,'required':list(props) if required is None else required,'additionalProperties':False}
def enum(*values):return {'enum':list(values)}
FIELDS={}
def add(path,label,kind='string',values=None,owner=None):
    schema={'type':[kind,'null']} if kind not in ('array','object') else ({'type':'array','items':{}} if kind=='array' else {'type':'object'})
    if values is not None:schema={'enum':list(values)+([None] if None not in values else [])}
    schema.update(description=label,**{'x-owner':owner or path.split('.')[0],'x-null-meaning':'未产生、未知或不适用；结合相邻状态读取','x-source':path,'x-mapping':path})
    FIELDS[path]=schema
for group,names in {
 'identity':{'patient_id':'内部患者标识','source_system':'来源系统','external_patient_id':'外部患者标识','episode_id':'病程段','assessment_id':'评估标识','revision':'评估修订'},
 'time':{'evaluation_at':'评价时点','knowledge_as_of':'知识截止时间','generated_at':'生成时间','precision':'时间精度','mode':'资料身份'},
 'version':{x:'采用版本：'+x for x in ['contract','evidence','mapping','rule','profile','skill','model']},
 'purpose':{'question':'当前问题','selection_reason':'工具选择理由','selected_by':'工具选择者'},
 'interpretation':{'clinical_question':'解释问题','course_context':'病程背景','treatment_effects':'治疗影响','statement':'病程解释','changed_since_previous':'解释变化'},
 'warning':{'status':'提示状态','kind':'提示种类','message':'提示内容','observation_purpose':'继续观察目的','delivery_status':'呈现状态'},
 'monitoring':{'profile_id':'监测方案标识','profile_revision':'方案版本','sensitivity':'线索敏感度'},
 'execution':{'status':'执行状态','supersedes':'替代评估标识'}
}.items():
 for key,label in names.items():add(group+'.'+key,label,'integer' if key in ('revision','profile_revision') else 'string')
for p in ['purpose.requested_tools','context.diagnosis_refs','context.treatment_order_refs','context.administration_refs','context.other_fact_refs','context.history_refs','monitoring.focus','interpretation.supporting_refs','interpretation.opposing_refs','interpretation.uncertainties','warning.evidence_refs','warning.action_refs','execution.errors']:add(p,p,'array')
for p in ['monitoring.priorities','monitoring.comparison_policy','monitoring.notification_policy','execution.module_statuses']:add(p,p,'object')
add('execution.stale','历史结果已因新版本失效','boolean')
for field,label in LABS.items():
 for attr in ['fact_id','state','value','operator','lower','upper','unit','observed_at','recorded_at']:
  add(f'input.lab.{field}.{attr}',label+'：'+attr)
 add(f'input.lab.{field}.fact_revision',label+'：事实版本','integer')
 for attr in ['lower_open','upper_open']:add(f'input.lab.{field}.{attr}',label+'：边界开闭','boolean')
 for attr in ['source_ref','verification']:add(f'input.lab.{field}.{attr}',label+'：'+attr,'object')
for field,label in CLINICAL.items():
 for attr in ['fact_id','state','observed_at','recorded_at','mapping_reason']:add(f'input.clinical.{field}.{attr}',label+'：'+attr)
 add(f'input.clinical.{field}.fact_revision',label+'：事实版本','integer')
 for attr in ['source_ref','verification']:add(f'input.clinical.{field}.{attr}',label+'：'+attr,'object')
 if field=='hemophagocytosis':add(f'input.clinical.{field}.site','噬血组织部位')
TRI=['met','not_met','unknown','not_applicable']
CRITERIA=['fever','splenomegaly','cytopenias_two_lineages','triglycerides_or_fibrinogen','hemophagocytosis','nk_activity_low','ferritin','scd25']
MS_FIELDS=['cns','hemorrhage','active_arthritis','platelets','ldh','fibrinogen','ferritin']
for tool in TOOLS:
 p='scale.'+tool
 for key,values in {'selection_status':['requested','not_requested'],'eligibility':TRI,'clinical_review':TRI,'execution_mode':['clinical','research_only','skipped']}.items():add(p+'.entry.'+key,key,values=values)
 for attr in ['reason','selection_reason']:add(p+'.entry.'+attr,attr)
 add(p+'.entry.evidence_refs','入口事实引用','array')
 add(p+'.process.calculation_status','计算完成状态',values=['complete','partial','failed','not_run'])
 add(p+'.process.mathematical_result','原表数学结果',values=TRI)
 add(p+'.process.source','原规则来源')
 for lab in LABS:
  for attr in ['lower','upper','unit']:add(p+f'.process.normalized.{lab}.{attr}',lab+'归一化'+attr)
  for attr in ['lower_open','upper_open','exact']:add(p+f'.process.normalized.{lab}.{attr}',lab+attr,'boolean')
 for attr in ['status','usable_conclusion','statement','reason']:add(p+'.result.'+attr,'原表结果'+attr)
 add(p+'.result.pending_requirements','原表待核事项','array')
 if tool=='mas_2016':
  for lab in ['ferritin','platelets','ast','triglycerides','fibrinogen']:
   for attr in ['operator','threshold','state']:add(p+f'.process.conditions.{lab}.{attr}',lab+attr)
  for attr in ['confirmed_count','unknown_count','required_additional_count']:add(p+'.process.'+attr,attr,'integer')
 if tool=='ms_2019':
  for lab in MS_FIELDS:
   for attr in ['coefficient','normalized_value','unit','contribution']:add(p+f'.process.contributions.{lab}.{attr}',lab+attr)
  for attr in ['score','lower','upper','cutoff_operator','cutoff_value','bound_interpretation']:add(p+'.process.'+attr,attr)
  for attr in ['score_complete','lower_open','upper_open']:add(p+'.process.'+attr,attr,'boolean')
 if tool=='hlh_2004':
  for name in CRITERIA:add(p+'.process.criteria.'+name,name,values=TRI)
  for name in ['molecular','five_of_eight','no_malignancy_background']:add(p+'.process.'+name,name,values=TRI)
  for attr in ['confirmed_count','unknown_count']:add(p+'.process.'+attr,attr,'integer')
for lab in LABS:
 for basis in ['previous','first']:
  p=f'trend.{lab}.{basis}'
  for attr in ['status','reference_fact_id','current_fact_id','reference_time','current_time','reason']:add(p+'.'+attr,attr)
  for attr in ['reference_value','current_value','absolute_change','percent_change']:add(p+'.'+attr,attr,'object')
  add(p+'.elapsed_hours_bounds','间隔范围','array')

# Source mappings and finite status values are contract data, not separate prose.
for path,spec in FIELDS.items():
 parts=path.split('.')
 if path.startswith('input.lab.') or path.startswith('input.clinical.'):
  concept=parts[2]; attr=parts[3]
  upstream={'sjia':'known_or_suspected_sjia','infant_under_4_weeks':'age_days or infant_under_4_weeks'}.get(concept,concept)
  member={'fact_id':'id (namespaced/versioned)','fact_revision':'revision','observed_at':'event_time','recorded_at':'recorded_time','source_ref':'source'}.get(attr,attr)
  spec['x-source']='ClinicalEvidenceEnvelope.facts[concept='+upstream+'].'+member
  spec['x-mapping']=upstream+' -> '+concept+'; '+member+' -> '+attr
  if attr=='state':spec['enum']=['present','absent','unknown','conflict','not_applicable',None]
 elif path.startswith('scale.'):
  spec['x-source']='existing api.evaluate: '+parts[1]+' (entry/process/result projection)'
 elif path.startswith('trend.'):
  spec['x-source']='existing living.comparison_facts over cited per-indicator observations'
 elif path.startswith('interpretation.') or path.startswith('warning.'):
  spec['x-source']='versioned Skill review; evidence references validated by record_monitoring_review'
 elif path.startswith('monitoring.'):
  spec['x-source']='saved monitoring_profile version'
for path,values in {
 'time.mode':['synthetic','retrospective','contemporaneous'],
 'monitoring.sensitivity':['early_attention','corroborated_attention'],
 'warning.status':['not_reviewed','none','observe','review'],
 'warning.kind':['none','observation','mas_possible','course_followup'],
 'warning.delivery_status':['not_generated','not_required','available','duplicate_suppressed'],
 'execution.status':['calculated','partial','complete']}.items():FIELDS[path]['enum']=values+[None]

def nested_schema():
 root=obj({})
 for path,spec in FIELDS.items():
  node=root
  bits=path.split('.')
  for bit in bits[:-1]:
   if bit not in node['properties']:node['properties'][bit]=obj({});node['required'].append(bit)
   node=node['properties'][bit]
  node['properties'][bits[-1]]=copy.deepcopy(spec);node['required'].append(bits[-1])
 return root
ASSESSMENT_SCHEMA=nested_schema()
def blank_assessment():
 out={}
 for path,spec in FIELDS.items():
  cursor=out;parts=path.split('.')
  for part in parts[:-1]:cursor=cursor.setdefault(part,{})
  cursor[parts[-1]]=[] if spec.get('type')=='array' else {} if spec.get('type')=='object' else None
 return out

def wide_row(assessment):
 row={}
 for path in FIELDS:
  v=assessment
  for k in path.split('.'):v=v[k]
  row[path]=copy.deepcopy(v)
 return row

def restore_wide(row):
 if set(row)!=set(FIELDS):raise ValueError('wide columns differ from contract')
 out=blank_assessment()
 for path,value in row.items():
  x=out;parts=path.split('.')
  for part in parts[:-1]:x=x[part]
  x[parts[-1]]=copy.deepcopy(value)
 return out
FIELD_DICTIONARY=[{'field':k,'label':v['description'],'schema':v,'owner':v['x-owner'],'null_meaning':v['x-null-meaning'],'source':v['x-source'],'mapping':v['x-mapping']} for k,v in FIELDS.items()]
FACT_SCHEMA={'type':'object','required':['id','concept','state','value','event_time','recorded_time','source'],'properties':{'id':S,'concept':S,'state':enum('present','absent','unknown','conflict'),'event_time':N,'recorded_time':N,'source':{'type':['object','string']},'kind':S,'fact_revision':{'type':'integer','minimum':1}},'additionalProperties':True}
PROFILE_SCHEMA=obj({'sensitivity':enum('early_attention','corroborated_attention'),'focus':A,'priorities':{'type':'object','additionalProperties':{'type':['string','number']}},'comparison_policy':obj({'references':{'type':'array','items':enum('previous','first'),'minItems':1,'uniqueItems':True}}),'notification_policy':obj({'repeat':enum('changes_only','each_review')}),'purpose':S,'team_requirements':A},['sensitivity','focus','priorities','comparison_policy','notification_policy'])
WARNING_SCHEMA=obj({'status':enum('none','observe','review'),'kind':enum('none','observation','mas_possible','course_followup'),'message':S,'evidence_refs':A,'observation_purpose':S,'action_refs':A})
REVIEW_SCHEMA=obj({'clinical_question':S,'course_context':S,'supporting_refs':A,'opposing_refs':A,'treatment_effects':S,'uncertainties':A,'statement':S,'changed_since_previous':S,'warning':WARNING_SCHEMA,'clues':{'type':'array','items':obj({'name':S,'status':enum('new','strengthened','weakened','unchanged','uncertain'),'evidence_refs':A,'statement':S})},'source_refs':A},['clinical_question','course_context','supporting_refs','opposing_refs','treatment_effects','uncertainties','statement','changed_since_previous','warning','clues','source_refs'])
BASE={'patient_id':S}
TOOL_SCHEMAS={
'import_evidence':obj({'source_system':S,'external_patient_id':S,'patient_id':S,'envelope':{'type':'object','required':['subject_id','facts'],'properties':{'subject_id':S,'facts':{'type':'array','items':FACT_SCHEMA}},'additionalProperties':True}},['source_system','external_patient_id','patient_id','envelope']),
'get_patient_context':obj({**BASE,'knowledge_as_of':S,'evaluation_at':S},['patient_id']),
'save_monitoring_profile':obj({**BASE,'profile_id':S,'expected_revision':{'type':'integer','minimum':1},'profile':PROFILE_SCHEMA,'change_reason':S},['patient_id','profile']),
'prepare_monitoring_review':obj({**BASE,'evaluation_at':S,'knowledge_as_of':S,'requested_tools':{'type':'array','items':enum(*TOOLS),'uniqueItems':True},'question':S,'selection_reason':S,'selected_by':S,'profile_id':S,'profile_revision':{'type':'integer'},'mode':enum('synthetic','retrospective','contemporaneous'),'episode_id':S},['patient_id','evaluation_at','knowledge_as_of','requested_tools','question']),
'record_monitoring_review':obj({'assessment_id':S,'expected_revision':{'type':'integer'},'review':REVIEW_SCHEMA,'skill_version':S,'model_version':S,'run_metadata':O},['assessment_id','expected_revision','review','skill_version','model_version']),
'export_assessment_bundle':obj({'assessment_id':S,'revision':{'type':'integer'}},['assessment_id']),
'list_assessment_changes':obj({**BASE,'cursor':{'type':'integer','minimum':0}},['patient_id'])}
RESULT_SCHEMA={'type':'object','properties':{'ok':{'type':'boolean'},'error':O,'assessment':ASSESSMENT_SCHEMA,'wide_row':{'type':'object','properties':FIELDS,'required':list(FIELDS),'additionalProperties':False},'assessment_id':S,'revision':{'type':'integer'},'patient_id':S,'profile_id':S,'field_dictionary':{'type':'array'},'facts':{'type':'array'},'profiles':{'type':'array'},'assessments':{'type':'array'},'changes':{'type':'array'},'cursor':{'type':'integer'}},'required':['ok'],'additionalProperties':True}

# Explicit failed interpreter attempt; keeps source computations and no medical conclusion.
TOOL_SCHEMAS['record_monitoring_failure']=obj({'assessment_id':S,'expected_revision':{'type':'integer'},'message':S,'model_version':S},['assessment_id','expected_revision','message','model_version'])

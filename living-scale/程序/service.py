"""Explicit product contract around the preserved scale calculation engine."""
from __future__ import annotations
import copy
import json
import sys
from pathlib import Path
import jsonschema

PROJECT = Path(__file__).resolve().parents[2]
ENGINE_DIR = Path(__file__).resolve().parent / 'engine'
sys.path.insert(0, str(ENGINE_DIR))
from model import evaluate, snapshot_relation, replay, RULES, VERSION, require_orderable_snapshot, _evidence_time
from dynamic import run_dynamic

SCHEMA = json.loads((ENGINE_DIR/'input.schema.json').read_text())
TOOLS = ('mas_2016', 'ms_2019', 'hlh_2004')
CONTRACT_VERSION = 'scale-service-1.0'
LAB_NAMES = {'ferritin':'铁蛋白','platelets':'血小板','ast':'AST','triglycerides':'甘油三酯','fibrinogen':'纤维蛋白原','ldh':'LDH','hemoglobin':'血红蛋白','anc':'中性粒细胞绝对计数','scd25':'可溶性CD25'}
CLIN_NAMES = {'fever':'发热','sjia':'已知或临床疑似sJIA','alternative_causes_excluded':'异常的其他解释已核查排除','cns':'中枢神经系统受累','hemorrhage':'出血表现','active_arthritis':'活动性关节炎','splenomegaly':'脾大','hemophagocytosis':'组织噬血','nk_activity_low':'NK活性低或缺失','fasting':'采样空腹情况','molecular_hlh':'符合HLH的分子诊断','infant_under_4_weeks':'观察时不足4周','no_malignancy':'无恶性肿瘤证据背景'}
FIELDS = {
 'mas_2016': {'labs':['ferritin','platelets','ast','triglycerides','fibrinogen'], 'clinical':['fever','sjia','alternative_causes_excluded']},
 'ms_2019': {'labs':['platelets','ldh','fibrinogen','ferritin'], 'clinical':['fever','sjia','cns','hemorrhage','active_arthritis']},
 'hlh_2004': {'labs':['hemoglobin','platelets','anc','triglycerides','fibrinogen','ferritin','scd25'], 'clinical':['fever','splenomegaly','hemophagocytosis','nk_activity_low','fasting','molecular_hlh','infant_under_4_weeks','no_malignancy']}}
PURPOSES = {'mas_2016':'核对2016 sJIA相关MAS分类条件','ms_2019':'按MS评分辅助识别发热sJIA背景下的MAS','hlh_2004':'调用方明确选择开展HLH条件评估'}

def check_snapshot(s):
 jsonschema.validate(s, SCHEMA, format_checker=jsonschema.FormatChecker())
 for key in ("observed_at","recorded_at"):_evidence_time(s[key])
 for group in ("labs","clinical"):
  for item in s[group].values():
   if isinstance(item,dict):
    for key in ("observed_at","recorded_at"):
     if key in item:_evidence_time(item[key])

def project_snapshot(s, tool):
 check_snapshot(s)
 p=copy.deepcopy(s)
 for group in ('labs','clinical'):
  p[group]={k:v for k,v in s[group].items() if k in FIELDS[tool][group]}
 return p

def unknown(v):
 return v is None or v=='unknown' or isinstance(v,dict) and v.get('state')=='unknown'

def missing(s, tool):
 return [f'{g}.{k}' for g in ('labs','clinical') for k in FIELDS[tool][g] if unknown(s[g].get(k))]

def facts_long(s, tool):
 rows=[]
 for group,names in [('labs',LAB_NAMES),('clinical',CLIN_NAMES)]:
  for k,label in names.items():
   item=s[group].get(k);d=item if isinstance(item,dict) else {}
   value=d.get('value',d.get('state',item))
   if 'lower' in d:value={'lower':d['lower'],'upper':d.get('upper'),'lower_open':d.get('lower_open',False),'upper_open':d.get('upper_open',False)}
   state='unknown' if unknown(item) else d.get('state','present' if group=='labs' else item)
   rows.append({'patient_id':s['patient_id'],'observed_at':d.get('observed_at',s['observed_at']),'recorded_at':d.get('recorded_at',s['recorded_at']),'tool':tool,'field':f'{group}.{k}','label':label,'value':value,'operator':d.get('operator','=' if group=='labs' else None),'unit':d.get('unit'),'evidence_state':state,'used_by_tool':k in FIELDS[tool][group],'source':d.get('source',s['source'])})
 return rows

def pending(s,tool,c):
 out=[]
 def add(id,fields,meaning,minimum=None):
  r={'node':id,'fields':fields,'information_needed':meaning,'action':'补已有报告或专业确认；本服务不自动要求新增检查'}
  if minimum is not None:r['minimum_additional_met']=minimum
  out.append(r)
 if tool in ('mas_2016','ms_2019') and c['eligibility']=='unknown':
  add('eligibility',[f'clinical.{k}' for k in ('fever','sjia') if unknown(s['clinical'].get(k))],'确认该次发热以及已知/临床疑似sJIA背景')
 if tool=='mas_2016':
  if c['clinical_review']=='unknown':add('clinical_review',['clinical.alternative_causes_excluded'],'确认化验异常的其他解释；不由家长勾选代替专业判断')
  if c['mathematical']=='unknown':
   if unknown(s['labs'].get('ferritin')):add('required_ferritin',['labs.ferritin'],'确认铁蛋白是否超过原门槛')
   fields=[f'labs.{k}' for k,v in zip(['platelets','ast','triglycerides','fibrinogen'],c['items']) if v=='unknown']
   if fields:add('two_of_four',fields,'核对未定附加项；无需机械要求每项都补齐',max(0,2-c['items'].count('met')))
 elif tool=='ms_2019' and not c['score_complete']:
  add('complete_ms_score',[x for x in missing(s,tool) if x not in ('clinical.fever','clinical.sjia')],'完整MS计分需七项可用；截尾/区间值需保留原形式，不能补成精确值')
  # Numeric interval evidence may be present but still prevent an exact score.
  out[-1]['non_exact_numeric_fields']=[f'labs.{k}' for k in FIELDS[tool]['labs'] if isinstance(s['labs'].get(k),dict) and (s['labs'][k].get('operator','=')!='=' or 'lower' in s['labs'][k])]
 elif tool=='hlh_2004' and c['mathematical']=='unknown':
  if c['molecular']=='unknown':add('molecular_route',['clinical.molecular_hlh'],'分子路径目前未知；不等于必须做基因检查')
  if c['five_of_eight']=='unknown':
   groups=[['clinical.fever'],['clinical.splenomegaly'],['labs.hemoglobin','labs.platelets','labs.anc','clinical.infant_under_4_weeks'],['labs.triglycerides','clinical.fasting','labs.fibrinogen'],['clinical.hemophagocytosis'],['clinical.nk_activity_low'],['labs.ferritin'],['labs.scd25']]
   add('five_of_eight',[{'criterion':i+1,'fields':f} for i,(v,f) in enumerate(zip(c['items'],groups)) if v=='unknown'],'只列未定条件组；两条路径为替代关系，不要求全部补齐',5-c['confirmed_count'])
 return out

def assess(tool,s):
 if tool not in TOOLS:raise ValueError('unknown tool')
 p=project_snapshot(s,tool);r=evaluate(p)
 if r['status']!='evaluated':return {'contract_version':CONTRACT_VERSION,'tool':tool,'use_state':'invalid_input','result':None,'issues':r,'calculation':None,'pending_requirements':[],'input_long':facts_long(s,tool),'result_wide':{'tool':tool,'use_state':'invalid_input','result':None}}
 c=r[tool];state='evaluated';reason='已完成该工具本次条件核对'
 if tool in ('mas_2016','ms_2019'):
  if c['eligibility']=='not_met':state='not_applicable';reason='不满足本工具的发热与sJIA适用前提'
  elif c['eligibility']=='unknown':state='needs_context';reason='适用前提未确认'
 if state=='evaluated':
  if tool=='mas_2016' and c['mathematical']!='not_met' and c['clinical_review']!='met':state='needs_clinical_review';reason='异常的其他解释未完成核查或不能视为已排除'
  elif tool=='ms_2019' and not c['score_complete']:state='needs_data';reason='无法形成原方法要求的完整MS分数'
  elif c['mathematical']=='unknown':state='needs_data';reason='现有资料尚不能确定条件结果'
 result=('criteria_met' if c['mathematical']=='met' else 'criteria_not_met') if state=='evaluated' else None
 wide={'patient_id':s['patient_id'],'observed_at':s['observed_at'],'recorded_at':s['recorded_at'],'tool':tool,'use_state':state,'result':result,'mathematical':c['mathematical'],'eligibility':c.get('eligibility'),'clinical_review':c.get('clinical_review'),'score':c.get('score'),'score_complete':c.get('score_complete'),'confirmed_count':c.get('confirmed_count'),'unknown_count':c.get('unknown_count'),'molecular_route':c.get('molecular'),'five_of_eight':c.get('five_of_eight'),'reason':reason}
 return {'temporal_validation':r['temporal_validation'],'temporal_note':r['temporal_note'],'contract_version':CONTRACT_VERSION,'rule_version':VERSION,'tool':tool,'purpose':PURPOSES[tool],'use_state':state,'result':result,'reason':reason,'calculation':c,'pending_requirements':pending(s,tool,c) if state not in ('not_applicable','evaluated') else [],'missing_input_fields':missing(s,tool),'input_long':facts_long(s,tool),'result_wide':wide,'clinical_scope':'工具条件结果不等于独立诊断或疾病排除；HLH调用表示调用方已选择HLH评估目的'}

def compare(tool,before,after):
 a=project_snapshot(before,tool);b=project_snapshot(after,tool);r=snapshot_relation(a,b)
 first,last=assess(tool,before),assess(tool,after)
 nodes=[n for n in r['node_changes'] if n['tool']==tool or n['node'].startswith('eligible.')]
 conclusion_changed=(first['use_state'],first['result'])!=(last['use_state'],last['result'])
 result_changed=conclusion_changed or first['calculation'].get('score')!=last['calculation'].get('score')
 return {'contract_version':CONTRACT_VERSION,'tool':tool,'before':first,'after':last,'change_kinds':r['kinds'],'changes_long':r['changes'],'result_changed':result_changed,'conclusion_changed':conclusion_changed,'internal_changed':bool(nodes),'node_changes':nodes}

def replay_tool(tool,events):
 projected=[]
 for e in events:
  item=copy.deepcopy(e);item['snapshot']=project_snapshot(e['snapshot'],tool);projected.append(item)
 r=replay(projected)
 versions=[]
 for v in r['versions']:
  changes=[]
  for x in v['transitions']:
   nodes=[n for n in x['node_changes'] if n['tool']==tool or n['node'].startswith('eligible.')]
   changes.append({'change_kinds':x['kinds'],'changes_long':x['changes'],'internal_changed':bool(nodes),'node_changes':nodes})
  versions.append({'event_id':v['event_id'],'known_at':v['known_at'],'history':[{'observation_id':x['observation_id'],**assess(tool,x['evidence_snapshot'])} for x in v['history']],'changes':changes})
 return {'contract_version':CONTRACT_VERSION,'tool':tool,'versions':versions,'current':versions[-1] if versions else None}

def dynamic(snapshots,config=None):
 projected=[]
 for s in snapshots:
  check_snapshot(s)
  if config is not None:require_orderable_snapshot(s)
  p=copy.deepcopy(s);p['clinical']={};projected.append(p)
 return {'contract_version':CONTRACT_VERSION,'scope':'仅执行调用方显式提供的研究配置；没有默认临床阈值','calculation':run_dynamic(projected,config)}

def contract():
 variables=[]
 for k in ['patient_id','observed_at','recorded_at','source','identity','notes']:
  variables.append({'direction':'input','field':k,'type':'string or source object' if k=='source' else 'string','unit':None,'used_by':list(TOOLS),'meaning':{'patient_id':'同一患者的标识','observed_at':'观察时间','recorded_at':'获知时间','source':'资料来源','identity':'资料身份','notes':'备注'}[k]})
 for group,names in [('labs',LAB_NAMES),('clinical',CLIN_NAMES)]:
  for k,label in names.items():
   variables.append({'direction':'input','field':f'{group}.{k}','type':'number / comparison / interval / unknown' if group=='labs' else 'present / absent / unknown / conflict','unit':RULES['labs'][k]['unit'] if group=='labs' else None,'used_by':[tool for tool in TOOLS if k in FIELDS[tool][group]],'meaning':label,'details':RULES.get('clinical_definitions',{}).get(k)})
 for key,meaning in {'use_state':'本次使用状态','result':'可采用的工具条件结果','reason':'状态原因','calculation':'原工具分项计算','pending_requirements':'待核实节点及替代条件组','missing_input_fields':'当前缺少的输入字段，不等于全部都必须补齐','input_long':'输入长表','result_wide':'结果宽表','rule_version':'医学规则编码版本','contract_version':'服务接口版本'}.items():
  variables.append({'direction':'output','field':key,'type':'object / array' if key in ('calculation','pending_requirements','missing_input_fields','input_long','result_wide') else 'string / null','unit':None,'used_by':list(TOOLS),'meaning':meaning})
 detail_fields={'mas_2016':['eligibility','mathematical','clinical_review','items[0:4]'],'ms_2019':['eligibility','mathematical','score_complete','score','bounds.lower','bounds.upper','bounds.lower_open','bounds.upper_open','bound_interpretation'],'hlh_2004':['mathematical','five_of_eight','molecular','items[0:8]','no_malignancy_background','confirmed_count','unknown_count']}
 for tool,fields in detail_fields.items():
  for key in fields:variables.append({'direction':'output','field':'calculation.'+key,'type':'number / string / boolean / array / null','unit':None,'used_by':[tool],'meaning':tool+'原计算结果；详见对应原表和输出样本'})
 for key,meaning in {'id':'配置名称','version':'配置版本','identity':'必须为research_only','max_interval_hours':'比较窗口小时数','metrics[].field':'要比较的化验字段','metrics[].direction':'rise或fall','metrics[].measure':'absolute或percent','metrics[].threshold':'显式测试门槛，非本服务医学建议','metrics[].weight':'该项权重','metrics[].group':'core或general','trigger.operator':'any、all或weighted_at_least','trigger.value':'加权聚合门槛','trigger.core_min_hits':'可选核心项命中数'}.items():
  variables.append({'direction':'config','field':'config.'+key,'type':'explicit research configuration','unit':None,'used_by':['evaluate_dynamic_rule'],'meaning':meaning})
 for key,meaning in {'event_id':'输入事件唯一ID','observation_id':'同次观察ID；补录纠正沿用','kind':'observation、completion或correction','recorded_at':'该事件的获知时间','snapshot':'该版本完整输入'}.items():
  variables.append({'direction':'event','field':'events[].'+key,'type':'event metadata','unit':None,'used_by':['replay_observations'],'meaning':meaning})
 return {'contract_version':CONTRACT_VERSION,'rule_version':VERSION,'purposes':PURPOSES,'variables':variables,'snapshot_schema':SCHEMA,'wide_columns':['patient_id','observed_at','recorded_at','tool','use_state','result','mathematical','eligibility','clinical_review','score','score_complete','confirmed_count','unknown_count','molecular_route','five_of_eight','reason'],'output_objects':{'calculation':'原计算器的选定工具完整分项结果','pending_requirements':'尚需核实的节点和条件组','input_long':'每个声明输入字段一行，包含使用标记和来源','result_wide':'一次工具调用一行','changes_long':'两次观察之间每个变化字段一行'},'use_states':{'evaluated':'已形成所选工具结果','not_applicable':'原工具适用前提明确不成立','needs_context':'适用前提尚未确认','needs_clinical_review':'需要核查异常的其他解释','needs_data':'所选工具尚不能形成完整结果','invalid_input':'格式/单位/冲突/时间等输入错误'},'code_boundary':'使用层状态是本项目接口设计；原医学规则与研究增加的数学上下界在calculation内分别保留'}

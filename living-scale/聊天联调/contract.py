"""Versioned chart envelope; values reference the integrated assessment contract."""
from pathlib import Path
import sys,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'统一服务'))
from unified_contract import ASSESSMENT_SCHEMA
A=ASSESSMENT_SCHEMA['properties']
SCHEMA={'$schema':'https://json-schema.org/draft/2020-12/schema','title':'Raft scale_chart_update','type':'object','required':['presentation_version','message_type','source_assessment','source_profile','facts','scales','trend','interpretation','warning','display_context','update','history','source_messages'],'properties':{
 'presentation_version':{'const':'chat-scale-card-1.0'},'message_type':{'const':'scale_chart_update'},
 'source_assessment':{'type':'object','required':['assessment_id','assessment_revision','patient_id','evaluation_at','knowledge_as_of','stale'],'properties':{'assessment_id':{'type':'string'},'assessment_revision':{'type':'integer'},'patient_id':{'type':'string'},'stale':{'type':'boolean'}}},
 'source_profile':{'type':'object'},'facts':{'type':'array','items':{'type':'object','required':['fact_ref','fact_revision','concept','source']}},
 'scales':A['scale'],'trend':{'type':'object'},'interpretation':A['interpretation'],'warning':A['warning'],
 'display_context':{'type':'object'},'execution':A['execution'],'update':{'type':'object'},'history':{'type':'array'},'source_messages':{'type':'array','items':{'type':'object','required':['message_id','channel_id']}}},'additionalProperties':False}
if __name__=='__main__':Path(__file__).with_name('scale-chart.schema.json').write_text(json.dumps(SCHEMA,ensure_ascii=False,indent=2))

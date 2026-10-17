"""scale-api-2.0: adapter over the preserved service; no HTTP server implied."""
from pathlib import Path
import sys, json, copy
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'程序'))
import service
from model import normalize, jnum, Q
import jsonschema
VERSION='scale-api-2.0'
STATEMENTS={
'mas_2016':('符合2016年sJIA相关MAS分类标准','本次未满足2016年sJIA相关MAS分类标准'),
'ms_2019':('MS评分达到原研究提示应考虑MAS的界值','MS评分未达到原研究提示考虑MAS的界值'),
'hlh_2004':('满足HLH-2004诊断标准','本次未满足HLH-2004诊断标准')}

def flatten(value,prefix='',out=None):
    out={} if out is None else out
    if isinstance(value,dict) and value:
        for key,item in value.items():flatten(item,f'{prefix}.{key}' if prefix else key,out)
    elif isinstance(value,list) and value:
        for i,item in enumerate(value):flatten(item,f'{prefix}.{i}',out)
    else:out[prefix]=value
    return out

def intermediate(tool,snapshot,calculation):
    result={'calculation':calculation,'source':service.RULES[tool]['source'], 'normalized_labs':{}}
    for key in service.FIELDS[tool]['labs']:
        item=snapshot['labs'].get(key)
        lo,hi,lop,hip,_=normalize(item,key)
        result['normalized_labs'][key]={'lower':jnum(Q(lo)), 'upper':None if hi is None else jnum(Q(hi)), 'lower_open':lop,'upper_open':hip,'unit':service.RULES['labs'][key]['unit'],'exact':hi is not None and str(lo)==str(hi) and not lop and not hip}
    if tool=='ms_2019':
        rows=[]
        for key,coefficient in service.RULES[tool]['coefficients'].items():
            value=None
            if key in result['normalized_labs']:
                norm=result['normalized_labs'][key]
                if norm['exact']:value=norm['lower']
                unit=norm['unit']
            else:
                state=snapshot['clinical'].get(key,'unknown')
                if isinstance(state,dict):state=state['state']
                value={'present':'1','absent':'0'}.get(state)
                unit='binary'
            rows.append({'field':key,'coefficient':coefficient,'normalized_value':value,'unit':unit,'contribution':None if value is None else jnum(Q(value)*Q(coefficient))})
        result['contributions']=rows
        result['cutoff']={'operator':service.RULES[tool]['comparison'],'value':service.RULES[tool]['cutoff']}
    elif tool=='mas_2016':
        result['required']=dict(zip(('field','operator','threshold'),service.RULES[tool]['required']))
        result['additional_conditions']=[{'field':spec[0],'operator':spec[1],'threshold':spec[2],'state':state} for spec,state in zip(service.RULES[tool]['additional'],calculation['items'])]
        result['confirmed_count']=calculation['items'].count('met')
        result['unknown_count']=calculation['items'].count('unknown')
        result['required_additional_count']=service.RULES[tool]['at_least']
    else:
        names=['fever','splenomegaly','cytopenias_two_lineages','triglycerides_or_fibrinogen','hemophagocytosis','nk_activity_low','ferritin','scd25']
        result['criteria']=[{'criterion':key,'state':state} for key,state in zip(names,calculation['items'])]
        result['routes']={'molecular':calculation['molecular'],'five_of_eight':calculation['five_of_eight']}
    return result

def error_response(request,exc):
    response={'contract_version':VERSION,'tool':request.get('tool') if isinstance(request,dict) and request.get('tool') in service.TOOLS else None,'input':copy.deepcopy(request) if isinstance(request,dict) else {'raw':request},'intermediate':{},'assessment':{'status':'invalid_input','conclusion':None,'statement':None,'reason':'输入错误；未作医学判断','pending_requirements':[]},'execution':{'status':'error','error':{'type':type(exc).__name__,'message':str(exc)},'rule_version':service.VERSION}}
    response['wide_row']=flatten({k:response[k] for k in ('input','intermediate','assessment','execution')})
    return response

def evaluate(request):
    try:
        jsonschema.validate(request,REQUEST_SCHEMA,format_checker=jsonschema.FormatChecker())
        tool=request['tool'];snapshot=request['snapshot'];raw=service.assess(tool,snapshot)
        if raw['use_state']=='invalid_input':raise ValueError(json.dumps(raw['issues'],ensure_ascii=False))
        c=raw['calculation'];conclusion=raw['result']
        response={'contract_version':VERSION,'tool':tool,'input':copy.deepcopy(request),'intermediate':intermediate(tool,snapshot,c),'assessment':{'status':raw['use_state'],'conclusion':conclusion,'statement':STATEMENTS[tool][0 if conclusion=='criteria_met' else 1] if conclusion is not None else None,'reason':raw['reason'],'pending_requirements':raw['pending_requirements']},'execution':{'status':'ok','error':None,'rule_version':raw['rule_version'],'temporal_validation':raw['temporal_validation'],'temporal_note':raw['temporal_note']}}
        if tool=='hlh_2004' and conclusion=='criteria_met':
            routes=[]
            if c['molecular']=='met':routes.append('分子诊断路径')
            if c['five_of_eight']=='met':routes.append('八项中至少五项路径')
            response['assessment']['statement']+='（'+'及'.join(routes)+'）'
        response['wide_row']=flatten({k:response[k] for k in ('input','intermediate','assessment','execution')})
        jsonschema.validate(response,RESPONSE_SCHEMA)
        return response
    except Exception as exc:return error_response(request,exc)

SNAPSHOT=copy.deepcopy(service.SCHEMA)
DEFS=SNAPSHOT.pop('$defs');SNAPSHOT.pop('$schema',None);DEFS['snapshot']=SNAPSHOT
REQUEST_SCHEMA={'$schema':'https://json-schema.org/draft/2020-12/schema','type':'object','properties':{'tool':{'enum':list(service.TOOLS)},'snapshot':{'$ref':'#/$defs/snapshot'}},'required':['tool','snapshot'],'additionalProperties':False,'$defs':DEFS}
RESPONSE_SCHEMA={'$schema':'https://json-schema.org/draft/2020-12/schema','type':'object','required':['contract_version','tool','input','intermediate','assessment','execution','wide_row'],'additionalProperties':False,'properties':{
'contract_version':{'const':VERSION},'tool':{'enum':[*service.TOOLS,None]},'input':{'type':'object'},'intermediate':{'type':'object','properties':{'calculation':{'type':'object'},'source':{'type':'string'},'normalized_labs':{'type':'object'},'contributions':{'type':'array','items':{'type':'object','required':['field','coefficient','normalized_value','unit','contribution'],'properties':{'field':{'type':'string'},'coefficient':{'type':'string'},'normalized_value':{'type':['string','null']},'unit':{'type':'string'},'contribution':{'type':['string','null']}}}}},'additionalProperties':True},
'assessment':{'type':'object','required':['status','conclusion','statement','reason','pending_requirements'],'properties':{'status':{'enum':['evaluated','not_applicable','needs_context','needs_clinical_review','needs_data','invalid_input']},'conclusion':{'enum':['criteria_met','criteria_not_met',None]},'statement':{'type':['string','null']},'reason':{'type':'string'},'pending_requirements':{'type':'array'}},'additionalProperties':False},
'execution':{'type':'object','required':['status','error','rule_version'],'properties':{'status':{'enum':['ok','error']},'error':{'type':['object','null']},'rule_version':{'type':'string'}},'additionalProperties':False},'wide_row':{'type':'object','description':'Recursive lossless dot-path projection of all four groups; arrays use numeric indexes; empty containers retained.'}}}

RESPONSE_SCHEMA['properties']['execution']['properties'].update({'temporal_validation':{'enum':['checked','not_asserted']},'temporal_note':{'type':'string'}})

# Successful responses declare the per-tool calculation payload, not only an opaque object.
RESPONSE_SCHEMA['allOf']=[]
for _tool,_required in {
    'ms_2019':['calculation','source','normalized_labs','contributions','cutoff'],
    'mas_2016':['calculation','source','normalized_labs','required','additional_conditions','confirmed_count','unknown_count','required_additional_count'],
    'hlh_2004':['calculation','source','normalized_labs','criteria','routes']}.items():
    RESPONSE_SCHEMA['allOf'].append({'if':{'properties':{'tool':{'const':_tool},'execution':{'properties':{'status':{'const':'ok'}}}}},'then':{'properties':{'intermediate':{'required':_required}}}})
RESPONSE_SCHEMA['properties']['intermediate']['properties'].update({
    'additional_conditions':{'type':'array','minItems':4,'maxItems':4,'items':{'type':'object','required':['field','operator','threshold','state']}},
    'confirmed_count':{'type':'integer','minimum':0,'maximum':4},
    'unknown_count':{'type':'integer','minimum':0,'maximum':4},
    'required_additional_count':{'const':2},
    'criteria':{'type':'array','minItems':8,'maxItems':8,'items':{'type':'object','required':['criterion','state']}},
    'routes':{'type':'object','required':['molecular','five_of_eight'],'properties':{'molecular':{'enum':['met','not_met','unknown']},'five_of_eight':{'enum':['met','not_met','unknown']}}}})

# Support operations deliberately preserve the existing service envelope.
SUPPORT_CONTRACT={'contract_version':VERSION,'response_version':service.CONTRACT_VERSION,'operations':{'compare':{'required':['tool','before','after'],'result':'before, after, change_kinds, changes_long, result_changed, conclusion_changed, internal_changed, node_changes'},'replay':{'required':['tool','events'],'result':'versions, current; each version preserves known_at, observation history and internal node changes'},'dynamic':{'required':['snapshots'],'optional':['config'],'result':'scope, calculation; explicit research configuration, no default clinical thresholds'}}}
def support(operation,arguments):
    if operation=='compare':return service.compare(**arguments)
    if operation=='replay':return service.replay_tool(**arguments)
    if operation=='dynamic':return service.dynamic(**arguments)
    raise ValueError('unknown support operation')

def export_contracts():
    for filename,data in [('request.schema.json',REQUEST_SCHEMA),('response.schema.json',RESPONSE_SCHEMA),('support-contract.json',SUPPORT_CONTRACT)]:
        (HERE/filename).write_text(json.dumps(data,ensure_ascii=False,indent=2))
    # OpenAPI references resolve from the document root, not this nested Schema.
    # Keep standalone JSON Schema and MCP schemas unchanged.
    openapi_request=copy.deepcopy(REQUEST_SCHEMA)
    openapi_defs=openapi_request.pop('$defs')
    def openapi_refs(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key=='$ref' and isinstance(item,str) and item.startswith('#/$defs/'):
                    value[key]='#/components/schemas/Snapshot_'+item[len('#/$defs/'):]
                else:openapi_refs(item)
        elif isinstance(value,list):
            for item in value:openapi_refs(item)
    openapi_refs(openapi_request)
    openapi_refs(openapi_defs)
    openapi_components={'Snapshot_'+key:value for key,value in openapi_defs.items()}
    doc={'openapi':'3.1.0','info':{'title':'Living Scale API design (not deployed HTTP)','version':VERSION},'paths':{'/evaluate':{'post':{'operationId':'evaluate','requestBody':{'required':True,'content':{'application/json':{'schema':openapi_request}}},'responses':{'200':{'description':'Medical unknown is assessment.conclusion=null; execution may still be ok. Structured validation error uses execution.status=error.','content':{'application/json':{'schema':RESPONSE_SCHEMA}}}}}}}}
    doc['components']={'schemas':openapi_components}
    (HERE/'openapi.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2))
    fields={'encoding':'dot paths; numeric array indexes; empty collections retained; values are JSON scalar/empty collection','groups':['input','intermediate','assessment','execution'],'roundtrip':'all leaves and empty containers from these groups are retained; one evaluate invocation per row','input':'Full original request including source, dates, evidence states, values and units','intermediate':'Original calculation plus normalized measurements, contributions or criteria','assessment':'Chinese statement, nullable medical conclusion, usability and pending nodes','execution':'Technical status, structured errors and rule version'}
    (HERE/'wide-fields.json').write_text(json.dumps(fields,ensure_ascii=False,indent=2))
if __name__=='__main__':export_contracts()

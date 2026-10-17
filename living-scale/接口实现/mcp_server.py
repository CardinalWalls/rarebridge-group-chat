"""stdio MCP v2; request/output schemas advertised and enforced."""
import asyncio, copy, json
import api
import sys
sys.path.insert(0, str(api.HERE.parent / "统一服务"))
from integrated_service import IntegratedService
from unified_contract import TOOL_SCHEMAS as INTEGRATED_TOOLS
_integrated = None
def integrated_service():
    global _integrated
    if _integrated is None: _integrated = IntegratedService()
    return _integrated
import living
import jsonschema
from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
import mcp.types as types
server=Server('living-scale-api-v2')
TOOL_NAMES={f'evaluate_{tool}':tool for tool in api.service.TOOLS}
INPUT={'type':'object','properties':{'snapshot':{'$ref':'#/$defs/snapshot'}},'required':['snapshot'],'additionalProperties':False,'$defs':api.DEFS}
TOOLS=[types.Tool(name=name,description=api.service.PURPOSES[tool],inputSchema=copy.deepcopy(INPUT),outputSchema=copy.deepcopy(api.RESPONSE_SCHEMA)) for name,tool in TOOL_NAMES.items()]
import importlib.util
spec=importlib.util.spec_from_file_location('legacy_mcp',api.HERE.parent/'程序/mcp_server.py')
legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
SUPPORT_NAMES={'compare_observations':'compare','replay_observations':'replay','evaluate_dynamic_rule':'dynamic'}
SUPPORT_OUTPUT={
'compare':{'type':'object','required':['contract_version','tool','before','after','change_kinds','changes_long','result_changed'],'properties':{'contract_version':{'const':api.service.CONTRACT_VERSION},'tool':{'enum':list(api.service.TOOLS)},'before':{'type':'object'},'after':{'type':'object'},'change_kinds':{'type':'array'},'changes_long':{'type':'array'},'result_changed':{'type':'boolean'}}},
'replay':{'type':'object','required':['contract_version','tool','versions','current'],'properties':{'contract_version':{'const':api.service.CONTRACT_VERSION},'tool':{'enum':list(api.service.TOOLS)},'versions':{'type':'array'},'current':{'type':['object','null']}}},
'dynamic':{'type':'object','required':['contract_version','scope','calculation'],'properties':{'contract_version':{'const':api.service.CONTRACT_VERSION},'scope':{'type':'string'},'calculation':{'type':'object'}}}}
SUPPORT_OUTPUT['compare']['required'] += ['conclusion_changed','internal_changed','node_changes']
SUPPORT_OUTPUT['compare']['properties'].update({'conclusion_changed':{'type':'boolean'},'internal_changed':{'type':'boolean'},'node_changes':{'type':'array','items':{'type':'object','required':['tool','node','before','after']}}})
for t in legacy.TOOL_LIST:
    if t.name in SUPPORT_NAMES:
        TOOLS.append(types.Tool(name=t.name,description=t.description+' 保留scale-service-1.0响应。',inputSchema=t.inputSchema,outputSchema={'anyOf':[SUPPORT_OUTPUT[SUPPORT_NAMES[t.name]],api.RESPONSE_SCHEMA]}))
TOOLS.append(types.Tool(name='evaluate_living_scale',description='按选定原表、连续观察和具名研究配置运行活量表服务；返回逐次原表结果、动态规则与两类提示。不包含用户协商或启用约定。',inputSchema=copy.deepcopy(living.REQUEST_SCHEMA),outputSchema=copy.deepcopy(living.RESPONSE_SCHEMA)))
for name, schema in INTEGRATED_TOOLS.items():
    TOOLS.append(types.Tool(name=name, description="统一病程与量表服务：" + name + "。完整字段字典由export_assessment_bundle提供。", inputSchema=copy.deepcopy(schema), outputSchema={"type":"object","required":["ok"],"properties":{"ok":{"type":"boolean"},"error":{"type":"object"}},"additionalProperties":True}))
def export_mcp():
    (api.HERE/'mcp-tools.json').write_text(json.dumps([t.model_dump(by_alias=True,exclude_none=True) for t in TOOLS],ensure_ascii=False,indent=2))
@server.list_tools()
async def list_tools():return TOOLS
@server.call_tool(validate_input=False)
async def call_tool(name,arguments):
    if name in INTEGRATED_TOOLS:
        result=integrated_service().dispatch(name,arguments)
        return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(result,ensure_ascii=False))],structuredContent=result,isError=not result['ok'])
    if name=='evaluate_living_scale':
        result=living.run(arguments)
        jsonschema.validate(result,living.RESPONSE_SCHEMA)
        return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(result,ensure_ascii=False))],structuredContent=result,isError=result['execution']['status']=='error')
    if name in SUPPORT_NAMES:
        try:
            declaration=next(t for t in TOOLS if t.name==name)
            jsonschema.validate(arguments,declaration.inputSchema,format_checker=jsonschema.FormatChecker())
            result=api.support(SUPPORT_NAMES[name],arguments)
            jsonschema.validate(result,declaration.outputSchema)
            return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(result,ensure_ascii=False))],structuredContent=result)
        except Exception as exc:result=api.error_response(arguments,exc)
    elif name not in TOOL_NAMES:
        result=api.error_response(arguments,ValueError('unknown tool'))
    else:
        try:
            jsonschema.validate(arguments,INPUT,format_checker=jsonschema.FormatChecker())
            result=api.evaluate({'tool':TOOL_NAMES[name],**arguments})
        except Exception as exc:result=api.error_response({'tool':TOOL_NAMES[name],**arguments},exc)
    jsonschema.validate(result,api.RESPONSE_SCHEMA)
    return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(result,ensure_ascii=False))],structuredContent=result,isError=result['execution']['status']=='error')
async def main():
    async with stdio_server() as (read,write):
        await server.run(read,write,InitializationOptions(server_name='living-scale-api-v2',server_version=api.VERSION,capabilities=server.get_capabilities(notification_options=NotificationOptions(),experimental_capabilities={})))
if __name__=='__main__':asyncio.run(main())

"""Local stdio MCP server. No messages or patient data sent over the network."""
import asyncio
import copy
import json
from mcp.server.lowlevel import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
import mcp.types as types
import service

server=Server('living-scale-research')
defs=copy.deepcopy(service.SCHEMA['$defs'])
snapshot=copy.deepcopy(service.SCHEMA);snapshot.pop('$defs');snapshot.pop('$schema',None);defs['snapshot']=snapshot

def schema(properties,required):
 return {'type':'object','properties':properties,'required':required,'additionalProperties':False,'$defs':defs}
snap={'$ref':'#/$defs/snapshot'}
tool_choice={'type':'string','enum':list(service.TOOLS)}
DESCRIPTIONS={
 'evaluate_mas_2016':'核对2016分类条件；分别返回适用性、临床核查、原数值结果和待补信息。',
 'evaluate_ms_2019':'计算MS评分；未知不填零，完整分数与数学上下界分别返回。',
 'evaluate_hlh_2004':'调用方选择HLH评估；保留分子路径、五项路径及八类分组。'}
TOOL_LIST=[types.Tool(name='describe_contract',description='返回全部输入变量、单位、用途、输出列和JSON Schema。',inputSchema={'type':'object','properties':{},'additionalProperties':False})]
for name,desc in DESCRIPTIONS.items():TOOL_LIST.append(types.Tool(name=name,description=desc,inputSchema=schema({'snapshot':snap},['snapshot'])))
TOOL_LIST.extend([
 types.Tool(name='compare_observations',description='比较同一患者两次资料，返回长表变化和所选工具前后结果。',inputSchema=schema({'tool':tool_choice,'before':snap,'after':snap},['tool','before','after'])),
 types.Tool(name='replay_observations',description='按获知时间重放新观察、补录及纠正，保留每个版本。',inputSchema=schema({'tool':tool_choice,'events':{'type':'array','items':{'type':'object','properties':{'event_id':{'type':'string'},'observation_id':{'type':'string'},'recorded_at':{'type':'string','format':'date-time'},'kind':{'enum':['observation','completion','correction']},'snapshot':snap},'required':['event_id','observation_id','recorded_at','kind','snapshot'],'additionalProperties':False}}},['tool','events'])),
 types.Tool(name='evaluate_dynamic_rule',description='只执行显式研究配置；无配置返回未配置，不返回未报警。',inputSchema=schema({'snapshots':{'type':'array','items':snap},'config':{'type':['object','null']}},['snapshots']))])

@server.list_tools()
async def list_tools():return TOOL_LIST

@server.call_tool(validate_input=False)
async def call_tool(name,arguments):
 import jsonschema
 try:
  declared=next(t for t in TOOL_LIST if t.name==name)
  jsonschema.validate(arguments,declared.inputSchema,format_checker=jsonschema.FormatChecker())
  if name=='describe_contract':result=service.contract()
  elif name in DESCRIPTIONS:
   selected={'evaluate_mas_2016':'mas_2016','evaluate_ms_2019':'ms_2019','evaluate_hlh_2004':'hlh_2004'}[name];result=service.assess(selected,arguments['snapshot'])
  elif name=='compare_observations':result=service.compare(arguments['tool'],arguments['before'],arguments['after'])
  elif name=='replay_observations':result=service.replay_tool(arguments['tool'],arguments['events'])
  else:result=service.dynamic(arguments['snapshots'],arguments.get('config'))
  return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(result,ensure_ascii=False))],structuredContent=result)
 except Exception as exc:
  result={'error':type(exc).__name__,'message':str(exc)}
  return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps(result,ensure_ascii=False))],isError=True)

async def main():
 async with stdio_server() as (read,write):
  await server.run(read,write,InitializationOptions(server_name='living-scale-research',server_version=service.CONTRACT_VERSION,capabilities=server.get_capabilities(notification_options=NotificationOptions(),experimental_capabilities={})))

if __name__=='__main__':asyncio.run(main())

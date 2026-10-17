"""Capability-scoped MCP facade installed only in the chosen native Raft agent."""
import argparse,asyncio,json,sys
from pathlib import Path
from mcp.server.lowlevel import Server,NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
import mcp.types as types
from processor import Processor
p=argparse.ArgumentParser();p.add_argument('--binding',required=True);args=p.parse_args()
binding=json.loads(Path(args.binding).read_text());processor=Processor(binding);server=Server('raft-living-scale')
@server.list_tools()
async def tools():
 return [types.Tool(name='process_scale_message',description='处理本角色授权频道内一条人类消息：按完整量表Skill提取事实，真实调用统一量表MCP，将确定性图表直接作为本角色消息发回原频道。仅传入消息UUID，不传臆造的病情。工具已发消息，不要重复回复。',inputSchema={'type':'object','properties':{'message_id':{'type':'string'}},'required':['message_id'],'additionalProperties':False}),types.Tool(name='read_scale_context',description='读取本角色绑定患者的当前事实及方案，不能访问其他患者。',inputSchema={'type':'object','properties':{},'additionalProperties':False})]
@server.call_tool()
async def call(name,arguments):
 try:
  result=await asyncio.to_thread(processor.process,arguments['message_id']) if name=='process_scale_message' else await asyncio.to_thread(processor.context) if name=='read_scale_context' else {'error':'unknown tool'}
  return [types.TextContent(type='text',text=json.dumps(result,ensure_ascii=False))]
 except Exception as e:return types.CallToolResult(content=[types.TextContent(type='text',text=json.dumps({'status':'failed','error':str(e)},ensure_ascii=False))],isError=True)
async def main():
 async with stdio_server() as (r,w):await server.run(r,w,InitializationOptions(server_name='raft-living-scale',server_version='1.0',capabilities=server.get_capabilities(notification_options=NotificationOptions(),experimental_capabilities={})))
if __name__=='__main__':asyncio.run(main())

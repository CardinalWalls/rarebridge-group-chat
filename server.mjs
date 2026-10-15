import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID,randomBytes} from 'node:crypto';
import {validateProfile,installWorkspace,jsonFile,saveJson} from './lib.mjs';
const ROOT=path.dirname(fileURLToPath(import.meta.url));
const local=path.join(ROOT,'.local'),config=await jsonFile(path.join(local,'config.json'),{});
const runtime=process.env.RAFT_RUNTIME_DIR||config.runtime;
if(!runtime)throw new Error('先运行 npm run bootstrap -- --runtime <独立Raft目录>');
const environment=process.env.RAFT_ENV||config.environment||'rarebridge-chat';
if(!/^[a-z0-9-]+$/.test(environment))throw new Error('非法环境名');
const seed=await jsonFile(path.join(runtime,`.dev-env-${environment}.json`),null);
if(!seed)throw new Error('Raft环境未启动，请先运行bootstrap');
const api=process.env.RAFT_API_URL||config.api||'http://127.0.0.1:13019';
if(!['127.0.0.1','localhost','[::1]'].includes(new URL(api).hostname))throw new Error('本地工作室仅连接回环地址');
const web=config.web||'http://localhost:15191';
const stateFile=path.join(local,'state.json');
const state=await jsonFile(stateFile,{profiles:await jsonFile(path.join(ROOT,'profiles/defaults.json'),[]),agents:{},channelId:null});
const csrf=randomBytes(32).toString('hex');
let accessToken='',loginAt=0,busy=false;
async function request(endpoint,method='GET',body,retry=true){
  if(!accessToken||Date.now()-loginAt>8*60*1000){
    const r=await fetch(api+'/api/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({email:seed.user.email,password:seed.user.password}),signal:AbortSignal.timeout(15000)});
    if(!r.ok)throw new Error('Raft登录失败：'+r.status);accessToken=(await r.json()).accessToken;loginAt=Date.now();
  }
  const r=await fetch(api+'/api/'+endpoint,{method,headers:{authorization:'Bearer '+accessToken,'x-server-id':seed.server.id,'content-type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(30000)});
  if(r.status===401&&retry){accessToken='';return request(endpoint,method,body,false);}
  const data=await r.json();if(!r.ok)throw new Error(`Raft ${r.status}: ${data.error||'请求失败'}`);return data;
}
async function apply(id){
  const p=state.profiles.find(x=>x.id===id);if(!p)throw new Error('未知角色');validateProfile(p);
  let agentId=state.agents[id];
  if(!agentId){
    const existing=await request('agents');
    if(existing.some(a=>a.name===p.name))throw new Error('同名Agent已存在但不由本工作室管理；请更改角色名称');
    const a=await request('agents','POST',{name:p.name,description:p.description,runtime:'grok',reasoningEffort:'low',machineId:seed.machine.id,...(config.grokHome?{envVars:{...config.grokEnv,GROK_HOME:config.grokHome}}:{})});
    agentId=a.id;state.agents[id]=agentId;await saveJson(stateFile,state);
  }
  await request(`agents/${agentId}/stop`,'POST',{});
  const manifest=await installWorkspace(path.join(runtime,'.slockdev',environment,'home','agents',agentId),p);
  await request(`agents/${agentId}`,'PATCH',{displayName:p.label,description:p.description,...(config.grokHome?{envVars:{...config.grokEnv,GROK_HOME:config.grokHome}}:{})});
  if(!state.channelId){
    const ch=await request('channels','POST',{name:'rarebridge-lounge',description:'RareBridge 群聊实验室 · 人与Grok角色共同讨论',visibility:'private',agentIds:Object.values(state.agents),userIds:[seed.accounts.member.id]});
    state.channelId=ch.id;await saveJson(stateFile,state);
  }else{
    const members=await request(`channels/${state.channelId}/members`);
    const encoded=JSON.stringify(members);
    if(!encoded.includes(agentId))await request(`channels/${state.channelId}/members`,'POST',{agentId});
  }
  // A new runtime session ensures edited instructions are not hidden behind old context.
  await request(`agents/${agentId}/reset`,'POST',{mode:'session'});
  state.lastApplied={id,agentId,manifest,at:new Date().toISOString()};await saveJson(stateFile,state);
  return state.lastApplied;
}
const port=Number(process.env.PORT||4318);
const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');
  const host=req.headers.host||'';
  if(![`127.0.0.1:${port}`,`localhost:${port}`,`[::1]:${port}`].includes(host)){res.writeHead(403).end();return;}
  const send=(status,data)=>{res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store'});res.end(JSON.stringify(data));};
  try{
    if(req.method==='GET'&&url.pathname==='/'){res.writeHead(200,{'content-type':'text/html; charset=utf-8','cache-control':'no-store','content-security-policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"});res.end((await fs.readFile(path.join(ROOT,'public/index.html'),'utf8')).replace('__TOKEN__',csrf));return;}
    if(req.method==='GET'&&url.pathname==='/api/state'){
      const agents=await request('agents');send(200,{profiles:state.profiles,agents:state.agents,channelId:state.channelId,lastApplied:state.lastApplied,web,runtimeStatuses:agents.filter(a=>Object.values(state.agents).includes(a.id)).map(a=>({id:a.id,name:a.name,status:a.status,activity:a.activity,error:a.lastRuntimeError}))});return;
    }
    if(req.method==='GET'&&url.pathname==='/api/messages'){
      send(200,state.channelId?await request(`messages/channel/${state.channelId}?limit=60`):[]);return;
    }
    if(req.method!=='POST'||req.headers['x-workspace-token']!==csrf){send(403,{error:'无效的工作室请求'});return;}
    if(busy){send(409,{error:'正在保存或应用配置，请稍候'});return;}
    let raw='';for await(const chunk of req){raw+=chunk;if(Buffer.byteLength(raw)>80000)throw new Error('请求过大');}
    const body=JSON.parse(raw||'{}');busy=true;
    try{
      if(url.pathname==='/api/save'){
        const p=validateProfile(body);const index=state.profiles.findIndex(x=>x.id===p.id);if(index<0)state.profiles.push(p);else {if(state.agents[p.id]&&state.profiles[index].name!==p.name)throw new Error('已创建角色不能更改账号名称');state.profiles[index]=p;}await saveJson(stateFile,state);send(200,{ok:true});
      }else if(url.pathname==='/api/apply'){send(200,await apply(body.id));}
      else if(url.pathname==='/api/message'){
        if(typeof body.content!=='string'||!body.content.trim()||body.content.length>8000)throw new Error('请填写消息（不超过8000字符）');
        if(!state.channelId)throw new Error('先应用一个角色，建立群聊');
        const mentions=[];let content=body.content;
        if(body.roleId){const agentId=state.agents[body.roleId];if(!agentId)throw new Error('该角色尚未应用');const p=state.profiles.find(x=>x.id===body.roleId);mentions.push({type:'agent',id:agentId,name:p.name});content=`@${p.name} ${content}`;}
        send(200,await request('messages','POST',{channelId:state.channelId,content,mentions,randomId:randomUUID()}));
      }else send(404,{error:'未找到接口'});
    }finally{busy=false;}
  }catch(e){send(400,{error:e.message});}
});
server.listen(port,'127.0.0.1',()=>console.log(`RareBridge工作室 http://127.0.0.1:${port} · Raft ${web}`));

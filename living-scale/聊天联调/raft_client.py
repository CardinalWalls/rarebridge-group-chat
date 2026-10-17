"""Local authenticated Raft transport. Credentials never enter prompts or artifacts."""
import json,urllib.request,urllib.error,urllib.parse
from pathlib import Path
class SafeAttachmentRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):
  target=urllib.parse.urlparse(newurl)
  if target.hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('nonlocal attachment redirect refused')
  new=super().redirect_request(req,fp,code,msg,headers,newurl)
  if new and urllib.parse.urlparse(req.full_url).netloc!=target.netloc:
   new.remove_header('Authorization');new.remove_header('X-server-id')
  return new
class RaftClient:
 def __init__(self,binding):self.b=binding;self.token=None
 def request(self,path,method='GET',body=None,raw=False):
  if not self.token:
   seed=json.loads(Path(self.b['seed_file']).read_text())
   req=urllib.request.Request(self.b['api']+'/api/auth/login',data=json.dumps({'email':seed['user']['email'],'password':seed['user']['password']}).encode(),headers={'Content-Type':'application/json'})
   with urllib.request.urlopen(req,timeout=30) as r:self.token=json.load(r)['accessToken']
  headers={'Authorization':'Bearer '+self.token,'x-server-id':self.b['server_id'],'Content-Type':'application/json'}
  req=urllib.request.Request(self.b['api']+'/api/'+path,data=None if body is None else json.dumps(body).encode(),headers=headers,method=method)
  try:
   with urllib.request.build_opener(SafeAttachmentRedirect()).open(req,timeout=45) as r:return r.read() if raw else json.load(r)
  except urllib.error.HTTPError as e:
   error=e.read().decode()[:1200]
   raise RuntimeError('Raft HTTP '+str(e.code)+' for '+path.split('?')[0]+': '+error) from None
 def authorize(self):
  if self.b.get('agent_id'):
   membership=self.request('channels/'+self.b['channel_id']+'/members')
   if not any(a.get('id')==self.b['agent_id'] for a in membership.get('agents',[])):raise PermissionError('agent is not a member of bound channel')
 def messages(self):
  self.authorize()
  result=self.request('messages/channel/'+self.b['channel_id']+'?limit=100')
  return result if isinstance(result,list) else result.get('messages',result.get('data',[]))
 def source_message(self,message_id):
  # The configured channel, not any caller-provided channel, controls lookup.
  messages=self.messages();matches=[m for m in messages if m['id']==message_id or m['id'].startswith(message_id)]
  if len(matches)!=1:raise ValueError('source message not uniquely present in authorized channel')
  m=matches[0]
  if m.get('senderType') not in ('user','human'):raise ValueError('only human source messages can create facts')
  return m

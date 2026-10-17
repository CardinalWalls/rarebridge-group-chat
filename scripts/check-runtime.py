"""Read-only runtime preflight; never prints credentials or starts models."""
import os,shutil,subprocess,sys,json
from pathlib import Path
checks={}
for name in ['node','pnpm','docker','tmux','grok']:
 checks[name]=bool(shutil.which(name))
checks['grok_home_explicit']=bool(os.environ.get('GROK_HOME'))
checks['grok_home_directory']=Path(os.environ.get('GROK_HOME',str(Path.home()/'.grok'))).is_dir()
checks['raft_runtime_directory']=Path(os.environ.get('RAFT_RUNTIME_DIR','/nonexistent')).is_dir()
for name in ['mcp','jsonschema','z3']:
 try:__import__(name);checks['python_'+name]=True
 except ImportError:checks['python_'+name]=False
if checks['grok']:
 r=subprocess.run(['grok','--help'],capture_output=True,text=True,timeout=10)
 checks['grok_required_flags']=all(flag in r.stdout for flag in ['--prompt-file','--json-schema','--output-format','--no-subagents','--no-plan'])
print(json.dumps(checks,indent=2))
print('This verifies installation/configuration only. Run GROK_HOME=/your/private/path grok login on the deployment host, then verify a real model response. Never commit that directory.')
sys.exit(0 if all(v for k,v in checks.items() if k!='grok_home_explicit') else 1)

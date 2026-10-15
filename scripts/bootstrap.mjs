import {spawnSync} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {fileURLToPath} from 'node:url';
import {saveJson,jsonFile} from '../lib.mjs';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const args=process.argv.slice(2),index=args.indexOf('--runtime');
const runtime=path.resolve(index>=0?args[index+1]:path.join(os.homedir(),'.local/share/rarebridge-raft/runtime'));
const environment='rarebridge-chat';
const revision='05f7d8fd77d2535f993d5d90b85118438bc18216';
function run(cmd,args,options={}){const r=spawnSync(cmd,args,{cwd:runtime,stdio:'inherit',...options});if(r.status!==0)throw new Error(`${cmd}执行失败`);}
try{await fs.access(path.join(runtime,'.git'));}catch{
 await fs.mkdir(path.dirname(runtime),{recursive:true});const r=spawnSync('git',['clone','https://github.com/botiverse/raft-source.git',runtime],{stdio:'inherit'});if(r.status!==0)throw new Error('Raft下载失败');run('git',['checkout','--detach',revision]);
}
const current=spawnSync('git',['rev-parse','HEAD'],{cwd:runtime,encoding:'utf8'});if(current.stdout.trim()!==revision)throw new Error('仅支持锁定的Raft版本；请使用独立检出，不覆盖已有工作');
const clean=spawnSync('git',['diff','--quiet','HEAD','--'],{cwd:runtime});if(clean.status!==0)throw new Error('Raft检出有已跟踪文件修改，请使用独立干净检出；本程序不覆盖它们');
run('pnpm',['install','--frozen-lockfile']);
const env={...process.env,PATH:path.dirname(process.execPath)+path.delimiter+process.env.PATH,SLOCKDEV_TUNNEL:'0'};
run(process.execPath,['--import','tsx','scripts/dev/raftdev.ts','start',environment,'--description','RareBridge 群聊本地开发'],{env});
// This is a private development seed, never a hosted-service entitlement change.
run('docker',['exec',`slock-dev-${environment}-pg`,'psql','-U','postgres','-d','slock','-v','ON_ERROR_STOP=1','-c',"UPDATE servers SET plan='partner' WHERE slug='dev'; INSERT INTO feature_flags (key,description,enabled,kill_switch,randomization_unit,default_enabled,salt) VALUES ('grok_runtime_v0','Local RareBridge Grok runtime',true,false,'server',true,'grok_runtime_v0') ON CONFLICT (key) DO UPDATE SET enabled=true,default_enabled=true,kill_switch=false;"]);
await saveJson(path.join(root,'.local/config.json'),{...await jsonFile(path.join(root,'.local/config.json'),{}),runtime,environment,api:'http://127.0.0.1:13019',web:'http://localhost:15191'});
console.log('已准备本地开发环境。请确认本机 grok login 已完成，然后 npm start。默认种子登录仅用于本地，不可作为公网部署配置。');

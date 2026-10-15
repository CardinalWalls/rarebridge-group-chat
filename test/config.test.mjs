import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {installWorkspace,validateProfile,workspaceFiles} from '../lib.mjs';
const p={id:'host',name:'community-host',label:'小桥',description:'讨论主持',prompt:'只回应被点名的讨论',skill:'## 目标\n整理讨论'};
test('rejects traversal and incomplete profiles',()=>{assert.throws(()=>validateProfile({...p,id:'../../foo'}));assert.throws(()=>validateProfile({...p,skill:''}));});
test('configuration reaches both actual runtime files and preserves prior revision',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'community-test-'));
 try{const first=await installWorkspace(root,p);assert.equal(Object.keys(first).length,2);assert.match(await fs.readFile(path.join(root,'AGENTS.md'),'utf8'),/只回应被点名/);
 await installWorkspace(root,{...p,prompt:'新版中文主持'});assert.match(await fs.readFile(path.join(root,'AGENTS.md.previous'),'utf8'),/只回应被点名/);assert.match(await fs.readFile(path.join(root,'AGENTS.md'),'utf8'),/新版中文主持/);
 const manifest=JSON.parse(await fs.readFile(path.join(root,'community-profile-version.json')));assert.notEqual(manifest.files['AGENTS.md'],first['AGENTS.md']);assert.equal(manifest.files['.agents/skills/community-role/SKILL.md'],first['.agents/skills/community-role/SKILL.md']);
 }finally{await fs.rm(root,{recursive:true,force:true});}
});
test('does not overwrite a linked instruction file',async()=>{const root=await fs.mkdtemp(path.join(os.tmpdir(),'community-test-'));try{const outside=path.join(root,'outside');await fs.writeFile(outside,'keep');await fs.symlink(outside,path.join(root,'AGENTS.md'));await assert.rejects(installWorkspace(root,p),/符号链接/);assert.equal(await fs.readFile(outside,'utf8'),'keep');}finally{await fs.rm(root,{recursive:true,force:true});}});
test('backup and manifest replacement never follow destination symlinks',async()=>{
 const root=await fs.mkdtemp(path.join(os.tmpdir(),'community-test-'));try{
 await installWorkspace(root,p);const outside=path.join(root,'outside');await fs.writeFile(outside,'keep');
 await fs.symlink(outside,path.join(root,'AGENTS.md.previous'));await fs.rm(path.join(root,'community-profile-version.json'));await fs.symlink(outside,path.join(root,'community-profile-version.json'));
 await installWorkspace(root,{...p,prompt:'第二版'});assert.equal(await fs.readFile(outside,'utf8'),'keep');assert.equal((await fs.lstat(path.join(root,'AGENTS.md.previous'))).isSymbolicLink(),false);assert.equal((await fs.lstat(path.join(root,'community-profile-version.json'))).isSymbolicLink(),false);
 }finally{await fs.rm(root,{recursive:true,force:true});}
});

// Test the actual trusted stage function with inert transport, never model output as code.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {hash} from '../../../../src/pi/alexandria/graph/assets/pi-trial/preservation.mjs';
const source=fs.readFileSync(new URL('../../../../src/pi/alexandria/graph/assets/pi-trial/preservation-trial.mjs',import.meta.url),'utf8');
const code=source.slice(source.indexOf('let requests=0;'),source.indexOf('\ntry {\n const context='));

test('thrown call leaves a receipt, old attempt is immutable, fresh attempt can run',async()=>{
 const temporary=fs.mkdtempSync(path.join(os.tmpdir(),'preservation-recovery-'));
 try {
  const first=path.join(temporary,'first'), next=path.join(temporary,'next');fs.mkdirSync(first);fs.mkdirSync(next);
  let calls=0;
  class Broken {state={messages:[]};async prompt(){calls++;throw Error('inert transport failure');} abort(){}}
  class Working {state={messages:[]};async prompt(){calls++;this.state.messages.push({role:'assistant',content:[{type:'text',text:'{"ok":true}'}],stopReason:'stop'});} abort(){}}
  function stage(root,Agent){const ctx=vm.createContext({fs,path,root,Agent,model:{},id:'gpt-5.6-sol',system:'fixture',streamSimple(){throw Error('No transport allowed');},hash,setTimeout,clearTimeout,console:{log(){}}});vm.runInContext(code+'\nglobalThis.runStage=stage;',ctx);return ctx.runStage;}
  await assert.rejects(stage(first,Broken)('01-test','fixture prompt'));
  const receipt=JSON.parse(fs.readFileSync(path.join(first,'01-test.receipt.json')));assert.equal(receipt.call_threw,true);assert.equal(receipt.error_category,'transport_or_harness_error');
  const original=fs.readFileSync(path.join(first,'01-test.prompt.txt'),'utf8');
  await assert.rejects(stage(first,Working)('01-test','different prompt'));
  assert.equal(calls,1);assert.equal(fs.readFileSync(path.join(first,'01-test.prompt.txt'),'utf8'),original);
  const result=await stage(next,Working)('01-test','fixture prompt');assert.equal(result.ok,true);assert.equal(calls,2);
 }finally{fs.rmSync(temporary,{recursive:true});}
});

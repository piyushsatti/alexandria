import test from 'node:test';
import assert from 'node:assert/strict';
import {inventory,validateCandidate,freezeParagraphs,applyEdits,editableParagraphs,reviewQueue,holdReceipt} from '../pi-trial/preservation.mjs';

function fixture() {
 const docs={'policy.md':'# Policy\n\nRelease requires approval.\n\nRetention policy is unresolved.\n'};
 const regions=inventory(docs);
 const claim={id:'c1',subject:'Release',predicate:'requires',object:'approval',path:'policy.md',quote:'Release requires approval.',source_status:'requirement',scope:{text:'For this policy, release requires approval.',path:'policy.md',quote:'Release requires approval.'},annotation_ids:[]};
 const annotation={id:'a1',path:'policy.md',quote:'Retention policy is unresolved.',kind:'ambiguity',reason:'No duration specified',resolution_question:'What duration?'};
 const data={claims:[claim],annotations:[annotation],coverage:[
  {id:regions[0].id,disposition:'excluded',claim_ids:[],annotation_ids:[],reason:'Heading retained as page title'},
  {id:regions[1].id,disposition:'represented',claim_ids:['c1'],annotation_ids:[],reason:'Approval condition retained'},
  {id:regions[2].id,disposition:'unresolved',claim_ids:[],annotation_ids:['a1'],reason:'Policy requires a decision'},
 ],pages:[{title:'Policy',sections:[{heading:'Rules',paragraphs:[{text:'Release requires approval.',claim_ids:['c1'],annotation_ids:[]},{text:'Retention is unresolved. [AMBIGUOUS: a1]',claim_ids:[],annotation_ids:['a1']}]}]}]};
 return {docs,regions,data};
}
function editOf(data) {return {edits:editableParagraphs(data)};}
const goodReview={unsupported_additions:[],lost_information:[],status_errors:[],ambiguity_marker_errors:[],atomicity_errors:[],standalone_scope_errors:[],sensible:true};

test('inventory preserves exact nonempty source coverage including fenced blank lines and table headers',()=>{
 const text='# Heading\n\n| Field | Value |\n|---|---|\n| Owner | Alice |\n\n```mermaid\nA --> B\n\nB --> C\n```\n';
 const rows=inventory({'x.md':text});
 assert.equal(rows.length,3);
 for(const r of rows)assert.equal(text.slice(r.start,r.end),r.quote);
 assert.ok(rows[2].quote.includes('A --> B\n\nB --> C'));
 for(let i=0;i<text.length;i++)if(text[i].trim())assert.ok(rows.some(r=>i>=r.start&&i<r.end));
});

test('host reconstruction preserves citations, claims, annotations and coverage',()=>{
 const {data,docs,regions}=fixture();validateCandidate(data,docs,regions);
 const frozen=freezeParagraphs(data), edits=editOf(frozen);edits.edits[0].text='Approval is required before release.';
 const result=applyEdits(frozen,edits);validateCandidate(result,docs,regions);
 assert.deepEqual(result.claims,frozen.claims);assert.deepEqual(result.annotations,frozen.annotations);assert.deepEqual(result.coverage,frozen.coverage);
 const p=result.pages[0].sections[0].paragraphs[0];assert.deepEqual(p.claim_ids,['c1']);assert.deepEqual(p.annotation_ids,[]);
 assert.equal(data.pages[0].sections[0].paragraphs[0].id,undefined);
});

test('rejects model-owned references, missing/extra/duplicate paragraphs and moved markers',()=>{
 const {data}=fixture();const frozen=freezeParagraphs(data);
 const variants=[e=>e.edits[0].claim_ids=['other'], e=>e.edits.pop(),e=>e.edits.push({...e.edits[0],id:'invented'}),e=>e.edits[1].id=e.edits[0].id,e=>{e.edits[0].text+=' [AMBIGUOUS: a1]';e.edits[1].text='Retention unresolved.';}];
 for(const mutate of variants){const e=editOf(frozen);mutate(e);assert.throws(()=>applyEdits(frozen,e));}
});

test('rejects missing source regions, false non-overlap links and unexplained exclusions',()=>{
 for(const mutate of [d=>d.coverage.pop(), d=>{d.coverage[0]={...d.coverage[0],disposition:'represented',claim_ids:['c1']};},d=>d.coverage[0].reason='', d=>d.coverage[1].claim_ids=[]]){
  const {data,docs,regions}=fixture();mutate(data);assert.throws(()=>validateCandidate(data,docs,regions));
 }
});

test('an empty extraction needs a complete explicit ledger and is always held',()=>{
 const {docs,regions}=fixture();const empty={claims:[],annotations:[],pages:[],coverage:[]};
 assert.throws(()=>validateCandidate(empty,docs,regions));
 empty.coverage=regions.map(r=>({id:r.id,disposition:'not_checked',claim_ids:[],annotation_ids:[],reason:'No extraction returned; needs review'}));
 validateCandidate(empty,docs,regions);
 const receipt=holdReceipt(empty,regions,goodReview);assert.equal(receipt.empty_extraction,true);assert.equal(receipt.automatic_acceptance,false);assert.equal(receipt.status,'held-pending-owner-review');
});

test('meaning-inverted prose cannot acquire approval from structural success',()=>{
 const {data,docs,regions}=fixture();const frozen=freezeParagraphs(data);const edits=editOf(frozen);edits.edits[0].text='Publish without approval.';
 const changed=applyEdits(frozen,edits);validateCandidate(changed,docs,regions);
 const bad={...goodReview,sensible:false,standalone_scope_errors:['Approval requirement reversed']};
 const receipt=holdReceipt(changed,regions,bad);assert.equal(receipt.model_review_pass,false);assert.equal(receipt.automatic_acceptance,false);
 assert.throws(()=>holdReceipt(changed,regions,{sensible:true}));
});

test('review queue preserves unflagged regions as unchecked and warns on repeated labels',()=>{
 const {data,regions}=fixture();data.claims.push({...data.claims[0],id:'c2',quote:'Other release requires another approval.'});
 const queue=reviewQueue(data,regions);
 assert.equal(queue.filter(q=>q.type==='source-region').length,regions.length);
 assert.ok(queue.some(q=>q.flags.includes('conditions-negation')));
 assert.ok(queue.some(q=>q.type==='repeated-label'));
 assert.ok(queue.every(q=>q.review_state==='not_checked'));
});

test('scope evidence accounts for a governing heading without accepting unrelated refs',()=>{
 const {data,docs,regions}=fixture();
 data.claims[0].scope={text:'Policy in this source',path:'policy.md',quote:'# Policy'};
 data.coverage[0]={id:regions[0].id,disposition:'represented',claim_ids:['c1'],annotation_ids:[],reason:'Claim scope cites this heading'};
 validateCandidate(data,docs,regions);
 data.coverage[2].claim_ids=['c1'];
 assert.throws(()=>validateCandidate(data,docs,regions),/does not overlap/);
});

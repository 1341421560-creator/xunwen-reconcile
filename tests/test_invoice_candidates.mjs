import assert from 'node:assert/strict';
import {invoiceCandidates} from '../web/invoice-candidates.js';

const bank={party_match_key:'深圳市鸿运星电子商务有限公司',currency:'CNY'};
const invoice=(id,key,values={})=>({id,party_match_key:key,currency:'CNY',hold_reasons:[],status:'unmatched',distributable_cents:10000,...values});
const view={invoices:[invoice('same',bank.party_match_key),invoice('other','其他公司'),invoice('blocked',bank.party_match_key,{distributable_cents:0}),invoice('held',bank.party_match_key,{hold_reasons:['红字']}),invoice('review',bank.party_match_key,{status:'review'}),invoice('foreign',bank.party_match_key,{currency:'USD'})]};
assert.deepEqual(invoiceCandidates(view,bank).map(row=>row.id),['same']);
assert.deepEqual(invoiceCandidates(view,bank,true).map(row=>row.id),['same','other']);
assert.deepEqual(invoiceCandidates(view,{...bank,party_match_key:'其他公司'}).map(row=>row.id),['other']);
assert.deepEqual(invoiceCandidates(view,{...bank,party_match_key:''}),[]);
assert.deepEqual(invoiceCandidates(view,null,true),[]);
const many={invoices:Array.from({length:150},(_,n)=>invoice(String(n),bank.party_match_key))};
assert.equal(invoiceCandidates(many,bank).length,150);
console.log('6 组发票候选范围校验通过');

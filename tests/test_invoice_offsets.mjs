import assert from 'node:assert/strict';
import test from 'node:test';
import {offsetCandidates,offsetSummary,offsetPreviewHtml} from '../web/invoice-offset-display.js';
import {filterInvoices,selectionSummary} from '../web/invoice-filter.js';
import {createCompanyContext} from '../web/company-context.js';

test('冲红候选忽略当前红票连带待复核，仍排除其他销方、币种、异常和冲突',()=>{
 const base={id:'B',red:false,amount_cents:100000,number:'原票1',party:'甲公司',party_match_key:'甲公司',currency:'CNY',date:'2026-08-03',hold_reasons:['同销方红票待复核']};
 const rows=[base,...[{party_match_key:'乙公司'},{currency:'USD'},{blocking_invalid:'作废'},{offset_conflict:true},{red:true,amount_cents:-20000}].map((x,n)=>({...base,id:'I'+n,...x}))];
 assert.deepEqual(offsetCandidates({invoices:rows},{party_match_key:'甲公司',currency:'CNY'}).map(i=>i.id),['B']);
 assert.equal(offsetCandidates({invoices:rows},{party_match_key:'甲公司',currency:'CNY'},'2026-08').length,1);
});

test('冲红筛选与月份组合，票面勾选合计不再扣冲红净额',()=>{
 const base={date:'2026-08-03',party:'甲公司',number:'B',id:'B',difference_status:'none',offset_status:'partial',amount_cents:155000,net_amount_cents:122400,offset_cents:32600};
 const rows=[base,{...base,id:'R',number:'R',offset_status:'linked',amount_cents:-32600,date:'2026-10-03'}];
 assert.equal(selectionSummary(rows).totalCents,122400);
 assert.equal(selectionSummary([rows[0],{...rows[1],include_in_total:false}]).totalCents,155000);
 const state={invoiceSearch:'',invoiceFilter:'all',differenceFilter:'all',offsetFilter:'linked',invoiceMonth:'2026-10'};
 assert.deepEqual(filterInvoices(rows,state).map(i=>i.id),['R']);
});

test('展示转义号码、依据与提示，不将全额冲红显示成付款完成',()=>{
 const view={offset_labels:{full:'全额冲红，不可核销'},difference_labels:{none:'无差额记录'}};
 const html=offsetSummary({offset_status:'full',offset_cents:100000,net_amount_cents:0,offset_invoice_ids:['R'],offset_invoice_numbers:['<img>']},view);
 assert.match(html,/全额冲红，不可核销/);assert.match(html,/&lt;img&gt;/);assert.doesNotMatch(html,/已匹配/);
 const preview=offsetPreviewHtml({amount_cents:20000,previous_net_cents:100000,net_amount_cents:80000,allocated_cents:100000,remaining_cents:-20000,can_save:false,excess_cents:20000,allocations:[{bank_date:'2026-08-01',bank_party:'<甲>',bank_id:'B',bank_reference:'R',amount_cents:100000}]},view);
 assert.match(preview,/超出 200.00/);assert.match(preview,/data-bank="B"/);assert.match(preview,/&lt;甲&gt;/);
});

test('冲红预览是只读请求，切换公司后相同版本的旧响应被拒绝',async()=>{
 let resolve;const promise=new Promise(r=>{resolve=r;});const context=createCompanyContext(()=>promise);
 const pending=context.capture().request('/api/invoice-offset/preview',{revision:2});
 const rejected=assert.rejects(pending,error=>error.silent);assert.equal(context.busy,false);context.select('haisi');
 resolve({company_key:'moderate',revision:2,can_save:true});await rejected;
});

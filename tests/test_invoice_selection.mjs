import assert from 'node:assert/strict';
import test from 'node:test';
import {filterInvoices,selectionSummary,visibleSelection} from '../web/invoice-filter.js';
import {paginate} from '../web/pagination.js';
import {createInvoiceSelection} from '../web/invoice-selection.js';
import {createCompanyContext} from '../web/company-context.js';
import {renderInvoiceTable} from '../web/invoice-table.js';
import {installSaveGuard} from '../web/save-guard.js';

function invoice(index,changes={}){
 return {id:`I${index}`,number:`NO${index}`,party:'甲公司',date:'2026-08-15',amount_cents:101,
  allocated_cents:0,remaining_cents:101,distributable_cents:101,status:'unmatched',difference_status:'none',
  hold_reasons:[],...changes};
}
function filters(changes={}){
 return {invoiceMonth:'',invoiceSearch:'',invoiceFilter:'all',differenceFilter:'all',invoicePage:0,...changes};
}
function ledger(rows,revision=7){
 return {company_key:'moderate',revision,invoices:rows,invoice_page_size:100,
  difference_labels:{none:'无差额记录',pending:'差额待确认',carry_forward:'已确认跨月使用',amount_error:'开票金额有误',cleared:'差额已用完'}};
}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
async function flush(){for(let index=0;index<8;index++)await Promise.resolve();}
function setup(t,rows,changes={}){
 const elements=new Map(),calls=[],frames=[];
 const get=id=>{
  if(!elements.has(id)){
   const classes=new Set();
   elements.set(id,{value:'',innerHTML:'',textContent:'',disabled:false,checked:false,indeterminate:false,listeners:{},
    classList:{toggle:(name,on)=>on?classes.add(name):classes.delete(name),contains:name=>classes.has(name)},
    addEventListener(name,handler){this.listeners[name]=handler;}});
  }
  return elements.get(id);
 };
 const previous=globalThis.document;
 globalThis.document={getElementById:get,querySelector:get};
 t.after(()=>{globalThis.document=previous;});
 const state={...filters(),result:ledger(rows),labels:{unmatched:'未匹配',review:'待确认'},
  companySaving:false,invoiceSelectionBusy:false,invoiceSelectionNeedsRefresh:false,invoiceSelectionPending:null,...changes};
 const context=createCompanyContext((path,payload)=>{const pending=deferred();calls.push({path,payload,...pending});return pending.promise;},
  {onBusy:busy=>{state.companySaving=busy;}});
 let controller;
 const render=()=>{
  renderInvoiceTable(state,()=>'<span>状态</span>');
  frames.push({busy:state.invoiceSelectionBusy,total:get('invoice-selection-total').textContent,
   status:get('invoice-selection-status').textContent,bulkDisabled:get('invoice-select-all').disabled});
 };
 const apply=result=>{state.result=result;controller.onLedger();render();};
 controller=createInvoiceSelection(state,context,{apply,render});controller.bind();render();
 return {get,state,context,controller,calls,frames,apply,render};
}

test('月份、搜索、分配状态和差额状态同时取交集，搜索使用原文字面包含',()=>{
 const rows=[invoice(1,{party:'ACME[甲]',status:'review',difference_status:'pending',distributable_cents:0}),
  invoice(2,{party:'ACME[甲]',date:'2026-09-01',status:'review',difference_status:'pending',distributable_cents:0}),
  invoice(3,{party:'另一公司',status:'review',difference_status:'pending',distributable_cents:0}),
  invoice(4,{party:'ACME[甲]',difference_status:'pending',distributable_cents:0}),
  invoice(5,{party:'ACME[甲]',status:'review',difference_status:'amount_error',distributable_cents:0}),
  invoice(6,{allocated_cents:50,remaining_cents:51,distributable_cents:51})];
 const before=structuredClone(rows);
 assert.deepEqual(filterInvoices(rows,filters({invoiceMonth:'2026-08',invoiceSearch:'acme[甲]',invoiceFilter:'review',differenceFilter:'pending'})).map(row=>row.id),['I1']);
 assert.deepEqual(filterInvoices(rows,filters({invoiceFilter:'available'})).map(row=>row.id),['I6']);
 assert.deepEqual(filterInvoices(rows,filters({invoiceFilter:'used'})).map(row=>row.id),['I6']);
 assert.deepEqual(filterInvoices(rows,filters({invoiceSearch:'NO6'})).map(row=>row.id),['I6']);
 assert.deepEqual(filterInvoices(rows,filters({invoiceSearch:'ACME.*'})),[]);
 assert.deepEqual(rows,before);
});

test('默认和显式勾选计入，取消勾选不计入，红字负额与分金额准确相加',()=>{
 const rows=[invoice(1,{amount_cents:100001}),invoice(2,{amount_cents:2,include_in_total:true}),
  invoice(3,{amount_cents:-10000}),invoice(4,{amount_cents:900000,include_in_total:false}),
  invoice(5,{amount_cents:-5000,include_in_total:false})];
 assert.deepEqual(selectionSummary(rows),{count:5,selectedCount:3,totalCents:90003});
 assert.equal(visibleSelection(rows[0],null),true);
 assert.equal(visibleSelection(rows[3],null),false);
 assert.deepEqual(selectionSummary([]),{count:0,selectedCount:0,totalCents:0});
});

test('205及301张发票按100张分页，合计覆盖全部筛选结果且不改变记录顺序',()=>{
 for(const count of [205,301]){
  const rows=Array.from({length:count},(_,index)=>invoice(index,{amount_cents:index%11===0?-101:index+1,include_in_total:index%7!==0}));
  const before=structuredClone(rows),filtered=filterInvoices(rows,filters({invoiceMonth:'2026-08'}));
  let expectedTotal=0,expectedCount=0;
  for(const row of rows)if(row.include_in_total){expectedTotal+=row.amount_cents;expectedCount++;}
  const allIds=[];
  for(let page=0;page<Math.ceil(count/100);page++){
   const info=paginate(filtered,page,100);allIds.push(...info.rows.map(row=>row.id));
   assert.equal(info.rows.length,Math.min(100,count-page*100));
   assert.equal(info.last,Math.ceil(count/100)-1);
   assert.deepEqual(selectionSummary(filtered),{count,selectedCount:expectedCount,totalCents:expectedTotal});
  }
  assert.deepEqual(allIds,rows.map(row=>row.id));
  assert.equal(paginate(filtered,99,100).index,Math.ceil(count/100)-1);
  assert.equal(paginate(filtered,-2,100).index,0);
  assert.deepEqual(rows,before);
 }
 assert.deepEqual(paginate([],9,100),{rows:[],index:0,last:0,total:0});
});

test('全选操作包含筛选结果所有页205个编号，不包含其他月份96张',async t=>{
 const rows=Array.from({length:301},(_,index)=>invoice(index,{date:index<205?'2026-08-15':'2026-09-15'}));
 const ui=setup(t,rows,{invoiceMonth:'2026-08',invoicePage:2});
 assert.match(ui.get('invoice-page-info').textContent,/第 3 \/ 3 页/);
 ui.get('invoice-select-all').onchange({target:{checked:false}});
 assert.equal(ui.calls.length,1);
 assert.deepEqual(ui.calls[0].payload,{revision:7,invoice_ids:rows.slice(0,205).map(row=>row.id),selected:false,company_key:'moderate'});
 ui.calls[0].resolve(ledger(rows.map((row,index)=>index<205?{...row,include_in_total:false}:row),8));
 await flush();
 assert.equal(ui.get('invoice-selection-total').textContent,'0.00 元');
 assert.equal(selectionSummary(ui.state.result.invoices).selectedCount,96);
 assert.equal(ui.state.invoiceMonth,'2026-08');assert.equal(ui.state.invoicePage,2);
});

test('保存中的勾选只作待定显示，保持已保存合计并禁用全部勾选入口且拒绝重复提交',async t=>{
 const rows=Array.from({length:205},(_,index)=>invoice(index));
 const ui=setup(t,rows,{invoiceMonth:'2026-08',invoicePage:2});
 const saving=ui.controller.save(['I204'],false);
 assert.equal(ui.state.invoiceSelectionBusy,true);assert.equal(ui.context.busy,true);
 assert.equal(visibleSelection(ui.state.result.invoices[204],ui.state.invoiceSelectionPending),false);
 assert.equal(ui.state.result.invoices[204].include_in_total,undefined);
 assert.equal(ui.get('invoice-selection-total').textContent,'207.05 元');
 assert.equal(ui.get('invoice-select-all').disabled,true);
 const inputs=ui.get('invoice-table').innerHTML.match(/<input[^>]+>/g);
 assert.equal(inputs.length,5);assert.ok(inputs.every(value=>value.includes('disabled')));
 assert.ok(!inputs.find(value=>value.includes('data-invoice-select="I204"')).includes(' checked'));
 assert.match(ui.get('invoice-selection-status').textContent,/合计将在保存成功后更新/);
 await ui.controller.save(['I203'],false);assert.equal(ui.calls.length,1);
 assert.throws(()=>ui.context.select('haisi'),/正在保存/);
 ui.calls[0].resolve(ledger(rows.map(row=>row.id==='I204'?{...row,include_in_total:false}:row),8));await saving;
 assert.equal(ui.state.invoiceSelectionBusy,false);assert.equal(ui.state.invoiceSelectionPending,null);assert.equal(ui.context.busy,false);
 assert.equal(ui.get('invoice-selection-total').textContent,'206.04 元');assert.equal(ui.get('invoice-select-all').disabled,false);
 assert.equal(ui.state.invoiceMonth,'2026-08');assert.equal(ui.state.invoicePage,2);
});

test('保存响应失败后只回读一次最新账本，不自动重试写入或采用旧状态',async t=>{
 const rows=[invoice(1),invoice(2,{include_in_total:false})],ui=setup(t,rows);
 const saving=ui.controller.save(['I1'],false);
 ui.calls[0].reject(new Error('连接中断'));await flush();
 assert.equal(ui.calls.length,2);assert.equal(ui.calls[1].path,'/api/ledger?company_key=moderate');
 assert.equal(ui.calls[1].payload,undefined);assert.equal(ui.context.busy,true);
 ui.calls[1].resolve(ledger(rows.map(row=>({...row,include_in_total:false})),8));await saving;
 assert.equal(ui.state.result.revision,8);assert.equal(ui.get('invoice-selection-total').textContent,'0.00 元');
 assert.equal(ui.get('invoice-select-all').checked,false);assert.equal(ui.get('invoice-select-all').disabled,false);
 assert.match(ui.get('invoice-selection-status').textContent,/未收到保存成功确认.*连接中断.*已重新读取/);
 assert.equal(ui.calls.filter(call=>call.payload!==undefined).length,1);
});

test('保存及回读同时失败时禁止继续，人工刷新确认后才允许新的写入',async t=>{
 const rows=[invoice(1),invoice(2)],ui=setup(t,rows);
 const saving=ui.controller.save(['I1'],false);
 ui.calls[0].reject(new Error('保存响应丢失'));await flush();
 ui.calls[1].reject(new Error('服务未连接'));await saving;
 assert.equal(ui.state.invoiceSelectionNeedsRefresh,true);assert.equal(ui.state.invoiceSelectionBusy,false);
 assert.equal(ui.get('invoice-select-all').disabled,true);assert.equal(ui.get('invoice-selection-total').textContent,'2.02 元');
 assert.match(ui.get('invoice-selection-status').textContent,/保存结果尚未确认.*刷新账本/);
 await ui.controller.save(['I2'],false);assert.equal(ui.calls.length,2);
 ui.apply(ledger([invoice(1,{include_in_total:false}),invoice(2)],8));
 assert.equal(ui.state.invoiceSelectionNeedsRefresh,false);assert.equal(ui.get('invoice-select-all').disabled,false);
 assert.equal(ui.get('invoice-selection-total').textContent,'1.01 元');
 const retry=ui.controller.save(['I2'],false);assert.equal(ui.calls.length,3);
 assert.equal(ui.calls[2].payload.revision,8);
 ui.calls[2].resolve(ledger([invoice(1,{include_in_total:false}),invoice(2,{include_in_total:false})],9));await retry;
 assert.equal(ui.get('invoice-selection-total').textContent,'0.00 元');
});

test('其他保存已占用或筛选无记录时不发起勾选写入，恢复后可以正常操作',async t=>{
 const ui=setup(t,[invoice(1)]),release=ui.context.capture().hold();
 await ui.controller.save(['I1'],false);await ui.controller.save([],true);
 assert.equal(ui.calls.length,0);assert.equal(ui.state.invoiceSelectionBusy,false);
 release();
 ui.state.invoiceMonth='2025-01';ui.get('invoice-select-all').onchange({target:{checked:false}});await flush();
 assert.equal(ui.calls.length,0);
 ui.get('invoice-table').listeners.change({target:{dataset:{},checked:false}});assert.equal(ui.calls.length,0);
 ui.get('invoice-table').listeners.change({target:{dataset:{invoiceSelect:'I1'},checked:false}});
 assert.deepEqual(ui.calls[0].payload.invoice_ids,['I1']);
 ui.calls[0].resolve(ledger([invoice(1,{include_in_total:false})],8));await flush();
 assert.equal(ui.state.invoiceSelectionBusy,false);
});

test('变更发票月份重置页码，不重置搜索或其他筛选，空月份只表示全部月份',t=>{
 const ui=setup(t,[invoice(1),invoice(2,{date:'2026-09-01'})],{invoiceMonth:'2026-08',invoicePage:3,invoiceSearch:'甲公司',invoiceFilter:'available'});
 ui.state.invoicePage=3;
 ui.get('invoice-month').onchange({target:{value:'2026-09'}});
 assert.equal(ui.state.invoiceMonth,'2026-09');assert.equal(ui.state.invoicePage,0);
 assert.equal(ui.state.invoiceSearch,'甲公司');assert.equal(ui.state.invoiceFilter,'available');
 assert.match(ui.get('invoice-selection-count').textContent,/筛选共 1 张/);
 ui.get('invoice-month').onchange({target:{value:''}});
 assert.equal(ui.state.invoiceMonth,'');assert.match(ui.get('invoice-selection-count').textContent,/筛选共 2 张/);
 assert.equal(ui.calls.length,0);
});

test('没有匹配记录时全选不可用且合计归零，当前页越界自动夹取',t=>{
 const ui=setup(t,[invoice(1)],{invoiceSearch:'不存在',invoicePage:6});
 assert.equal(ui.state.invoicePage,0);assert.equal(ui.get('invoice-select-all').disabled,true);
 assert.equal(ui.get('invoice-selection-total').textContent,'0.00 元');assert.match(ui.get('invoice-table').innerHTML,/暂无符合条件/);
 ui.state.invoiceSearch='';ui.render();
 assert.equal(ui.get('invoice-select-all').disabled,false);assert.equal(ui.get('invoice-select-all').checked,true);
});

test('关闭窗口保护只在实际保存期间触发，保存完成后和空闲时不提示',async t=>{
 const ui=setup(t,[invoice(1)]),listeners={};
 installSaveGuard({addEventListener:(name,handler)=>{listeners[name]=handler;}},()=>ui.context.busy);
 const event=()=>({prevented:false,preventDefault(){this.prevented=true;}});
 const idle=event();listeners.beforeunload(idle);assert.equal(idle.prevented,false);assert.equal(idle.returnValue,undefined);
 const saving=ui.controller.save(['I1'],false),pending=event();listeners.beforeunload(pending);
 assert.equal(pending.prevented,true);assert.equal(pending.returnValue,'');
 ui.calls[0].resolve(ledger([invoice(1,{include_in_total:false})],8));await saving;
 const finished=event();listeners.beforeunload(finished);assert.equal(finished.prevented,false);assert.equal(finished.returnValue,undefined);
});

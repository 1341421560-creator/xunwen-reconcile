import assert from 'node:assert/strict';
import test from 'node:test';
import {createExpenseStatistics} from '../web/expense-statistics.js';

const categories=[['goods','货款'],['logistics','物流'],['labor','人工'],['utilities','水电费'],['other','其他支出']].map(([id,label])=>({id,label,keywords:[]}));
function dataset({revision=1,count=25,amount=101,party='甲公司',category='goods',start_date='',end_date=''}={}){
 const rows=Array.from({length:count},(_,i)=>({id:String(i),date:'2026-08-01',party,summary:'货款',debit_cents:amount,expense_category:category,expense_category_reason:'摘要分类',manual_note:''}));
 return {revision,rows,start_date,end_date,categories,count,debit_cents:count*amount};
}
function setup(t,request){
 const elements=new Map();
 const get=id=>{if(!elements.has(id))elements.set(id,{value:'',innerHTML:'',textContent:'',hidden:false,disabled:false});return elements.get(id);};
 const old=globalThis.document;
 globalThis.document={getElementById:get};
 t.after(()=>{globalThis.document=old;});
 const state={page:'expenses',result:{revision:1,expense_categories:categories}};
 const controller=createExpenseStatistics(state,request);controller.bind();
 const company=value=>{get('expense-party-filter').value=value;get('expense-party-filter').oninput();};
 const category=value=>{get('expense-category-filter').value=value;get('expense-category-filter').onchange();};
 return {get,state,controller,company,category};
}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
async function flush(){await Promise.resolve();await Promise.resolve();}

test('较早月份查询晚到，不覆盖随后全部时间的公司筛选结果',async t=>{
 const requests=[];
 const ui=setup(t,(path,payload)=>{const item=deferred();requests.push({...item,path,payload});return item.promise;});
 ui.category('goods');ui.company('甲公司');
 ui.get('expense-start').value='2026-08-01';ui.get('expense-end').value='2026-08-31';
 const first=ui.controller.refresh();ui.get('expense-all-time').onclick();
 assert.deepEqual(requests[1].payload,{start_date:'',end_date:''});
 requests[1].resolve(dataset());await flush();
 requests[0].resolve(dataset({count:1,start_date:'2026-08-01',end_date:'2026-08-31'}));await first;
 assert.match(ui.get('expense-page-info').textContent,/25 笔/);
 assert.equal(ui.get('expense-total').textContent,'25.25 元');
 assert.match(ui.get('expense-scope').textContent,/最早记录.*货款.*甲公司/);
});
test('刷新中的筛选变更使用最新条件，旧类别不带入合计',async t=>{
 const pending=deferred();const ui=setup(t,()=>pending.promise);
 const request=ui.controller.refresh();ui.company('乙公司');ui.category('logistics');
 const data=dataset();data.rows.push(...dataset({party:'乙公司',category:'logistics',count:2,amount:7654}).rows);
 pending.resolve(data);await request;
 assert.equal(ui.get('expense-total').textContent,'153.08 元');
 assert.match(ui.get('expense-page-info').textContent,/物流：2 笔/);
 assert.ok(!ui.get('expense-table').innerHTML.includes('甲公司'));
});
test('过期账本响应隐藏统计并提示刷新，新版返回后保留公司条件',async t=>{
 let response=dataset();const ui=setup(t,async()=>response);
 await ui.controller.refresh();ui.company('甲公司');ui.category('goods');
 ui.state.result.revision=2;await ui.controller.refresh();
 assert.equal(ui.get('expense-content').hidden,true);
 assert.match(ui.get('expense-error').textContent,/账本已更新/);
 response=dataset({revision:2,count:2});await ui.controller.refresh();
 assert.equal(ui.get('expense-content').hidden,false);assert.equal(ui.get('expense-error').textContent,'');
 assert.equal(ui.get('expense-total').textContent,'2.02 元');
 assert.match(ui.get('expense-scope').textContent,/货款.*甲公司/);
});
test('查询失败不展示旧结果，旧响应也不能覆盖最新错误',async t=>{
 const requests=[];const ui=setup(t,()=>{const d=deferred();requests.push(d);return d.promise;});
 const first=ui.controller.refresh();const second=ui.controller.refresh();
 requests[1].reject(new Error('开始日期不能晚于结束日期'));await second;
 requests[0].resolve(dataset());await first;
 assert.equal(ui.get('expense-content').hidden,true);
 assert.equal(ui.get('expense-error').textContent,'开始日期不能晚于结束日期');
 ui.get('expense-all-time').onclick();requests[2].resolve(dataset());await flush();
 assert.equal(ui.get('expense-content').hidden,false);assert.equal(ui.get('expense-error').textContent,'');
});
test('跨三页筛选和重新分类后，页码及全量合计保持有效',async t=>{
 let response=dataset({count:61});const ui=setup(t,async()=>response);
 await ui.controller.refresh();ui.get('expense-next').onclick();ui.get('expense-next').onclick();ui.get('expense-next').onclick();
 assert.match(ui.get('expense-page-info').textContent,/第 4 \/ 4 页/);
 assert.equal(ui.get('expense-total').textContent,'61.61 元');
 ui.company('不存在');assert.match(ui.get('expense-page-info').textContent,/0 笔.*第 1 \/ 1 页/);
 ui.company('甲公司');ui.category('goods');ui.get('expense-next').onclick();ui.get('expense-next').onclick();
 response=dataset({revision:2,count:1});ui.state.result.revision=2;ui.controller.onLedger();await flush();
 assert.match(ui.get('expense-page-info').textContent,/1 笔.*第 1 \/ 1 页/);
 assert.equal(ui.get('expense-total').textContent,'1.01 元');
 assert.equal(ui.get('expense-prev').disabled,true);assert.equal(ui.get('expense-next').disabled,true);
});
test('切离页面会丢弃未完成响应，返回后读取最新统计',async t=>{
 const requests=[];const ui=setup(t,()=>{const d=deferred();requests.push(d);return d.promise;});
 const first=ui.controller.refresh();ui.state.page='bank';await ui.controller.refresh();
 requests[0].resolve(dataset());await first;
 assert.equal(ui.get('expense-total').textContent,'');
 ui.state.page='expenses';const next=ui.controller.refresh();requests[1].resolve(dataset({count:3}));await next;
 assert.equal(ui.get('expense-total').textContent,'3.03 元');
});
test('公司名称、备注和摘要中的特殊字符作为文字显示',async t=>{
 const name='甲 <script>alert(1)</script> & 公司';
 const data=dataset({party:name,count:1});data.rows[0].manual_note='<img src=x onerror=alert(1)>';data.rows[0].summary='采购 <svg onload=alert(1)>';
 const ui=setup(t,async()=>data);await ui.controller.refresh();ui.company('<script>');
 assert.equal(ui.get('expense-total').textContent,'1.01 元');
 assert.ok(ui.get('expense-table').innerHTML.includes('&lt;script&gt;'));
 assert.ok(!ui.get('expense-table').innerHTML.includes('<img'));
 assert.ok(!ui.get('expense-table').innerHTML.includes('<svg'));
 assert.ok(ui.get('expense-company-options').innerHTML.includes('&lt;script&gt;'));
});
test('清除类别和公司保留日期，分类汇总点击保留公司',async t=>{
 const ui=setup(t,async()=>dataset({start_date:'2026-08-01',end_date:'2026-08-31'}));
 ui.get('expense-start').value='2026-08-01';ui.get('expense-end').value='2026-08-31';
 await ui.controller.refresh();ui.company('甲公司');ui.category('goods');
 ui.get('expense-summary').onclick({target:{closest:()=>({dataset:{expenseFilter:'logistics'}})}});
 assert.equal(ui.get('expense-total').textContent,'0.00 元');assert.equal(ui.get('expense-party-filter').value,'甲公司');
 ui.get('expense-clear-filters').onclick();
 assert.equal(ui.get('expense-start').value,'2026-08-01');assert.equal(ui.get('expense-end').value,'2026-08-31');
 assert.equal(ui.get('expense-total').textContent,'25.25 元');assert.equal(ui.get('expense-category-filter').value,'all');
});

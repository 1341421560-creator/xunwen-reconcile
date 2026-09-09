import assert from 'node:assert/strict';
import test from 'node:test';
import {filterExpenseStatistics,expenseCompanyOptions} from '../web/expense-statistics-filter.js';

const rows=[
 {id:'1',date:'2026-09-01',party:'深圳市富芯通电子有限公司',expense_category:'goods',debit_cents:122400},
 {id:'2',date:'2026-08-01',party:'深圳市富芯通电子有限公司',expense_category:'goods',debit_cents:100000},
 {id:'3',date:'2026-07-01',party:'深圳市富芯通电子有限公司',expense_category:'logistics',debit_cents:30000},
 {id:'4',date:'2026-08-02',party:'深圳市鸿运星电子商务有限公司',expense_category:'goods',debit_cents:50000},
 {id:'5',date:'2026-08-03',party:'ACME 公司',expense_category:'labor',debit_cents:12345},
 {id:'6',date:'2026-08-03',party:'',expense_category:'other',debit_cents:100},
];
const categories=['goods','logistics','labor','utilities','other'].map(id=>({id,label:id,count:0,debit_cents:0}));
const result={revision:8,start_date:'',end_date:'',categories,rows,count:6,debit_cents:314845};

test('类别与公司组合筛选跨月记录，金额与分类汇总一致',()=>{
 const filtered=filterExpenseStatistics(result,{category:'goods',party:'深圳市富芯通电子有限公司'});
 assert.deepEqual(filtered.rows.map(row=>row.id),['1','2']);
 assert.equal(filtered.count,2);assert.equal(filtered.debit_cents,222400);
 assert.equal(filtered.categories[0].debit_cents,222400);
 assert.equal(filtered.categories.reduce((sum,item)=>sum+item.count,0),filtered.count);
 assert.equal(filtered.categories.reduce((sum,item)=>sum+item.debit_cents,0),filtered.debit_cents);
});
test('只筛公司包含其全部支出类别，输入支持首尾空白和关键词',()=>{
 const filtered=filterExpenseStatistics(result,{party:'  富芯通  '});
 assert.equal(filtered.count,3);assert.equal(filtered.debit_cents,252400);
 assert.equal(filterExpenseStatistics(result,{party:'acme'}).debit_cents,12345);
});
test('清空公司后保留所选类别，清空全部条件恢复完整统计',()=>{
 assert.equal(filterExpenseStatistics(result,{category:'goods',party:''}).debit_cents,272400);
 assert.equal(filterExpenseStatistics(result,{party:'   '}).count,6);
 assert.equal(filterExpenseStatistics(result).debit_cents,314845);
});
test('不存在的公司或类别交集为空时，所有合计为零',()=>{
 for(const filters of [{party:'不存在的公司'},{category:'labor',party:'富芯通'}]){
  const filtered=filterExpenseStatistics(result,filters);
  assert.equal(filtered.count,0);assert.equal(filtered.debit_cents,0);
  assert.ok(filtered.categories.every(item=>item.count===0&&item.debit_cents===0));
 }
});
test('仅在服务端已返回的日期范围内筛选，不补入范围外记录',()=>{
 const dated={...result,start_date:'2026-08-01',end_date:'2026-08-31',rows:rows.filter(row=>row.date.startsWith('2026-08'))};
 const filtered=filterExpenseStatistics(dated,{category:'goods',party:'富芯通'});
 assert.deepEqual(filtered.rows.map(row=>row.id),['2']);
 assert.equal(filtered.start_date,'2026-08-01');assert.equal(filtered.end_date,'2026-08-31');
});
test('超过一页时汇总全部命中明细，保持金额分值精度',()=>{
 const source={...result,rows:Array.from({length:25},(_,i)=>({...rows[0],id:String(i),debit_cents:101}))};
 const filtered=filterExpenseStatistics(source,{category:'goods',party:'富芯通'});
 assert.equal(filtered.count,25);assert.equal(filtered.debit_cents,2525);
});
test('反复组合查询不修改原统计数据，能随账本分类刷新',()=>{
 const before=JSON.stringify(result);
 filterExpenseStatistics(result,{party:'富芯通',category:'goods'});
 filterExpenseStatistics(result,{party:'鸿运星'});
 assert.equal(JSON.stringify(result),before);
 const updated={...result,rows:rows.map(row=>row.id==='1'?{...row,expense_category:'logistics'}:row)};
 assert.equal(filterExpenseStatistics(updated,{party:'富芯通',category:'goods'}).debit_cents,100000);
});
test('公司建议去重且忽略空名称，不修改行顺序',()=>{
 const before=JSON.stringify(rows);const companies=expenseCompanyOptions(rows);
 assert.equal(companies.length,3);assert.ok(companies.includes('深圳市富芯通电子有限公司'));
 assert.ok(!companies.includes(''));assert.equal(JSON.stringify(rows),before);
});

test('一万条记录的多公司多类别组合均与独立逐笔合计一致',()=>{
 const names=['富芯通','富芯通分公司','鸿运星','A&B 公司','网银*公司',''];
 const kinds=categories.map(item=>item.id);
 const bulk=Array.from({length:10037},(_,i)=>({id:String(i),party:names[i%names.length],date:`2026-${String(i%12+1).padStart(2,'0')}-01`,expense_category:kinds[i%kinds.length],debit_cents:(i*157)%100003+1}));
 const source={...result,rows:bulk};
 for(const category of ['all',...kinds])for(const party of ['',...names,'公司','*','不存在']){
  const expected={count:0,total:0,ids:[]};
  for(const row of bulk){
   if(category!=='all'&&row.expense_category!==category)continue;
   if(party&&row.party.indexOf(party)===-1)continue;
   expected.count++;expected.total+=row.debit_cents;expected.ids.push(row.id);
  }
  const filtered=filterExpenseStatistics(source,{category,party});
  assert.equal(filtered.count,expected.count);assert.equal(filtered.debit_cents,expected.total);
  assert.deepEqual(filtered.rows.map(row=>row.id),expected.ids);
  assert.equal(filtered.categories.reduce((sum,item)=>sum+item.debit_cents,0),expected.total);
 }
});
test('公司名称中的通配符和正则符号按原文包含，不扩大命中范围',()=>{
 const names=['甲*公司','甲乙公司','甲[乙]公司','甲.公司','甲?公司'];
 const source={...result,rows:names.map((party,i)=>({...rows[0],id:String(i),party}))};
 for(const symbol of ['*','[乙]','.','?'])assert.equal(filterExpenseStatistics(source,{party:symbol}).count,1);
});

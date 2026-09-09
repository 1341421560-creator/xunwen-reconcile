import {escape as esc,money,el} from './format.js';
import {filterExpenseStatistics,expenseCompanyOptions} from './expense-statistics-filter.js';

export function createExpenseStatistics(state,request){
 let result=null,sequence=0,page=0,category='all',party='';
 function render(){
  if(!result)return;
  const filtered=filterExpenseStatistics(result,{category,party});
  const total=filtered.debit_cents;
  const labels=Object.fromEntries(result.categories.map(item=>[item.id,item.label]));
  el('expense-total').textContent=`${money(total)} 元`;
  el('expense-scope').textContent=`${result.start_date||'最早记录'} 至 ${result.end_date||'最新记录'}（含起止日） · ${category==='all'?'全部类别':labels[category]} · ${party.trim()?'交易对方包含：'+party.trim():'全部交易对方'} · ${filtered.count} 笔支出`;
  el('expense-summary').innerHTML=filtered.categories.map(item=>`<tr><td><button type="button" class="text-button" data-expense-filter="${esc(item.id)}">${esc(item.label)}</button></td><td class="number">${item.count}</td><td class="number">${money(item.debit_cents)}</td><td class="expense-share"><progress max="100" value="${total?item.debit_cents/total*100:0}"></progress><span>${total?(item.debit_cents/total*100).toFixed(1):'0.0'}%</span></td></tr>`).join('');
  const rows=filtered.rows;
  const last=Math.max(0,Math.ceil(rows.length/20)-1);page=Math.min(page,last);
  el('expense-table').innerHTML=rows.slice(page*20,(page+1)*20).map(row=>`<tr><td>${esc(row.date)}</td><td class="party">${esc(row.party||'银行费用')}<span class="subtext">${esc(row.summary)}</span></td><td class="number">${money(row.debit_cents)}</td><td>${esc(labels[row.expense_category])}<span class="subtext">${esc(row.expense_category_reason)}</span></td><td class="bank-note-text">${esc(row.manual_note||'—')}</td><td><button class="text-button" data-expense-category="${esc(row.id)}">调整分类</button><button class="text-button" data-bank-note="${esc(row.id)}">备注</button></td></tr>`).join('')||'<tr><td colspan="6" class="empty-row">没有符合当前时间、类别和公司条件的支出流水</td></tr>';
  el('expense-page-info').textContent=`${category==='all'?'全部类别':labels[category]}：${rows.length} 笔 · ${money(rows.reduce((sum,row)=>sum+row.debit_cents,0))} 元 · 第 ${page+1} / ${last+1} 页`;
  el('expense-prev').disabled=page===0;el('expense-next').disabled=page===last;
 }
 async function refresh(){
  const current=++sequence;
  if(state.page!=='expenses'||!state.result)return;
  el('expense-error').textContent='';el('expense-content').hidden=true;
  try{
   const data=await request('/api/expense-statistics',{start_date:el('expense-start').value,end_date:el('expense-end').value});
   if(current!==sequence)return;
   if(data.revision!==state.result.revision)throw new Error('账本已更新，请点击“刷新账本”后查看统计');
   result=data;el('expense-company-options').innerHTML=expenseCompanyOptions(result.rows).map(name=>`<option value="${esc(name)}"></option>`).join('');el('expense-content').hidden=false;render();
  }catch(error){if(current===sequence)el('expense-error').textContent=error.message;}
 }
 function bind(){
  el('expense-form').onsubmit=event=>{event.preventDefault();page=0;refresh();};
  el('expense-all-time').onclick=()=>{el('expense-start').value='';el('expense-end').value='';page=0;refresh();};
  el('expense-category-filter').onchange=()=>{category=el('expense-category-filter').value;page=0;render();};
  el('expense-party-filter').oninput=()=>{party=el('expense-party-filter').value;page=0;render();};
  el('expense-clear-filters').onclick=()=>{category='all';party='';el('expense-category-filter').value=category;el('expense-party-filter').value='';page=0;render();};
  el('expense-summary').onclick=event=>{const button=event.target.closest('[data-expense-filter]');if(button){category=button.dataset.expenseFilter;el('expense-category-filter').value=category;page=0;render();}};
  el('expense-prev').onclick=()=>{page--;render();};el('expense-next').onclick=()=>{page++;render();};
 }
 function onLedger(){
  el('expense-category-filter').innerHTML='<option value="all">全部类别</option>'+state.result.expense_categories.map(item=>`<option value="${esc(item.id)}">${esc(item.label)}</option>`).join('');
  el('expense-category-filter').value=category;
  el('expense-rule-help').textContent=state.result.expense_categories.filter(item=>item.keywords.length).map(item=>`${item.label}：${item.keywords.join('、')}`).join('；')+'。未命中或同时命中多个类别时归为其他支出，可手工更正。';
  refresh();
 }
 return {bind,refresh,onLedger};
}

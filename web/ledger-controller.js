import {request as transport,readUpload} from './api.js';
import {escape as esc,el} from './format.js';
import {renderLedger,renderTables,message,page} from './ledger-view.js';
import {renderConflicts} from './conflict-view.js';
import {showBank,showInvoice,showLegacy} from './ledger-detail.js';
import {openBankNote} from './bank-note-editor.js';
import {openExpenseCategory} from './expense-category-editor.js';
import {createExpenseStatistics} from './expense-statistics.js';
import {readSummaryKeywords} from './summary-keywords-editor.js';

import {createState} from './ledger-state.js';
import {createCompanyContext} from './company-context.js';
import {createCompanySwitcher} from './company-switcher.js';
import {createInvoiceSelection} from './invoice-selection.js';
import {installSaveGuard} from './save-guard.js';

export function createController(state){
 let importRevision=0,refreshAttempt=0,initializing=true;
 const context=createCompanyContext(transport,{storage:window.sessionStorage,onBusy:busy=>{state.companySaving=busy;for(const control of document.querySelectorAll('[data-invoice-select],#invoice-select-all'))control.disabled=busy||state.invoiceSelectionNeedsRefresh;el('company-select').disabled=busy;el('company-activate-submit').disabled=busy;}});
 const request=context.request;
 const switcher=createCompanySwitcher(context,switchCompany,activate);
 const reportError=error=>{if(!error.silent)message(error.message,true);};
 const expenseStats=createExpenseStatistics(state,request);
 const invoiceSelection=createInvoiceSelection(state,context,{apply,render:()=>renderTables(state)});
 function setReady(enabled){for(const control of document.querySelectorAll('[data-page],#import-open,#export-open,#month,#unfinished,#bank-search,#invoice-search,#invoice-month,[data-invoice-select],#invoice-select-all,#invoice-filter,#difference-filter,#offset-filter,#settings-form input,#settings-form textarea,#settings-form button,[data-pager],[data-bank],[data-invoice]'))control.disabled=!enabled;}
 function render(){switcher.preserveSettings(()=>renderLedger(state));}
 function apply(result){if(result.company_key!==context.key)return;if(state.result&&result.revision<state.result.revision)return;state.result=result;invoiceSelection.onLedger();setReady(true);if(state.month&&!result.months.includes(state.month))state.month='';render();switcher.preserveReview(()=>renderConflicts(state,boundMutate()));expenseStats.onLedger();}
 async function refresh(initial=false){const attempt=++refreshAttempt;try{if(initializing){const catalog=await transport('/api/companies');if(!catalog.companies.some(p=>p.key===context.key))context.select(catalog.default_company_key);initializing=false;}const data=await request('/api/bootstrap');if(attempt!==refreshAttempt)return;state.labels=data.statuses;state.history=data.history;switcher.render(data);if(data.setup_required){state.result=null;setReady(false);page(state,'company-setup');return;}if(initial)state.month=data.result.months[0]||'';apply(data.result);if(initial)page(state,'bank');if(data.migration_required)message('旧数据尚未迁移，请先运行迁移脚本。当前账本暂不接收导入。',true);}catch(error){if(attempt!==refreshAttempt)return;setReady(false);throw error;}}
 function boundMutate(){const scope=context.capture();const action=(path,payload,close=true)=>mutate(path,payload,close,scope);action.preview=payload=>scope.request('/api/invoice-offset/preview',payload);return action;}
 async function mutate(path,payload,close=true,scope=context.capture()){const result=await scope.request(path,payload);if(close&&el('detail-dialog').open)el('detail-dialog').close();if(path==='/api/settings')switcher.saved();switcher.savedRecord(path,payload);apply(result);message(path==='/api/bank-note'?'备注已保存到当前公司账本，重启后继续保留。':'已保存到账本，金额进度已更新。');}
 async function switchCompany(key){
  if(context.busy)return;context.select(key);refreshAttempt++;switcher.clear();Object.assign(state,createState());expenseStats.reset();
  for(const id of ['bank-search','invoice-search'])el(id).value='';el('invoice-filter').value='all';el('difference-filter').value='all';el('offset-filter').value='all';
  el('conflict-count').textContent='0';el('company-select').value=key;el('company').textContent='正在读取…';message('');setReady(false);page(state,'company-loading');
  try{await refresh(true);}catch(error){reportError(error);}
 }
 async function activate(legal_name){
  el('company-activate-error').textContent='';try{const data=await request('/api/companies/activate',{legal_name});switcher.clear();switcher.render(data);state.labels=data.statuses;state.history=data.history;apply(data.result);page(state,'bank');message('公司账本已启用，可以导入对应公司的文件。');}
  catch(error){if(!error.silent)el('company-activate-error').textContent=error.message;}
 }
 function openImport(){importRevision=state.result.revision;el('import-form').reset();el('import-error').textContent='';el('import-dialog').showModal();}
 function bind(){
  setReady(false);expenseStats.bind();switcher.bind();invoiceSelection.bind();installSaveGuard(window,()=>context.busy);page(state,'company-loading');
  document.addEventListener('click',async e=>{const target=e.target.closest('button');if(!target)return;try{
   if(target.dataset.page){page(state,target.dataset.page);expenseStats.refresh();}
   if(target.dataset.bankNote)openBankNote(state.result,target.dataset.bankNote,boundMutate());
   if(target.dataset.expenseCategory)openExpenseCategory(state.result,target.dataset.expenseCategory,boundMutate());
   if(target.dataset.close)el(target.dataset.close).close();
   if(target.dataset.filter){state.filter=target.dataset.filter;state.bankPage=0;renderTables(state);}
   if(target.dataset.bank)showBank(state,target.dataset.bank,boundMutate());
   if(target.dataset.invoice)showInvoice(state,target.dataset.invoice,boundMutate());
   if(target.dataset.legacy)showLegacy(await request('/api/restore',{session_id:target.dataset.legacy}),state.labels);
   if(target.dataset.pager){const [kind,direction]=target.dataset.pager.split(':');state[kind+'Page']+=Number(direction);renderTables(state);}
  }catch(error){reportError(error);}});
  el('refresh').onclick=async()=>{try{if(!await switcher.mayDiscard())return;switcher.clear();await refresh();message('已读取最新账本。');}catch(error){reportError(error);}};
  el('month').onchange=()=>{state.month=el('month').value;state.bankPage=0;render();};
  el('unfinished').onchange=()=>{state.unfinished=el('unfinished').checked;state.filter='all';state.bankPage=0;render();};
  for(const [id,key] of [['bank-search','bankSearch'],['invoice-search','invoiceSearch'],['invoice-filter','invoiceFilter'],['difference-filter','differenceFilter'],['offset-filter','offsetFilter']])el(id).addEventListener(id.endsWith('-filter')?'change':'input',()=>{state[key]=el(id).value;state[id.startsWith('bank')?'bankPage':'invoicePage']=0;renderTables(state);});
  el('import-open').onclick=openImport;
  el('import-form').onsubmit=async e=>{e.preventDefault();el('import-submit').disabled=true;el('import-error').textContent='';const scope=context.capture(),release=scope.hold();try{
   const payload={revision:importRevision};for(const [key,id] of [['bank','bank-file'],['invoice','invoice-file']]){const file=el(id).files[0];if(file)payload[key]=await readUpload(file,key);}if(!payload.bank&&!payload.invoice)throw new Error('请至少选择一份流水或进项发票清单');
   const result=await scope.request('/api/import',payload);apply(result);el('import-dialog').close();const b=result.last_import;message(`导入完成。流水新增 ${b.counts.bank.new} / 重复 ${b.counts.bank.duplicate} / 冲突 ${b.counts.bank.conflict}；发票新增 ${b.counts.invoices.new} / 重复 ${b.counts.invoices.duplicate} / 冲突 ${b.counts.invoices.conflict}。新关联 ${b.auto_matched_count} 笔，其中历史付款 ${b.historical_matched_count} 笔。`);
  }catch(error){if(!error.silent)el('import-error').textContent=error.message;}finally{release();el('import-submit').disabled=false;}};
  el('settings-form').onsubmit=async e=>{e.preventDefault();try{const aliases={};for(const line of el('aliases').value.split('\n').map(s=>s.trim()).filter(Boolean)){const parts=line.split('=');if(parts.length!==2||!parts.every(s=>s.trim()))throw new Error('名称映射每行使用：银行对方名称 = 发票销方名称');const [k,v]=parts.map(s=>s.trim());if(k in aliases&&aliases[k]!==v)throw new Error('同一银行名称不能对应多个销方');aliases[k]=v;}await mutate('/api/settings',{revision:state.result.revision,aliases,exclude_special:el('exclude-special').checked,custom_exclude_keywords:readSummaryKeywords(el('summary-keywords-editor'))},false);}catch(error){reportError(error);}};
  el('export-open').onclick=()=>{el('export-error').textContent='';el('export-files').innerHTML='';el('export-location').textContent='';el('export-scope').querySelector('[value="month"]').disabled=!state.month||state.unfinished;el('export-scope').value=state.month&&!state.unfinished?'month':'all';el('export-dialog').showModal();};
  el('export-submit').onclick=async()=>{el('export-submit').disabled=true;try{const month=el('export-scope').value==='month'?state.month:'';const result=await request('/api/export',{revision:state.result.revision,month});el('export-files').innerHTML=result.files.map(f=>`<a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.name)}</a>`).join('');el('export-location').textContent=result.directory;el('export-error').textContent='';}catch(error){if(!error.silent)el('export-error').textContent=error.message;}finally{el('export-submit').disabled=false;}};
 }
 return {bind,refresh};
}

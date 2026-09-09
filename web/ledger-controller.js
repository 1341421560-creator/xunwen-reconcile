import {request,readUpload} from './api.js';
import {escape as esc,el} from './format.js';
import {renderLedger,renderTables,message,page} from './ledger-view.js';
import {renderConflicts} from './conflict-view.js';
import {showBank,showInvoice,showLegacy} from './ledger-detail.js';

export function createController(state){
 let importRevision=0,refreshAttempt=0;
 function setReady(enabled){for(const control of document.querySelectorAll('#import-open,#export-open,#month,#unfinished,#bank-search,#invoice-search,#invoice-filter,#difference-filter,#settings-form input,#settings-form textarea,#settings-form button,[data-pager],[data-bank],[data-invoice]'))control.disabled=!enabled;}
 function apply(result){if(state.result&&result.company.id===state.result.company.id&&result.revision<state.result.revision)return;state.result=result;setReady(true);if(state.month&&!result.months.includes(state.month))state.month='';renderLedger(state);renderConflicts(state,mutate);}
 async function refresh(initial=false){const attempt=++refreshAttempt;try{const data=await request('/api/bootstrap');if(attempt!==refreshAttempt)return;state.labels=data.statuses;state.history=data.history;if(initial)state.month=data.result.months[0]||'';apply(data.result);if(data.migration_required)message('旧数据尚未迁移，请先运行迁移脚本。当前账本暂不接收导入。',true);}catch(error){if(attempt!==refreshAttempt)return;setReady(false);throw error;}}
 async function mutate(path,payload,close=true){const result=await request(path,payload);if(close&&el('detail-dialog').open)el('detail-dialog').close();apply(result);message('已保存到账本，金额进度已更新。');}
 function openImport(){importRevision=state.result.revision;el('import-form').reset();el('import-error').textContent='';el('import-dialog').showModal();}
 function bind(){
  setReady(false);
  document.addEventListener('click',async e=>{const target=e.target.closest('button');if(!target)return;try{
   if(target.dataset.page)page(state,target.dataset.page);
   if(target.dataset.close)el(target.dataset.close).close();
   if(target.dataset.filter){state.filter=target.dataset.filter;state.bankPage=0;renderTables(state);}
   if(target.dataset.bank)showBank(state,target.dataset.bank,mutate);
   if(target.dataset.invoice)showInvoice(state,target.dataset.invoice,mutate);
   if(target.dataset.legacy)showLegacy(await request('/api/restore',{session_id:target.dataset.legacy}),state.labels);
   if(target.dataset.pager){const [kind,direction]=target.dataset.pager.split(':');state[kind+'Page']+=Number(direction);renderTables(state);}
  }catch(error){message(error.message,true);}});
  el('refresh').onclick=async()=>{try{await refresh();message('已读取最新账本。');}catch(error){message(error.message,true);}};
  el('month').onchange=()=>{state.month=el('month').value;state.bankPage=0;renderLedger(state);};
  el('unfinished').onchange=()=>{state.unfinished=el('unfinished').checked;state.filter='all';state.bankPage=0;renderLedger(state);};
  for(const [id,key] of [['bank-search','bankSearch'],['invoice-search','invoiceSearch'],['invoice-filter','invoiceFilter'],['difference-filter','differenceFilter']])el(id).addEventListener(id.endsWith('-filter')?'change':'input',()=>{state[key]=el(id).value;state[id.startsWith('bank')?'bankPage':'invoicePage']=0;renderTables(state);});
  el('import-open').onclick=openImport;
  el('import-form').onsubmit=async e=>{e.preventDefault();el('import-submit').disabled=true;el('import-error').textContent='';try{
   const payload={revision:importRevision};for(const [key,id] of [['bank','bank-file'],['invoice','invoice-file']]){const file=el(id).files[0];if(file)payload[key]=await readUpload(file);}if(!payload.bank&&!payload.invoice)throw new Error('请至少选择一份流水或进项发票清单');
   const result=await request('/api/import',payload);apply(result);el('import-dialog').close();const b=result.last_import;message(`导入完成。流水新增 ${b.counts.bank.new} / 重复 ${b.counts.bank.duplicate} / 冲突 ${b.counts.bank.conflict}；发票新增 ${b.counts.invoices.new} / 重复 ${b.counts.invoices.duplicate} / 冲突 ${b.counts.invoices.conflict}。新关联 ${b.auto_matched_count} 笔，其中历史付款 ${b.historical_matched_count} 笔。`);
  }catch(error){el('import-error').textContent=error.message;}finally{el('import-submit').disabled=false;}};
  el('settings-form').onsubmit=async e=>{e.preventDefault();try{const aliases={};for(const line of el('aliases').value.split('\n').map(s=>s.trim()).filter(Boolean)){const parts=line.split('=');if(parts.length!==2||!parts.every(s=>s.trim()))throw new Error('名称映射每行使用：银行对方名称 = 发票销方名称');const [k,v]=parts.map(s=>s.trim());if(k in aliases&&aliases[k]!==v)throw new Error('同一银行名称不能对应多个销方');aliases[k]=v;}await mutate('/api/settings',{revision:state.result.revision,aliases,exclude_special:el('exclude-special').checked},false);}catch(error){message(error.message,true);}};
  el('export-open').onclick=()=>{el('export-error').textContent='';el('export-files').innerHTML='';el('export-location').textContent='';el('export-scope').querySelector('[value="month"]').disabled=!state.month||state.unfinished;el('export-scope').value=state.month&&!state.unfinished?'month':'all';el('export-dialog').showModal();};
  el('export-submit').onclick=async()=>{el('export-submit').disabled=true;try{const month=el('export-scope').value==='month'?state.month:'';const result=await request('/api/export',{revision:state.result.revision,month});el('export-files').innerHTML=result.files.map(f=>`<a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.name)}</a>`).join('');el('export-location').textContent=result.directory;el('export-error').textContent='';}catch(error){el('export-error').textContent=error.message;}finally{el('export-submit').disabled=false;}};
 }
 return {bind,refresh};
}

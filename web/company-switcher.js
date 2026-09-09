import {escape as esc,el} from './format.js';
import {createFormDrafts} from './form-drafts.js';

export function createCompanySwitcher(context,onSwitch,onActivate){
 const drafts=createFormDrafts();
 function bind(){
  drafts.bind();
  el('company-select').onchange=async()=>{const key=el('company-select').value;el('company-select').value=context.key;if(key===context.key)return;if(await mayDiscard())await onSwitch(key);};
  el('company-activate-form').onsubmit=async e=>{e.preventDefault();if(!el('company-confirm').checked)return;await onActivate(el('company-legal-name').value);};
 }
 async function mayDiscard(){
  if(context.busy)return false;if(!drafts.changed())return true;
  const dialog=el('company-switch-dialog');dialog.returnValue='stay';dialog.showModal();
  return new Promise(resolve=>{
   const finish=discard=>{dialog.onclick=null;dialog.oncancel=null;dialog.close();resolve(discard);};
   dialog.onclick=event=>{const button=event.target.closest('button[value]');if(button){event.preventDefault();finish(button.value==='discard');}};
   dialog.oncancel=event=>{event.preventDefault();finish(false);};
  });
 }
 function render(data){
  el('company-select').innerHTML=data.companies.map(p=>`<option value="${esc(p.key)}">${esc(p.label)}${p.error?' · 数据异常':p.initialized?'':' · 未启用'}</option>`).join('');
  el('company-select').value=context.key;el('company-select').disabled=context.busy;
  const profile=data.company_profile;el('company').textContent=profile.legal_name||`${profile.label} · 未启用`;
  el('company-activate-title').textContent=`启用${profile.label}的独立账本`;
  el('company-legal-name').placeholder=profile.name_hint;
 }
 function clear(){drafts.clear();for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();el('company-activate-form').reset();el('company-activate-error').textContent='';}
 function saved(){drafts.saved(el('settings-form'));}
 return {bind,render,clear,saved,mayDiscard,preserveSettings:drafts.preserveSettings,preserveReview:drafts.preserveReview,savedRecord:drafts.savedRecord};
}

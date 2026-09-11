import {escape as esc,el} from './format.js';

export function createCompanyNameEditor(state,context,{mayDiscard,onSaved}){
 function render(){
  const result=state.result;
  el('company-name-open').hidden=!(result&&state.companyNameCorrection?.allowed&&result.stats.bank_count===0&&result.stats.invoice_count===0&&!result.batches.length);
 }
 async function open(){
  if(!await mayDiscard())return;
  const view=state.result,scope=context.capture(),dialog=el('company-name-dialog');
  dialog.innerHTML=`<form><div class="dialog-heading"><h2>更正公司全称</h2><button class="icon-button" type="button" data-close="company-name-dialog" aria-label="关闭">×</button></div><p>当前绑定：<strong>${esc(view.company.name)}</strong></p><p class="field-hint">仅限尚未导入数据的公司。保存前自动备份原账本，保留公司编号、规则设置和更正日志。</p><label for="company-name-input">正确的完整公司名称</label><input id="company-name-input" type="text" maxlength="200" required autocomplete="organization"><label class="check-row"><input id="company-name-confirm" type="checkbox" required> 我已核对正确全称，与银行户名、进项发票清单抬头一致</label><p class="form-error" role="alert"></p><button class="primary">确认更正</button></form>`;
  el('company-name-input').value=view.company.name;
  let saving=false;
  dialog.oncancel=event=>{if(saving)event.preventDefault();};
  dialog.querySelector('form').onsubmit=async event=>{
   event.preventDefault();if(saving||!el('company-name-confirm').checked)return;
   saving=true;dialog.querySelector('.form-error').textContent='';
   for(const button of dialog.querySelectorAll('button'))button.disabled=true;
   try{
    const data=await scope.request('/api/companies/correct-name',{revision:view.revision,company_id:view.company.id,original_name:view.company.name,legal_name:el('company-name-input').value,confirmed:true});
    dialog.close();onSaved(data);
   }catch(error){if(!error.silent)dialog.querySelector('.form-error').textContent=error.message;}
   finally{saving=false;for(const button of dialog.querySelectorAll('button'))button.disabled=false;}
  };
  dialog.showModal();
 }
 return {render,bind:()=>{el('company-name-open').onclick=open;}};
}

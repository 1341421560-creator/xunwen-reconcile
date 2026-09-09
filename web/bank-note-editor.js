import {escape as esc,el} from './format.js';

export function noteCell(bank){
 return `<td class="bank-note-cell"><span class="bank-note-text">${esc(bank.manual_note||'—')}</span><button class="text-button" data-bank-note="${esc(bank.id)}">${bank.manual_note?'编辑备注':'添加备注'}</button></td>`;
}

export function openBankNote(view,id,onMutation){
 const bank=view.bank.find(row=>row.id===id);if(!bank)return;
 const dialog=el('bank-note-dialog');
 dialog.innerHTML=`<form><div class="dialog-heading"><h2>流水手工备注</h2><button class="icon-button" type="button" data-close="bank-note-dialog" aria-label="关闭">×</button></div><p>${esc(bank.date)} · ${esc(bank.party||'银行费用')}</p><p class="field-hint">${esc(bank.summary)}</p><label for="bank-note-input">备注</label><textarea id="bank-note-input" rows="5" maxlength="${view.manual_note_max_length}" placeholder="填写需要跟进的事项或其他说明，留空保存可清空备注"></textarea><p class="field-hint">备注独立保存，不改变摘要、匹配关系或支出分类。</p><p class="form-error" role="alert"></p><button class="primary">保存备注</button></form>`;
 el('bank-note-input').value=bank.manual_note||'';
 dialog.querySelector('form').onsubmit=async event=>{event.preventDefault();const button=dialog.querySelector('.primary');button.disabled=true;try{await onMutation('/api/bank-note',{revision:view.revision,bank_id:id,note:el('bank-note-input').value});dialog.close();}catch(error){dialog.querySelector('.form-error').textContent=error.message;}finally{button.disabled=false;}};
 dialog.showModal();
}

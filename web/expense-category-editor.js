import {escape as esc,el} from './format.js';

export function openExpenseCategory(view,id,onMutation){
 const bank=view.bank.find(row=>row.id===id);if(!bank||bank.direction!=='支出')return;
 const dialog=el('expense-category-dialog');
 dialog.innerHTML=`<form><div class="dialog-heading"><h2>调整支出分类</h2><button type="button" class="icon-button" data-close="expense-category-dialog" aria-label="关闭">×</button></div><p>${esc(bank.date)} · ${esc(bank.party)}</p><p>${esc(bank.summary)}</p><label>支出类别<select class="expense-category-select"><option value="auto">按摘要自动分类</option>${view.expense_categories.map(item=>`<option value="${esc(item.id)}">${esc(item.label)}</option>`).join('')}</select></label><label>分类说明（可选）<textarea class="expense-category-note" maxlength="1000" rows="2"></textarea></label><p class="field-hint">分类只影响支出统计；选择“按摘要自动分类”可撤回手工指定。</p><p class="form-error" role="alert"></p><button class="primary">保存支出分类</button></form>`;
 dialog.querySelector('select').value=bank.expense_classification?.category||'auto';
 dialog.querySelector('textarea').value=bank.expense_classification?.note||'';
 dialog.querySelector('form').onsubmit=async event=>{event.preventDefault();const button=dialog.querySelector('.primary');button.disabled=true;try{await onMutation('/api/expense-category',{revision:view.revision,bank_id:id,category:dialog.querySelector('select').value,note:dialog.querySelector('textarea').value});dialog.close();}catch(error){dialog.querySelector('.form-error').textContent=error.message;}finally{button.disabled=false;}};
 dialog.showModal();
}

import {escape as esc,money} from './format.js';

export function renderRelations(container,view,allocations,onMutation){
 const banks=new Map(view.bank.map(row=>[row.id,row])),invoices=new Map(view.invoices.map(row=>[row.id,row]));
 container.innerHTML=`<h2>金额关联明细</h2><div class="table-scroll"><table class="dialog-table"><thead><tr><th>付款日期 / 对方</th><th>开票日期 / 号码</th><th>本次核销</th><th>状态与依据</th><th>操作</th></tr></thead><tbody>${allocations.map(a=>`<tr><td>${esc(banks.get(a.bank_id).date)}<span class="subtext">${esc(banks.get(a.bank_id).party)}</span></td><td>${esc(invoices.get(a.invoice_id).date)}<span class="subtext">${esc(invoices.get(a.invoice_id).number)}</span></td><td class="number">${money(a.amount_cents)}</td><td>${a.state==='active'?'有效':'已撤回'}<span class="subtext">${esc(a.note)}${a.revoke_note?'<br>撤回：'+esc(a.revoke_note):''}</span></td><td>${a.state==='active'?`<button type="button" class="text-button" data-revoke="${esc(a.id)}">撤回关联</button>`:'—'}</td></tr>`).join('')||'<tr><td colspan="5" class="empty-row">尚未建立金额关联</td></tr>'}</tbody></table></div><div class="relation-revoke-area"></div>`;
 for(const button of container.querySelectorAll('[data-revoke]'))button.onclick=()=>{
  const area=container.querySelector('.relation-revoke-area');
  area.innerHTML='<h3>撤回金额关联</h3><p class="field-hint">原关联与依据会保留，撤回后该组合不再自动重新关联；之后仍可人工确认。</p><label>撤回依据<textarea rows="2" class="relation-revoke-note"></textarea></label><button type="button" class="primary relation-revoke-save">确认撤回本条关联</button><p class="form-error" role="alert"></p>';
  const save=area.querySelector('button');save.onclick=async()=>{save.disabled=true;try{await onMutation('/api/undo',{revision:view.revision,allocation_id:button.dataset.revoke,note:area.querySelector('textarea').value});}catch(error){area.querySelector('.form-error').textContent=error.message;save.disabled=false;}};
  area.scrollIntoView({block:'nearest'});
 };
}

import {renderRelations} from './allocation-relations.js';
import {escape as esc,money} from './format.js';

const editableStatuses=['pending','carry_forward','amount_error'];

export function differenceBadge(invoice,view){
 if(invoice.difference_status==='none')return '';
 return `<span class="badge difference-${esc(invoice.difference_status)}">${esc(view.difference_labels[invoice.difference_status])}</span>`;
}

export function differenceSummary(invoice,view){
 if(invoice.difference_status==='none')return '<span class="muted">—</span>';
 return `${differenceBadge(invoice,view)}<span class="subtext">${esc(invoice.difference_note)}</span><span class="subtext">${esc(invoice.difference_updated_at||'历史记录自动识别，尚未人工确认')}</span>`;
}

export function differenceEditor(container,invoice,view,onMutation){
 if(invoice.difference_status==='none'){container.innerHTML='';return;}
 const editable=invoice.difference_cents>0;
 container.innerHTML=`<div class="difference-panel"><h2>发票差额处理</h2>${differenceSummary(invoice,view)}<p>未分配余额 <strong>${money(invoice.remaining_cents)}</strong> 元 · 可继续分配 <strong>${money(invoice.distributable_cents)}</strong> 元</p><p class="field-hint">差额待确认或开票金额有误时，余额暂停自动和手动分配。已保存的关联保留；确认跨月使用后，仍须满足其他分配条件。</p>${editable?`<label>差额状态<select class="difference-select" aria-label="差额状态">${editableStatuses.map(s=>`<option value="${s}" ${s===invoice.difference_status?'selected':''}>${esc(view.difference_labels[s])}</option>`).join('')}</select></label><label>处理依据<textarea class="difference-note" rows="2" maxlength="1000" aria-label="差额处理依据" placeholder="填写与对方核实的结果，或需要继续跟进的原因"></textarea></label><button type="button" class="primary difference-save">保存差额状态</button><p class="form-error difference-error" role="alert"></p>`:'<p class="field-hint">余额已结清，原处理依据保留。若撤回关联或冲红使余额增加，需重新确认。</p>'}</div>`;
 if(!editable)return;
 const button=container.querySelector('.difference-save');
 button.onclick=async()=>{
  const error=container.querySelector('.difference-error');
  const note=container.querySelector('.difference-note').value.trim();
  if(!note){error.textContent='请填写差额处理依据';return;}
  button.disabled=true;error.textContent='';
  try{await onMutation('/api/invoice-difference',{revision:view.revision,invoice_id:invoice.id,status:container.querySelector('.difference-select').value,note});}
  catch(reason){error.textContent=reason.message;button.disabled=false;}
 };
}

export function renderDifferences(container,view,onMutation){
 const pending=view.invoices.filter(i=>i.difference_blocked);
 container.innerHTML=pending.map(i=>`<article class="conflict-card difference-card"><strong>${esc(i.party)} · 未分配余额 ${money(i.remaining_cents)} 元</strong><p class="compact-text">${esc(i.date)} · ${esc(i.number)}</p><button class="text-button" data-invoice="${esc(i.id)}">查看发票与关联</button><div data-difference-editor="${esc(i.id)}"></div><div data-difference-relations="${esc(i.id)}" class="review-relations"></div></article>`).join('')||'<p class="muted">没有待处理的发票差额</p>';
 for(const target of container.querySelectorAll('[data-difference-relations]'))renderRelations(target,view,view.allocations.filter(a=>a.invoice_id===target.dataset.differenceRelations),(path,payload)=>onMutation(path,payload,false));
 const invoices=new Map(pending.map(i=>[i.id,i]));
 for(const target of container.querySelectorAll('[data-difference-editor]'))differenceEditor(target,invoices.get(target.dataset.differenceEditor),view,(path,payload)=>onMutation(path,payload,false));
}

export function differencePreview(invoice,allocated,view){
 const remaining=invoice.remaining_cents-allocated;
 if(remaining<=0)return invoice.difference_status==='none'?'':`${view.difference_labels.cleared}，保留原确认记录。`;
 const status=invoice.difference_status==='carry_forward'?'carry_forward':'pending';
 return `保存后未分配 ${money(remaining)} 元 · ${view.difference_labels[status]}${status==='pending'?'；余额暂停继续分配，请到发票详情确认原因。':'。'}`;
}

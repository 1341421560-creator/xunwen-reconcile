import {escape as esc,money} from './format.js';

export function offsetSummary(invoice,view){
 if(!invoice.offset_status||invoice.offset_status==='none'&&!invoice.offset_source_hold)return '';
 const links=(invoice.offset_invoice_ids||[]).map((id,index)=>`<button class="text-button" data-invoice="${esc(id)}">${esc(invoice.offset_invoice_numbers[index])}</button>`).join(' · ');
 return `<div class="offset-summary"><span class="badge">${esc(view.offset_labels[invoice.offset_status])}</span>${invoice.red?'':`<span class="subtext">累计冲红 ${money(invoice.offset_cents)} 元 · 净额 ${money(invoice.net_amount_cents)} 元</span>`}${links?`<span class="subtext">对应票：${links}</span>`:''}${invoice.offset_source_hold?`<span class="subtext balance-warning">${esc(invoice.offset_source_hold)}</span>`:''}</div>`;
}

export function offsetCandidates(view,red,search=''){
 const query=search.trim().toLowerCase();
 return view.invoices.filter(i=>!i.red&&i.amount_cents>0&&i.party_match_key&&i.party_match_key===red.party_match_key&&i.currency===red.currency&&
  !i.blocking_invalid&&!i.offset_conflict&&[i.number,i.date,i.party].join(' ').toLowerCase().includes(query));
}

export function offsetPreviewHtml(preview,view){
 const details=preview.allocations.map(a=>`<tr><td>${esc(a.bank_date)} ${esc(a.bank_party)}<br>${esc(a.bank_reference)}</td><td>${money(a.amount_cents)} 元</td><td><button class="text-button" data-bank="${esc(a.bank_id)}">查看／撤回关联</button></td></tr>`).join('');
 return `<div class="offset-preview"><p>本次冲红 <strong>${money(preview.amount_cents)}</strong> 元；原票净额 <strong>${money(preview.previous_net_cents)} → ${money(preview.net_amount_cents)}</strong> 元。</p><p>已关联 ${money(preview.allocated_cents)} 元 · 冲红后未分配余额 ${money(preview.remaining_cents)} 元</p>${preview.can_save?`<p>${esc(view.difference_labels[preview.difference_status])}</p>`:`<p class="form-error">已关联金额超出 ${money(preview.excess_cents)} 元。请先撤回足够的整条付款关联，再重新预览。</p>`}${preview.source_hold?`<p class="balance-warning">${esc(preview.source_hold)}</p>`:''}${details?`<details ${preview.can_save?'':'open'}><summary>原票当前付款关联</summary><table class="dialog-table"><tbody>${details}</tbody></table></details>`:''}</div>`;
}

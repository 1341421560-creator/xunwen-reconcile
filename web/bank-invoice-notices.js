import {escape as esc,money} from './format.js';

function itemNotice(item,bank){
 const source=item.relation_source==='revoked'?`<span class="subtext">原关联已撤回，依据：${esc(item.revoke_note||'未记录')}<br>${esc(item.revoked_at)}</span>`:item.relation_source==='same_party'?'<span class="subtext">仅为同公司发票，尚未确认对应关系。</span>':'';
 return `<div class="bank-invoice-notice"><span class="subtext balance-warning">${esc(item.summary)}</span><span class="subtext">${esc(item.party)}<br>${esc(item.invoice_date)} · ${esc(item.invoice_number)}</span>${source}<span class="subtext">付款未核销：${money(bank.remaining_cents)} 元<br>发票未分配余额：${money(item.remaining_cents)} 元</span><button class="text-button" data-invoice="${esc(item.invoice_id)}">查看发票／处理差额</button></div>`;
}

export function bankInvoiceNotices(bank){
 const notices=bank.related_invoice_notices||[];
 const offsets=(bank.invoice_offset_notices||[]).map(item=>`<div class="bank-invoice-notice"><span class="subtext">${esc(item.source_label||'发票')} ${esc(item.number)} · 累计冲红 ${money(item.offset_cents)} 元 · 净额 ${money(item.net_amount_cents)} 元</span><button class="text-button" data-invoice="${esc(item.invoice_id)}">查看冲红记录</button></div>`).join('');
 if(!notices.length&&!offsets)return '';
 return `<div class="bank-invoice-notices">${notices.length?itemNotice(notices[0],bank):''}${notices.length>1?`<details><summary>另有 ${notices.length-1} 张相关发票</summary>${notices.slice(1).map(item=>itemNotice(item,bank)).join('')}</details>`:''}${offsets}</div>`;
}

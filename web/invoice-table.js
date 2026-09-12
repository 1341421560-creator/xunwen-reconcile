import {escape as esc,money,el} from './format.js';
import {offsetSummary} from './invoice-offset-display.js';
import {differenceSummary} from './invoice-difference.js';
import {filterInvoices,selectionSummary,visibleSelection} from './invoice-filter.js';
import {paginate,renderPager} from './pagination.js';

export function renderInvoiceTable(state,badge){
 const view=state.result;
 el('offset-filter').innerHTML='<option value="all">全部冲红状态</option>'+Object.entries(view.offset_labels||{}).map(([key,label])=>`<option value="${esc(key)}">${esc(label)}</option>`).join('');el('offset-filter').value=state.offsetFilter||'all';
 const months=view.invoice_months||[...new Set(view.invoices.map(invoice=>invoice.date.slice(0,7)))].sort().reverse();
 if(state.invoiceMonth&&!months.includes(state.invoiceMonth))state.invoiceMonth='';
 el('invoice-month').innerHTML='<option value="">全部月份</option>'+months.map(month=>`<option value="${esc(month)}">${esc(month)}</option>`).join('');
 el('invoice-month').value=state.invoiceMonth;
 const invoices=filterInvoices(view.invoices,state),summary=selectionSummary(invoices);
 const size=view.invoice_page_size||100,info=paginate(invoices,state.invoicePage,size);
 state.invoicePage=info.index;renderPager('invoice',info,` · 每页 ${size} 张`);
 const disabled=state.companySaving||state.invoiceSelectionBusy||state.invoiceSelectionNeedsRefresh;
 const pending=state.invoiceSelectionPending;
 const selectedCount=invoices.filter(invoice=>visibleSelection(invoice,pending)).length;
 const all=el('invoice-select-all');all.checked=!!invoices.length&&selectedCount===invoices.length;all.indeterminate=selectedCount>0&&selectedCount<invoices.length;all.disabled=disabled||!invoices.length;
 el('invoice-selection-count').textContent=`筛选共 ${summary.count} 张 · 已勾选 ${summary.selectedCount} 张（含所有分页）`;
 el('invoice-selection-total').textContent=`${money(summary.totalCents)} 元`;
 const feedback=state.invoiceSelectionFeedback;
 el('invoice-selection-status').textContent=feedback?.text||'勾选状态随当前公司账本保存。';
 el('invoice-selection-status').classList.toggle('form-error',!!feedback?.error);
 el('invoice-table').innerHTML=info.rows.map(invoice=>`<tr><td class="invoice-check-cell"><input type="checkbox" data-invoice-select="${esc(invoice.id)}" aria-label="计入合计：${esc(invoice.number)} ${esc(invoice.party)}" ${visibleSelection(invoice,pending)?'checked':''} ${disabled?'disabled':''}></td><td>${esc(invoice.date)}</td><td class="party">${esc(invoice.party)}<span class="subtext monospace">${esc(invoice.number)}</span></td><td class="number">${money(invoice.amount_cents)}</td><td class="number">${money(invoice.allocated_cents)}</td><td class="number">${money(invoice.remaining_cents)}<span class="muted-amount">可分配 ${money(invoice.distributable_cents)}</span></td><td>${invoice.red||invoice.status==='offset_full'?'':badge(invoice,state.labels)}${offsetSummary(invoice,view)}<span class="subtext">${esc(invoice.hold_reasons.join('；'))}</span></td><td class="difference-cell">${differenceSummary(invoice,view)}</td><td><button class="text-button" data-invoice="${esc(invoice.id)}">详情</button></td></tr>`).join('')||'<tr><td colspan="9" class="empty-row">暂无符合条件的发票</td></tr>';
}

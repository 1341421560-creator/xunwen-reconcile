export function filterInvoices(invoices,state){
 const search=state.invoiceSearch.toLowerCase();
 return invoices.filter(invoice=>(!state.invoiceMonth||invoice.date.startsWith(state.invoiceMonth))&&
  [invoice.party,invoice.number,invoice.date,invoice.id].join(' ').toLowerCase().includes(search)&&
  (state.invoiceFilter==='all'||state.invoiceFilter==='available'&&invoice.distributable_cents>0||state.invoiceFilter==='used'&&invoice.allocated_cents>0||state.invoiceFilter==='review'&&invoice.status==='review'&&(!['linked','reviewed'].includes(invoice.offset_status)||invoice.blocking_invalid||invoice.offset_conflict))&&
  (state.differenceFilter==='all'||invoice.difference_status===state.differenceFilter)&&
  (!state.offsetFilter||state.offsetFilter==='all'||invoice.offset_status===state.offsetFilter));
}

export function selectionSummary(invoices){
 const selected=invoices.filter(invoice=>invoice.include_in_total!==false);
 return {count:invoices.length,selectedCount:selected.length,totalCents:selected.reduce((total,invoice)=>total+invoice.amount_cents,0)};
}

export function visibleSelection(invoice,pending){
 return pending?.ids.has(invoice.id)?pending.selected:invoice.include_in_total!==false;
}

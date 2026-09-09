import {filterInvoices} from './invoice-filter.js';

export function createInvoiceSelection(state,context,{apply,render}){
 async function save(ids,selected){
  if(!ids.length||state.invoiceSelectionBusy||context.busy||state.invoiceSelectionNeedsRefresh){render();return;}
  const scope=context.capture(),release=scope.hold();
  const revision=state.result.revision;
  state.invoiceSelectionBusy=true;state.invoiceSelectionPending={ids:new Set(ids),selected};
  state.invoiceSelectionFeedback={text:'正在保存勾选，合计将在保存成功后更新…',error:false};render();
  try{
   const result=await scope.request('/api/invoice-selection',{revision,invoice_ids:ids,selected});
   apply(result);state.invoiceSelectionFeedback={text:'已保存勾选，重启或恢复账本后继续保留。',error:false};
  }catch(error){
   if(error.silent)return;
   try{
    apply(await scope.request('/api/ledger'));
    state.invoiceSelectionFeedback={text:`未收到保存成功确认：${error.message}。已重新读取账本中的勾选状态，请核对后重新选择。`,error:true};
   }catch(readError){
    if(readError.silent)return;
    state.invoiceSelectionNeedsRefresh=true;
    state.invoiceSelectionFeedback={text:'保存结果尚未确认，当前显示上次读取的状态。请刷新账本确认后继续勾选。',error:true};
   }
  }finally{
   state.invoiceSelectionBusy=false;state.invoiceSelectionPending=null;release();render();
  }
 }
 function bind(){
  document.getElementById('invoice-month').onchange=event=>{state.invoiceMonth=event.target.value;state.invoicePage=0;render();};
  document.getElementById('invoice-select-all').onchange=event=>save(filterInvoices(state.result.invoices,state).map(invoice=>invoice.id),event.target.checked);
  document.getElementById('invoice-table').addEventListener('change',event=>{const id=event.target.dataset.invoiceSelect;if(id)save([id],event.target.checked);});
 }
 function onLedger(){
  if(state.invoiceSelectionNeedsRefresh&&!state.invoiceSelectionBusy)state.invoiceSelectionFeedback={text:'已读取最新勾选状态，可以继续选择。',error:false};
  state.invoiceSelectionNeedsRefresh=false;
 }
 return {bind,save,onLedger};
}

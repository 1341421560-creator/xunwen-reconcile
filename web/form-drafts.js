export function createFormDrafts(){
 const dirty=new Set();
 function bind(){
  for(const event of ['input','change'])document.addEventListener(event,e=>{
   const form=e.target.closest('#settings-form,#company-activate-form,dialog,#page-conflicts .conflict-card');
   if(form&&form.id!=='company-switch-dialog'&&form.id!=='export-dialog')dirty.add(form);
  });
  document.addEventListener('close',e=>dirty.delete(e.target),true);
 }
 function changed(){return [...dirty].some(form=>form.isConnected&&(form.tagName==='DIALOG'?form.open:true));}
 function preserveSettings(render){
  const form=document.getElementById('settings-form');
  const values=dirty.has(form)?[...form.querySelectorAll('input,textarea')].map(input=>({id:input.id,value:input.value,checked:input.checked})):[];
  render();for(const item of values){const input=document.getElementById(item.id);if(input){input.value=item.value;input.checked=item.checked;}}
 }
 function reviewKey(card){return card.dataset.conflict?'conflict:'+card.dataset.conflict:card.dataset.exception?'exception:'+card.dataset.exception:card.querySelector('[data-difference-editor]')?.dataset.differenceEditor;}
 function preserveReview(render){
  const drafts=[...dirty].filter(card=>card.isConnected&&card.classList.contains('conflict-card')).map(card=>({key:reviewKey(card),values:[...card.querySelectorAll('input,select,textarea')].map(input=>({value:input.value,checked:input.checked}))}));
  render();
  for(const form of dirty)if(!form.isConnected)dirty.delete(form);
  for(const draft of drafts){const card=[...document.querySelectorAll('#page-conflicts .conflict-card')].find(item=>reviewKey(item)===draft.key);if(!card)continue;const controls=[...card.querySelectorAll('input,select,textarea')];if(controls.length!==draft.values.length)continue;controls.forEach((control,i)=>{control.value=draft.values[i].value;control.checked=draft.values[i].checked;});dirty.add(card);}
 }
 function savedRecord(path,payload){
  for(const card of dirty){
   const key=card.classList.contains('conflict-card')?reviewKey(card):null;
   if(path==='/api/conflict'&&key==='conflict:'+payload.conflict_id||path==='/api/exception'&&key==='exception:'+payload.invoice_id||path==='/api/invoice-difference'&&key===payload.invoice_id)dirty.delete(card);
  }
 }
 return {bind,changed,preserveSettings,preserveReview,savedRecord,clear:()=>dirty.clear(),saved:form=>dirty.delete(form)};
}

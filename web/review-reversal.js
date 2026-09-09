export function reviewReversal(container,view,kind,id,onMutation){
 container.innerHTML='<button type="button" class="small-button review-reverse-open">撤回本项核对结论</button><div class="review-reverse-form" hidden></div>';
 container.querySelector('button').onclick=()=>{
  const form=container.querySelector('.review-reverse-form');form.hidden=false;
  form.innerHTML='<p class="field-hint">撤回后重新进入待核对，原结论和撤回依据保留。接受过新版本或新增入账的记录需先撤回有效金额关联；异常票重新待复核后会恢复对相关记录的匹配限制。</p><label>撤回复核依据<textarea rows="2" class="review-reverse-note"></textarea></label><button type="button" class="primary review-reverse-save">确认撤回核对结论</button><p class="form-error" role="alert"></p>';
  const save=form.querySelector('button');save.onclick=async()=>{save.disabled=true;try{await onMutation(kind==='conflict'?'/api/conflict-undo':'/api/exception-undo',{revision:view.revision,[kind==='conflict'?'conflict_id':'invoice_id']:id,note:form.querySelector('textarea').value},false);}catch(error){form.querySelector('.form-error').textContent=error.message;save.disabled=false;}};
 };
}

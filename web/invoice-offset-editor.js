import {escape as esc,money} from './format.js';
import {offsetCandidates,offsetPreviewHtml,offsetSummary} from './invoice-offset-display.js';

export function offsetEditor(container,invoice,view,onMutation,{close=true}={}){
 const history=(view.invoice_offsets||[]).filter(r=>r.red_invoice_id===invoice.id||r.blue_invoice_id===invoice.id);
 const mayPair=invoice.red&&invoice.offset_status==='pending'&&!invoice.blocking_invalid&&!invoice.offset_conflict;
 const oldReview=invoice.red&&invoice.exception_review;
 const blocked=invoice.blocking_invalid|| (invoice.offset_conflict?'请先处理来源版本冲突':'');
 if(!invoice.red&&!history.length){container.innerHTML='';return;}
 const invoices=new Map(view.invoices.map(i=>[i.id,i]));
 container.innerHTML=`<div class="difference-panel offset-editor"><h2>${invoice.red?'红票对应原票':'冲红记录'}</h2>${offsetSummary(invoice,view)}${mayPair?`<label>搜索原票<input class="offset-search" type="search" aria-label="搜索原票" placeholder="发票号码、日期或销方"></label><label>选择原票<select class="offset-blue" aria-label="选择原票"></select></label><p class="field-hint">仅列出同销方、同币种且无来源冲突或真实异常的原票。当前红票引起的连带待复核不影响选择。</p><label>冲红依据<textarea class="offset-note" rows="2" maxlength="1000" aria-label="冲红依据" placeholder="填写确认这张红票对应所选原票的依据"></textarea></label><button class="small-button offset-preview-button">预览冲红</button><div class="offset-preview-area" aria-live="polite"></div><button class="primary offset-save" hidden>确认冲红</button>`:''}${blocked?`<p class="form-error">${esc(blocked)}</p>`:''}${oldReview?`<p>已有复核结论：${esc(invoice.exception_review.note)}。未对账本执行冲减。</p><label>撤回复核依据<textarea class="offset-review-note" rows="2" maxlength="1000" aria-label="撤回旧复核依据"></textarea></label><button class="small-button offset-review-undo">撤回旧复核结论</button>`:''}${invoice.red&&!oldReview&&invoice.offset_status!=='linked'?'<details><summary>此红票不冲减当前账本</summary><p class="field-hint">只有确认无需冲抵当前账本时才使用此结论；红票仍不可直接核销付款。</p><textarea class="offset-exception-note" rows="2" maxlength="1000" aria-label="不冲减依据" placeholder="填写依据"></textarea><button class="small-button offset-exception-save">已复核，保留异常票</button></details>':''}<p class="form-error offset-error" role="alert"></p>${history.length?`<details class="offset-history"><summary>冲红历史（${history.length} 条）</summary>${history.map(r=>`<article data-offset-relation="${esc(r.id)}"><p>${r.state==='active'?'有效':'已撤回'} · ${money(r.amount_cents)} 元 · ${esc(r.at)}<br>红票 <button class="text-button" data-invoice="${esc(r.red_invoice_id)}">${esc(invoices.get(r.red_invoice_id).number)}</button> → 原票 <button class="text-button" data-invoice="${esc(r.blue_invoice_id)}">${esc(invoices.get(r.blue_invoice_id).number)}</button><br>确认依据：${esc(r.note)}</p>${r.state==='active'?'<label>撤回冲红依据<textarea class="offset-undo-note" rows="2" maxlength="1000" aria-label="撤回冲红依据"></textarea></label><button class="small-button offset-undo">撤回冲红</button>':`<p>撤回：${esc(r.revoked_at)} · ${esc(r.revoke_note)}</p>`}</article>`).join('')}</details>`:''}</div>`;
 const past=invoice.exception_review_history||[];
 if(past.length)container.querySelector('.offset-editor').insertAdjacentHTML('beforeend',`<details><summary>旧复核结论撤回历史</summary>${past.map(r=>`<p>${esc(r.at)} · 原依据：${esc(r.previous.note)}<br>撤回依据：${esc(r.note)}</p>`).join('')}</details>`);
 const error=container.querySelector('.offset-error');
 function submit(button,path,payload){button.onclick=async()=>{const data=payload();if(!data.note?.trim()){error.textContent='请填写处理依据';return;}error.textContent='';button.disabled=true;try{await onMutation(path,{revision:view.revision,...data},close);}catch(reason){if(!reason.silent)error.textContent=reason.message;button.disabled=false;}};}
 if(mayPair){
  const select=container.querySelector('.offset-blue'),search=container.querySelector('.offset-search'),area=container.querySelector('.offset-preview-area'),save=container.querySelector('.offset-save'),preview=container.querySelector('.offset-preview-button');
  let serial=0,prepared=null;
  function invalidate(){serial++;prepared=null;save.hidden=true;area.innerHTML='';error.textContent='';}
  function candidates(){const selected=select.value;select.innerHTML='<option value="">请选择原票</option>'+offsetCandidates(view,invoice,search.value).map(i=>`<option value="${esc(i.id)}">${esc(i.date)} ${esc(i.party)} · ${esc(i.number)} · 票面 ${money(i.amount_cents)} / 已冲红 ${money(i.offset_cents)} / 净额 ${money(i.net_amount_cents)} / 已关联 ${money(i.allocated_cents)}</option>`).join('');if([...select.options].some(o=>o.value===selected))select.value=selected;invalidate();}
  candidates();search.oninput=candidates;select.onchange=invalidate;
  preview.onclick=async()=>{invalidate();if(!select.value){error.textContent='请选择对应原票；未找到时可先补导入。';return;}const request=serial;preview.disabled=true;try{const result=await onMutation.preview({revision:view.revision,red_invoice_id:invoice.id,blue_invoice_id:select.value});if(request!==serial||!container.isConnected)return;prepared=result;area.innerHTML=offsetPreviewHtml(result,view);save.hidden=!result.can_save;}catch(reason){if(request===serial&&!reason.silent)error.textContent=reason.message;}finally{preview.disabled=false;}};
  submit(save,'/api/invoice-offset',()=>({red_invoice_id:invoice.id,blue_invoice_id:prepared?.blue_invoice_id,note:container.querySelector('.offset-note').value}));
 }
 if(oldReview)submit(container.querySelector('.offset-review-undo'),'/api/exception-undo',()=>({invoice_id:invoice.id,note:container.querySelector('.offset-review-note').value}));
 const exception=container.querySelector('.offset-exception-save');if(exception)submit(exception,'/api/exception',()=>({invoice_id:invoice.id,note:container.querySelector('.offset-exception-note').value}));
 for(const card of container.querySelectorAll('[data-offset-relation]')){const button=card.querySelector('.offset-undo');if(button)submit(button,'/api/invoice-offset/undo',()=>({offset_id:card.dataset.offsetRelation,red_invoice_id:history.find(r=>r.id===card.dataset.offsetRelation).red_invoice_id,note:card.querySelector('.offset-undo-note').value}));}
}

export function renderOffsetTasks(container,view,onMutation){
 const reds=view.invoices.filter(i=>i.red);
 container.innerHTML=reds.map(i=>`<article class="conflict-card" data-offset="${esc(i.id)}"><strong>${esc(i.party)} · ${money(i.amount_cents)} 元</strong><p>${esc(i.date)} · ${esc(i.number)}</p><button class="text-button" data-invoice="${esc(i.id)}">发票详情</button><div class="offset-task-editor"></div></article>`).join('')||'<p class="muted">没有红票</p>';
 const sourcePending=view.invoices.filter(i=>!i.red&&i.offset_source_hold);
 container.insertAdjacentHTML('beforeend',sourcePending.map(i=>`<article class="conflict-card"><strong>${esc(i.party)} · ${esc(i.number)}</strong><p>${esc(i.offset_source_hold)}</p><button class="text-button" data-invoice="${esc(i.id)}">查看原票与冲红历史</button></article>`).join(''));
 const indexed=new Map(reds.map(i=>[i.id,i]));
 for(const card of container.querySelectorAll('[data-offset]'))offsetEditor(card.querySelector('.offset-task-editor'),indexed.get(card.dataset.offset),view,onMutation,{close:false});
}

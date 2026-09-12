import {escape as esc,money,el} from './format.js';
import {renderDifferences} from './invoice-difference.js';
import {renderRelations} from './allocation-relations.js';
import {reviewReversal} from './review-reversal.js';
import {renderOffsetTasks} from './invoice-offset-editor.js';

function conflictCard(conflict,view){
 const resolved=conflict.state==='resolved',existing=view[conflict.kind].filter(row=>conflict.record_ids.includes(row.id));
 const results={keep:'保留原记录',accept:'接受新版本',new:'确认另一笔付款并新增入账'};
 return `<article class="conflict-card" data-conflict="${esc(conflict.id)}"><strong>${conflict.type==='version'?'同一编号出现不同版本':'缺少流水号，发现疑似重复'}</strong><p class="compact-text">新增来源：${esc(conflict.incoming.source)} · 第 ${conflict.incoming.row} 行</p><div class="table-scroll"><table class="dialog-table"><thead><tr><th>版本</th><th>日期</th><th>主体</th><th>金额</th><th>源状态 / 已分配</th><th>详情</th></tr></thead><tbody>${existing.map(row=>`<tr><td>账本当前记录</td><td>${esc(row.date)}</td><td>${esc(row.party)}</td><td>${money(row.amount_cents)}</td><td>${esc(row.source_status||row.direction)} / ${money(row.allocated_cents)}</td><td><button class="text-button" data-${conflict.kind==='bank'?'bank':'invoice'}="${esc(row.id)}">详情</button></td></tr>`).join('')}<tr><td>来源版本</td><td>${esc(conflict.incoming.date)}</td><td>${esc(conflict.incoming.party)}</td><td>${money(conflict.incoming.amount_cents)}</td><td>${esc(conflict.incoming.source_status||conflict.incoming.direction)}</td><td>${resolved?'已核对':'待核对'}</td></tr></tbody></table></div>${resolved?`<p>已处理：${esc(results[conflict.resolution]||conflict.resolution)} · ${esc(conflict.resolved_at)}</p><p>${esc(conflict.note)}</p><div class="review-reversal"></div>`:`<label>核对结果<select class="resolution"><option value="keep">${conflict.type==='version'?'保留原记录，新版本仅保留为证据':'确认是重复记录，不新增金额'}</option><option value="${conflict.type==='version'?'accept':'new'}">${conflict.type==='version'?'接受新版本（先撤回原关联）':'确认是另一笔真实付款，新增入账'}</option></select></label><textarea class="resolution-note" rows="2" aria-label="冲突核对依据" placeholder="填写核对依据"></textarea><button class="primary resolve-button">保存本项核对</button><p class="form-error" role="alert"></p>`}${(conflict.reversal_history||[]).length?`<details class="review-history"><summary>核对撤回历史</summary>${conflict.reversal_history.map(item=>`<p>${esc(item.at)} · 原结论：${esc(results[item.resolution]||item.resolution)}<br>原依据：${esc(item.previous_note)}<br>撤回依据：${esc(item.note)}</p>`).join('')}</details>`:''}<div class="review-relations"></div></article>`;
}

function mountConflictCards(container,conflicts,view,onMutation){
 container.innerHTML=conflicts.map(conflict=>conflictCard(conflict,view)).join('')||'<div class="empty">暂无记录</div>';
 const items=new Map(conflicts.map(conflict=>[conflict.id,conflict]));
 for(const card of container.querySelectorAll('[data-conflict]')){
  const conflict=items.get(card.dataset.conflict),ids=new Set([...conflict.record_ids,conflict.accepted_record_id]);
  renderRelations(card.querySelector('.review-relations'),view,view.allocations.filter(a=>ids.has(a[conflict.kind==='bank'?'bank_id':'invoice_id'])),(path,payload)=>onMutation(path,payload,false));
  if(conflict.state==='resolved'){reviewReversal(card.querySelector('.review-reversal'),view,'conflict',conflict.id,onMutation);continue;}
  const button=card.querySelector('.resolve-button');button.onclick=async()=>{button.disabled=true;try{await onMutation('/api/conflict',{revision:view.revision,conflict_id:conflict.id,action:card.querySelector('.resolution').value,note:card.querySelector('.resolution-note').value},false);}catch(error){card.querySelector('.form-error').textContent=error.message;button.disabled=false;}};
 }
}

function mountExceptions(container,exceptions,view,onMutation){
 container.innerHTML=exceptions.map(invoice=>`<article class="conflict-card" data-exception="${esc(invoice.id)}"><strong>${esc(invoice.party)} · ${money(invoice.amount_cents)} 元</strong><p class="compact-text">${esc(invoice.number)} · ${esc(invoice.date)} · ${esc(invoice.red?'红字票':invoice.invalid)}</p><button class="text-button" data-invoice="${esc(invoice.id)}">查看关联与来源</button>${invoice.exception_review?`<p class="field-hint">已复核并保留异常票：${esc(invoice.exception_review.note)} · ${esc(invoice.exception_review.at)}</p><div class="review-reversal"></div>`:'<p class="field-hint">确认此异常票不需要冲抵当前付款后，可解除对同一销方其他记录的匹配阻止。此票仍保留异常状态，不用于核销；如涉及既有付款，请先撤回相应关联。</p><textarea class="exception-note" rows="2" aria-label="异常票复核依据" placeholder="说明与已有付款的关系及处理依据"></textarea><button class="small-button exception-button">已复核，保留异常票</button><p class="form-error" role="alert"></p>'}${(invoice.exception_review_history||[]).length?`<details><summary>异常复核撤回历史</summary>${invoice.exception_review_history.map(item=>`<p>${esc(item.at)}<br>原依据：${esc(item.previous.note)}<br>撤回依据：${esc(item.note)}</p>`).join('')}</details>`:''}<p class="field-hint">以下列出本票及同一销方相关付款的金额关联，便于核查与撤回。</p><div class="review-relations"></div></article>`).join('')||'<p class="muted">无红字或异常票</p>';
 for(const card of container.querySelectorAll('[data-exception]')){
  const invoice=exceptions.find(item=>item.id===card.dataset.exception);
  const bankIds=new Set(view.bank.filter(bank=>bank.party_match_key&&bank.party_match_key===invoice.party_match_key&&bank.currency===invoice.currency).map(bank=>bank.id));
  renderRelations(card.querySelector('.review-relations'),view,view.allocations.filter(a=>a.invoice_id===invoice.id||bankIds.has(a.bank_id)),(path,payload)=>onMutation(path,payload,false));
  if(invoice.exception_review){reviewReversal(card.querySelector('.review-reversal'),view,'exception',invoice.id,onMutation);continue;}
  const button=card.querySelector('.exception-button');button.onclick=async()=>{button.disabled=true;try{await onMutation('/api/exception',{revision:view.revision,invoice_id:invoice.id,note:card.querySelector('.exception-note').value},false);}catch(error){card.querySelector('.form-error').textContent=error.message;button.disabled=false;}};
 }
}

export function renderConflicts(state,onMutation){
 const view=state.result,pending=view.conflicts.filter(conflict=>conflict.state==='pending'),exceptions=view.invoices.filter(invoice=>!invoice.red&&(invoice.blocking_invalid||invoice.exception_review));
 const invoiceTasks=new Set([...pending.filter(conflict=>conflict.kind==='invoices').flatMap(conflict=>conflict.record_ids),...[...exceptions.filter(invoice=>!invoice.exception_review),...view.invoices.filter(invoice=>invoice.difference_blocked||invoice.offset_source_hold||invoice.offset_status==='pending')].map(invoice=>invoice.id)]);
 el('conflict-count').textContent=pending.filter(conflict=>conflict.kind==='bank').length+invoiceTasks.size;
 renderDifferences(el('difference-list'),view,onMutation);
 renderOffsetTasks(el('offset-list'),view,onMutation);
 mountConflictCards(el('conflict-list'),pending,view,onMutation);
 mountConflictCards(el('resolved-conflict-list'),view.conflicts.filter(conflict=>conflict.state==='resolved').reverse(),view,onMutation);
 mountExceptions(el('exception-list'),exceptions,view,onMutation);
}

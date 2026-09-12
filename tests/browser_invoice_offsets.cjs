const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

async function main(){
 const [url,directory]=process.argv.slice(2),checks=[],errors=[];
 const browser=await chromium.launch({headless:true,channel:'msedge',downloadsPath:path.join(directory,'temp','browser-downloads')});
 const context=await browser.newContext({viewport:{width:1600,height:1100}}),page=await context.newPage();
 page.on('pageerror',e=>errors.push(e.message));
 const check=name=>{checks.push(name);console.log('通过：'+name);};
 const read=async(key='moderate')=>(await context.request.get(url+'/api/ledger?company_key='+key)).json();
 const initial=await read(),ids=Object.fromEntries(initial.invoices.map(i=>[i.number,i.id]));
 const card=name=>page.locator(`[data-offset="${ids[name]}"]`);
 const invoice=id=>`#invoice-table tr:has([data-invoice-select="${id}"])`;
 const awaitSave=async()=>{await page.waitForFunction(()=>!document.querySelector('#company-select').disabled&&document.querySelector('#message').textContent.includes('已保存'));};
 async function navigate(name){await page.locator(`[data-page="${name}"]`).click();await page.locator(`#page-${name}`).waitFor({state:'visible'});}
 async function preview(target,blue){await target.locator('.offset-blue').selectOption(ids[blue]);await target.locator('.offset-preview-button').click();await target.locator('.offset-preview').waitFor({state:'visible'});}
 try{
  await page.goto(url,{waitUntil:'networkidle'});await navigate('conflicts');
  assert.equal(await page.locator('#conflict-count').innerText(),'5');
  assert.equal(await card('富芯通红票').locator('.offset-blue option').count(),2);
  assert.match(await card('富芯通红票').locator('.offset-blue').innerText(),/票面 1,550.00.*已关联 1,224.00/);
  assert.ok(await card('富芯通红票').locator('.offset-editor').isVisible());
  await card('富芯通红票').locator('.offset-search').fill('其他公司');assert.equal(await card('富芯通红票').locator('.offset-blue option').count(),1);
  await card('富芯通红票').locator('.offset-search').fill('富芯通原票');
  check('红票入口可见，同公司候选不被当前红票连带限制隐藏，号码和销方搜索有效');
  const before=await read();await preview(card('富芯通红票'),'富芯通原票');
  assert.equal((await read()).revision,before.revision);assert.match(await card('富芯通红票').locator('.offset-preview').innerText(),/差额已冲销/);
  await card('富芯通红票').locator('.offset-save').click();assert.match(await card('富芯通红票').locator('.offset-error').innerText(),/请填写/);
  assert.equal((await read()).revision,before.revision);
  await card('富芯通红票').locator('.offset-note').fill('原票多开326元，已收到对应红票');await card('富芯通红票').locator('.offset-save').click();
  await page.waitForFunction(()=>document.querySelector('#conflict-count').textContent==='3');
  let view=await read();assert.equal(view.bank.find(b=>b.reference==='富芯通付款').status,'matched');assert.equal(view.invoices.find(i=>i.id===ids['富芯通原票']).difference_status,'offset_cleared');
  check('只读预览、必填依据、确认后差额冲销及导航去重数量即时刷新');
  await navigate('bank');assert.match(await page.locator('#bank-table').innerText(),/累计冲红 326.00/);await page.locator('#bank-table button[data-invoice]').first().click();
  assert.match(await page.locator('#detail-content').innerText(),/净额 1,224.00/);assert.match(await page.locator('#detail-content').innerText(),/多开/);await page.locator('[data-close="detail-dialog"]').click();
  check('付款保留已匹配和原依据，列表与详情显示同一冲红净额');
  await navigate('conflicts');await preview(card('超额红票'),'超额原票');
  assert.match(await card('超额红票').locator('.offset-preview').innerText(),/超出 200.00/);assert.ok(await card('超额红票').locator('.offset-save').isHidden());
  assert.equal(await card('超额红票').locator('.offset-preview [data-bank]').count(),1);
  await page.screenshot({path:path.join(directory,'offset-overallocation.png'),fullPage:true});
  check('超额预览列出200元超额及付款撤回入口，确认按钮保持隐藏');
  await navigate('invoices');const signedTotal=await page.locator('#invoice-selection-total').innerText();
  await page.locator(invoice(ids['全额红票'])+' button[data-invoice]').last().click();const detail=page.locator('#invoice-offset-editor');
  await preview(detail,'全额原票');await detail.locator('.offset-note').fill('整张原票全额冲红');await detail.locator('.offset-save').click();await page.locator('#detail-dialog').waitFor({state:'hidden'});
  assert.equal(await page.locator('#invoice-selection-total').innerText(),signedTotal);
  assert.match(await page.locator(invoice(ids['全额原票'])).innerText(),/全额冲红，不可核销/);
  assert.doesNotMatch(await page.locator(invoice(ids['全额原票'])).innerText(),/已匹配发票/);
  await page.locator('#offset-filter').selectOption('full');assert.equal(await page.locator('[data-invoice-select]').count(),1);
  await page.locator('#offset-filter').selectOption('linked');assert.equal(await page.locator('[data-invoice-select]').count(),2);
  await page.locator('#invoice-month').selectOption('2026-10');assert.equal(await page.locator('[data-invoice-select]').count(),2);
  check('详情共用编辑组件，全额原票不可核销，冲红与月份筛选联动且合计不重复扣减');
  await page.locator('#invoice-month').selectOption('');await page.locator('#offset-filter').selectOption('all');
  await page.locator(`[data-invoice-select="${ids['全额红票']}"]`).uncheck();
  await page.waitForFunction(()=>document.querySelector('#invoice-selection-status').textContent.startsWith('已保存勾选'));
  assert.equal((await read()).invoices.find(i=>i.id===ids['全额原票']).net_amount_cents,0);
  await page.reload({waitUntil:'networkidle'});await navigate('invoices');assert.ok(!(await page.locator(`[data-invoice-select="${ids['全额红票']}"]`).isChecked()));
  await page.locator('#offset-filter').selectOption('partial');assert.match(await page.locator('#invoice-table').innerText(),/富芯通/);
  await page.screenshot({path:path.join(directory,'offset-invoice-list.png'),fullPage:false});
  check('取消红票合计勾选不撤销冲红，刷新后净额和勾选继续保留');
  await navigate('conflicts');await card('富芯通红票').locator('.offset-history summary').click();
  await card('富芯通红票').locator('.offset-undo-note').fill('重新核对原票对应');await card('富芯通红票').locator('.offset-undo').click();
  await page.waitForFunction(()=>document.querySelector('#conflict-count').textContent==='4');
  view=await read();assert.equal(view.invoices.find(i=>i.id===ids['富芯通原票']).difference_status,'pending');assert.equal(view.bank.find(b=>b.reference==='富芯通付款').allocated_cents,122400);
  await card('富芯通红票').locator('.offset-history summary').click();assert.match(await card('富芯通红票').innerText(),/重新核对原票对应/);check('独立撤回冲红保留历史，差额重开待确认，原付款关联未被改写');
  const multi=card('多票红票');assert.equal(await multi.locator('.offset-blue option').count(),3);
  await multi.locator('.offset-blue').selectOption(ids['多票原票1']);let held;
  await page.route('**/api/invoice-offset/preview',route=>{held=route;});await multi.locator('.offset-preview-button').click();
  while(!held)await page.waitForTimeout(20);
  await multi.locator('.offset-blue').selectOption(ids['多票原票2']);await held.continue();await page.unroute('**/api/invoice-offset/preview');
  await page.waitForFunction(()=>!document.querySelector('[data-offset] .offset-preview-button')?.disabled);
  assert.ok(await multi.locator('.offset-save').isHidden());
  await preview(multi,'多票原票2');await multi.locator('.offset-note').fill('人工指定第二张同额原票');await multi.locator('.offset-save').click();
  await page.waitForFunction(()=>document.querySelector('#conflict-count').textContent==='3');
  view=await read();assert.equal(view.invoices.find(i=>i.id===ids['多票原票1']).net_amount_cents,100000);assert.equal(view.invoices.find(i=>i.id===ids['多票原票2']).net_amount_cents,80000);
  check('同名同额原票由人工选择，切换选择后迟到预览不会开放旧原票的确认按钮');
  await page.reload({waitUntil:'networkidle'});await page.locator('#company-select').selectOption('haisi');await page.locator('#page-bank').waitFor({state:'visible'});
  assert.equal((await read('haisi')).invoice_offsets.length,0);await navigate('invoices');assert.equal(await page.locator('#offset-filter').inputValue(),'all');
  await page.locator('#company-select').selectOption('moderate');await page.locator('#page-bank').waitFor({state:'visible'});
  await page.locator('#export-open').click();await page.locator('#export-scope').selectOption('all');await page.locator('#export-submit').click();await page.locator('#export-files a').first().waitFor();
  assert.equal(await page.locator('#export-files a').filter({hasText:'红冲关系明细.csv'}).count(),1);
  check('另一家公司保持独立，切换重置冲红筛选，导出包含红冲关系明细');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(directory,'browser-result.json'),JSON.stringify({passed:true,checks,errors},null,2),'utf8');
 }catch(error){await page.screenshot({path:path.join(directory,'browser-failure.png'),fullPage:true});fs.writeFileSync(path.join(directory,'browser-result.json'),JSON.stringify({passed:false,checks,errors,error:String(error)},null,2),'utf8');throw error;}
 finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

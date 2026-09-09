const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

async function main(){
 const [url,directory]=process.argv.slice(2);
 const browser=await chromium.launch({headless:true,channel:'msedge',downloadsPath:path.join(directory,'temp','browser-downloads')});
 const context=await browser.newContext({viewport:{width:1600,height:1050},acceptDownloads:false});
 const page=await context.newPage(),errors=[],checks=[];
 context.on('page',tab=>tab.on('pageerror',error=>errors.push(error.message)));page.on('pageerror',error=>errors.push(error.message));
 const check=name=>{checks.push(name);console.log('通过：'+name);};
 const cents=text=>Math.round(Number(text.replace(/[元,\s]/g,''))*100);
 const total=async tab=>cents(await tab.locator('#invoice-selection-total').innerText());
 const read=async(key='moderate')=>(await context.request.get(url+'/api/ledger?company_key='+key)).json();
 async function invoices(tab){await tab.locator('[data-page="invoices"]').click();await tab.locator('#page-invoices').waitFor({state:'visible'});}
 async function open(tab){await tab.goto(url,{waitUntil:'networkidle'});await tab.locator('#page-bank').waitFor({state:'visible'});await invoices(tab);}
 async function saved(tab){await tab.waitForFunction(()=>document.querySelector('#invoice-selection-status').textContent.startsWith('已保存勾选')&&!document.querySelector('#company-select').disabled);}
 async function selection(tab,selector,value){await tab.locator(selector).setChecked(value);await saved(tab);}
 try{
  await open(page);const initial=await read();
  assert.equal(initial.invoices.length,208);assert.equal(await page.locator('#invoice-table tr').count(),100);
  assert.equal(await page.locator('#bank-table tr').count(),15);assert.equal(await total(page),initial.invoices.reduce((s,row)=>s+row.amount_cents,0));
  assert.match(await page.locator('#invoice-page-info').innerText(),/每页 100 张/);
  assert.deepEqual(await page.locator('#invoice-month option').allTextContents(),['全部月份','2026-09','2026-08']);
  check('默认全部勾选，按票面正负金额跨全部分页合计，发票每页100张而流水仍为15笔');
  await page.locator('#invoice-month').selectOption('2026-08');await page.locator('[data-pager="invoice:1"]').click();
  assert.equal(await page.locator('#invoice-table tr').count(),20);
  const oldTotal=await total(page),before=(await read()).revision;let held;
  await page.route('**/api/invoice-selection',route=>{held=route;});
  await page.locator('#invoice-select-all').uncheck();await page.waitForFunction(()=>document.querySelector('#company-select').disabled);
  assert.equal(held.request().postDataJSON().invoice_ids.length,120);
  assert.equal(await total(page),oldTotal);assert.ok(await page.locator('#invoice-select-all').isDisabled());assert.ok(await page.locator('[data-invoice-select]').first().isDisabled());
  assert.match(await page.locator('#invoice-selection-status').innerText(),/正在保存/);
  await held.continue();await page.unroute('**/api/invoice-selection');await saved(page);
  let view=await read();assert.equal(view.revision,before+1);assert.equal(view.invoices.filter(row=>!row.include_in_total).length,120);
  assert.equal(await total(page),0);assert.match(await page.locator('#invoice-page-info').innerText(),/第 2/);
  await page.locator('#invoice-month').selectOption('2026-09');assert.equal(await total(page),initial.invoices.filter(row=>row.date.startsWith('2026-09')).reduce((s,row)=>s+row.amount_cents,0));
  check('全选作用于当前月份全部120张，保存期间禁用勾选与公司切换，其他月份选择保留');
  await page.locator('#invoice-search').fill('富芯通');await page.locator('#invoice-filter').selectOption('used');await page.locator('#difference-filter').selectOption('pending');
  assert.equal(await page.locator('[data-invoice-select]').count(),1);assert.equal(await total(page),155000);
  await page.locator('#invoice-table [data-invoice]').click();await page.locator('#detail-dialog').waitFor({state:'visible'});assert.match(await page.locator('#detail-content').innerText(),/差额待确认/);await page.locator('[data-close="detail-dialog"]').click();
  await selection(page,'#invoice-select-all',false);view=await read();const diff=view.invoices.find(row=>row.number==='差额测试');
  assert.equal(diff.allocated_cents,122400);assert.equal(diff.distributable_cents,0);assert.equal(diff.difference_status,'pending');assert.equal(await total(page),0);
  check('月份、名称、关联状态和差额状态交集有效，取消勾选不改变差额限制及已关联金额');
  await page.reload({waitUntil:'networkidle'});await invoices(page);
  assert.equal(await page.locator('#invoice-month').inputValue(),'');assert.equal(await total(page),initial.invoices.filter(row=>row.date.startsWith('2026-09')&&row.number!=='差额测试').reduce((s,row)=>s+row.amount_cents,0));
  const second=await context.newPage();await open(second);assert.equal(await total(second),await total(page));
  check('刷新与新标签页读回账本勾选，筛选默认全部月份');
  await page.locator('#invoice-search').fill('普通120');await second.locator('#invoice-search').fill('普通120');
  await selection(page,'[data-invoice-select]',false);
  let stalePosts=0;second.on('request',request=>{if(request.url().endsWith('/api/invoice-selection'))stalePosts++;});
  await second.locator('[data-invoice-select]').uncheck();
  await second.waitForFunction(()=>document.querySelector('#invoice-selection-status').textContent.includes('已重新读取')&&!document.querySelector('#company-select').disabled);
  assert.equal(stalePosts,1);assert.equal(await second.locator('[data-invoice-select]').isChecked(),false);assert.equal(await total(second),0);
  check('旧标签页过期提交被拒绝，自动回读最新勾选且不会自动重试覆盖');
  await page.locator('#invoice-search').fill('普通121');let abortedPosts=0;
  await page.route('**/api/invoice-selection',async route=>{abortedPosts++;const response=await route.fetch();assert.equal(response.status(),200);await route.abort();});
  await page.locator('[data-invoice-select]').uncheck();await page.waitForFunction(()=>document.querySelector('#invoice-selection-status').textContent.includes('已重新读取')&&!document.querySelector('#company-select').disabled);
  assert.equal(abortedPosts,1);assert.equal(await total(page),0);assert.equal((await read()).invoices.find(row=>row.number==='普通121').include_in_total,false);
  await page.unroute('**/api/invoice-selection');check('服务器已保存但响应中断时，回读真实结果，不重复提交');
  await page.locator('#invoice-search').fill('普通122');
  await page.route('**/api/invoice-selection',route=>route.abort());await page.route('**/api/ledger?company_key=moderate',route=>route.abort());
  await page.locator('[data-invoice-select]').uncheck();await page.waitForFunction(()=>document.querySelector('#invoice-selection-status').textContent.includes('尚未确认')&&!document.querySelector('#company-select').disabled);
  assert.ok(await page.locator('[data-invoice-select]').isDisabled());assert.ok(await page.locator('[data-invoice-select]').isChecked());
  await page.unroute('**/api/invoice-selection');await page.unroute('**/api/ledger?company_key=moderate');
  await page.locator('#refresh').click();await page.waitForFunction(()=>document.querySelector('#invoice-selection-status').textContent.includes('已读取最新勾选'));
  assert.ok(await page.locator('[data-invoice-select]').isEnabled());assert.ok(await page.locator('[data-invoice-select]').isChecked());check('保存及回读均失败时锁定勾选，刷新确认后恢复操作');
  await page.locator('#invoice-month').selectOption('2026-09');await page.locator('#company-select').selectOption('haisi');await page.locator('#page-bank').waitFor({state:'visible'});await invoices(page);
  assert.equal(await page.locator('#invoice-month').inputValue(),'');assert.equal(await page.locator('#invoice-search').inputValue(),'');assert.equal(await total(page),initial.invoices.reduce((s,row)=>s+row.amount_cents,0));
  assert.ok((await read('haisi')).invoices.every(row=>row.include_in_total));
  await page.locator('#company-select').selectOption('moderate');await page.locator('#page-bank').waitFor({state:'visible'});await invoices(page);
  await page.locator('#invoice-month').selectOption('2026-09');await page.screenshot({path:path.join(directory,'invoice-selection.png'),fullPage:false});
  check('公司切换重置月份与搜索，各公司勾选独立保留');
  await page.locator('[data-page="expenses"]').click();await page.locator('#expense-content').waitFor({state:'visible'});
  for(const [id,label,summary] of [['tax','缴税','8月缴税'],['operations','运营','代运营服务货款'],['reimbursement','报销款','报销款'],['rent','租金','租金水电']]){
   await page.locator('#expense-category-filter').selectOption(id);assert.equal(await page.locator('#expense-table tr').count(),1);assert.match(await page.locator('#expense-table').innerText(),new RegExp(summary));assert.match(await page.locator('#expense-scope').innerText(),new RegExp(label));
  }
  await page.locator('#expense-category-filter').selectOption('all');await page.screenshot({path:path.join(directory,'expense-categories.png'),fullPage:false});check('缴税、运营、报销款、租金分类可筛选，运营货款与租金水电按新类别优先');
  await page.locator('[data-page="bank"]').click();await page.locator('[data-bank-note]').first().click();
  assert.match(await page.locator('#bank-note-dialog').innerText(),/重启/);await page.locator('#bank-note-input').fill('保存后重启仍保留的手工备注');await page.locator('#bank-note-dialog button.primary').click();await page.locator('#bank-note-dialog').waitFor({state:'hidden'});
  assert.match(await page.locator('#message').innerText(),/备注已保存/);await page.reload({waitUntil:'networkidle'});await page.locator('#page-bank').waitFor({state:'visible'});
  assert.match(await page.locator('#bank-table').innerText(),/保存后重启仍保留的手工备注/);check('备注明确提示保存成功，刷新后仍从当前公司账本显示');
  await page.locator('#export-open').click();await page.locator('#export-scope').selectOption('all');await page.locator('#export-submit').click();await page.locator('#export-files a').first().waitFor();
  const snapshotLink=await page.locator('#export-files a').filter({hasText:'完整对账快照.json'}).getAttribute('href');const snapshot=await(await context.request.get(url+snapshotLink)).json();
  assert.equal(snapshot.invoices.length,208);assert.equal(snapshot.invoices.filter(row=>!row.include_in_total).length,123);assert.ok(snapshot.bank.some(row=>row.manual_note==='保存后重启仍保留的手工备注'));check('导出范围不受勾选缩减，完整快照保留勾选、备注及差额数据');
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(directory,'browser-result.json'),JSON.stringify({passed:true,checks,errors},null,2),'utf8');
 }catch(error){await page.screenshot({path:path.join(directory,'browser-failure.png'),fullPage:true});fs.writeFileSync(path.join(directory,'browser-result.json'),JSON.stringify({passed:false,checks,errors,error:String(error)},null,2),'utf8');throw error;}
 finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

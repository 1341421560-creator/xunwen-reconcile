const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

async function main(){
 const [url,directory]=process.argv.slice(2),fixtures=JSON.parse(fs.readFileSync(path.join(directory,'fixtures.json'),'utf8'));
 const browser=await chromium.launch({headless:true,channel:'msedge',downloadsPath:path.join(directory,'temp','browser-downloads')});
 const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:false});
 const page=await context.newPage(),errors=[],checks=[];
 context.on('page',tab=>tab.on('pageerror',error=>errors.push(error.message)));page.on('pageerror',error=>errors.push(error.message));
 const check=name=>{checks.push(name);console.log('通过：'+name);};
 async function boot(key){return (await context.request.get(url+'/api/bootstrap?company_key='+key)).json();}
 async function bank(tab,key){await tab.locator('#company-select').selectOption(key);await tab.waitForFunction(key=>document.querySelector('#company-select').value===key&&!document.querySelector('#page-bank').hidden,key);}
 async function setup(tab,key){await tab.locator('#company-select').selectOption(key);await tab.locator('#page-company-setup').waitFor({state:'visible'});}
 try{
  await page.goto(url,{waitUntil:'networkidle'});await page.locator('#page-bank').waitFor({state:'visible'});
  assert.equal(await page.locator('#company-select').inputValue(),'moderate');assert.match(await page.locator('#ledger-total').innerText(),/31 笔流水/);
  await setup(page,'haisi');assert.equal(await page.locator('#company-legal-name').inputValue(),'');
  assert.match(await page.locator('#company').innerText(),/海思.*未启用/);
  await page.locator('#company-legal-name').fill('尚未确认的名称');await page.locator('#company-select').selectOption('huachuangxing');
  await page.locator('#company-switch-dialog').waitFor({state:'visible'});await page.locator('#company-switch-dialog button[value=stay]').click();
  assert.equal(await page.locator('#company-select').inputValue(),'haisi');assert.equal(await page.locator('#company-legal-name').inputValue(),'尚未确认的名称');
  await page.locator('#company-select').selectOption('huachuangxing');await page.locator('#company-switch-dialog button[value=discard]').click();
  await page.waitForFunction(()=>document.querySelector('#company').textContent.includes('华创星'));
  assert.equal(await page.locator('#company-legal-name').inputValue(),'');check('未启用公司不猜全称，未保存启用表单支持留下或放弃');
  await setup(page,'haisi');await page.locator('#company-legal-name').fill('隔离浏览器测试海思');await page.locator('#company-confirm').check();
  let activation;
  await page.route('**/api/companies/activate',route=>{activation=route;});
  await page.locator('#company-activate-submit').click();await page.waitForFunction(()=>document.querySelector('#company-select').disabled);
  assert.ok(activation);await activation.continue();await page.unroute('**/api/companies/activate');await page.locator('#page-bank').waitFor({state:'visible'});
  assert.match(await page.locator('#ledger-total').innerText(),/0 笔流水/);check('完整名称确认后创建空账本，保存期间禁止切换');
  const nameTab=await context.newPage();await nameTab.goto(url,{waitUntil:'networkidle'});await nameTab.locator('#page-bank').waitFor({state:'visible'});await bank(nameTab,'haisi');
  await nameTab.locator('#company-name-open').click();await nameTab.locator('#company-name-input').fill('旧页面不应覆盖的全称');await nameTab.locator('#company-name-confirm').check();
  await page.locator('#company-name-open').click();assert.equal(await page.locator('#company-name-input').inputValue(),'隔离浏览器测试海思');
  await page.locator('#company-name-input').fill('隔离浏览器测试海思有限公司');await page.locator('#company-name-confirm').check();
  await page.screenshot({path:path.join(directory,'company-name-correction.png'),fullPage:true});
  let correction;await page.route('**/api/companies/correct-name',route=>{correction=route;});
  await page.locator('#company-name-dialog button.primary').click();await page.waitForFunction(()=>document.querySelector('#company-select').disabled);
  assert.ok(correction);assert.ok(await page.locator('#company-name-dialog [data-close]').isDisabled());
  await correction.continue();await page.unroute('**/api/companies/correct-name');await page.locator('#company-name-dialog').waitFor({state:'hidden'});
  assert.equal(await page.locator('#company').innerText(),'隔离浏览器测试海思有限公司');assert.match(await page.locator('#message').innerText(),/备份/);
  await nameTab.locator('#company-name-dialog button.primary').click();await nameTab.waitForFunction(()=>document.querySelector('#company-name-dialog .form-error').textContent.includes('刷新'));await nameTab.close();
  await page.reload({waitUntil:'networkidle'});await page.locator('#page-bank').waitFor({state:'visible'});assert.equal(await page.locator('#company').innerText(),'隔离浏览器测试海思有限公司');
  check('空账本更正全称、自动备份、刷新保留，保存锁定公司且拒绝旧页面覆盖');
  const beforeWrong=(await boot('haisi')).result.revision;
  await page.locator('#import-open').click();await page.locator('#bank-file').setInputFiles(fixtures.moderate_bank);await page.locator('#import-submit').click();
  await page.waitForFunction(()=>document.querySelector('#import-error').textContent.length>0);
  assert.equal((await boot('haisi')).result.revision,beforeWrong);await page.locator('[data-close="import-dialog"]').click();
  await page.locator('#import-open').click();await page.locator('#bank-file').setInputFiles(fixtures.haisi_bank);await page.locator('#invoice-file').setInputFiles(fixtures.haisi_invoice);await page.locator('#import-submit').click();
  await page.locator('#import-dialog').waitFor({state:'hidden'});assert.match(await page.locator('#ledger-total').innerText(),/31 笔流水/);
  assert.ok(await page.locator('#company-name-open').isHidden());
  check('选错公司导入整次拒绝，对应公司导入与历史独立');
  await page.locator('[data-page="expenses"]').click();await page.locator('#expense-content').waitFor({state:'visible'});
  await page.locator('#expense-start').fill('2026-08-01');await page.locator('#expense-end').fill('2026-08-31');await page.locator('#expense-category-filter').selectOption('goods');await page.locator('#expense-party-filter').fill('测试供应商');await page.locator('#expense-form button.primary').click();
  await page.waitForFunction(()=>document.querySelector('#expense-scope').textContent.includes('2026-08-31'));
  await bank(page,'moderate');assert.equal(await page.locator('#expense-start').inputValue(),'');assert.equal(await page.locator('#expense-end').inputValue(),'');
  assert.equal(await page.locator('#expense-category-filter').inputValue(),'all');assert.equal(await page.locator('#expense-party-filter').inputValue(),'');
  await page.locator('#month').selectOption('');await page.locator('[data-pager="bank:1"]').click();await page.locator('#bank-search').fill('R1');
  await bank(page,'haisi');assert.equal(await page.locator('#bank-search').inputValue(),'');assert.match(await page.locator('#bank-page-info').innerText(),/第 1/);
  check('切换回流水，统计日期、类别、公司、搜索及分页重置');
  await page.reload({waitUntil:'networkidle'});await page.locator('#page-bank').waitFor({state:'visible'});assert.equal(await page.locator('#company-select').inputValue(),'haisi');
  const second=await context.newPage();await second.goto(url,{waitUntil:'networkidle'});await second.locator('#page-bank').waitFor({state:'visible'});
  assert.equal(await second.locator('#company-select').inputValue(),'moderate');await setup(second,'huachuangxing');assert.equal(await page.locator('#company-select').inputValue(),'haisi');
  check('刷新保留公司，同浏览器两个标签页分别记住选择');
  await page.locator('[data-page="settings"]').click();await page.locator('#custom-exclude-keywords').fill('海思未保存关键词');
  await page.locator('[data-page="bank"]').click();await page.locator('#month').selectOption('');
  await page.locator('[data-page="settings"]').click();assert.equal(await page.locator('#custom-exclude-keywords').inputValue(),'海思未保存关键词');
  await page.locator('[data-page="bank"]').click();await page.locator('#company-select').selectOption('moderate');await page.locator('#company-switch-dialog button[value=stay]').click();
  assert.equal(await page.locator('#company-select').inputValue(),'haisi');
  await page.locator('#company-select').selectOption('moderate');await page.locator('#company-switch-dialog button[value=discard]').click();await page.waitForFunction(()=>document.querySelector('#company-select').value==='moderate'&&!document.querySelector('#page-bank').hidden);
  assert.deepEqual((await boot('haisi')).result.settings.custom_exclude_keywords,[]);check('隐藏规则表单仍提示未保存，放弃不会改动正式规则');
  const delayed=[];
  await page.route('**/api/bootstrap?company_key=haisi',async route=>{const response=await route.fetch();delayed.push({route,response});});
  await page.locator('#company-select').selectOption('haisi');await page.waitForFunction(()=>document.querySelector('#page-company-loading').hidden===false);
  await bank(page,'moderate');while(!delayed.length)await new Promise(resolve=>setTimeout(resolve,10));
  await delayed[0].route.fulfill({response:delayed[0].response});await page.unroute('**/api/bootstrap?company_key=haisi');
  await page.waitForLoadState('networkidle');assert.match(await page.locator('#company').innerText(),/摩德瑞特/);assert.equal(await page.locator('#company-select').inputValue(),'moderate');
  check('相同版本下迟到的其他公司响应不能覆盖当前页面');
  await bank(page,'haisi');await page.locator('#export-open').click();await page.locator('#export-scope').selectOption('all');await page.locator('#export-submit').click();await page.locator('#export-files a').first().waitFor();
  const links=await page.locator('#export-files a').evaluateAll(items=>items.map(item=>item.getAttribute('href')));
  for(const link of links){assert.ok(link.startsWith('/reports/haisi/'));assert.equal((await context.request.get(url+link)).status(),200);}
  await page.locator('[data-close="export-dialog"]').click();check('全账本导出仅对应当前公司且带公司标识的下载链接可用');
  await bank(second,'haisi');await second.locator('[data-bank-note]').first().click();await second.locator('#bank-note-input').fill('旧页面不应保存');
  await page.locator('[data-bank-note]').first().click();await page.locator('#bank-note-input').fill('仅海思的备注');
  let noteRoute;await page.route('**/api/bank-note',route=>{noteRoute=route;});await page.locator('#bank-note-dialog button.primary').click();await page.waitForFunction(()=>document.querySelector('#company-select').disabled);
  assert.ok(noteRoute);await noteRoute.continue();await page.unroute('**/api/bank-note');await page.locator('#bank-note-dialog').waitFor({state:'hidden'});
  await second.locator('#bank-note-dialog button.primary').click();await second.waitForFunction(()=>document.querySelector('#bank-note-dialog .form-error').textContent.includes('刷新'));
  await second.locator('[data-close="bank-note-dialog"]').click();assert.ok((await boot('haisi')).result.bank.some(row=>row.manual_note==='仅海思的备注'));
  assert.ok((await boot('moderate')).result.bank.every(row=>!row.manual_note));check('两个页面编辑同公司时过期版本拒绝，备注不写入其他公司');
  const moderate=(await boot('moderate')).result;
  const imported=await context.request.post(url+'/api/import',{data:{company_key:'moderate',revision:moderate.revision,invoice:{name:'changed.xlsx',content:fs.readFileSync(fixtures.moderate_changed).toString('base64')}}});assert.equal(imported.status(),200);
  await bank(page,'moderate');await page.locator('[data-page="conflicts"]').click();await page.locator('.resolution-note').first().fill('未保存的复核依据');
  await page.locator('#company-select').selectOption('haisi');await page.locator('#company-switch-dialog button[value=stay]').click();assert.equal(await page.locator('.resolution-note').first().inputValue(),'未保存的复核依据');
  await page.locator('#company-select').selectOption('haisi');await page.locator('#company-switch-dialog button[value=discard]').click();await page.waitForFunction(()=>document.querySelector('#company-select').value==='haisi'&&!document.querySelector('#page-bank').hidden);
  assert.equal((await boot('moderate')).result.conflicts[0].state,'pending');check('冲突与复核卡片未保存内容同样受公司切换提示保护');
  await page.screenshot({path:path.join(directory,'company-bank.png'),fullPage:true});await setup(second,'dongguan_xunwen');await second.screenshot({path:path.join(directory,'company-first-use.png'),fullPage:true});
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(directory,'browser-result.json'),JSON.stringify({passed:true,checks,errors},null,2),'utf8');
 }catch(error){await page.screenshot({path:path.join(directory,'browser-failure.png'),fullPage:true});fs.writeFileSync(path.join(directory,'browser-result.json'),JSON.stringify({passed:false,checks,errors,error:String(error)},null,2),'utf8');throw error;}
 finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');

async function main(){
 const [url,directory]=process.argv.slice(2),expected=JSON.parse(fs.readFileSync(path.join(directory,'fixtures.json'),'utf8'));
 const browser=await chromium.launch({headless:true,channel:'msedge',downloadsPath:path.join(directory,'temp','browser-downloads')});
 const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:false});
 const page=await context.newPage(),errors=[],checks=[];
 page.on('pageerror',error=>errors.push(error.message));
 const check=name=>{checks.push(name);console.log('通过：'+name);};
 async function boot(key){const response=await context.request.get(url+'/api/bootstrap?company_key='+key);assert.equal(response.status(),200);return (await response.json()).result;}
 async function select(key){await page.locator('#company-select').selectOption(key);await page.waitForFunction(key=>document.querySelector('#company-select').value===key&&!document.querySelector('#page-bank').hidden,key);}
 async function importFile(){await page.locator('#import-open').click();await page.locator('#bank-file').setInputFiles(path.join(directory,expected.upload));await page.locator('#import-submit').click();}
 try{
  await page.goto(url,{waitUntil:'networkidle'});await page.locator('#page-bank').waitFor({state:'visible'});
  await select('haisi');await page.locator('#import-open').click();
  const text=await page.locator('#import-dialog').innerText();assert.ok(text.includes('交通银行')&&text.includes('网商银行'));
  await page.screenshot({path:path.join(directory,'mybank-import.png'),fullPage:true});
  await page.locator('[data-close="import-dialog"]').click();
  await importFile();await page.locator('#import-dialog').waitFor({state:'hidden'});
  assert.match(await page.locator('#ledger-total').innerText(),new RegExp(expected.count+' 笔流水'));
  let ledger=await boot('haisi');
  assert.equal(ledger.bank.length,expected.count);assert.deepEqual(ledger.bank.map(row=>row.reference),expected.references);
  assert.equal(ledger.stats.debit_cents,expected.debit_cents);assert.equal(ledger.stats.credit_cents,expected.credit_cents);
  assert.equal(ledger.bank.filter(row=>row.direction==='收入').length,expected.credit_count);
  assert.equal(ledger.bank.filter(row=>row.direction==='支出').length,expected.debit_count);
  assert.ok(ledger.bank.filter(row=>row.direction==='收入').every(row=>row.status==='excluded'));
  check('通过浏览器导入原文件，全部行和长流水号保留，收入与支出金额正确');
  const first=ledger.bank.map(row=>row.id);
  await page.locator('#month').selectOption(expected.months.at(-1));
  assert.match(await page.locator('#ledger-total').innerText(),new RegExp(expected.count+' 笔流水'));
  await page.locator('#month').selectOption('');
  await importFile();await page.locator('#import-dialog').waitFor({state:'hidden'});
  ledger=await boot('haisi');assert.deepEqual(ledger.bank.map(row=>row.id),first);
  assert.deepEqual(ledger.batches.at(-1).counts.bank,{new:0,duplicate:expected.count,conflict:0});
  check('月份只筛选显示；重复导入原文件只新增来源，流水笔数和稳定编号保持不变');
  await page.locator('[data-page="history"]').click();
  await page.locator('#batch-list .source-details').first().locator('summary').click();
  const history=await page.locator('#batch-list').innerText();assert.ok(history.includes('识别为网商银行'));
  assert.ok(history.includes('笔数 '+expected.credit_count+' / '+expected.credit_count));
  assert.ok(history.includes('笔数 '+expected.debit_count+' / '+expected.debit_count));
  await page.screenshot({path:path.join(directory,'mybank-controls.png'),fullPage:true});
  check('导入历史显示银行格式、收支方向说明及金额和笔数校验');
  await page.locator('[data-page="expenses"]').click();await page.locator('#expense-content').waitFor({state:'visible'});
  await page.locator('#expense-all-time').click();
  const amount=(expected.debit_cents/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  await page.waitForFunction(amount=>document.querySelector('#expense-total').textContent.includes(amount),amount);
  check('支出统计只计入支出列，网商银行收入不计入支出');
  await select('moderate');const before=await boot('moderate');await importFile();
  await page.waitForFunction(()=>document.querySelector('#import-error').textContent.includes('当前公司'));
  const after=await boot('moderate');
  for(const key of ['revision','bank','batches','audit','allocations','conflicts','stats'])assert.deepEqual(after[key],before[key]);
  assert.deepEqual(after.company.accounts,before.company.accounts);assert.equal(after.company.name,before.company.name);
  await page.locator('[data-close="import-dialog"]').click();await select('haisi');
  assert.deepEqual((await boot('haisi')).bank.map(row=>row.id),first);
  check('选错公司拒绝整次导入，切换回来原账本金额与记录保持一致');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(directory,'browser-result.json'),JSON.stringify({passed:true,checks,errors},null,2),'utf8');
 }catch(error){await page.screenshot({path:path.join(directory,'browser-failure.png'),fullPage:true});fs.writeFileSync(path.join(directory,'browser-result.json'),JSON.stringify({passed:false,checks,errors,error:String(error)},null,2),'utf8');throw error;}
 finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});

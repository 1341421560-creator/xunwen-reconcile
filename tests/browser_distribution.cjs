const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const [url, directory] = process.argv.slice(2);
  if (!url || !directory) throw new Error('请指定本机地址和项目临时输出目录');
  fs.mkdirSync(directory, {recursive: true});
  const browser = await chromium.launch({headless: true, channel: 'msedge'});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [], failedRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => failedRequests.push(request.url()));
  try {
    const boot = await (await page.request.get(url + '/api/bootstrap')).json();
    await page.goto(url, {waitUntil: 'networkidle'});
    await page.waitForFunction(() => document.querySelector('#ledger-total').textContent.includes('笔流水'));
    const total = await page.locator('#ledger-total').innerText();
    if (!total.includes(`${boot.result.stats.bank_count} 笔流水`) || !total.includes(`${boot.result.stats.invoice_count} 张发票`)) throw new Error('页面账本数量与接口不符');
    await page.locator('#unfinished').check();
    await page.waitForFunction(() => document.querySelector('#scope-info').textContent.includes('未完成'));
    await page.locator('#unfinished').uncheck();
    await page.reload({waitUntil: 'networkidle'});
    await page.waitForFunction(() => document.querySelector('#ledger-total').textContent.includes('笔流水'));
    if (await page.locator('#ledger-total').innerText() !== total) throw new Error('刷新后的账本状态不一致');
    await page.screenshot({path: path.join(directory, 'clone-browser.png'), fullPage: true});
    const result = {passed: errors.length === 0 && failedRequests.length === 0, total, errors, failedRequests};
    fs.writeFileSync(path.join(directory, 'browser-result.json'), JSON.stringify(result, null, 2), 'utf8');
    console.log(JSON.stringify(result));
    if (!result.passed) throw new Error('浏览器出现脚本或资源加载错误');
  } finally { await browser.close(); }
}

main().catch(error => {console.error(error); process.exitCode = 1;});

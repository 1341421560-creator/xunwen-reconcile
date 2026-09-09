import assert from 'node:assert/strict';
import {bankInvoiceNotices} from '../web/bank-invoice-notices.js';

const item={invoice_id:'I1',summary:'曾关联发票：1,550.00 元，差额待确认，暂停分配。',party:'测试公司',invoice_date:'2026-08-06',invoice_number:'123',relation_source:'revoked',revoke_note:'错开 <script>',revoked_at:'2026-09-09',remaining_cents:155000};
const bank={remaining_cents:122400,related_invoice_notices:[item]};
const html=bankInvoiceNotices(bank);
assert.ok(html.includes('data-invoice="I1"'));
assert.ok(html.includes('查看发票／处理差额'));
assert.ok(html.includes('付款未核销：1,224.00 元'));
assert.ok(html.includes('发票未分配余额：1,550.00 元'));
assert.ok(html.includes('错开 &lt;script&gt;'));
assert.ok(!html.includes('<script>'));
assert.equal(bankInvoiceNotices({}), '');
const multi=bankInvoiceNotices({...bank,related_invoice_notices:[item,{...item,invoice_id:'I2',relation_source:'same_party'}]});
assert.ok(multi.includes('另有 1 张相关发票'));
assert.ok(multi.includes('尚未确认对应关系'));
assert.equal((multi.match(/data-invoice=/g)||[]).length,2);
console.log('流水相关发票提示：10 项渲染检查通过');

import test from 'node:test';
import assert from 'node:assert/strict';
import {createCompanyContext} from '../web/company-context.js';

function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function storage(){const values=new Map();return {getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)};}

test('相同版本、连续切换与迟到响应只接受当前公司',async()=>{
 const calls=[];const context=createCompanyContext((path,payload)=>{const d=deferred();calls.push({path,payload,...d});return d.promise;});
 const first=context.request('/api/bootstrap');const firstError=assert.rejects(first,e=>e.silent);
 context.select('haisi');const second=context.request('/api/bootstrap');const secondError=assert.rejects(second,e=>e.silent);
 context.select('moderate');const third=context.request('/api/bootstrap');
 calls[2].resolve({company_key:'moderate',revision:2,marker:'最新'});
 assert.equal((await third).marker,'最新');
 calls[0].resolve({company_key:'moderate',revision:2,marker:'旧公司响应'});
 calls[1].resolve({company_key:'haisi',revision:2});
 await Promise.all([firstError,secondError]);
 assert.equal(calls[1].path,'/api/bootstrap?company_key=haisi');
});

test('保存时固定公司并禁止切换，完成后释放，非法响应不能应用',async()=>{
 const d=deferred(),states=[],calls=[];const context=createCompanyContext((path,payload)=>{calls.push(payload);return d.promise;},{onBusy:value=>states.push(value)});
 context.select('haisi');const saving=context.request('/api/settings',{company_key:'moderate',revision:1});
 assert.equal(calls[0].company_key,'haisi');assert.equal(context.busy,true);
 assert.throws(()=>context.select('moderate'),/正在保存/);
 d.resolve({company_key:'moderate',revision:2});await assert.rejects(saving,/响应公司/);
 assert.equal(context.busy,false);assert.deepEqual(states,[true,false]);context.select('moderate');
});

test('读统计不锁定切换，旧公司的失败不覆盖新公司的提示',async()=>{
 const d=deferred();const context=createCompanyContext(()=>d.promise);
 const query=context.request('/api/expense-statistics',{});const rejected=assert.rejects(query,e=>e.silent);
 assert.equal(context.busy,false);context.select('haisi');d.reject(new Error('旧公司的错误'));await rejected;
});

test('旧表单捕获的公司上下文不能在切换后提交',async()=>{
 let calls=0;const context=createCompanyContext(async()=>{calls++;return {company_key:'moderate'};});
 const old=context.capture();context.select('haisi');await assert.rejects(old.request('/api/review',{}),e=>e.silent);
 assert.equal(calls,0);
});

test('各标签页分别记住公司，刷新还原，默认摩德瑞特',()=>{
 const one=storage(),two=storage();const a=createCompanyContext(null,{storage:one}),b=createCompanyContext(null,{storage:two});
 assert.equal(a.key,'moderate');a.select('haisi');b.select('huachuangxing');
 assert.equal(createCompanyContext(null,{storage:one}).key,'haisi');assert.equal(createCompanyContext(null,{storage:two}).key,'huachuangxing');
});

test('上传读文件期间持有保存状态，异常释放后可以切换',()=>{
 const context=createCompanyContext(null);const scope=context.capture();const release=scope.hold();
 assert.throws(()=>context.select('haisi'),/正在保存/);release();release();assert.equal(context.busy,false);context.select('haisi');
});

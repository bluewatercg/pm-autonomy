'use strict';
// 标准库 VM 检查页面逻辑；不替代真实浏览器渲染验收。
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert');
const root=__dirname;
const proposal=JSON.parse(fs.readFileSync(root+'/pending-proposal.json','utf8'));
const ids=['status','proposal','connection','register','holder','approve','change','reject','execute','message','note','offline-proposal'];
const elements=Object.fromEntries(ids.map(id=>[id,{textContent:'',disabled:true,checked:false,value:''}]));
elements['offline-proposal'].textContent=JSON.stringify(proposal);
const context={document:{getElementById:id=>elements[id]},location:{protocol:'file:',hash:'',pathname:'/'},URLSearchParams,window:{},JSON,console};
vm.createContext(context);vm.runInContext(fs.readFileSync(root+'/card.js','utf8'),context);
assert(elements.status.textContent.includes('PENDING'));assert(elements.proposal.textContent.includes(proposal.base));
for(const id of ['register','approve','change','reject','execute'])assert(elements[id].disabled);
vm.runInContext("card={status:'APPROVED_EXECUTION_PENDING',proposal:{}};buttons(true)",context);
assert(!elements.change.disabled);assert(elements.approve.disabled);assert(elements.reject.disabled);
vm.runInContext("card={status:'PENDING',proposal:{revision_required:true}};buttons(true)",context);
assert(elements.approve.disabled);assert(!elements.change.disabled);assert(!elements.reject.disabled);
console.log(JSON.stringify({checks:9,pass:true,offlinePending:true,offlineDecisionsDisabled:true,revisionApprovalDisabled:true,approvedChangeEnabled:true,browserRenderingTested:false}));

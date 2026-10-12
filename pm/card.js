'use strict';
const $=id=>document.getElementById(id);
let capability=new URLSearchParams(location.hash.slice(1)).get('bootstrap'), csrf='', card=null, challenge='', rid=null, discussionOnly=false;
if(location.protocol!=='file:') history.replaceState(null,'',location.pathname);
async function api(path,data){
 const options=data?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)}:{};
 const response=await fetch(path,options), value=await response.json();
 if(!response.ok||value.ok===false) throw new Error(value.code||'请求失败');
 return value;
}
function display(value){card=value; $('proposal').textContent=JSON.stringify(value.proposal,null,2); $('status').textContent=value.status+' · '+value.identity;}
function buttons(enabled){enabled=enabled&&!discussionOnly;for(const name of ['approve','change','reject']){const allowed=card.status==='PENDING'||(name==='change'&&['APPROVED_EXECUTION_PENDING','EXECUTION_BLOCKED'].includes(card.status));$(name).disabled=!enabled||!allowed||(name==='approve'&&card.proposal.revision_required);}}
function clearRequest(){rid=null;window.lastAction=null;sessionStorage.removeItem('approval-request');}
async function refresh(){const value=await api('/api/card');challenge=value.challenge;display(value);buttons(!!csrf);}
function message(value){$('message').textContent=typeof value==='string'?value:JSON.stringify(value,null,2);}
$('holder').onchange=()=>{$('register').disabled=!capability||!$('holder').checked;};
$('register').onclick=async()=>{try{const result=await api('/api/register',{capability,challenge,confirm_capability_holder:$('holder').checked});csrf=result.csrf;discussionOnly=(await api('/api/session')).privilege==='discussion';capability=null;$('register').disabled=true;$('connection').textContent='会话已由能力持有人确认；任务仍待亲自审批。';await refresh();}catch(error){message(error.message);}};
for(const action of ['approve','change','reject']) $(action).onclick=async()=>{
 buttons(false);
 try{
  const p=card.proposal;
  if(!rid || window.lastAction!==action){rid=crypto.randomUUID();window.lastAction=action;}
 sessionStorage.setItem('approval-request',rid);
  const result=await api('/api/decision',{request_id:rid,decision:action,version:p.version,proposal_hash:card.proposal_hash,scope:p.scope,base:p.base,expiry:p.expiry,actions:p.actions,note:$('note').value});
  message(result);await refresh();
  if(action==='approve'){$('execute').disabled=false;sessionStorage.setItem('approval-request',rid);}else{rid=null;sessionStorage.removeItem('approval-request');}
 }catch(error){message(error.message);await refresh().catch(()=>{});if(card.status==='APPROVED_EXECUTION_PENDING')$('execute').disabled=false;}
};
$('execute').onclick=async()=>{
 $('execute').disabled=true;message('正在请求隔离内核；只有真实回执可确认结果。');
 try{message(await api('/api/execute',{request_id:rid}));await refresh();}
 catch(error){message(error.message+'；补齐必需审查后可使用同一请求恢复。');$('execute').disabled=true;await refresh().then(()=>{$('execute').disabled=!csrf||!rid||!['APPROVED_EXECUTION_PENDING','EXECUTION_BLOCKED'].includes(card.status);}).catch(()=>{});}
};
(async()=>{
 if(location.protocol==='file:'){
  const data=$('offline-proposal');
  if(data)display({proposal:JSON.parse(data.textContent),status:'PENDING · 离线预览',identity:'注册未确认；真实审批未完成'});
  $('connection').textContent='未连接 backend，所有审批和执行按钮禁用。在运行目录执行 python3 approval_api.py 可启动回环入口。';
  return;
 }
 try{await refresh();$('connection').textContent='已连接回环审批 API；请亲自确认能力注册。';
  try{const session=await api('/api/session');csrf=session.csrf;discussionOnly=session.privilege==='discussion';rid=sessionStorage.getItem('approval-request');buttons(true);if(rid&&['APPROVED_EXECUTION_PENDING','EXECUTION_BLOCKED'].includes(card.status))$('execute').disabled=false;}catch(error){}
 }catch(error){$('connection').textContent='入口不可用，未取得审批。';message(error.message);}
})();

'use strict';
const el=id=>document.getElementById(id);
let csrf='', privilege='', items=[], selected=null, pending=null;
const lines=id=>el(id).value.split('\n').map(x=>x.trim()).filter(Boolean);
async function api(path,data){
 const response=await fetch(path,data?{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)}:{});
 const value=await response.json();if(!response.ok||value.ok===false)throw new Error(value.code||'请求失败');return value;
}
function controls(){
 const editable=!!csrf&&selected&&!['converted','rejected','superseded'].includes(selected.status);
 for(const id of ['append','update','defer','change-state'])el(id).disabled=!editable;
 el('convert').disabled=!editable||privilege==='discussion'||!selected.ready_for_decision;
 el('add').disabled=!csrf;
}
function choose(item){
 selected=item;el('selected').textContent=item.requirement_id+' · '+item.status_label+' · 版本 '+item.version+' · '+item.readiness;
 el('owner').value=item.owner||'';el('priority').value=item.priority||'';el('questions').value=item.open_questions.join('\n');el('after').value=item.after_task.join('\n');
 el('details').textContent=JSON.stringify(item,null,2);controls();
}
function render(){
 el('list').replaceChildren();
 for(const item of items){
  if(el('filter').value==='ready'&&!item.ready_for_decision)continue;
  if(el('filter').value==='deferred'&&item.status!=='deferred')continue;
  const button=document.createElement('button');button.textContent=item.requirement_id+' · '+item.status_label+' · '+item.goal+' · '+item.readiness;button.onclick=()=>choose(item);el('list').append(button);
 }
}
async function refresh(){
 const result=await api('/api/requirements');items=result.requirements;render();
 if(selected){const item=items.find(x=>x.requirement_id===selected.requirement_id);if(item)choose(item);}
}
async function write(op,extra,rid=selected&&selected.requirement_id,version=selected&&selected.version){
 const body={operation:op,requirement_id:rid,expected_version:version,...extra};const encoded=JSON.stringify(body);
 if(!pending||pending.encoded!==encoded)pending={encoded,request_id:crypto.randomUUID()};
 try{
  controls();await api('/api/requirements',{...body,request_id:pending.request_id});pending=null;el('message').textContent='已持久化；未批准、未执行。';await refresh();
  if(op==='convert'){const link=document.createElement('a');link.href='/';link.textContent='打开待审批卡';el('message').append(' ',link);}
 }catch(error){el('message').textContent=error.message;await refresh().catch(()=>{});}
}
el('add').onclick=()=>write('add',{goal:el('goal').value,source:{ref:el('source').value,time:el('time').value,uncertain:el('uncertain').checked},owner:null,priority:null,open_questions:[]},el('id').value,0);
el('append').onclick=()=>write('note',{note:el('note').value});
el('update').onclick=()=>write('update',{owner:el('owner').value||null,priority:el('priority').value||null,open_questions:lines('questions'),after_task:lines('after')});
el('defer').onclick=()=>write('defer',{reason:el('note').value,after_task:lines('after')});
el('change-state').onclick=()=>write('state',{status:el('state').value,reason:el('note').value||null,superseded_by:el('state').value==='superseded'?el('replacement').value:null});
el('convert').onclick=async()=>{
 try{const card=await api('/api/card');await write('convert',{proposal_version:card.proposal.version,proposal_hash:card.proposal_hash,scope:card.proposal.scope});}catch(error){el('message').textContent=error.message;}
};
el('filter').onchange=render;el('refresh').onclick=()=>refresh().catch(error=>el('message').textContent=error.message);
el('time').value=new Date().toISOString();
(async()=>{try{const s=await api('/api/session');csrf=s.csrf;privilege=s.privilege;el('session').textContent=privilege==='discussion'?'讨论能力：可记录需求，无审批与转换权限。':'能力会话已确认；转换与批准仍是分别操作。';controls();await refresh();}catch(error){el('session').textContent='需要先到审批卡确认能力会话。';el('message').textContent=error.message;}})();

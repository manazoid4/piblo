from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .authority import AuthorityBroker, AuthorityError, Scope
from .config import Settings
from .pairing import PairingStore
from .security import FUNNEL_HEADER, client_info, is_lan_ip
from .work_routes import install_work_routes
from .work_service import WorkService
from .work_store import WorkStore
from .validation import install_validation_exception_handler
from .voices import install_voice_routes


CONTROL_HEADER = "X-MAZ-Control"
COOKIE = "maz_control_session"


class ManualGrantRequest(BaseModel):
    scope: Scope = "pc_full"
    seconds: int = Field(default=600, ge=30, le=3600)
    task: str = Field(default="Manual phone-authorized MAZ session", min_length=1, max_length=240)


PAGE = r"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#080b0e"><title>Piblo Control</title><style>
:root{color-scheme:dark;--b:#080b0e;--p:#11171c;--p2:#0c1115;--l:#27323b;--t:#f2f4f5;--d:#91a0aa;--a:#ff7a18;--g:#42d58a;--r:#ff676d;--w:#ffc04d}*{box-sizing:border-box}body{margin:0;background:var(--b);color:var(--t);font:14px ui-monospace,SFMono-Regular,Consolas,monospace;overflow-x:hidden}main{max-width:760px;margin:auto;padding:12px}.top{position:sticky;top:0;z-index:4;background:#080b0ef3;border-bottom:1px solid var(--l);padding:10px 0;display:flex;justify-content:space-between;align-items:center;gap:8px}.brand{font-size:20px;font-weight:900}.brand b{color:var(--a)}.token{font-size:11px;color:var(--d);border:1px solid var(--l);border-radius:999px;padding:5px 8px}.tabs{display:flex;gap:6px;margin:10px 0 0}.tabbtn{flex:1;text-align:center;padding:9px;border:1px solid var(--l);border-radius:8px;background:#080c0f;color:var(--d);cursor:pointer;font-weight:900;font-size:12px;letter-spacing:.08em}.tabbtn.active{background:var(--a);border-color:var(--a);color:#08090a}.pane{display:none}.pane.active{display:block}.hero{padding:17px 0 7px}.hero h1{font-size:24px;margin:0 0 5px}.hero p{color:var(--d);line-height:1.45;margin:0}.sec{margin-top:15px}.sec h2{font-size:10px;letter-spacing:.14em;color:var(--d)}.card{border:1px solid var(--l);border-radius:10px;background:var(--p);padding:12px;margin:7px 0;max-width:100%}.req{border-left:3px solid var(--w)}.grant{border-left:3px solid var(--g)}.kv{display:grid;grid-template-columns:1fr auto;gap:7px 10px}.kv span{color:var(--d)}.row{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}.row>*{flex:1 1 140px}button,input,select{font:inherit;border:1px solid var(--l);border-radius:7px;background:#080c0f;color:var(--t);padding:9px;min-width:0;max-width:100%}button{cursor:pointer;min-width:110px;min-height:44px}.yes{background:var(--a);border-color:var(--a);color:#08090a;font-weight:900}.danger{border-color:#6a3438;color:var(--r)}.muted{color:var(--d);font-size:12px;line-height:1.4}.bad{color:var(--r)}.good{color:var(--g)}.warn{color:var(--w)}pre{white-space:pre-wrap;word-break:break-word;background:var(--p2);border-radius:7px;padding:9px;font-size:11px;color:var(--d);max-height:220px;overflow:auto}.empty{padding:16px;text-align:center;color:var(--d);border:1px dashed var(--l);border-radius:9px}.stale{color:var(--w);font-weight:900}.quickgrid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}.histgrid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px;margin-top:8px;font-size:10px;text-align:center}.histday{background:var(--p2);border-radius:6px;padding:6px 2px;min-width:0}.eventedit{display:grid;grid-template-columns:1fr;gap:6px;margin-top:9px}.eventrow{display:grid;grid-template-columns:minmax(120px,1fr) auto auto auto;gap:6px;align-items:center;padding:7px;border:1px solid var(--l);border-radius:7px}.eventrow.inactive{opacity:.58}.eventrow button{min-width:84px}.check{display:flex;gap:7px;align-items:center;color:var(--d)}.check input{flex:0 0 auto}.pipelinegrid{display:grid;grid-template-columns:1fr 1fr;gap:8px}.pipelinegrid .card{margin:0}.pipeline-item{border-left:3px solid var(--a)}.pipeline-item a{color:var(--g);word-break:break-all}.pipeline-item summary{cursor:pointer;font-weight:900}.today-action{border-left:3px solid var(--g)}@media(max-width:520px){main{padding:9px}.hero h1{font-size:20px}.kv{grid-template-columns:1fr}.kv b{text-align:left}.top{align-items:flex-start;flex-direction:column}.quickgrid,.pipelinegrid{grid-template-columns:1fr}.eventrow{grid-template-columns:1fr 1fr}.eventrow [data-field=event-label]{grid-column:1/-1}}
</style></head><body><main>
<div class="top"><div class="brand"><b>Piblo</b> Control</div><div class="token">CORE ID <b id="tokenId">-</b></div></div>
<div class="tabs"><button class="tabbtn active" id="tabbtn-work" onclick="showTab('work')">WORK</button><button class="tabbtn" id="tabbtn-authority" onclick="showTab('authority')">AUTHORITY</button><button class="tabbtn" id="tabbtn-voice" data-testid="tab-voice" onclick="showTab('voice')">VOICE</button></div>

<div class="pane active" id="pane-work">
<div class="hero"><h1>Today</h1><p id="workFreshness" class="muted">Loading...</p></div>
<div class="sec"><h2>TODAY</h2><div id="workCards"></div></div>
<div class="sec"><h2>WHAT SHOULD I DO TODAY?</h2><div id="todayActions"></div></div>
<div class="sec"><h2>PIPELINE STATE</h2><div id="pipelineStats" class="pipelinegrid"></div></div>
<div class="sec"><h2>QUICK ACTIONS</h2><div id="workQuick" class="quickgrid"></div></div>
<div class="sec"><div class="row"><button data-testid="undo-last" onclick="undoLast()">UNDO LAST</button></div></div>
<div class="sec"><h2>SEVEN DAYS</h2><div id="workHistory" class="histgrid"></div></div>
<div class="sec"><h2>CLIENT PIPELINE</h2><div id="pipelineItems"></div><div id="pipelineStatus" class="muted"></div></div>
<div class="sec"><h2>MANAGE TRACKS</h2><div class="card"><div id="manageTracks"></div>
<p class="muted" style="margin-top:8px">New custom track</p>
<div class="row"><input id="newTrackName" placeholder="Name"><input id="newTrackShort" placeholder="Short label"></div>
<div class="row"><select id="newTrackMode"><option value="count">count</option><option value="time">time</option><option value="checkin">checkin</option></select><input id="newTrackUnit" placeholder="Unit (e.g. reps)"></div>
<div class="row"><select id="newTrackCadence"><option value="daily">daily</option><option value="weekly">weekly</option><option value="none">no cadence</option></select><input id="newTrackTarget" type="number" min="0" step="any" placeholder="Optional target"></div>
<div class="row"><input id="newTrackEventTypes" placeholder="Event types, comma separated (first = primary)"></div>
<label class="check"><input id="newTrackPinned" type="checkbox"> PIN ON TODAY + CARDPUTER</label>
<div class="row"><button class="yes" onclick="createTrack()">CREATE TRACK</button></div>
<div id="manageStatus" class="muted"></div>
</div></div>
</div>

<div class="pane" id="pane-authority">
<div class="hero"><h1>Phone approval centre</h1><p>Agents can request power here. They cannot approve themselves. Confirm a scoped, expiring grant, watch the action feed, or revoke everything instantly.</p></div>
<div class="sec"><h2>EMERGENCY</h2><div class="card"><div class="row"><button class="danger" onclick="revokeAll()">REVOKE ALL CONTROL</button><button onclick="refreshAll()">REFRESH</button></div><div id="status" class="muted" style="margin-top:8px">Loading...</div></div></div>
<div class="sec"><h2>PENDING APPROVALS</h2><div id="pending"></div></div>
<div class="sec"><h2>ACTIVE GRANTS</h2><div id="grants"></div></div>
<div class="sec"><h2>MANUAL SESSION</h2><div class="card"><p class="muted">Use this when you intentionally want Piblo to have broad control before an agent asks.</p><div class="row"><select id="manualScope"><option value="project_full">PROJECT FULL</option><option value="pc_full" selected>PC FULL</option><option value="admin">PC FULL + ADMIN GRANT</option></select><select id="manualSeconds"><option value="300">5 min</option><option value="600" selected>10 min</option><option value="900">15 min</option><option value="1800">30 min</option></select><button class="yes" onclick="manualGrant()">CONFIRM GRANT</button></div></div></div>
<div class="sec"><h2>ACTION FEED</h2><div class="card"><pre id="audit">No actions yet.</pre></div></div>
<div class="sec"><h2>SESSION</h2><div class="card"><div class="row"><button onclick="logout()">LOG OUT THIS PHONE</button></div><p class="muted">Core ID identifies the Piblo installation you are controlling.</p></div></div>
</div>

<div class="pane" id="pane-voice">
<div class="hero"><h1>Reply voice</h1><p id="voiceNow" class="muted">Loading...</p></div>
<div class="sec"><h2>SEARCH FISH LIBRARY</h2><div class="card"><div class="row"><input id="voiceQ" data-testid="voice-search" placeholder="Search voices (needs Fish key)" onkeydown="if(event.key==='Enter')searchVoices()"><button onclick="searchVoices()">SEARCH</button></div><div id="voiceSearchRes"></div></div></div>
<div class="sec"><h2>SAY</h2><div class="card"><div class="row"><input id="sayText" data-testid="say-text" maxlength="300" placeholder="Type something for Piblo to say" onkeydown="if(event.key==='Enter')sayIt()"><button class="yes" data-testid="say-btn" onclick="sayIt()">SAY</button></div><div id="sayStatus" class="muted"></div></div></div>
<div class="sec"><h2>VOICES</h2><div id="voiceList"></div><div id="voiceStatus" class="muted"></div></div>
</div>
<script>
const H={'X-MAZ-Control':'1','Content-Type':'application/json'};const $=x=>document.getElementById(x);function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}async function api(path,opt={}){let r=await fetch(path,{cache:'no-store',...opt,headers:{...H,...(opt.headers||{})}});if(r.status===401){location.href='./';throw new Error('login required')}let d=await r.json();if(!r.ok)throw new Error(d.detail||d.error||'request failed');return d}function secs(t){return Math.max(0,Math.round(t-Date.now()/1000))+'s'}
function showTab(name){if(name==='voice')loadVoices();for(const t of ['work','authority','voice']){$('pane-'+t).classList.toggle('active',t===name);$('tabbtn-'+t).classList.toggle('active',t===name)}}

// ------------------------------------------------------------------- WORK
let workBusy=false;let lastEventId=null;let managedTracks=[];let managedTracksLoaded=false;let pipelineItems=[];
function trackCard(t){let value=t.current_total??t.today_total;let period=t.current_window==='week'?'THIS WEEK':'TODAY';let pct=t.target?Math.min(100,Math.round(100*value/t.target)):null;let raw=t.breakdown.map(et=>`${esc(et.label)} ${et.total}`).join(' · ');return `<div class="card"><div class="kv"><span>${esc(t.name)} · ${period}</span><b>${value} ${esc(t.unit)}</b>${t.target?`<span>TARGET</span><b>${pct}%</b>`:''}</div><p class="muted" style="margin:8px 0 0">${raw}</p></div>`}
function quickButtons(t){return t.breakdown.map(et=>{const testid=t.track_id==='job_hunt'&&et.event_type_id==='job_hunt.application'?' data-testid="quick-log-application"':'';const disabled=workBusy?' disabled':'';return `<button${testid}${disabled} data-track="${esc(t.track_id)}" data-et="${esc(et.event_type_id)}" onclick="quickLog(this)">+1 ${esc(et.label)}<br><small>${esc(t.short_label)} · ${et.total}</small></button>`}).join('')}
function statCard(title,stats){return `<div class="card"><b>${esc(title)}</b><div class="kv" style="margin-top:8px">${Object.entries(stats).map(([k,v])=>`<span>${esc(k.replaceAll('_',' ').toUpperCase())}</span><b>${v}</b>`).join('')}</div></div>`}
function actionCard(x,i){return `<div class="card today-action"><b>${i+1}. ${esc(x.action)}</b><p class="muted">${esc(x.reason)} · ${esc(x.pipeline.toUpperCase())} / ${esc(x.stage)}</p></div>`}
const PIPELINE_STAGES={client:['RESEARCHED','QUALIFIED','REJECTED','READY_TO_SEND','SENT','FOLLOW_UP_DUE','WON','LOST']};
function pipelineCard(x){let url=/^https?:\/\//i.test(x.source_url)?`<a href="${esc(x.source_url)}" target="_blank" rel="noreferrer">SOURCE</a>`:'';let options=PIPELINE_STAGES[x.pipeline].map(s=>`<option value="${s}"${s===x.stage?' selected':''}>${s}</option>`).join('');return `<details class="card pipeline-item" data-pipeline-id="${esc(x.item_id)}"><summary>${esc(x.title)} · ${esc(x.stage)} · ${x.score??'-'}/100</summary><p class="muted">${esc(x.organisation)} ${url}</p>${x.friction?`<p><b>FRICTION</b><br>${esc(x.friction)}</p>`:''}${x.rationale?`<p><b>FIT / RATIONALE</b><br>${esc(x.rationale)}</p>`:''}${x.evidence?`<p><b>EVIDENCE</b><br>${esc(x.evidence)}</p>`:''}${x.solution?`<p><b>SOLUTION</b><br>${esc(x.solution)}</p>`:''}${x.outreach_draft?`<pre>${esc(x.outreach_draft)}</pre>`:''}<div class="row"><select data-field="stage">${options}</select><select data-field="proof_status"><option${x.proof_status==='NOT_STARTED'?' selected':''}>NOT_STARTED</option><option${x.proof_status==='IN_PROGRESS'?' selected':''}>IN_PROGRESS</option><option${x.proof_status==='READY'?' selected':''}>READY</option></select></div><div class="row"><input data-field="next_action" value="${esc(x.next_action)}" placeholder="Next action"><button class="yes" onclick="savePipeline(this)">SAVE STATE</button></div></details>`}
async function loadPipeline(){let [today,pipeline]=await Promise.all([api('work/today'),api('work/pipeline')]);$('todayActions').innerHTML=today.recommendations.length?today.recommendations.map(actionCard).join(''):'<div class="empty">No active stored action.</div>';$('pipelineStats').innerHTML=statCard('MAZ WORKS',today.stats.clients);pipelineItems=pipeline.items;$('pipelineItems').innerHTML=pipelineItems.length?pipelineItems.map(pipelineCard).join(''):'<div class="empty">No researched client prospects stored.</div>'}
async function savePipeline(button){let card=button.closest('[data-pipeline-id]');let body={stage:card.querySelector('[data-field=stage]').value,proof_status:card.querySelector('[data-field=proof_status]').value,next_action:card.querySelector('[data-field=next_action]').value.trim()};try{await api('work/pipeline/'+encodeURIComponent(card.dataset.pipelineId),{method:'PATCH',body:JSON.stringify(body)});$('pipelineStatus').innerHTML='<span class="good">Pipeline state saved.</span>';await loadPipeline()}catch(e){$('pipelineStatus').innerHTML='<span class="bad">'+esc(e.message)+'</span>'}}
async function loadWork(refreshManage=false){try{let d=await api('work/summary?window=today');$('workFreshness').textContent='Updated '+new Date(d.generated_at*1000).toLocaleTimeString();$('workCards').innerHTML=d.tracks.length?d.tracks.map(trackCard).join(''):'<div class="empty">No pinned active tracks.</div>';$('workQuick').innerHTML=d.tracks.map(quickButtons).join('');if(refreshManage||!managedTracksLoaded)await loadTracks();await loadPipeline()}catch(e){$('workFreshness').innerHTML='<span class="bad">'+esc(e.message)+'</span>'}try{let h=await api('work/history?days=7');$('workHistory').innerHTML=h.history.map(day=>{let total=Object.values(day.totals).reduce((a,b)=>a+b,0);return `<div class="histday">${new Date(day.day_start*1000).toLocaleDateString(undefined,{weekday:'short'})}<br><b>${total}</b></div>`}).join('')}catch(e){}}
async function quickLog(btn){if(workBusy)return;workBusy=true;document.querySelectorAll('#workQuick button').forEach(b=>b.disabled=true);const releaseAt=Date.now()+600;const eventId='evt_'+(crypto.randomUUID?crypto.randomUUID():Date.now().toString(36)+Math.random().toString(36).slice(2,8));try{let d=await api('work/tracks/'+encodeURIComponent(btn.dataset.track)+'/events',{method:'POST',body:JSON.stringify({event_id:eventId,event_type_id:btn.dataset.et,source:'phone_manual'})});lastEventId=d.event.event_id;await loadWork()}catch(e){$('workFreshness').innerHTML='<span class="bad">'+esc(e.message)+'</span>'}finally{setTimeout(()=>{workBusy=false;document.querySelectorAll('#workQuick button').forEach(b=>b.disabled=false)},Math.max(0,releaseAt-Date.now()))}}
async function undoLast(){if(!lastEventId){$('workFreshness').innerHTML='<span class="bad">Nothing logged this session yet.</span>';return}try{await api('work/events/'+encodeURIComponent(lastEventId)+'/undo',{method:'POST'});lastEventId=null;await loadWork()}catch(e){$('workFreshness').innerHTML='<span class="bad">'+esc(e.message)+'</span>'}}
function eventEditorRow(et){let active=et.active===undefined?true:Boolean(et.active);return `<div class="eventrow${active?'':' inactive'}" data-event-row data-event-id="${esc(et.id||'')}"><input data-field="event-label" value="${esc(et.label||'')}" aria-label="Event type label" placeholder="Event type label"><label class="check"><input data-field="event-active" type="checkbox"${active?' checked':''}> ACTIVE</label><label class="check"><input data-field="event-headline" type="checkbox"${et.contributes_to_headline?' checked':''}> HEADLINE</label><button type="button" onclick="toggleEventActive(this)">${active?'REMOVE':'RESTORE'}</button></div>`}
function trackEditor(t){let primary=t.event_types.findIndex(et=>et.id===t.primary_event_type_id);if(primary<0)primary=0;let eventRows=t.event_types.map(eventEditorRow).join('');let primaryOptions=t.event_types.map((et,i)=>`<option value="${i}"${i===primary?' selected':''}>${esc(et.label)}</option>`).join('');return `<div class="card" data-track-card="${esc(t.id)}"><div class="row"><input data-field="name" value="${esc(t.name)}" aria-label="Track name"><input data-field="short_label" value="${esc(t.short_label)}" aria-label="Short label"></div><div class="row"><select data-field="cadence"><option value="daily"${t.cadence==='daily'?' selected':''}>daily</option><option value="weekly"${t.cadence==='weekly'?' selected':''}>weekly</option><option value="none"${t.cadence==='none'?' selected':''}>no cadence</option></select><input data-field="target" type="number" min="0" step="any" value="${t.target??''}" placeholder="Optional target"><select data-field="state"><option value="active"${t.state==='active'?' selected':''}>active</option><option value="paused"${t.state==='paused'?' selected':''}>paused</option><option value="archived"${t.state==='archived'?' selected':''}>archived</option></select></div><div class="row"><label class="muted">PRIMARY EVENT TYPE</label><select data-field="primary_event_type_index">${primaryOptions}</select></div><div class="eventedit"><span class="muted">EVENT TYPES</span>${eventRows}</div><div class="row"><button type="button" onclick="addEventType(this)">ADD EVENT TYPE</button></div><div class="row"><button class="yes" onclick="saveTrack('${esc(t.id)}')">SAVE EDITS</button><button onclick="togglePin('${esc(t.id)}',${t.pinned?'false':'true'})">${t.pinned?'UNPIN':'PIN'}</button><button onclick="moveTrack('${esc(t.id)}',-1)">MOVE UP</button><button onclick="moveTrack('${esc(t.id)}',1)">MOVE DOWN</button></div></div>`}
async function loadTracks(){try{let d=await api('work/tracks');managedTracks=d.tracks.slice().sort((a,b)=>a.sort_order-b.sort_order||a.created_at-b.created_at);$('manageTracks').innerHTML=managedTracks.map(trackEditor).join('');managedTracksLoaded=true}catch(e){$('manageStatus').innerHTML='<span class="bad">'+esc(e.message)+'</span>'}}
function trackCardElement(id){return [...document.querySelectorAll('[data-track-card]')].find(card=>card.dataset.trackCard===id)}
function addEventType(button){let card=button.closest('[data-track-card]'),editor=card.querySelector('.eventedit'),index=editor.querySelectorAll('[data-event-row]').length;editor.insertAdjacentHTML('beforeend',eventEditorRow({id:'',label:'',active:true,contributes_to_headline:true}));card.querySelector('[data-field=primary_event_type_index]').insertAdjacentHTML('beforeend',`<option value="${index}">New event ${index+1}</option>`)}
function toggleEventActive(button){let row=button.closest('[data-event-row]'),active=row.querySelector('[data-field=event-active]');active.checked=!active.checked;row.classList.toggle('inactive',!active.checked);button.textContent=active.checked?'REMOVE':'RESTORE'}
async function saveTrack(id){let card=trackCardElement(id),target=card.querySelector('[data-field=target]').value.trim();let rows=[...card.querySelectorAll('[data-event-row]')];let event_types=rows.map((row,index)=>({id:row.dataset.eventId||null,label:row.querySelector('[data-field=event-label]').value.trim(),sort_order:index,active:row.querySelector('[data-field=event-active]').checked,contributes_to_headline:row.querySelector('[data-field=event-headline]').checked}));let primary_event_type_index=Number(card.querySelector('[data-field=primary_event_type_index]').value);if(event_types.some(et=>!et.label)){$('manageStatus').innerHTML='<span class="bad">Event type labels cannot be blank.</span>';return}if(!event_types[primary_event_type_index]?.active){$('manageStatus').innerHTML='<span class="bad">Choose an active primary event type.</span>';return}let body={name:card.querySelector('[data-field=name]').value.trim(),short_label:card.querySelector('[data-field=short_label]').value.trim(),cadence:card.querySelector('[data-field=cadence]').value,state:card.querySelector('[data-field=state]').value,target:target===''?null:Number(target),primary_event_type_index,event_types};try{await api('work/tracks/'+encodeURIComponent(id),{method:'PATCH',body:JSON.stringify(body)});$('manageStatus').innerHTML='<span class="good">Track updated. Removed event types stay in history.</span>';await loadWork(true)}catch(e){$('manageStatus').innerHTML='<span class="bad">'+esc(e.message)+'</span>'}}
async function togglePin(id,pinned){await api('work/tracks/'+encodeURIComponent(id),{method:'PATCH',body:JSON.stringify({pinned})});await loadWork(true)}
async function moveTrack(id,delta){let ordered=managedTracks.slice(),index=ordered.findIndex(t=>t.id===id),next=index+delta;if(index<0||next<0||next>=ordered.length)return;[ordered[index],ordered[next]]=[ordered[next],ordered[index]];try{for(let i=0;i<ordered.length;i++)await api('work/tracks/'+encodeURIComponent(ordered[i].id),{method:'PATCH',body:JSON.stringify({sort_order:i*10})});await loadWork(true)}catch(e){$('manageStatus').innerHTML='<span class="bad">'+esc(e.message)+'</span>';await loadTracks()}}
async function createTrack(){const name=$('newTrackName').value.trim();const short=$('newTrackShort').value.trim();const unit=$('newTrackUnit').value.trim();const mode=$('newTrackMode').value;const cadence=$('newTrackCadence').value;const targetText=$('newTrackTarget').value.trim();const pinned=$('newTrackPinned').checked;const ets=$('newTrackEventTypes').value.split(',').map(x=>x.trim()).filter(Boolean);if(!name||!short||!unit||!ets.length){$('manageStatus').innerHTML='<span class="bad">Fill name, short label, unit and at least one event type.</span>';return}try{await api('work/tracks',{method:'POST',body:JSON.stringify({name,short_label:short,mode,unit,cadence,target:targetText===''?null:Number(targetText),event_types:ets,primary_event_type_index:0,pinned,sort_order:managedTracks.length*10})});$('manageStatus').innerHTML='<span class="good">Track created.</span>';$('newTrackName').value='';$('newTrackShort').value='';$('newTrackUnit').value='';$('newTrackTarget').value='';$('newTrackEventTypes').value='';$('newTrackPinned').checked=false;await loadWork(true)}catch(e){$('manageStatus').innerHTML='<span class="bad">'+esc(e.message)+'</span>'}}

// --------------------------------------------------------------- AUTHORITY
function reqCard(x){let cmds=(x.commands_preview||[]).map(esc).join('\n');let files=(x.files||[]).map(esc).join('\n');return `<div class="card req"><div class="kv"><span>TASK</span><b>${esc(x.task)}</b><span>AGENT</span><b>${esc(x.agent)}</b><span>ACCESS</span><b class="warn">${esc(x.scope).toUpperCase()}</b><span>PROJECT</span><b>${esc(x.project||'whole PC')}</b><span>DURATION</span><b>${x.duration_seconds}s</b><span>ADMIN</span><b>${x.requires_admin?'YES':'NO'}</b></div>${cmds?`<pre>${cmds}</pre>`:''}${files?`<pre>${files}</pre>`:''}<div class="row"><button class="danger" onclick="deny('${x.request_id}')">DENY</button><button class="yes" onclick="approve('${x.request_id}')">CONFIRM</button></div></div>`}
function grantCard(x){return `<div class="card grant"><div class="kv"><span>TASK</span><b>${esc(x.task)}</b><span>AGENT</span><b>${esc(x.agent)}</b><span>ACCESS</span><b class="good">${esc(x.scope).toUpperCase()}</b><span>PROJECT</span><b>${esc(x.project||'whole PC')}</b><span>REMAINING</span><b>${secs(x.expires_at)}</b></div><div class="row"><button class="danger" onclick="revoke('${x.grant_id}')">REVOKE</button></div></div>`}
async function refreshAll(){try{let d=await api('api/state');$('tokenId').textContent=d.token_id;$('pending').innerHTML=d.pending.length?d.pending.map(reqCard).join(''):'<div class="empty">No approval waiting.</div>';$('grants').innerHTML=d.grants.length?d.grants.map(grantCard).join(''):'<div class="empty">No elevated control active.</div>';$('status').innerHTML=`<span class="good">PHONE AUTHENTICATED</span> · ${d.pending.length} pending · ${d.grants.length} active`;$('audit').textContent=(d.audit||[]).slice().reverse().map(x=>new Date(x.at*1000).toLocaleTimeString()+'  '+x.event+'  '+JSON.stringify(x.details)).join('\n')||'No actions yet.'}catch(e){$('status').innerHTML='<span class="bad">'+esc(e.message)+'</span>'}}
async function approve(id){await api('api/approve/'+encodeURIComponent(id),{method:'POST'});refreshAll()}
async function deny(id){await api('api/deny/'+encodeURIComponent(id),{method:'POST'});refreshAll()}
async function revoke(id){await api('api/revoke/'+encodeURIComponent(id),{method:'POST'});refreshAll()}
async function revokeAll(){await api('api/revoke-all',{method:'POST'});refreshAll()}
async function manualGrant(){await api('api/manual-grant',{method:'POST',body:JSON.stringify({scope:$('manualScope').value,seconds:Number($('manualSeconds').value),task:'Manual phone-authorized MAZ session'})});refreshAll()}
async function logout(){await api('api/logout',{method:'POST'});location.href='./'}

// ------------------------------------------------------------------ VOICE
let voiceAudio=null;
function voiceRow(v,cur){return `<div class="card voice" data-voice-id="${esc(v.reference_id)}"><div class="row"><b>${esc(v.name)}</b>${v.reference_id===cur?' <span class="good">IN USE</span>':''}</div><p class="muted">${esc(v.description||'')}</p><div class="row"><button data-act="preview" data-id="${esc(v.reference_id)}" onclick="previewVoice(this.dataset.id)">PLAY</button><button class="yes" data-act="use" data-id="${esc(v.reference_id)}" data-name="${esc(v.name)}" onclick="useVoice(this.dataset.id,this.dataset.name)">USE</button></div></div>`}
async function loadVoices(){try{let d=await api('api/voices');$('voiceNow').textContent='Now speaking: '+(d.current_name||d.current);$('voiceList').innerHTML=d.voices.map(v=>voiceRow(v,d.current)).join('');window.voiceCur=d.current}catch(e){$('voiceStatus').textContent=e.message}}
async function previewVoice(id){$('voiceStatus').textContent='Loading preview...';try{let r=await fetch('api/voices/preview',{method:'POST',headers:H,body:JSON.stringify({reference_id:id})});if(!r.ok){let d=await r.json().catch(()=>({}));throw new Error(d.detail||'preview failed')}let b=await r.blob();if(voiceAudio)voiceAudio.pause();voiceAudio=new Audio(URL.createObjectURL(b));await voiceAudio.play();$('voiceStatus').textContent=''}catch(e){$('voiceStatus').textContent='Preview: '+e.message}}
async function sayIt(){let t=$('sayText').value.trim();if(!t){$('sayStatus').textContent='Type some text first.';return}$('sayStatus').textContent='Speaking...';try{let r=await fetch('api/say',{method:'POST',headers:H,body:JSON.stringify({text:t,voice:window.voiceCur||null})});if(!r.ok){let d=await r.json().catch(()=>({}));throw new Error(d.detail||'say failed')}let b=await r.blob();if(voiceAudio)voiceAudio.pause();voiceAudio=new Audio(URL.createObjectURL(b));await voiceAudio.play();$('sayStatus').textContent='Played.'}catch(e){$('sayStatus').textContent=e.message}}
async function useVoice(id,name){try{await api('api/voices/select',{method:'POST',body:JSON.stringify({reference_id:id,name:name||''})});$('voiceStatus').textContent='Voice saved.';loadVoices()}catch(e){$('voiceStatus').textContent=e.message}}
async function searchVoices(){let q=$('voiceQ').value.trim();if(!q)return;$('voiceSearchRes').textContent='Searching...';try{let d=await api('api/voices/search?q='+encodeURIComponent(q));$('voiceSearchRes').innerHTML=d.voices.length?d.voices.map(v=>voiceRow(v,window.voiceCur)).join(''):'<p class="muted">No matches.</p>'}catch(e){$('voiceSearchRes').textContent=e.message}}

loadWork(true);refreshAll();setInterval(()=>loadWork(false),4000);setInterval(refreshAll,3000);
</script></main></body></html>"""


LOGIN = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#fbfaf7" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#2a1a12" media="(prefers-color-scheme: dark)"><meta name="referrer" content="no-referrer">
<title>Sign in to Piblo</title><style>
:root{color-scheme:light dark;--bg:#fbfaf7;--card:#fffaf0;--ink:#2a1a12;--muted:#5b4336;--line:#c9a877;--accent:#ee5a36;--error:#b3301a}
@media(prefers-color-scheme:dark){:root{--bg:#2a1a12;--card:#352318;--ink:#fff4e0;--muted:#c8b8a8;--line:#7a624e;--error:#ff987d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,sans-serif;display:grid;min-height:100vh;min-height:100dvh;place-items:center;padding:20px}
main{width:min(100%,420px);padding:clamp(22px,7vw,38px);background:var(--card);border:1px solid var(--line);border-radius:24px}
h1{margin:0 0 8px;font-size:clamp(26px,7vw,36px);line-height:1.13;letter-spacing:-.035em}
p{color:var(--muted);margin:0 0 22px}label{display:block;font-weight:650;margin-bottom:6px}
input,button{display:block;width:100%;min-height:48px;border:1px solid var(--line);border-radius:10px;font:inherit;padding:10px 12px}
input{letter-spacing:.12em;text-transform:uppercase;background:var(--bg);color:var(--ink)}
button{margin-top:12px;border-color:transparent;background:var(--accent);color:#2a1a12;font-weight:750;cursor:pointer}
:focus-visible{outline:2px solid var(--ink);outline-offset:3px}#sign-in-error{color:var(--error);margin-top:12px}
</style></head><body><main>
<h1>Sign in to Piblo</h1>
<p>In the Piblo Windows app, go to <strong>Settings &rsaquo; Browser sign-in</strong> to generate a one-time code.</p>
<form method="post" action="/control/session/code">
<label for="code">Sign-in code</label>
<input id="code" name="code" type="text" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" maxlength="8" required>
<p id="sign-in-error" hidden role="alert">This code is invalid or expired. Generate a new one on your computer.</p>
<button>Sign in</button>
</form></main></body></html>"""


def build_phone_app(
    settings: Settings, broker: AuthorityBroker, *, work_store: WorkStore | None = None, voices=None,
    browser_codes: PairingStore | None = None,
) -> FastAPI:
    phone = FastAPI(title="Piblo Control", docs_url=None, redoc_url=None, openapi_url=None)
    browser_codes = browser_codes if browser_codes is not None else PairingStore()
    install_validation_exception_handler(phone)
    if work_store is None:
        work_store = WorkStore(settings.work_dir)
    work_store.bootstrap()
    work_service = WorkService(work_store)

    def session_identity(request: Request, session: str | None) -> str:
        if request.headers.get(CONTROL_HEADER) != "1":
            raise HTTPException(403, "control_header_required")
        if not session:
            raise HTTPException(401, "phone_login_required")
        try:
            return broker.verify_phone_session(session, request.headers.get("user-agent", ""))
        except AuthorityError as error:
            raise HTTPException(401, str(error)) from error

    def require_session(request: Request, session: str | None) -> None:
        session_identity(request, session)

    @phone.get("/", response_class=HTMLResponse)
    def home(request: Request, maz_control_session: Annotated[str | None, Cookie()] = None):
        if maz_control_session:
            try:
                broker.verify_phone_session(
                    maz_control_session, request.headers.get("user-agent", "")
                )
                return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})
            except AuthorityError:
                pass
        return HTMLResponse(LOGIN, headers={"Cache-Control": "no-store"})

    def signed_session(request: Request):
        signed = broker.issue_phone_session(request.headers.get("user-agent", ""))
        response = RedirectResponse(url="/control/", status_code=303, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            COOKIE,
            signed,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=settings.control_phone_session_seconds,
            path="/control",
        )
        return response

    @phone.post("/session/code")
    def sign_in_with_code(request: Request, code: Annotated[str, Form(max_length=24)]):
        peer, _, _ = client_info(request)
        if request.headers.get(FUNNEL_HEADER) or not is_lan_ip(peer):
            raise HTTPException(403, "browser_sign_in_requires_local_network_or_tailnet")
        if not browser_codes.claim(code.strip().upper(), peer):
            return HTMLResponse(LOGIN.replace('id="sign-in-error" hidden', 'id="sign-in-error"'),
                                status_code=401, headers={"Cache-Control": "no-store"})
        return signed_session(request)

    @phone.post("/session/login")
    def login(request: Request, token: Annotated[str, Form()]):
        # The existing /pair browser flow still posts a Core credential here.
        if not settings.token_configured:
            raise HTTPException(503, "MAZ_TOKEN_not_configured")
        if not hmac.compare_digest(token.encode(), settings.token.encode()):
            raise HTTPException(401, "invalid_pairing_token")
        return signed_session(request)

    @phone.get("/api/state")
    def state(request: Request, maz_control_session: Annotated[str | None, Cookie()] = None):
        require_session(request, maz_control_session)
        return {
            "ok": True,
            "token_id": broker.token_id,
            "pending": broker.pending(),
            "grants": broker.active_grants(),
            "audit": broker.recent_audit(60),
        }

    @phone.post("/api/approve/{request_id}")
    def approve(request_id: str, request: Request, maz_control_session: Annotated[str | None, Cookie()] = None):
        require_session(request, maz_control_session)
        try:
            result = broker.approve(request_id)
        except AuthorityError as error:
            raise HTTPException(400, str(error)) from error
        result.pop("grant_token", None)
        return {"ok": True, "request": result}

    @phone.post("/api/deny/{request_id}")
    def deny(request_id: str, request: Request, maz_control_session: Annotated[str | None, Cookie()] = None):
        require_session(request, maz_control_session)
        try:
            return {"ok": True, "request": broker.deny(request_id)}
        except AuthorityError as error:
            raise HTTPException(400, str(error)) from error

    @phone.post("/api/revoke/{grant_id}")
    def revoke(grant_id: str, request: Request, maz_control_session: Annotated[str | None, Cookie()] = None):
        require_session(request, maz_control_session)
        try:
            return {"ok": True, "grant": broker.revoke(grant_id)}
        except AuthorityError as error:
            raise HTTPException(400, str(error)) from error

    @phone.post("/api/revoke-all")
    def revoke_all(request: Request, maz_control_session: Annotated[str | None, Cookie()] = None):
        require_session(request, maz_control_session)
        return {"ok": True, "revoked": broker.revoke_all()}

    @phone.post("/api/manual-grant")
    def manual_grant(body: ManualGrantRequest, request: Request, maz_control_session: Annotated[str | None, Cookie()] = None):
        require_session(request, maz_control_session)
        scope: Scope = body.scope
        requires_admin = scope == "admin"
        item = broker.request(
            task=body.task,
            agent="any",
            scope=scope,
            duration_seconds=body.seconds,
            requires_admin=requires_admin,
        )
        approved = broker.approve(item["request_id"])
        approved.pop("grant_token", None)
        return {"ok": True, "request": approved}

    @phone.post("/api/logout")
    def logout(request: Request, maz_control_session: Annotated[str | None, Cookie()] = None):
        require_session(request, maz_control_session)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/control")
        return response

    if voices is not None:
        def voice_guard(request: Request, maz_control_session: Annotated[str | None, Cookie()] = None) -> None:
            require_session(request, maz_control_session)

        install_voice_routes(phone, voices, prefix="/api", dependencies=[Depends(voice_guard)])

    install_work_routes(
        phone,
        service=work_service,
        store=work_store,
        require_session=require_session,
        session_identity=session_identity,
    )

    return phone

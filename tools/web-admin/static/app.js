// VE3 Studio Web — app.js
const API='';
let selCode=null,genCode=null,jobStart=null,lastLogLen=0,curPage=1,totalPages=1,charsVis=true;

// === Log state: per-job log tracking, only show running job's tab ===
let activeLogJobId=null;        // current running job id
let activeLogCode=null;         // project code of running job
let logHistory={};              // {jobId: {code, lines[], status}}
let viewingLogJobId=null;       // which tab user is viewing

// === Tab navigation ===
function showTab(t){
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('act'));
  document.getElementById('page-'+t).classList.add('act');
  document.querySelectorAll('.sb-btn').forEach(b=>b.classList.remove('act'));
  document.querySelectorAll('.sb-btn')[['home','gen','cfg'].indexOf(t)].classList.add('act');
  if(t==='gen') refreshGenProjectList();
  if(t==='cfg') loadConfig();
}

// === API helper ===
async function api(p,m='GET',b=null){
  try{const o={method:m,headers:{'Content-Type':'application/json'}};if(b)o.body=JSON.stringify(b);const r=await fetch(API+p,o);return await r.json()}catch(e){return{error:e.message}}
}

// === Clock ===
setInterval(()=>{const e=document.getElementById('clk');if(e)e.textContent=new Date().toLocaleTimeString('vi-VN')},1000);

function escapeHtml(value){
  return String(value ?? '').replace(/[&<>'"]/g,ch=>({
    '&':'&amp;',
    '<':'&lt;',
    '>':'&gt;',
    "'":"&#39;",
    '"':'&quot;'
  }[ch]));
}

function pct(done,total){return total>0?Math.round(done/total*100):0}

// === Mini progress bar HTML ===
function miniBar(done,total,fillClass){
  const p=pct(done,total);
  return '<div class="mini-bar">'
    +'<div class="mini-bar-track"><div class="mini-bar-fill '+fillClass+'" style="width:'+p+'%"></div></div>'
    +'<div class="mini-bar-label">'+done+'/'+total+'</div>'
    +'</div>';
}

// === Log rendering ===
function renderLogLines(logs){
  const box=document.getElementById('logs');
  box.innerHTML='';
  logs.forEach(l=>{
    const d=document.createElement('div');
    const text=String(l||'');
    let lvl='INFO';
    if(text.includes('ERROR')||text.includes('FATAL'))lvl='ERROR';
    else if(text.includes('WARNING')||text.includes('WARN'))lvl='WARNING';
    else if(text.includes('SUCCESS')||text.includes('DONE')||text.includes('FINISH'))lvl='SUCCESS';
    d.className='ll l'+lvl;
    d.textContent=text;
    box.appendChild(d);
  });
  box.scrollTop=box.scrollHeight;
}

// === Log tabs ===
function renderLogTabs(){
  const tabsEl=document.getElementById('log-tabs');
  if(!tabsEl) return;

  // Only show tabs for running jobs
  const entries=Object.entries(logHistory).filter(([id,h])=>h.status==='running');

  if(entries.length===0){
    tabsEl.innerHTML='';
    // If no running job, show empty or keep last finished
    if(!activeLogJobId){
      document.getElementById('logs').innerHTML='<div class="log-empty">No running job</div>';
    }
    return;
  }

  tabsEl.innerHTML=entries.map(([id,h])=>{
    const isActive=(viewingLogJobId===id);
    const dotClass=h.status==='running'?'dot-run':(h.status==='done'?'dot-done':(h.status==='error'?'dot-err':'dot-idle'));
    return '<button class="log-tab'+(isActive?' active':'')+'" onclick="switchLogTab(\''+id+'\')">'
      +'<span class="tab-dot '+dotClass+'"></span>'
      +escapeHtml(h.code)
      +'</button>';
  }).join('');
}

function switchLogTab(jobId){
  viewingLogJobId=jobId;
  const h=logHistory[jobId];
  if(h){
    renderLogLines(h.lines);
  }
  renderLogTabs();
}

// === HOME: Overview ===
async function pollOverview(){
  const r=await api('/api/overview');
  if(!r||r.error)return;
  document.getElementById('ov-proj').textContent=r.project_count||0;
  document.getElementById('ov-xls').textContent=r.projects_with_excel||0;
  document.getElementById('ov-vid').textContent=r.projects_with_video||0;
  const active=r.active_job;
  const runBtn=document.getElementById('btn-run');

  if(!(active&&active.status==='running')){
    runBtn.textContent='RUN';
    runBtn.classList.remove('running');

    // Job just finished — update history
    if(activeLogJobId && logHistory[activeLogJobId]){
      logHistory[activeLogJobId].status='done';
    }
    activeLogJobId=null;
    activeLogCode=null;
    lastLogLen=0;
    renderLogTabs();
    return;
  }

  // Active running job
  runBtn.textContent='STOP';
  runBtn.classList.add('running');

  const jobId=active.id;
  const jobCode=active.project_code||'';
  const logs=active.log_lines||[];

  // New job started
  if(activeLogJobId!==jobId){
    activeLogJobId=jobId;
    activeLogCode=jobCode;
    lastLogLen=0;
    logHistory[jobId]={code:jobCode,lines:[],status:'running'};
    viewingLogJobId=jobId; // auto-switch to new running job
    document.getElementById('logs').innerHTML='';
  }

  // Append new log lines
  if(logs.length>lastLogLen){
    const newLogs=logs.slice(lastLogLen);
    lastLogLen=logs.length;
    if(logHistory[jobId]){
      logHistory[jobId].lines=logHistory[jobId].lines.concat(newLogs);
      // Keep max 400 lines
      if(logHistory[jobId].lines.length>400){
        logHistory[jobId].lines=logHistory[jobId].lines.slice(-400);
      }
    }
    // Only render if user is viewing this tab
    if(viewingLogJobId===jobId){
      const box=document.getElementById('logs');
      newLogs.forEach(l=>{
        const d=document.createElement('div');
        const text=String(l||'');
        let lvl='INFO';
        if(text.includes('ERROR')||text.includes('FATAL'))lvl='ERROR';
        else if(text.includes('WARNING')||text.includes('WARN'))lvl='WARNING';
        else if(text.includes('SUCCESS')||text.includes('DONE')||text.includes('FINISH'))lvl='SUCCESS';
        d.className='ll l'+lvl;
        d.textContent=text;
        box.appendChild(d);
      });
      box.scrollTop=box.scrollHeight;
    }
  }

  renderLogTabs();
}


async function pollPing(){
  const r=await api('/api/overview');
  if(r&&!r.error){
    const cnt=r.server_count||0;
    document.getElementById('ov-srv').textContent=cnt;
  }
}

// === HOME: Projects (TABLE layout) ===
async function loadProjects(){
  const el=document.getElementById('plist');
  el.innerHTML='<div class="empty">Loading...</div>';
  const r=await api('/api/projects');
  if(!r||!r.items||!r.items.length){el.innerHTML='<div class="empty">No projects</div>';return}

  // Determine which project is currently running
  const runningCode=activeLogCode||null;

  let html='<table class="ptable">';
  html+='<thead><tr>'
    +'<th style="width:30px"></th>'
    +'<th>Code</th>'
    +'<th class="th-center">Ref</th>'
    +'<th>Characters</th>'
    +'<th>Images</th>'
    +'<th>Videos</th>'
    +'<th>Music</th>'
    +'<th style="width:50px"></th>'
    +'</tr></thead><tbody>';

  r.items.forEach(p=>{
    const c=p.counts||{};
    const cd=c.characters_done||0,ct=c.characters_total||0;
    const id_=c.scenes_done||0,it=c.scenes_total||c.images||0;
    const vd=c.videos_done||0,vt=c.videos||0;
    const md=c.music_done||0,mt_=c.music_total||0;
    const isRunning=(runningCode===p.code);
    const isSel=(selCode===p.code);
    const rowClass=(isRunning?'running-row':'')+(isSel?' sel':'');
    const statusClass=isRunning?'s-run':'s-idle';

    html+='<tr class="'+rowClass+'" onclick="selProj(\''+p.code+'\')">'
      +'<td><span class="proj-status '+statusClass+'"></span></td>'
      +'<td><span class="proj-code">'+escapeHtml(p.code)+'</span></td>'
      +'<td class="td-center td-num">'+(c.references||0)+'</td>'
      +'<td>'+miniBar(cd,ct,'fill-char')+'</td>'
      +'<td>'+miniBar(id_,it,'fill-img')+'</td>'
      +'<td>'+miniBar(vd,vt,'fill-vid')+'</td>'
      +'<td>'+miniBar(md,mt_,'fill-mus')+'</td>'
      +'<td><button class="btn-run-sm" onclick="event.stopPropagation();openModal(\''+p.code+'\')">▶</button></td>'
      +'</tr>';
  });

  html+='</tbody></table>';
  el.innerHTML=html;
}

function selProj(code){selCode=code;loadProjects()}

// === HOME: Servers ===
async function pollServers(){
  const r=await api('/api/servers');
  const el=document.getElementById('srvlist');
  if(!r||!r.items||!r.items.length){el.innerHTML='<div class="empty">No servers</div>';return}
  el.innerHTML=r.items.map(s=>
    '<div class="srv"><div class="sdot" style="background:'+(s.online?'var(--gn)':'var(--rd)')+'"></div>'
    +'<div class="srv-name">'+(s.name||s.url)+'</div>'
    +'<div class="srv-lat">'+(s.online?s.latency_ms+'ms':'offline')+'</div></div>'
  ).join('');
}

// === HOME: Logs ===
function clearLogs(){
  document.getElementById('logs').innerHTML='';
  lastLogLen=0;
  // Clear history for non-running jobs
  Object.keys(logHistory).forEach(id=>{
    if(logHistory[id].status!=='running'){
      delete logHistory[id];
    }
  });
  renderLogTabs();
}

// === HOME: Job controls ===
function openModal(code){document.getElementById('rm-proj').value=code;document.getElementById('run-modal').classList.add('show')}
function closeModal(){document.getElementById('run-modal').classList.remove('show')}
async function submitJob(){
  const code=document.getElementById('rm-proj').value;
  const mode=document.getElementById('rm-mode').value;
  closeModal();lastLogLen=0;
  document.getElementById('logs').innerHTML='';
  jobStart=Date.now();
  await api('/api/jobs','POST',{project_code:code,mode:mode});
  pollOverview();
}
async function toggleRun(){
  // Toggle queue worker via API
  const btn=document.getElementById('btn-run');
  if(btn.classList.contains('running')){
    // Stop active job
    if(activeLogJobId){
      await api('/api/jobs/'+activeLogJobId+'/stop','POST');
    }
    btn.textContent='▶ RUN';btn.classList.remove('running');
  } else {
    btn.textContent='⏹ STOP';btn.classList.add('running');
  }
}

// === GENERATE: Project list ===
async function refreshGenProjectList(){
  const r=await api('/api/projects');
  const sel=document.getElementById('gen-proj');
  if(!r||!r.items){sel.innerHTML='<option>No projects</option>';return}
  const ready=r.items.filter(p=>p.has_excel);
  sel.innerHTML=ready.map(p=>'<option value="'+p.code+'">'+p.code+'</option>').join('');
  document.getElementById('gen-hint').textContent=ready.length+' projects with Excel';
  if(genCode&&ready.find(p=>p.code===genCode))sel.value=genCode;
}

async function loadGenProject(){
  genCode=document.getElementById('gen-proj').value;
  if(!genCode)return;
  loadChars(genCode);
  loadScenes(genCode,1);
}

function toggleChars(){
  charsVis=!charsVis;
  document.getElementById('charlist').style.display=charsVis?'':'none';
  document.getElementById('chr-toggle').textContent=charsVis?'▼':'▶';
}

function bdg(st){
  const c={'done':'b-done','running':'b-run','error':'b-err'}[st]||'b-pend';
  const l={'done':'Done','running':'Running','error':'Error'}[st]||'Pending';
  return '<span class="badge '+c+'">'+l+'</span>';
}

// === GENERATE: Characters ===
async function loadChars(code){
  const el=document.getElementById('charlist');
  el.innerHTML='<div class="empty">Loading...</div>';
  const r=await api('/api/projects/'+encodeURIComponent(code)+'/characters');
  if(!r||!r.items){el.innerHTML='<div class="empty">No data</div>';return}
  document.getElementById('chr-cnt').textContent='('+r.total+')';
  if(!r.items.length){el.innerHTML='<div class="empty">No characters</div>';return}
  el.innerHTML=r.items.map(c=>{
    const img=c.image_url?'<img class="ithumb" src="'+c.image_url+'" loading="lazy">':'<div class="ithumb-ph">'+c.id+'</div>';
    const pr=c.english_prompt||c.vietnamese_prompt||'';
    return '<div class="icard">'+img+'<div class="iinfo">'
      +'<div class="irow"><span class="iid">'+c.id+'</span><span class="iname">'+(c.name||'')+' '+(c.role?'· '+c.role:'')+'</span>'+bdg(c.status)+'</div>'
      +'<textarea class="iprompt" rows="2">'+pr+'</textarea>'
      +'</div></div>'
  }).join('');
}

// === GENERATE: Scenes ===
async function loadScenes(code,page){
  const el=document.getElementById('scenelist');
  el.innerHTML='<div class="empty">Loading...</div>';
  const r=await api('/api/projects/'+encodeURIComponent(code)+'/scenes?page='+page+'&size=20');
  if(!r||!r.items){el.innerHTML='<div class="empty">No scenes</div>';return}
  curPage=r.page;totalPages=r.pages;
  document.getElementById('scn-cnt').textContent='('+r.total+')';
  const pg=document.getElementById('pager');
  pg.style.display=r.total>20?'flex':'none';
  document.getElementById('pg-info').textContent=curPage+'/'+totalPages;
  document.getElementById('pg-prev').disabled=curPage<=1;
  document.getElementById('pg-next').disabled=curPage>=totalPages;
  if(!r.items.length){el.innerHTML='<div class="empty">No scenes</div>';return}
  el.innerHTML=r.items.map(s=>{
    const sid='S'+String(s.scene_id).padStart(3,'0');
    const img=s.image_url?'<img class="ithumb" src="'+s.image_url+'" loading="lazy">':'<div class="ithumb-ph">'+sid+'</div>';
    const srt=s.srt_text?(s.srt_text.length>60?s.srt_text.substring(0,60)+'…':s.srt_text):'';
    return '<div class="icard">'+img+'<div class="iinfo">'
      +'<div class="irow"><span class="iid">'+sid+'</span><span class="iname">'+srt+'</span>'+bdg(s.status_img)+'<span class="badge '+(s.status_vid==='done'?'b-done':'b-pend')+'">V:'+(s.status_vid||'pending')+'</span></div>'
      +'<textarea class="iprompt" rows="2" placeholder="Image prompt...">'+(s.img_prompt||'')+'</textarea>'
      +'<textarea class="iprompt" rows="1" placeholder="Video prompt..." style="background:rgba(99,102,241,.06)">'+(s.video_prompt||'')+'</textarea>'
      +'</div></div>'
  }).join('');
}

function prevPage(){if(genCode&&curPage>1)loadScenes(genCode,curPage-1)}
function nextPage(){if(genCode&&curPage<totalPages)loadScenes(genCode,curPage+1)}

async function loadConfig(){
  const r=await api('/api/config');
  if(!r||r.error)return;
  const v=r.ve3||{};
  document.getElementById('cfg-conc').value=v.max_concurrent||1;
  document.getElementById('cfg-retry').value=v.retry_count||3;
  document.getElementById('cfg-ar').value=v.flow_aspect_ratio||'landscape';
  // Servers with toggle
  const srvEl=document.getElementById('cfg-servers');
  const srvs=v.servers||[];
  if(srvs.length){
    srvEl.innerHTML=srvs.map((s,i)=>{
      const en=s.enabled!==false;
      return '<div class="srv"><div class="sdot" style="background:'+(en?'var(--gn)':'var(--rd)')+'"></div>'
        +'<div class="srv-name">'+(s.name||'Sv-'+(i+1))+'</div>'
        +'<div style="font-size:9px;color:var(--mt);flex:1" title="'+s.url+'">'+s.url+'</div>'
        +'<button class="btn btn-sm" onclick="toggleSrv('+i+')">'+(en?'ON':'OFF')+'</button>'
        +'</div>'
    }).join('');
  } else {
    srvEl.innerHTML='<div class="empty">No servers configured</div>';
  }
}

async function toggleSrv(i){
  const r=await api('/api/config');
  if(!r||!r.ve3)return;
  const srvs=r.ve3.servers||[];
  if(i<srvs.length){
    srvs[i].enabled=!srvs[i].enabled;
    await api('/api/config','POST',{ve3:{servers:srvs}});
    loadConfig();
  }
}

async function saveSettings(){
  const data={ve3:{
    max_concurrent:parseInt(document.getElementById('cfg-conc').value)||1,
    retry_count:parseInt(document.getElementById('cfg-retry').value)||3,
    flow_aspect_ratio:document.getElementById('cfg-ar').value
  }};
  await api('/api/config','POST',data);
  document.getElementById('cfg-saved').textContent='✓ Saved';
  setTimeout(()=>document.getElementById('cfg-saved').textContent='',2000);
}

// === Boot ===
loadProjects();pollOverview();pollServers();
setInterval(pollOverview,3000);setInterval(loadProjects,10000);setInterval(pollServers,15000);

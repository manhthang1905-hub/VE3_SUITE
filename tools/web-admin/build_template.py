"""Generate index.html template for VE3 Web Admin."""
from pathlib import Path

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#0a0a0f;--sf:rgba(255,255,255,0.04);--bd:rgba(255,255,255,0.08);--tx:#e2e8f0;--mt:#64748b;--ac:#6366f1;--cy:#22d3ee;--gn:#10b981;--yl:#f59e0b;--rd:#ef4444}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--tx);height:100vh;overflow:hidden;display:flex;flex-direction:column}
a{color:var(--cy);text-decoration:none}
#hdr{display:flex;align-items:center;gap:12px;padding:10px 20px;border-bottom:1px solid var(--bd);flex-shrink:0;background:rgba(0,0,0,.5)}
.logo{font-weight:700;font-size:17px;background:linear-gradient(135deg,var(--ac),var(--cy));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.pill{display:flex;align-items:center;gap:6px;padding:5px 12px;border-radius:20px;font-size:11px;font-weight:500;border:1px solid var(--bd);background:var(--sf)}
.dot{width:8px;height:8px;border-radius:50%}
.dg{background:var(--gn);box-shadow:0 0 6px var(--gn)}.dr{background:var(--rd)}.dy{background:var(--yl)}
.pulse{animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.mla{margin-left:auto}
#main{display:grid;grid-template-columns:280px 1fr 300px;flex:1;overflow:hidden}
.pnl{display:flex;flex-direction:column;overflow:hidden;border-right:1px solid var(--bd)}
.pnl:last-child{border-right:none}
.pt{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.8px;color:var(--mt);padding:10px 14px;border-bottom:1px solid var(--bd);display:flex;align-items:center;gap:6px;flex-shrink:0}
.pb{flex:1;overflow-y:auto;padding:10px}
.pb::-webkit-scrollbar{width:3px}.pb::-webkit-scrollbar-thumb{background:var(--bd);border-radius:2px}
.card{padding:10px;border-radius:9px;border:1px solid var(--bd);margin-bottom:7px;cursor:pointer;transition:all .15s;background:var(--sf)}
.card:hover,.card.act{border-color:var(--ac);background:rgba(99,102,241,.08)}
.cname{font-size:12px;font-weight:600;margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cmeta{font-size:10px;color:var(--mt);margin-bottom:6px}
.prow{display:flex;align-items:center;gap:5px;font-size:9px;color:var(--mt);margin-bottom:3px}
.pbar{flex:1;height:3px;background:rgba(255,255,255,.07);border-radius:2px;overflow:hidden}
.pfill{height:100%;border-radius:2px;transition:width .3s}
.pref{background:var(--cy)}.pimg{background:var(--gn)}.pvid{background:var(--ac)}
.cact{display:flex;gap:5px;margin-top:6px}
.btn{padding:3px 9px;border-radius:6px;border:none;font-size:10px;font-weight:500;cursor:pointer;transition:all .15s}
.bg{background:var(--gn);color:#000}.br{background:var(--rd);color:#fff}
.bsm{background:var(--sf);border:1px solid var(--bd);color:var(--tx);padding:2px 7px;font-size:10px;cursor:pointer}
.btn:hover,.bsm:hover{opacity:.8}
.sec{padding:10px;border-top:1px solid var(--bd);flex-shrink:0}
.slbl{font-size:10px;font-weight:600;color:var(--mt);text-transform:uppercase;letter-spacing:.8px;margin-bottom:7px}
.sgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-bottom:8px}
.sstat{text-align:center;padding:5px;background:var(--sf);border-radius:7px;border:1px solid var(--bd)}
.snum{font-size:16px;font-weight:700}.slab{font-size:9px;color:var(--mt)}
.amon{padding:14px;flex-shrink:0;border-bottom:1px solid var(--bd)}
.empty{text-align:center;color:var(--mt);font-size:12px;padding:24px 0}
.mhd{display:flex;align-items:center;gap:9px;margin-bottom:10px}
.sdot{width:11px;height:11px;border-radius:50%;flex-shrink:0}
.mname{font-size:15px;font-weight:700}
.mop{font-size:12px;color:var(--cy);font-weight:500;margin-bottom:10px;padding:4px 10px;background:rgba(34,211,238,.06);border-radius:6px;display:inline-block}
.tmrs{display:flex;gap:12px;margin-bottom:10px}
.tbox{padding:7px 12px;border-radius:8px;border:1px solid var(--bd);background:rgba(0,0,0,.3);text-align:center;flex:1}
.tval{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.tlab{font-size:9px;color:var(--mt);margin-top:1px}
.sbrow{display:flex;align-items:center;gap:7px;font-size:10px}
.sbar{flex:1;height:4px;background:rgba(255,255,255,.07);border-radius:2px;overflow:hidden}
.sbfill{height:100%;border-radius:2px;transition:width 1s,background 1s}
.sgw{flex:1;overflow-y:auto;padding:10px}
.scgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(58px,1fr));gap:5px}
.scell{border-radius:7px;padding:5px 3px;border:1px solid var(--bd);text-align:center;font-size:9px;transition:border-color .15s;background:var(--sf)}
.scell:hover{border-color:var(--ac)}
.scnum{font-size:10px;font-weight:600;margin-bottom:3px}
.scic{display:flex;justify-content:center;gap:2px}
.si{width:9px;height:9px;border-radius:2px}
.sip{background:rgba(255,255,255,.1)}.sid{background:var(--gn)}.sie{background:var(--rd)}.sig{background:var(--yl);animation:pulse 1s infinite}
.lhdr{display:flex;align-items:center;gap:7px;padding:7px 12px;border-bottom:1px solid var(--bd);flex-shrink:0}
.ltitle{font-size:10px;font-weight:600;color:var(--mt);text-transform:uppercase;letter-spacing:.8px;flex:1}
#logs{flex:1;overflow-y:auto;padding:7px 10px;font-family:'Cascadia Code',monospace;font-size:10px;line-height:1.6}
#logs::-webkit-scrollbar{width:3px}#logs::-webkit-scrollbar-thumb{background:var(--bd);border-radius:2px}
.ll{padding:1px 0;border-bottom:1px solid rgba(255,255,255,.025);white-space:pre-wrap;word-break:break-all}
.lINFO{color:#94a3b8}.lSUCCESS{color:var(--gn)}.lERROR{color:var(--rd)}.lWARNING{color:var(--yl)}
.srvsec{border-top:1px solid var(--bd);flex-shrink:0;padding:10px}
.srvcard{display:flex;align-items:center;gap:7px;padding:6px 9px;border-radius:7px;border:1px solid var(--bd);margin-bottom:5px;background:var(--sf);font-size:11px}
.srvname{flex:1;font-weight:500}
.srvlat{font-size:10px;color:var(--mt)}
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:100}
.modal-bg.show{display:flex}
.modal{background:#12121a;border:1px solid var(--bd);border-radius:14px;padding:20px;width:420px;max-height:80vh;overflow-y:auto}
.modal h3{font-size:14px;margin-bottom:12px;color:var(--cy)}
.modal label{display:block;font-size:11px;color:var(--mt);margin:8px 0 3px}
.modal input,.modal select{width:100%;padding:6px 10px;background:var(--sf);border:1px solid var(--bd);border-radius:6px;color:var(--tx);font-size:12px}
.modal .mbtn{display:flex;gap:8px;margin-top:14px;justify-content:flex-end}
.icard{display:flex;gap:8px;padding:8px;border-radius:8px;border:1px solid var(--bd);margin-bottom:6px;background:var(--sf);transition:border-color .15s}
.icard:hover{border-color:var(--ac)}
.ithumb{width:80px;height:56px;border-radius:5px;object-fit:cover;background:#1a1a2e;flex-shrink:0}
.ithumb-ph{width:80px;height:56px;border-radius:5px;background:#1a1a2e;flex-shrink:0;display:flex;align-items:center;justify-content:center;color:var(--mt);font-size:9px}
.iinfo{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}
.irow{display:flex;align-items:center;gap:6px}
.iid{font-size:11px;font-weight:600}
.iname{font-size:10px;color:var(--mt);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ibadge{padding:1px 6px;border-radius:4px;font-size:9px;font-weight:600}
.ib-done{background:rgba(16,185,129,.15);color:var(--gn)}
.ib-pending{background:rgba(255,255,255,.06);color:var(--mt)}
.ib-running{background:rgba(99,102,241,.15);color:var(--ac)}
.ib-error{background:rgba(239,68,68,.15);color:var(--rd)}
.iprompt{font-size:10px;color:#94a3b8;background:rgba(0,0,0,.2);border:1px solid var(--bd);border-radius:4px;padding:3px 6px;width:100%;resize:none;font-family:inherit;line-height:1.4}
.iprompt:focus{outline:none;border-color:var(--ac)}
.pager{display:flex;align-items:center;gap:8px;padding:8px 10px;border-top:1px solid var(--bd);flex-shrink:0}
.pager button{padding:3px 10px;border-radius:5px;border:1px solid var(--bd);background:var(--sf);color:var(--tx);font-size:10px;cursor:pointer}
.pager button:hover{border-color:var(--ac)}
.pager button:disabled{opacity:.3;cursor:default}
.pager span{font-size:10px;color:var(--mt)}
.collapse-btn{background:none;border:none;color:var(--mt);font-size:10px;cursor:pointer;padding:2px 6px}
"""

BODY = """
<div id="hdr">
  <span class="logo">VE3 Suite</span>
  <div class="pill"><div id="srv-dot" class="dot dr"></div><span id="srv-lbl">Server</span></div>
  <div class="pill"><div id="job-dot" class="dot dr"></div><span id="job-lbl">No Job</span></div>
  <div class="pill"><span id="proj-cnt">0 projects</span></div>
  <div class="pill mla"><span id="clk"></span></div>
</div>

<div id="main">
  <div class="pnl">
    <div class="pt">📁 Projects <button onclick="loadProjects()" class="bsm" style="margin-left:auto">↺</button></div>
    <div class="pb" id="plist"><div class="empty">Loading...</div></div>
    <div class="sec">
      <div class="slbl">Overview</div>
      <div class="sgrid">
        <div class="sstat"><div class="snum" id="ov-proj">0</div><div class="slab">Projects</div></div>
        <div class="sstat"><div class="snum" id="ov-xls" style="color:var(--cy)">0</div><div class="slab">Excel</div></div>
        <div class="sstat"><div class="snum" id="ov-vid" style="color:var(--gn)">0</div><div class="slab">w/ Video</div></div>
      </div>
    </div>
    <div class="sec">
      <div class="slbl">Progress</div>
      <div id="progress-cards" style="padding:4px 8px"></div>
    </div>
  </div>

  <div class="pnl">
    <div class="pt">🎯 Active Job</div>
    <div class="amon" id="amon"><div class="empty">No active job</div></div>
    <div class="pt" style="border-top:1px solid var(--bd)">👤 Characters <span id="chr-cnt" style="color:var(--mt);font-weight:400;text-transform:none;letter-spacing:0">(0)</span> <button class="collapse-btn" id="chr-toggle" onclick="toggleChars()" style="margin-left:auto">▼</button></div>
    <div id="charlist" style="max-height:200px;overflow-y:auto;padding:6px"></div>
    <div class="pt" style="border-top:1px solid var(--bd)">🎬 Scenes <span id="sgn" style="color:var(--mt);font-weight:400;margin-left:4px;text-transform:none;letter-spacing:0"></span></div>
    <div class="sgw" id="scenelist"></div>
    <div class="pager" id="pager" style="display:none"><button id="pg-prev" onclick="prevPage()">◀ Prev</button><span id="pg-info">1/1</span><button id="pg-next" onclick="nextPage()">Next ▶</button></div>
  </div>

  <div class="pnl">
    <div class="lhdr">
      <span class="ltitle">📋 Logs</span>
      <div id="log-tabs" style="display:flex;gap:2px;flex:1;overflow-x:auto"></div>
      <button onclick="clearLogs()" class="bsm">🗑</button>
      <label style="font-size:10px;color:var(--mt);display:flex;align-items:center;gap:3px"><input type="checkbox" id="asc" checked> Auto</label>
    </div>
    <div id="logs"></div>
    <div class="srvsec">
      <div class="slbl" style="margin-bottom:7px;display:flex;align-items:center">
        ⚡ Servers
        <button onclick="pollServers()" class="bsm" style="margin-left:auto">↺</button>
      </div>
      <div id="srvlist"></div>
    </div>
  </div>
</div>

<div class="modal-bg" id="run-modal">
  <div class="modal">
    <h3>▶ Run Pipeline</h3>
    <label>Project</label>
    <input id="rm-proj" readonly>
    <label>Mode</label>
    <select id="rm-mode">
      <option value="all">All (Full Pipeline)</option>
      <option value="srt-excel-only">SRT → Excel Only</option>
      <option value="excel-only">Excel Only</option>
      <option value="ve3-only">VE3 Only</option>
    </select>
    <div class="mbtn">
      <button class="btn" style="background:var(--bd);color:var(--tx)" onclick="closeModal()">Cancel</button>
      <button class="btn bg" onclick="submitJob()">▶ Start</button>
    </div>
  </div>
</div>
"""

JS = r"""
const API='';
let selCode=null,jobStart=null,lastLogLen=0,pollId=null;

setInterval(()=>{document.getElementById('clk').textContent=new Date().toLocaleTimeString('vi-VN')},1000);
setInterval(()=>{const e=document.getElementById('jtimer');if(e&&jobStart)e.textContent=fmtT(Math.floor((Date.now()-jobStart)/1000))},1000);

async function api(p,m='GET',b=null){
  try{const o={method:m,headers:{'Content-Type':'application/json'}};if(b)o.body=JSON.stringify(b);const r=await fetch(API+p,o);return await r.json()}catch(e){return{error:e.message}}
}
function fmtT(s){if(s<60)return s+'s';const m=Math.floor(s/60),sec=s%60;return m<60?m+'m '+sec+'s':Math.floor(m/60)+'h '+(m%60)+'m'}

async function pollPing(){
  const r=await api('/api/ping');
  const ok=r&&r.ok;
  document.getElementById('srv-dot').className='dot '+(ok?'dg pulse':'dr');
  document.getElementById('srv-lbl').textContent=ok?'Online':'Offline';
}

async function pollOverview(){
  const r=await api('/api/overview');
  if(!r||r.error)return;
  document.getElementById('ov-proj').textContent=r.project_count||0;
  document.getElementById('ov-xls').textContent=r.projects_with_excel||0;
  document.getElementById('ov-vid').textContent=r.projects_with_video||0;
  document.getElementById('proj-cnt').textContent=(r.project_count||0)+' projects';
  const aj=r.active_job;
  const jd=document.getElementById('job-dot'),jl=document.getElementById('job-lbl');
  if(aj&&aj.status==='running'){
    jd.className='dot dg pulse';jl.textContent='Running: '+aj.project_code;
    if(!jobStart)jobStart=Date.now();
    renderMonitor(aj);
  }else{
    jd.className='dot dr';jl.textContent='No Job';jobStart=null;
    document.getElementById('amon').innerHTML='<div class="empty">No active job</div>';
  }
}

function renderMonitor(aj){
  const mon=document.getElementById('amon');
  const elapsed=jobStart?fmtT(Math.floor((Date.now()-jobStart)/1000)):'—';
  const logs=aj.log_lines||[];
  const lastMsg=logs.length?logs[logs.length-1]:'—';
  const lastClean=lastMsg.replace(/^\[.*?\]\s*/,'').substring(0,60);
  mon.innerHTML=`
    <div class="mhd"><div class="sdot dg pulse" style="box-shadow:0 0 7px var(--gn)"></div><div class="mname">${aj.project_code}</div>
      <span style="font-size:10px;color:var(--mt);margin-left:auto">mode: ${aj.mode}</span></div>
    <div class="mop">${lastClean}</div>
    <div class="tmrs">
      <div class="tbox"><div class="tval" id="jtimer">${elapsed}</div><div class="tlab">Runtime</div></div>
      <div class="tbox"><div class="tval" style="color:var(--cy)">${logs.length}</div><div class="tlab">Log Lines</div></div>
      <div class="tbox"><div class="tval" style="color:${aj.status==='running'?'var(--gn)':'var(--rd)'}">${aj.status}</div><div class="tlab">Status</div></div>
    </div>
    <button class="btn br" onclick="stopJob('${aj.id}')" style="width:100%;padding:6px">⏹ Stop Job</button>`;
  // Update logs panel
  if(logs.length>lastLogLen){
    const el=document.getElementById('logs');
    for(let i=lastLogLen;i<logs.length;i++){
      const d=document.createElement('div');
      const line=logs[i]||'';
      let cls='lINFO';
      if(line.includes('ERROR'))cls='lERROR';
      else if(line.includes('SUCCESS')||line.includes('✓')||line.includes('DONE'))cls='lSUCCESS';
      else if(line.includes('WARNING')||line.includes('⚠'))cls='lWARNING';
      d.className='ll '+cls;d.textContent=line;el.appendChild(d);
    }
    lastLogLen=logs.length;
    if(document.getElementById('asc').checked)el.scrollTop=el.scrollHeight;
  }
}

async function loadProjects(){
  const el=document.getElementById('plist');
  el.innerHTML='<div class="empty">Loading...</div>';
  const r=await api('/api/projects');
  if(!r||!r.items||!r.items.length){el.innerHTML='<div class="empty">No projects</div>';return}
  function pct(d,t){return t>0?Math.round(d/t*100):0}
  el.innerHTML=r.items.map(p=>{
    const c=p.counts||{};
    const charD=c.characters_done||0,charT=c.characters_total||0;
    const imgD=c.scenes_done||0,imgT=c.scenes_total||c.images||0;
    const vidD=c.videos_done||0,vidT=c.videos||0;
    const musD=c.music_done||0,musT=c.music_total||0;
    const id='pc-'+p.code;
    return `<div class="card" id="${id}" onclick="selProj('${p.code}')">
      <div class="cname" title="${p.code}">${p.code}</div>
      <div class="cmeta">${c.references||0} ref \u00b7 ${c.images||0} img \u00b7 ${c.videos||0} vid</div>
      <div class="prow"><span style="min-width:30px">Char</span><div class="pbar"><div class="pfill pref" style="width:${pct(charD,charT)}%"></div></div><span>${charD}/${charT}</span></div>
      <div class="prow"><span style="min-width:30px">Img</span><div class="pbar"><div class="pfill pimg" style="width:${pct(imgD,imgT)}%"></div></div><span>${imgD}/${imgT}</span></div>
      <div class="prow"><span style="min-width:30px">Vid</span><div class="pbar"><div class="pfill pvid" style="width:${pct(vidD,vidT)}%"></div></div><span>${vidD}/${vidT}</span></div>
      <div class="prow"><span style="min-width:30px">Music</span><div class="pbar"><div class="pfill" style="width:${pct(musD,musT)}%;background:var(--ac)"></div></div><span>${musD}/${musT}</span></div>
      <div class="cact">
        <button class="btn bg" onclick="event.stopPropagation();openModal('${p.code}')">\u25b6 Run</button>
      </div>
    </div>`
  }).join('');
  renderProgress(r.items);
}

function renderProgress(items){
  const el=document.getElementById('progress-cards');
  if(!items||!items.length){el.innerHTML='';return}
  function bar(d,t,col){const w=t>0?Math.round(d/t*100):0;return '<div style="display:flex;align-items:center;gap:4px;margin:1px 0"><div style="flex:1;height:5px;background:rgba(255,255,255,.06);border-radius:3px;overflow:hidden"><div style="width:'+w+'%;height:100%;background:'+col+';border-radius:3px;transition:width .3s"></div></div><span style="font-size:9px;color:var(--mt);min-width:32px;text-align:right">'+d+'/'+t+'</span></div>'}
  el.innerHTML=items.map(p=>{
    const c=p.counts||{};
    const charD=c.characters_done||0,charT=c.characters_total||0;
    const imgD=c.scenes_done||0,imgT=c.scenes_total||c.images||0;
    const vidD=c.videos_done||0,vidT=c.videos||0;
    const musD=c.music_done||0,musT=c.music_total||0;
    return '<div style="background:var(--sf);border:1px solid var(--bd);border-radius:6px;padding:6px 8px;margin-bottom:4px">'
      +'<div style="font-size:10px;font-weight:600;margin-bottom:3px;color:var(--cy)">'+p.code+'</div>'
      +'<div style="font-size:8px;color:var(--mt)">Characters</div>'+bar(charD,charT,'var(--rd)')
      +'<div style="font-size:8px;color:var(--mt)">Images</div>'+bar(imgD,imgT,'var(--gn)')
      +'<div style="font-size:8px;color:var(--mt)">Videos</div>'+bar(vidD,vidT,'var(--cy)')
      +'<div style="font-size:8px;color:var(--mt)">Music</div>'+bar(musD,musT,'var(--ac)')
      +'</div>'
  }).join('');
}


async function selProj(code){
  selCode=code;curPage=1;
  document.querySelectorAll('.card').forEach(c=>c.classList.remove('act'));
  const el=document.getElementById('pc-'+code);if(el)el.classList.add('act');
  document.getElementById('sgn').textContent=code;
  loadChars(code);loadScenes(code,1);
}

let charsVisible=true;
function toggleChars(){
  charsVisible=!charsVisible;
  document.getElementById('charlist').style.display=charsVisible?'':'none';
  document.getElementById('chr-toggle').textContent=charsVisible?'\u25bc':'\u25b6';
}

function badge(st){
  const cls={'done':'ib-done','running':'ib-running','error':'ib-error'}[st]||'ib-pending';
  const lbl={'done':'Done','running':'Running','error':'Error'}[st]||'Pending';
  return `<span class="ibadge ${cls}">${lbl}</span>`;
}

async function loadChars(code){
  const el=document.getElementById('charlist');
  el.innerHTML='<div class="empty" style="padding:8px">Loading...</div>';
  const r=await api('/api/projects/'+encodeURIComponent(code)+'/characters');
  if(!r||!r.items){el.innerHTML='<div class="empty">No data</div>';return}
  document.getElementById('chr-cnt').textContent='('+r.total+')';
  if(!r.items.length){el.innerHTML='<div class="empty">No characters</div>';return}
  el.innerHTML=r.items.map(c=>{
    const img=c.image_url?`<img class="ithumb" src="${c.image_url}" loading="lazy">`:`<div class="ithumb-ph">${c.id}</div>`;
    const prompt=c.english_prompt||c.vietnamese_prompt||'';
    return `<div class="icard">
      ${img}
      <div class="iinfo">
        <div class="irow"><span class="iid">${c.id}</span><span class="iname">${c.name||''} ${c.role?'· '+c.role:''}</span>${badge(c.status)}</div>
        <textarea class="iprompt" rows="2">${prompt}</textarea>
      </div>
    </div>`;
  }).join('');
}

let curPage=1,totalPages=1;
async function loadScenes(code,page){
  const el=document.getElementById('scenelist');
  el.innerHTML='<div class="empty" style="padding:12px">Loading scenes...</div>';
  const r=await api('/api/projects/'+encodeURIComponent(code)+'/scenes?page='+page+'&size=20');
  if(!r||!r.items){el.innerHTML='<div class="empty">No scenes</div>';return}
  curPage=r.page;totalPages=r.pages;
  document.getElementById('sgn').textContent=code+' ('+r.total+' scenes)';
  const pager=document.getElementById('pager');
  if(r.total>20){pager.style.display='flex'}else{pager.style.display='none'}
  document.getElementById('pg-info').textContent=curPage+'/'+totalPages;
  document.getElementById('pg-prev').disabled=curPage<=1;
  document.getElementById('pg-next').disabled=curPage>=totalPages;
  if(!r.items.length){el.innerHTML='<div class="empty">No scenes in this project</div>';return}
  el.innerHTML=r.items.map(s=>{
    const img=s.image_url?`<img class="ithumb" src="${s.image_url}" loading="lazy">`:`<div class="ithumb-ph">S${String(s.scene_id).padStart(3,'0')}</div>`;
    const srt=s.srt_text?s.srt_text.substring(0,60):'';
    return `<div class="icard">
      ${img}
      <div class="iinfo">
        <div class="irow"><span class="iid">S${String(s.scene_id).padStart(3,'0')}</span><span class="iname">${srt}</span>${badge(s.status_img)}<span class="ibadge ib-${s.status_vid==='done'?'done':'pending'}" style="margin-left:2px">V:${s.status_vid}</span></div>
        <textarea class="iprompt" rows="2" placeholder="Image prompt...">${s.img_prompt||''}</textarea>
        <textarea class="iprompt" rows="1" placeholder="Video prompt..." style="background:rgba(99,102,241,.06)">${s.video_prompt||''}</textarea>
      </div>
    </div>`;
  }).join('');
}

function prevPage(){if(selCode&&curPage>1)loadScenes(selCode,curPage-1)}
function nextPage(){if(selCode&&curPage<totalPages)loadScenes(selCode,curPage+1)}

function openModal(code){document.getElementById('rm-proj').value=code;document.getElementById('run-modal').classList.add('show')}
function closeModal(){document.getElementById('run-modal').classList.remove('show')}
async function submitJob(){
  const code=document.getElementById('rm-proj').value;
  const mode=document.getElementById('rm-mode').value;
  closeModal();lastLogLen=0;clearLogs();jobStart=Date.now();
  await api('/api/jobs','POST',{project_code:code,mode:mode});
  pollOverview();
}
async function stopJob(id){await api('/api/jobs/'+id+'/stop','POST');pollOverview()}
function clearLogs(){document.getElementById('logs').innerHTML='';lastLogLen=0}

async function pollServers(){
  const r=await api('/api/servers');
  const el=document.getElementById('srvlist');
  if(!r||!r.items||!r.items.length){el.innerHTML='<div style="color:var(--mt);font-size:11px">No servers</div>';return}
  el.innerHTML=r.items.map(s=>`
    <div class="srvcard">
      <div class="sdot" style="background:${s.online?'var(--gn)':'var(--rd)'}"></div>
      <div class="srvname">${s.name||s.url}</div>
      <div class="srvlat">${s.online?s.latency_ms+'ms':'offline'}</div>
    </div>`).join('');
}

loadProjects();pollPing();pollOverview();pollServers();
setInterval(pollPing,5000);setInterval(pollOverview,3000);setInterval(pollServers,15000);
"""

html = f"""<!doctype html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VE3 Suite Web Admin</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body>
{BODY}
<script>{JS}</script>
</body>
</html>"""

out = Path(__file__).parent / "templates" / "index.html"
out.write_text(html, encoding="utf-8")
print(f"Written {len(html)} bytes to {out}")

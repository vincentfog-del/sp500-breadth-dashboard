const $ = (id) => document.getElementById(id);
const fmtTime = (iso) => new Intl.DateTimeFormat('zh-TW',{dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Taipei'}).format(new Date(iso));
let allPoints=[], shownPoints=[], selectedDays=731, hoverIndex=-1;

async function json(path, fallback) {
  try { const r = await fetch(`${path}?v=${Date.now()}`); if (!r.ok) throw Error(r.status); return await r.json(); }
  catch (e) { console.warn(path, e); return fallback; }
}

function chartGeometry(count) {
  const c=$('chart'), w=c.clientWidth, h=300, pad={l:39,r:14,t:16,b:30};
  return {c,w,h,pad,iw:w-pad.l-pad.r,ih:h-pad.t-pad.b,px:i=>pad.l+(count===1?(w-pad.l-pad.r)/2:i*(w-pad.l-pad.r)/(count-1)),py:v=>pad.t+(h-pad.t-pad.b)*(1-v/100)};
}

function draw(points, active=-1) {
  const g=chartGeometry(points.length), {c,w,h,pad,ih,px,py}=g, dpr=devicePixelRatio||1;
  c.width=w*dpr;c.height=h*dpr;const x=c.getContext('2d');x.scale(dpr,dpr);x.clearRect(0,0,w,h);
  const css=getComputedStyle(document.documentElement), muted=css.getPropertyValue('--muted'), grid=css.getPropertyValue('--grid');
  x.font='11px system-ui';x.textAlign='right';x.fillStyle=muted;
  [0,25,50,75,100].forEach(v=>{const y=py(v);x.strokeStyle=v===50?'#71809b':grid;x.setLineDash(v===50?[5,5]:[]);x.beginPath();x.moveTo(pad.l,y);x.lineTo(w-pad.r,y);x.stroke();x.fillText(v+'%',pad.l-6,y+4)});x.setLineDash([]);
  if(!points.length)return g;
  const gradient=x.createLinearGradient(0,pad.t,0,pad.t+ih);gradient.addColorStop(0,'#ff5b6944');gradient.addColorStop(1,'#ff5b6900');
  x.beginPath();points.forEach((p,i)=>i?x.lineTo(px(i),py(p.up_pct)):x.moveTo(px(i),py(p.up_pct)));x.lineTo(px(points.length-1),pad.t+ih);x.lineTo(px(0),pad.t+ih);x.closePath();x.fillStyle=gradient;x.fill();
  x.strokeStyle='#ff5b69';x.lineWidth=2;x.lineJoin='round';x.beginPath();points.forEach((p,i)=>i?x.lineTo(px(i),py(p.up_pct)):x.moveTo(px(i),py(p.up_pct)));x.stroke();
  if(active>=0&&points[active]){const ax=px(active),ay=py(points[active].up_pct);x.strokeStyle='#aab5c9';x.lineWidth=1;x.beginPath();x.moveTo(ax,pad.t);x.lineTo(ax,pad.t+ih);x.stroke();x.fillStyle='#fff';x.beginPath();x.arc(ax,ay,5,0,Math.PI*2);x.fill();x.strokeStyle='#ff5b69';x.lineWidth=3;x.stroke()}
  x.textAlign='center';x.fillStyle=muted;if(points.length===1){x.fillText(points[0].session.slice(2),px(0),h-7)}else{const ticks=Math.min(4,points.length-1);for(let i=0;i<=ticks;i++){const n=Math.round(i*(points.length-1)/ticks);x.fillText(points[n].session.slice(2),px(n),h-7)}}
  return g;
}

function setRange(days) {
  selectedDays=days;const end=new Date(allPoints.at(-1)?.session||Date.now()), start=new Date(end);start.setDate(start.getDate()-days);
  shownPoints=allPoints.filter(p=>new Date(p.session)>=start);hoverIndex=-1;$('tooltip').hidden=true;draw(shownPoints);
  document.querySelectorAll('.ranges button').forEach(b=>b.classList.toggle('active',+b.dataset.days===days));
}

function inspectChart(event) {
  if(!shownPoints.length)return;const rect=$('chart').getBoundingClientRect(), clientX=event.clientX;
  if(clientX==null)return;const g=chartGeometry(shownPoints.length), local=Math.max(g.pad.l,Math.min(g.w-g.pad.r,clientX-rect.left));
  hoverIndex=Math.round((local-g.pad.l)/g.iw*(shownPoints.length-1));const p=shownPoints[hoverIndex], left=g.px(hoverIndex);
  draw(shownPoints,hoverIndex);const tip=$('tooltip');tip.innerHTML=`${p.session}<br><b>${p.up_pct.toFixed(1)}% 上漲</b><br>▲ ${p.up}　— ${p.flat}　▼ ${p.down}`;tip.hidden=false;tip.style.left=`${Math.max(78,Math.min(g.w-78,left))}px`;tip.style.top=`${g.py(p.up_pct)}px`;
}

async function load(){
  const [latest,history,intraday]=await Promise.all([json('data/latest.json',{}),json('data/history.json',[]),json('data/intraday.json',[])]);
  if(latest.up_pct==null){$('badge').textContent='尚無資料';$('updated').textContent='請先在 GitHub Actions 執行一次 Update workflow';draw([]);return}
  const live=latest.status==='intraday';$('badge').textContent=live?'盤中暫定':'正式收盤';$('badge').className=live?'live':'closed';
  $('session').textContent=latest.session;$('upPct').textContent=latest.up_pct.toFixed(1)+'%';$('upBar').style.width=latest.up_pct+'%';
  ['up','down','flat'].forEach(k=>$(k).textContent=latest[k]);$('updated').textContent=`更新：${fmtTime(latest.generated_at)}｜有效成分股 ${latest.total} 檔`;
  allPoints=[...history];if(live){const current=intraday.at(-1)||latest;allPoints=allPoints.filter(p=>p.session!==current.session);allPoints.push(current)}
  allPoints.sort((a,b)=>a.session.localeCompare(b.session));setRange(selectedDays);
}

document.querySelectorAll('.ranges button').forEach(b=>b.addEventListener('click',()=>setRange(+b.dataset.days)));
$('chart').addEventListener('pointermove',inspectChart);$('chart').addEventListener('pointerdown',inspectChart);$('chart').addEventListener('pointerleave',()=>{$('tooltip').hidden=true;hoverIndex=-1;draw(shownPoints)});
addEventListener('resize',()=>draw(shownPoints,hoverIndex));load();

// 重新整理：重新讀取最新的資料檔（不用重開頁面）
$('refreshBtn').addEventListener('click',async()=>{
  const btn=$('refreshBtn'), txt=$('refreshTxt');
  btn.disabled=true;btn.classList.add('spin');txt.textContent='更新中…';
  try{await load();const t=new Intl.DateTimeFormat('zh-TW',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:'Asia/Taipei'}).format(new Date());txt.textContent=`已更新 ${t}`}
  catch(e){txt.textContent='更新失敗，再試一次'}
  finally{btn.disabled=false;btn.classList.remove('spin');setTimeout(()=>txt.textContent='重新整理',4000)}
});
// 重新計算：連到 GitHub Actions 的手動執行頁（在 <帳號>.github.io/<repo>/ 上自動帶入）
(()=>{const m=location.hostname.match(/^([^.]+)\.github\.io$/), repo=location.pathname.split('/').filter(Boolean)[0];
  if(m&&repo){$('runLink').href=`https://github.com/${m[1]}/${repo}/actions/workflows/update.yml`;$('runLink').hidden=false;$('refreshNote').hidden=false}})();
// 回到這個分頁時自動重抓一次
document.addEventListener('visibilitychange',()=>{if(!document.hidden)load()});

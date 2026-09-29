const $ = (id) => document.getElementById(id);
const fmtTime = (iso) => new Intl.DateTimeFormat('zh-TW',{dateStyle:'medium',timeStyle:'short',timeZone:'Asia/Taipei'}).format(new Date(iso));

async function json(path, fallback) {
  try { const r = await fetch(`${path}?v=${Date.now()}`); if (!r.ok) throw Error(r.status); return await r.json(); }
  catch (e) { console.warn(path, e); return fallback; }
}

function draw(points) {
  const c=$('chart'), dpr=devicePixelRatio||1, w=c.clientWidth, h=230;
  c.width=w*dpr;c.height=h*dpr;const x=c.getContext('2d');x.scale(dpr,dpr);x.clearRect(0,0,w,h);
  const pad={l:34,r:12,t:12,b:28}, iw=w-pad.l-pad.r, ih=h-pad.t-pad.b;
  x.font='11px system-ui';x.textAlign='right';x.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--muted');
  [0,25,50,75,100].forEach(v=>{const y=pad.t+ih*(1-v/100);x.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--grid');x.beginPath();x.moveTo(pad.l,y);x.lineTo(w-pad.r,y);x.stroke();x.fillText(v+'%',pad.l-6,y+4)});
  if(!points.length)return;
  const px=i=>pad.l+(points.length===1?iw/2:i*iw/(points.length-1)), py=v=>pad.t+ih*(1-v/100);
  x.strokeStyle='#ff5b69';x.lineWidth=3;x.lineJoin='round';x.beginPath();points.forEach((p,i)=>i?x.lineTo(px(i),py(p.up_pct)):x.moveTo(px(i),py(p.up_pct)));x.stroke();
  points.forEach((p,i)=>{x.fillStyle='#ff5b69';x.beginPath();x.arc(px(i),py(p.up_pct),3.5,0,Math.PI*2);x.fill()});
  x.textAlign='center';x.fillStyle=getComputedStyle(document.documentElement).getPropertyValue('--muted');
  const first=points[0],last=points.at(-1);x.fillText((first.minute||first.session).slice(5,16).replace('T',' '),px(0),h-7);if(points.length>1)x.fillText((last.minute||last.session).slice(5,16).replace('T',' '),px(points.length-1),h-7);
}

async function load(){
  const [latest,history,intraday]=await Promise.all([json('data/latest.json',{}),json('data/history.json',[]),json('data/intraday.json',[])]);
  if(latest.up_pct==null){$('badge').textContent='尚無資料';$('updated').textContent='請先在 GitHub Actions 執行一次 Update workflow';draw([]);return}
  const live=latest.status==='intraday';$('badge').textContent=live?'盤中暫定':'正式收盤';$('badge').className=live?'live':'closed';
  $('session').textContent=latest.session;$('upPct').textContent=latest.up_pct.toFixed(1)+'%';$('upBar').style.width=latest.up_pct+'%';
  ['up','down','flat'].forEach(k=>$(k).textContent=latest[k]);$('updated').textContent=`更新：${fmtTime(latest.generated_at)}｜有效成分股 ${latest.total} 檔`;
  const points=live&&intraday.length?intraday:history;$('chartTitle').textContent=live?'今日盤中走勢':'近期收盤走勢';draw(points.slice(-40));
}
addEventListener('resize',()=>load());load();


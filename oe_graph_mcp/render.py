"""单文件交互图谱渲染器 —— self-contained interactive graph renderer.

产出：一个离线 HTML（无 CDN、无网络请求、无外部字体）。
Output: one offline HTML file. No CDN, no network, no telemetry.

为什么不用图表库：一个"给同行自由使用"的工具，任何 <script src="...">
都会在对方的内网/断网/国产浏览器环境下变成白屏。零外部依赖是可移植性。
"""

from __future__ import annotations

import html
import json

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{
    --bg:#0e1116; --panel:#151a21; --panel2:#1b222b; --line:#27303b;
    --fg:#e6edf3; --mut:#8b98a8; --accent:#f0b429; --ok:#3fb950; --warn:#d29922; --bad:#f85149;
    --c-cat:#f0b429; --c-out:#3fb950; --c-ven:#7d8590; --c-item:#58a6ff;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font:13px/1.55 "Microsoft YaHei","PingFang SC","Segoe UI",system-ui,sans-serif}
  #app{display:grid;grid-template-columns:236px 1fr 300px;grid-template-rows:auto 1fr;height:100%}
  header{grid-column:1/4;display:flex;align-items:center;gap:14px;padding:9px 16px;
    background:var(--panel);border-bottom:1px solid var(--line)}
  header h1{font-size:14px;margin:0;font-weight:600;letter-spacing:.3px}
  .badge{font-size:11px;padding:2px 8px;border:1px solid var(--line);border-radius:99px;color:var(--mut)}
  .badge.local{border-color:#1f6f3f;color:#6fdc8c}
  .spacer{flex:1}
  .tabs{display:flex;gap:2px}
  .tabs button{background:none;border:1px solid transparent;color:var(--mut);padding:4px 11px;
    border-radius:6px;cursor:pointer;font:inherit;font-size:12px}
  .tabs button:hover{color:var(--fg);background:var(--panel2)}
  .tabs button.on{color:#10141a;background:var(--accent);font-weight:600}
  aside{background:var(--panel);overflow-y:auto;padding:12px}
  aside.l{border-right:1px solid var(--line)}
  aside.r{border-left:1px solid var(--line)}
  h2{font-size:11px;text-transform:uppercase;letter-spacing:.9px;color:var(--mut);
    margin:16px 0 7px;font-weight:600}
  h2:first-child{margin-top:0}
  #stage{position:relative;overflow:hidden}
  canvas{display:block;cursor:grab}
  canvas.drag{cursor:grabbing}
  .hud{position:absolute;left:12px;bottom:12px;display:flex;gap:6px}
  .hud button,.mini{background:var(--panel);border:1px solid var(--line);color:var(--fg);
    padding:4px 10px;border-radius:6px;cursor:pointer;font:inherit;font-size:12px}
  .hud button:hover,.mini:hover{border-color:var(--accent);color:var(--accent)}
  .statgrid{display:grid;grid-template-columns:1fr 1fr;gap:6px}
  .stat{background:var(--panel2);border:1px solid var(--line);border-radius:7px;padding:7px 9px}
  .stat b{display:block;font-size:16px;font-weight:600;font-variant-numeric:tabular-nums}
  .stat span{font-size:10.5px;color:var(--mut)}
  .row{display:flex;align-items:center;gap:8px;padding:3.5px 6px;border-radius:5px;cursor:pointer;
    font-size:12px}
  .row:hover{background:var(--panel2)}
  .row.off{opacity:.32}
  .dot{width:9px;height:9px;border-radius:50%;flex:none}
  .row .n{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .row .v{color:var(--mut);font-variant-numeric:tabular-nums;font-size:11px}
  input[type=search],input[type=text]{width:100%;background:var(--panel2);border:1px solid var(--line);
    color:var(--fg);border-radius:7px;padding:6px 9px;font:inherit;font-size:12px}
  input:focus{outline:none;border-color:var(--accent)}
  .kv{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;font-size:12px}
  .kv dt{color:var(--mut)}
  .kv dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
  .tag{display:inline-block;font-size:10.5px;padding:1px 6px;border-radius:4px;
    border:1px solid var(--line);color:var(--mut);margin:2px 3px 0 0}
  .tag.bad{border-color:#6e2622;color:#ff9b94}
  .tag.warn{border-color:#6b5218;color:#e3b341}
  .card{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:9px 11px;margin-bottom:8px}
  .card h3{margin:0 0 4px;font-size:12.5px;font-weight:600}
  .card .sub{font-size:11px;color:var(--mut)}
  .bar{height:5px;background:var(--line);border-radius:99px;overflow:hidden;margin-top:5px}
  .bar i{display:block;height:100%;background:var(--accent)}
  table{width:100%;border-collapse:collapse;font-size:12px}
  th,td{text-align:left;padding:5px 7px;border-bottom:1px solid var(--line)}
  th{color:var(--mut);font-weight:500;font-size:11px;position:sticky;top:0;background:var(--panel)}
  td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
  .gradepill{display:inline-block;width:17px;text-align:center;border-radius:4px;font-size:10.5px;
    font-weight:700;color:#10141a}
  .gA{background:var(--bad)} .gB{background:var(--warn)} .gC{background:#4b5563;color:#cfd8e3}
  .muted{color:var(--mut);font-size:11.5px}
  .err{color:#ff9b94} .warnc{color:#e3b341} .okc{color:#6fdc8c}
  .list{max-height:none;overflow:visible}
  ::-webkit-scrollbar{width:9px;height:9px}
  ::-webkit-scrollbar-thumb{background:#2c3541;border-radius:9px}
  ::-webkit-scrollbar-thumb:hover{background:#3a4653}
  @media (max-width:1100px){#app{grid-template-columns:200px 1fr 250px}}
</style>
</head>
<body>
<div id="app">
  <header>
    <h1>__TITLE__</h1>
    <span class="badge local">本地生成 · 无网络请求</span>
    <span class="badge">契约 v__SPECV__</span>
    <span class="badge" id="bSource">__SOURCE__</span>
    <span class="spacer"></span>
    <div class="tabs">
      <button data-view="graph" class="on">图谱</button>
      <button data-view="pareto">帕累托</button>
      <button data-view="risk">风险</button>
      <button data-view="quality">数据质量</button>
    </div>
  </header>

  <aside class="l">
    <div id="leftGraph">
      <h2>概览</h2>
      <div class="statgrid" id="statGrid"></div>
      <h2>搜索</h2>
      <input type="search" id="q" placeholder="品名 / 营业点 / 拼音首字母">
      <h2>类别</h2>
      <div id="catList"></div>
      <h2>营业点</h2>
      <div id="outList"></div>
      <h2>关系</h2>
      <div id="kindList"></div>
    </div>
    <div id="leftOther" style="display:none"></div>
  </aside>

  <div id="stage">
    <canvas id="cv"></canvas>
    <div class="hud">
      <button id="btnFit">适应窗口</button>
      <button id="btnReplay">重播布局</button>
      <button id="btnLabels">标签 开</button>
    </div>
  </div>

  <aside class="r" id="right"></aside>
</div>

<script>
const DATA = /*__DATA__*/;
const ISSUES = /*__ISSUES__*/;
const $ = s => document.querySelector(s);
const el = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c;
  if (x !== undefined) e.textContent = x; return e; };
/* 说明 / safety note: this file only ever assigns the EMPTY STRING to
   innerHTML (e.g. `node.innerHTML = ""`) as a way to clear a container.
   No data is ever interpolated into innerHTML — all text goes through
   textContent (see `el()` above) — so there is no injection surface.
   The graph payload is injected once, as a JSON literal, into a script tag,
   with `</` escaped by the Python renderer. */

/* ---------- 类别配色：确定性哈希，保证同一类别每次同色 ---------- */
const PALETTE = ["#f0b429","#58a6ff","#3fb950","#db6d28","#bc8cff","#39c5cf",
                 "#f778ba","#e3b341","#6fdc8c","#ff9b94","#a5d6ff","#d2a8ff"];
const catColor = {};
function assignColors(){
  const cats = [...new Set(DATA.nodes.filter(n=>n.type==="oe_category").map(n=>n.name))].sort();
  cats.forEach((c,i)=>catColor[c]=PALETTE[i%PALETTE.length]);
}
assignColors();
const TYPECOLOR = {oe_category:"var(--c-cat)",oe_outlet:"var(--c-out)",oe_vendor:"var(--c-ven)"};

/* 拼音首字母（轻量：只处理常用字表，够用于"py"/"pyi"式搜索） */
const PY = (()=>{ const m={}; const s="不锈钢餐刀叉勺玻杯盘瓷碗碟布草床单枕套浴巾台口银壶托烛具厨汤桶锅砧板客房吹风电水衣架清洁吸尘器红威士忌骨咖行宴会中西南北大堂吧健公区管部办"; return s;})();
function pyInit(str){ // 无字典时退化为按字符——保证不报错，能搜中文即可
  return (str||"").toLowerCase();
}

/* ---------- 视图构建 ---------- */
const nodeById = new Map(DATA.nodes.map(n=>[n.id,n]));
const itemNodes = DATA.nodes.filter(n=>n.type==="oe_item");
const maxVal = Math.max(1, ...DATA.nodes.map(n=>n.val||0));
const onlyOn = new Set(["oe_category","oe_item","oe_outlet","oe_vendor"]);

function fmtMoney(v){ if(v==null) return "—";
  if(Math.abs(v)>=1e8) return (v/1e8).toFixed(2)+" 亿";
  if(Math.abs(v)>=1e4) return (v/1e4).toFixed(2)+" 万";
  return v.toFixed(2); }
function fmtQty(v){ if(v==null) return "—";
  return Number.isInteger(v)? v.toLocaleString() : v.toFixed(1); }

function buildStats(){
  const s = DATA.stats, g = $("#statGrid");
  const cells = [
    [s.item_count, "物料品种"],
    [s.total_amount>=1e4? fmtMoney(s.total_amount): s.total_amount.toFixed(0), "持有金额 (元)"],
    [s.outlet_count, "营业点"],
    [s.vendor_count, "供应商"],
    [s.row_count, "明细行"],
    [s.row_count? s.total_amount/s.row_count : 0, "行均金额"]
  ];
  cells.forEach(([v,k],i)=>{
    const d = el("div","stat");
    const b = el("b",null, i===5? "¥"+Number(v).toFixed(0) : String(v));
    d.appendChild(b); d.appendChild(el("span",null,k)); g.appendChild(d);
  });
}

function buildCatList(){
  const box = $("#catList"); box.innerHTML="";
  const catVal = DATA.stats.category_value||{};
  Object.entries(catVal).sort((a,b)=>b[1]-a[1]).forEach(([name,val])=>{
    const r = el("div","row"); r.dataset.cat = name;
    const d = el("div","dot"); d.style.background = catColor[name]||"#666";
    r.appendChild(d); r.appendChild(el("div","n",name));
    r.appendChild(el("div","v", fmtMoney(val)));
    r.title = name+" · ¥"+val.toLocaleString();
    r.onclick = ()=>toggleFilter("cat", name, r);
    box.appendChild(r);
  });
}
function buildOutList(){
  const box = $("#outList"); box.innerHTML="";
  (DATA.stats.top_outlets||[]).forEach(o=>{
    const r = el("div","row"); r.dataset.out = o.name;
    const d = el("div","dot"); d.style.background = "#3fb950";
    r.appendChild(d); r.appendChild(el("div","n",o.name));
    r.appendChild(el("div","v", fmtMoney(o.val)));
    r.onclick = ()=>toggleFilter("out", o.name, r);
    box.appendChild(r);
  });
}
const KIND_LABEL = {listed_in:"类别归属", held_at:"持有位置", supplied_by:"供应商"};
const kindOn = new Set(["listed_in","held_at","supplied_by"]);
function buildKindList(){
  const box = $("#kindList"); box.innerHTML="";
  const cnt = {};
  DATA.links.forEach(l=>cnt[l.kind]=(cnt[l.kind]||0)+1);
  Object.entries(KIND_LABEL).forEach(([k,label])=>{
    const r = el("div","row");
    const d = el("div","dot"); d.style.background="#58a6ff";
    r.appendChild(d); r.appendChild(el("div","n",label));
    r.appendChild(el("div","v", (cnt[k]||0).toLocaleString()));
    r.onclick = ()=>{ if(kindOn.has(k)){kindOn.delete(k); r.classList.add("off");}
                      else {kindOn.add(k); r.classList.remove("off");} refresh(); };
    box.appendChild(r);
  });
}

const filter = { cat:new Set(), out:new Set(), q:"" };
function toggleFilter(kind, value, rowEl){
  const s = filter[kind];
  if(s.has(value)){ s.delete(value); rowEl.classList.add("off"); }
  else { s.add(value); rowEl.classList.remove("off"); }
  refresh();
}
function passes(n){
  if(filter.cat.size && n.type==="oe_item" && !filter.cat.has(n.cat)) return false;
  if(filter.out.size && n.type==="oe_item" && !(n.outlets||[]).some(o=>filter.out.has(o))) return false;
  if(filter.out.size && n.type==="oe_outlet" && !filter.out.has(n.name)) return false;
  if(filter.q){
    const q = filter.q.toLowerCase();
    if(!(n.name||"").toLowerCase().includes(q)) return false;
  }
  return true;
}

/* ---------- 物理布局（网格近邻排斥 + 弹簧） ---------- */
const R = 4;
const sim = {
  nodes: DATA.nodes.map((n,i)=>{
    const ang = (i/Math.max(1,DATA.nodes.length))*Math.PI*2;
    let rad = 300;
    if(n.type==="oe_category") rad = 0;
    else if(n.type==="oe_outlet") rad = 220;
    else if(n.type==="oe_vendor") rad = 460;
    else {
      const c = DATA.nodes.filter(x=>x.type==="oe_category").findIndex(x=>x.name===n.cat);
      const ca = c>=0? (c/Math.max(1,DATA.nodes.filter(x=>x.type==="oe_category").length))*Math.PI*2 : ang;
      return {n, x:Math.cos(ca)*340 + (Math.random()-.5)*90,
                 y:Math.sin(ca)*340 + (Math.random()-.5)*90,
                 vx:0, vy:0, deg:0};
    }
    return {n, x:Math.cos(ang)*rad + (Math.random()-.5)*60,
               y:Math.sin(ang)*rad + (Math.random()-.5)*60, vx:0, vy:0, deg:0};
  }),
  links: DATA.links.map(l=>({l, s:null, t:null})),
};
sim.nodes.forEach(s=>{ s.r = 3.5 + 13*Math.sqrt((s.n.val||0)/maxVal); });
sim.nodes.forEach((s,i)=>s.i=i);
sim.links.forEach(L=>{ L.s = sim.nodes.find(x=>x.n.id===L.l.source);
                       L.t = sim.nodes.find(x=>x.n.id===L.l.target);
                       if(L.s&&L.t){L.s.deg++;L.t.deg++;} });

function step(alpha){
  const n = sim.nodes;
  // 空间网格近邻排斥
  let maxR = 0; for(const s of n) if(s.r>maxR) maxR=s.r;
  const cell = Math.max(48, maxR*4);
  const grid = new Map();
  for(const s of n){
    const cx = Math.floor(s.x/cell), cy = Math.floor(s.y/cell);
    const key = cx+","+cy;
    let a = grid.get(key); if(!a){ a=[]; grid.set(key,a); }
    a.push(s);
  }
  for(const s of n){
    const cx = Math.floor(s.x/cell), cy = Math.floor(s.y/cell);
    for(let dx=-1;dx<=1;dx++) for(let dy=-1;dy<=1;dy++){
      const a = grid.get((cx+dx)+","+(cy+dy)); if(!a) continue;
      for(const o of a){
        if(o===s) continue;
        let ddx = o.x-s.x, ddy = o.y-s.y;
        let d2 = ddx*ddx+ddy*ddy;
        const min = s.r+o.r+6;
        if(d2 > (cell*2)*(cell*2)) continue;
        if(d2 < 1e-4){ ddx=(Math.random()-.5); ddy=(Math.random()-.5); d2=1e-4; }
        if(d2 < min*min){ const d=Math.sqrt(d2); const f=(min-d)/d*0.5;
          s.x -= ddx*f; s.y -= ddy*f; o.x += ddx*f; o.y += ddy*f; continue; }
        const f = 1400/(d2*Math.sqrt(d2)) * (s.r*o.r)/36;
        s.vx -= ddx*f; s.vy -= ddy*f;
      }
    }
  }
  // 弹簧
  for(const L of sim.links){
    if(!L.s||!L.t) continue;
    let dx = L.t.x-L.s.x, dy = L.t.y-L.s.y;
    const d = Math.max(1, Math.hypot(dx,dy));
    const rest = L.l.kind==="listed_in"? 130 : L.l.kind==="held_at"? 170 : 230;
    const f = (d-rest)/d * 0.055 * alpha;
    L.t.vx -= dx*f; L.t.vy -= dy*f;
    L.s.vx += dx*f; L.s.vy += dy*f;
  }
  for(const s of n){
    s.vx += -s.x*0.0016*alpha; s.vy += -s.y*0.0016*alpha;
    s.vx *= 0.82; s.vy *= 0.82;
    s.x += Math.max(-9,Math.min(9,s.vx));
    s.y += Math.max(-9,Math.min(9,s.vy));
  }
}

/* ---------- 画布 ---------- */
const cv = $("#cv"), ctx = cv.getContext("2d");
const view = {k:1, x:0, y:0};
let hovered = null, selected = null, showLabels = true;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function resize(){
  const r = $("#stage").getBoundingClientRect();
  const dpr = Math.min(2, window.devicePixelRatio||1);
  cv.width = Math.max(1, r.width*dpr); cv.height = Math.max(1, r.height*dpr);
  cv.style.width = r.width+"px"; cv.style.height = r.height+"px";
  ctx.setTransform(dpr,0,0,dpr,0,0);
  draw();
}
window.addEventListener("resize", resize);

function fit(){
  let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
  for(const s of sim.nodes){ x0=Math.min(x0,s.x); y0=Math.min(y0,s.y);
    x1=Math.max(x1,s.x); y1=Math.max(y1,s.y); }
  const r = $("#stage").getBoundingClientRect();
  const pad = 60;
  const k = Math.min((r.width-pad*2)/Math.max(1,x1-x0), (r.height-pad*2)/Math.max(1,y1-y0));
  view.k = Math.max(0.05, Math.min(4, k));
  view.x = r.width/2  - ((x0+x1)/2)*view.k;
  view.y = r.height/2 - ((y0+y1)/2)*view.k;
  draw();
}

function activeNodes(){ return sim.nodes.filter(s=>passes(s.n)); }

function draw(){
  const r = $("#stage").getBoundingClientRect();
  ctx.clearRect(0,0,r.width,r.height);
  ctx.save();
  ctx.translate(view.x, view.y); ctx.scale(view.k, view.k);
  const act = new Set(activeNodes().map(s=>s.n.id));

  // 边
  for(const L of sim.links){
    if(!kindOn.has(L.l.kind)) continue;
    if(!L.s||!L.t) continue;
    if(!act.has(L.l.source)||!act.has(L.l.target)) continue;
    const hi = hovered && (L.s===hovered||L.t===hovered);
    ctx.strokeStyle = hi? "rgba(240,180,41,.85)" : "rgba(120,140,165,.17)";
    ctx.lineWidth = (hi? 1.6 : Math.min(1.4, 0.4+Math.sqrt((L.l.rows||1))/6)) / view.k;
    if(L.l.kind==="supplied_by"){ ctx.setLineDash([4/view.k,4/view.k]); } else ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(L.s.x,L.s.y); ctx.lineTo(L.t.x,L.t.y); ctx.stroke();
    ctx.setLineDash([]);
  }
  // 节点
  for(const s of sim.nodes){
    if(!act.has(s.n.id)) continue;
    const n = s.n;
    let fill;
    if(n.type==="oe_item") fill = catColor[n.cat]||"#58a6ff";
    else fill = TYPECOLOR[n.type]==="var(--c-cat)"?"#f0b429"
              : TYPECOLOR[n.type]==="var(--c-out)"?"#3fb950":"#7d8590";
    const dim = filter.q && !(n.name||"").toLowerCase().includes(filter.q.toLowerCase());
    ctx.globalAlpha = dim? .18 : 1;
    if(s===selected||s===hovered){ ctx.globalAlpha = 1;
      ctx.beginPath(); ctx.arc(s.x,s.y,s.r+5,0,7); ctx.strokeStyle = "#f0b429";
      ctx.lineWidth = 2/view.k; ctx.stroke(); }
    ctx.beginPath(); ctx.arc(s.x,s.y,s.r,0,7); ctx.fillStyle = fill; ctx.fill();
    if((n.type==="oe_category"||n.type==="oe_outlet")&&view.k>0.25){
      ctx.strokeStyle="rgba(255,255,255,.75)"; ctx.lineWidth=1.4/view.k; ctx.stroke(); }
    ctx.globalAlpha = 1;
  }
  // 标签
  if(showLabels && view.k>0.32){
    ctx.font = (11/view.k)+"px 'Microsoft YaHei',sans-serif";
    ctx.textAlign="center";
    for(const s of sim.nodes){
      if(!act.has(s.n.id)) continue;
      if(s.n.type==="oe_item" && s.r*view.k < 7 && s!==hovered && s!==selected) continue;
      if(filter.q && !(s.n.name||"").toLowerCase().includes(filter.q.toLowerCase())
         && s!==hovered && s!==selected) continue;
      ctx.fillStyle = (s===hovered||s===selected)? "#f0b429" : "rgba(230,237,243,.82)";
      ctx.fillText(s.n.name, s.x, s.y - s.r - 3.5/view.k);
    }
  }
  ctx.restore();
}

/* ---------- 交互 ---------- */
let drag = null;
cv.addEventListener("mousedown", e=>{
  drag = {x:e.clientX, y:e.clientY, moved:false};
  cv.classList.add("drag");
});
window.addEventListener("mouseup", ()=>{ drag=null; cv.classList.remove("drag"); });
cv.addEventListener("mousemove", e=>{
  const r = cv.getBoundingClientRect();
  const mx = e.clientX-r.left, my = e.clientY-r.top;
  if(drag){
    view.x += e.clientX-drag.x; view.y += e.clientY-drag.y;
    if(Math.abs(e.clientX-drag.x)+Math.abs(e.clientY-drag.y)>3) drag.moved=true;
    drag.x=e.clientX; drag.y=e.clientY; draw(); return;
  }
  const wx = (mx-view.x)/view.k, wy = (my-view.y)/view.k;
  let best=null, bd=1e9;
  for(const s of sim.nodes){
    if(!passes(s.n)) continue;
    const d = Math.hypot(s.x-wx, s.y-wy);
    if(d < Math.max(s.r+5, 9) && d<bd){ bd=d; best=s; }
  }
  if(best!==hovered){ hovered=best; draw();
    cv.style.cursor = best? "pointer":"grab"; }
});
cv.addEventListener("click", ()=>{
  if(drag && drag.moved) return;
  if(hovered){ selected = hovered; showDetail(hovered.n); draw(); }
  else { selected=null; showRightDefault(); draw(); }
});
cv.addEventListener("wheel", e=>{
  e.preventDefault();
  const r = cv.getBoundingClientRect();
  const mx = e.clientX-r.left, my = e.clientY-r.top;
  const k2 = Math.max(0.05, Math.min(6, view.k * (e.deltaY<0?1.12:1/1.12)));
  view.x = mx - (mx-view.x)*(k2/view.k);
  view.y = my - (my-view.y)*(k2/view.k);
  view.k = k2; draw();
}, {passive:false});

/* ---------- 右侧详情 ---------- */
function kv(dl, k, v, cls){
  dl.appendChild(el("dt",null,k));
  const dd = el("dd", cls||null, v); dl.appendChild(dd);
}
function showDetail(n){
  const box = $("#right"); box.innerHTML="";
  const h = el("h2",null,"节点详情"); box.appendChild(h);
  const c = el("div","card");
  c.appendChild(el("h3",null,n.name));
  const typeName = {oe_item:"物料",oe_category:"类别",oe_outlet:"营业点",oe_vendor:"供应商"}[n.type]||n.type;
  c.appendChild(el("div","sub",typeName + (n.spec? " · "+n.spec : "") + (n.grade? " · ABC "+n.grade : "")));
  box.appendChild(c);

  const dl = el("dl","kv");
  kv(dl,"类别", n.cat||"—");
  kv(dl,"金额","¥"+(n.val||0).toLocaleString(undefined,{maximumFractionDigits:2}));
  kv(dl,"数量", fmtQty(n.qty));
  if(n.type==="oe_item"){
    kv(dl,"明细行", String(n.rows||0));
    kv(dl,"营业点", (n.outlets||[]).join("、")||"—");
    kv(dl,"供应商", (n.vendors||[]).join("、")||"—");
    if(n.oldest_age!=null) kv(dl,"最老库龄", n.oldest_age+" 月",
      n.oldest_age>=36? "warnc":null);
    if(n.bad_status) kv(dl,"破损/报废行", String(n.bad_status), "err");
  } else {
    kv(dl,"关联物料", String(n.items||0));
    kv(dl,"数量", fmtQty(n.qty));
  }
  box.appendChild(dl);

  if(n.type==="oe_item"){
    const conn = DATA.links.filter(l=>l.source===n.id||l.target===n.id);
    box.appendChild(el("h2",null,"关联"));
    conn.slice(0,24).forEach(l=>{
      const other = nodeById.get(l.source===n.id? l.target : l.source);
      if(!other) return;
      const r = el("div","row");
      const d = el("div","dot");
      d.style.background = other.type==="oe_outlet"?"#3fb950"
        : other.type==="oe_vendor"?"#7d8590" : (catColor[other.name]||"#f0b429");
      r.appendChild(d); r.appendChild(el("div","n",other.name));
      r.appendChild(el("div","v", KIND_LABEL[l.kind]||l.kind));
      box.appendChild(r);
    });
  }
}
function showRightDefault(){
  const box = $("#right"); box.innerHTML="";
  box.appendChild(el("h2",null,"使用说明"));
  const c = el("div","card");
  c.appendChild(el("h3",null,"这是本地生成的图谱"));
  const p = el("div","sub","点击任意节点看明细。滚轮缩放，拖拽平移。左侧可筛选类别与营业点。");
  c.appendChild(p); box.appendChild(c);

  box.appendChild(el("h2",null,"金额构成"));
  Object.entries(DATA.stats.category_value||{}).forEach(([k,v])=>{
    const tot = DATA.stats.total_amount||1;
    const d = el("div"); d.style.marginBottom="7px";
    const top = el("div"); top.style.cssText="display:flex;justify-content:space-between;font-size:12px";
    top.appendChild(el("span",null,k)); top.appendChild(el("span","muted","¥"+fmtMoney(v)));
    const bar = el("div","bar");
    const i = el("i"); i.style.width = (100*v/tot).toFixed(1)+"%";
    i.style.background = catColor[k]||"#f0b429"; bar.appendChild(i);
    d.appendChild(top); d.appendChild(bar); box.appendChild(d);
  });

  if(ISSUES && ISSUES.warnings && ISSUES.warnings.length){
    box.appendChild(el("h2",null,"数据质量提示"));
    ISSUES.warnings.slice(0,6).forEach(w=>{
      const c2 = el("div","card");
      c2.appendChild(el("h3","warnc",w.code));
      c2.appendChild(el("div","sub",w.message));
      box.appendChild(c2);
    });
  }
}

/* ---------- 其他视图 ---------- */
function renderPareto(){
  const box = $("#leftOther"); box.innerHTML="";
  const wrap = $("#right"); wrap.innerHTML="";
  box.appendChild(el("h2",null,"帕累托 / ABC"));
  const tbl = el("table");
  const thead = el("thead"); const tr = el("tr");
  [["#",""],["品名",""],["类别",""],["金额","n"],["累计","n"]].forEach(([t,c])=>{
    const th = el("th",c,t); tr.appendChild(th); });
  thead.appendChild(tr); tbl.appendChild(thead);
  const tb = el("tbody");
  (DATA.pareto||[]).forEach(p=>{
    const r = el("tr");
    const g = el("td"); const pill = el("span","gradepill g"+p.grade, p.grade);
    g.appendChild(pill); r.appendChild(g);
    r.appendChild(el("td",null,p.name));
    r.appendChild(el("td",null,p.cat));
    r.appendChild(el("td","n","¥"+fmtMoney(p.val)));
    r.appendChild(el("td","n",(p.cum*100).toFixed(1)+"%"));
    r.onclick = ()=>{ const n = itemNodes.find(x=>x.name===p.name); if(n){ selected = sim.nodes.find(s=>s.n===n); switchView("graph"); showDetail(n); draw(); } };
    r.style.cursor="pointer";
    tb.appendChild(r);
  });
  tbl.appendChild(tb);
  box.appendChild(el("div","muted","按金额降序；A=前 70% 累计，B=70–90%，C=其余"));
  const scroll = el("div"); scroll.style.cssText="max-height:calc(100vh - 130px);overflow:auto";
  scroll.appendChild(tbl); box.appendChild(scroll);
  wrap.appendChild(el("h2",null,"ABC 分级"));
  const c = el("div","card"); c.appendChild(el("h3",null,
    "A "+DATA.stats.abc.A+" · B "+DATA.stats.abc.B+" · C "+DATA.stats.abc.C));
  c.appendChild(el("div","sub","A 类品种最少、金额最集中，是盘点与保管的重点。"));
  wrap.appendChild(c);
}

function renderRisk(){
  const box = $("#leftOther"); box.innerHTML="";
  box.appendChild(el("h2",null,"风险敞口"));
  const risks = DATA.risks||[];
  if(!risks.length){ box.appendChild(el("div","muted","未识别到风险项。需要「状态」或「库龄」列才能判定。")); }
  const wrap = $("#right"); wrap.innerHTML = "";
  risks.forEach(r0=>{
    const c = el("div","card");
    c.appendChild(el("h3",null,r0.name));
    const sub = el("div","sub","¥"+fmtMoney(r0.val)+" · 数量 "+fmtQty(r0.qty)+
      (r0.age!=null? " · 库龄 "+r0.age+" 月":""));
    c.appendChild(sub);
    r0.flags.forEach(f=> c.appendChild(el("span","tag bad",f)));
    wrap.appendChild(c);
  });
  box.appendChild(el("div","muted","判定口径：库龄≥36 月 / 破损报废 / 数量为零。点击条目在图谱中高亮。"));
}

function renderQuality(){
  const box = $("#leftOther"); box.innerHTML="";
  box.appendChild(el("h2",null,"契约体检"));
  const wrap = $("#right"); wrap.innerHTML="";
  const add = (list, cls, label, target)=>{
    if(!list||!list.length) return;
    target.appendChild(el("h2",null,label+" ("+list.length+")"));
    list.forEach(w=>{
      const c = el("div","card");
      c.appendChild(el("h3",cls,w.code));
      c.appendChild(el("div","sub",w.message||""));
      if(w.samples) c.appendChild(el("div","muted","样例："+w.samples.slice(0,10).join("、")));
      if(w.hint) c.appendChild(el("div","muted",w.hint));
      target.appendChild(c);
    });
  };
  add(ISSUES.errors,"err","错误 / 阻塞",wrap);
  add(ISSUES.warnings,"warnc","警告 / 可继续",wrap);
  add(ISSUES.info,"muted","信息",wrap);
  if(!ISSUES.errors.length && !ISSUES.warnings.length && !ISSUES.info.length){
    wrap.appendChild(el("div","muted","没有发现问题。"));
  }
  const c = el("div","card");
  c.appendChild(el("h3","okc","契约 v"+ISSUES.spec_version));
  c.appendChild(el("div","sub","判定："+(ISSUES.verdict||"-")));
  box.appendChild(c);
}

/* ---------- 视图切换 ---------- */
let view_ = "graph";
function switchView(v){
  view_ = v;
  document.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("on", b.dataset.view===v));
  const isGraph = v==="graph";
  $("#leftGraph").style.display = isGraph? "":"none";
  $("#leftOther").style.display = isGraph? "none":"";
  $("#stage").style.display = isGraph? "":"none";
  $("#app").style.gridTemplateColumns = isGraph? "236px 1fr 300px" : "236px 0 1fr";
  if(isGraph){ $("#right").style.display=""; showRightDefault(); resize(); }
  else {
    $("#right").style.display="none";
    if(v==="pareto") renderPareto();
    if(v==="risk") renderRisk();
    if(v==="quality") renderQuality();
  }
}
document.querySelectorAll(".tabs button").forEach(b=> b.onclick = ()=>switchView(b.dataset.view));

/* ---------- 刷新与主循环 ---------- */
function refresh(){ draw(); if(view_==="pareto") renderPareto();
  if(view_==="risk") renderRisk(); }
$("#q").addEventListener("input", e=>{ filter.q = e.target.value.trim(); refresh(); });
$("#btnFit").onclick = fit;
$("#btnReplay").onclick = ()=>{ sim.nodes.forEach(s=>{ s.vx=0;s.vy=0; });
  alpha = 1; if(reduceMotion){ for(let i=0;i<220;i++) step(0.6); fit(); } else { tick(); } };
$("#btnLabels").onclick = e=>{ showLabels=!showLabels;
  e.target.textContent = "标签 " + (showLabels?"开":"关"); draw(); };
window.addEventListener("keydown", e=>{
  if(e.key==="Escape"){ selected=null; showRightDefault(); draw(); }
  if(e.key==="0"){ fit(); }
});

let alpha = 1, raf = 0;
function tick(){
  cancelAnimationFrame(raf);
  const run = ()=>{
    if(alpha > 0.02){
      for(let i=0;i<2;i++) step(alpha);
      alpha *= 0.988;
      draw();
      raf = requestAnimationFrame(run);
    } else { draw(); }
  };
  raf = requestAnimationFrame(run);
}

buildStats(); buildCatList(); buildOutList(); buildKindList();
resize();
if(reduceMotion){ for(let i=0;i<260;i++) step(0.7); fit(); }
else { for(let i=0;i<90;i++) step(1.0); fit(); tick(); }
showRightDefault();
</script>
</body>
</html>
"""


def render_html(graph: dict, issues: dict, *, title: str | None = None) -> str:
    """把图数据渲染为一个完整、离线的 HTML 字符串。"""
    title = title or graph["meta"]["title"]
    data = json.dumps(graph, ensure_ascii=False, separators=(",", ":"))
    # 防止任何内容提前闭合 <script> 标签 / never let data break out of the tag
    data = data.replace("</", "<\\/")
    issues_json = json.dumps(issues, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    return (TEMPLATE
            .replace("__TITLE__", html.escape(title))
            .replace("__SPECV__", html.escape(str(graph["meta"].get("spec_version", ""))))
            .replace("__SOURCE__", html.escape(str(graph["meta"].get("source", ""))[:40]))
            .replace("/*__DATA__*/", data)
            .replace("/*__ISSUES__*/", issues_json))

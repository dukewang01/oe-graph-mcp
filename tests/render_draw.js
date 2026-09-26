#!/usr/bin/env node
/* 无浏览器的绘制路径测试 / headless draw-path test
 *
 *     node tests/render_draw.js <生成的图谱.html>
 *
 * 为什么需要它：一个"离线单文件 HTML"最容易骗过人的地方是 —— 页面能打开、
 * 侧栏有数字、标签能点，**但画布是空的**。DOM 层面的检查完全看不出来。
 *
 * 这个测试把 HTML 里真正的 JS 拿出来，在 Node 的 vm 里跑，用一个"录音"
 * canvas 上下文把每一次绘制调用记下来，然后回答三个问题：
 *
 *   1. 绘制路径有没有真的被执行？（arc / moveTo / lineTo 的调用次数）
 *   2. 送进 canvas 的坐标有没有 NaN / Infinity？（力导向布局最常见的崩法，
 *      而且在浏览器里表现为"什么都看不见"，肉眼极难定位）
 *   3. 画的东西跟数据量对得上吗？（节点数、边数）
 *
 * 不需要浏览器、不需要装 puppeteer。退出码 0 = 通过。
 */
"use strict";

const fs = require("fs");
const vm = require("vm");

const file = process.argv[2];
if (!file) {
  console.error("用法: node tests/render_draw.js <图谱.html>");
  process.exit(2);
}
const html = fs.readFileSync(file, "utf8");

/* ---------- 1. 取出页面里所有 <script> 的内容 ---------- */
const blocks = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]);
if (!blocks.length) {
  console.error("✗ 页面里没有 <script>");
  process.exit(1);
}
const source = blocks.join("\n;\n");

/* ---------- 2. 录音 canvas ---------- */
const calls = Object.create(null);
const badArgs = [];
const TAPPED = ["arc", "moveTo", "lineTo", "rect", "fill", "stroke", "fillText",
                "clearRect", "bezierCurveTo", "quadraticCurveTo", "roundRect",
                "strokeRect", "clip", "ellipse"];

function check(name, args) {
  for (const a of args) {
    if (typeof a === "number" && !Number.isFinite(a)) {
      badArgs.push(`${name}(${args.join(",")})`);
      return;
    }
  }
}

function makeCtx(canvas) {
  const ctx = { canvas };
  for (const m of TAPPED) {
    ctx[m] = (...args) => {
      calls[m] = (calls[m] || 0) + 1;
      check(m, args);
    };
  }
  for (const m of ["save", "restore", "beginPath", "closePath", "setLineDash",
                   "setTransform", "translate", "scale", "rotate", "transform",
                   "measureText", "createLinearGradient", "createRadialGradient",
                   "drawImage", "putImageData", "getImageData"]) {
    ctx[m] = m === "measureText" ? () => ({ width: 42 })
           : m === "getImageData" ? () => ({ data: new Uint8ClampedArray(4) })
           : m.startsWith("create") ? () => ({ addColorStop() {} })
           : () => {};
  }
  return ctx;
}

/* ---------- 3. 最小 DOM ---------- */
const registry = new Map();

function mkEl(tag) {
  const el = {
    tagName: String(tag || "div").toUpperCase(),
    style: {}, dataset: {}, children: [], value: "", checked: false,
    textContent: "", _innerHTML: "",
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    appendChild(c) { this.children.push(c); return c; },
    insertBefore(c) { this.children.push(c); return c; },
    removeChild(c) { this.children = this.children.filter((x) => x !== c); },
    replaceChildren() { this.children = []; },
    setAttribute() {}, getAttribute: () => null, removeAttribute() {},
    remove() {}, focus() {}, blur() {}, click() {}, scrollIntoView() {},
    addEventListener(t, f) { (this._h || (this._h = {}))[t] = f; },
    removeEventListener() {}, dispatchEvent() { return true; },
    querySelector(s) { return mkEl("div"); },
    querySelectorAll: () => [],
    getBoundingClientRect: () => ({ width: 960, height: 600, left: 0, top: 0, right: 960, bottom: 600 }),
    get offsetWidth() { return 960; }, get offsetHeight() { return 600; },
    get clientWidth() { return 960; }, get clientHeight() { return 600; },
    get firstChild() { return this.children[0] || null; },
    get lastChild() { return this.children[this.children.length - 1] || null; },
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return this._innerHTML; },
    set(v) { this._innerHTML = v; if (v === "") this.children = []; },
  });
  if (el.tagName === "CANVAS") {
    el.width = 960; el.height = 600;
    el.getContext = () => (el._ctx || (el._ctx = makeCtx(el)));
  }
  return el;
}

function pick(sel) {
  const key = String(sel);
  if (!registry.has(key)) registry.set(key, mkEl(key.includes("cv") || key === "canvas" ? "canvas" : "div"));
  return registry.get(key);
}

const document = {
  title: "",
  readyState: "complete",
  body: mkEl("body"),
  documentElement: mkEl("html"),
  createElement: mkEl,
  createTextNode: (t) => ({ textContent: t }),
  querySelector: pick,
  querySelectorAll: () => [],
  getElementById: (id) => pick("#" + id),
  addEventListener(t, f) { (this._h || (this._h = {}))[t] = f; },
  removeEventListener() {},
};

/* requestAnimationFrame：排队 + 有界排空。
   tick() 会一直自我调度到 alpha 收敛（约 320 帧），所以必须能排空但不能无限跑。 */
let rafId = 0;
const rafCbs = new Map();
function drain(maxFrames) {
  let n = 0;
  while (rafCbs.size && n < maxFrames) {
    const [id, fn] = rafCbs.entries().next().value;
    rafCbs.delete(id);
    fn(16 * (n + 1));
    n++;
  }
  return n;
}

const sandbox = {
  console, Math, JSON, Date, Object, Array, String, Number, Boolean, Map, Set,
  Promise, Error, TypeError, RangeError, isNaN, isFinite, parseFloat, parseInt,
  Uint8ClampedArray, Float64Array, Float32Array, Int32Array, Symbol, RegExp,
  document,
  devicePixelRatio: 1,
  requestAnimationFrame: (fn) => { const id = ++rafId; rafCbs.set(id, fn); return id; },
  cancelAnimationFrame: (id) => { rafCbs.delete(id); },
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  addEventListener() {}, removeEventListener() {},
  innerWidth: 1440, innerHeight: 900, scrollTo() {}, getComputedStyle: () => ({ getPropertyValue: () => "" }),
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;

/* ---------- 4. 跑 ---------- */
const ctx = vm.createContext(sandbox);
let threw = null;
try {
  vm.runInContext(source, ctx, { timeout: 120000, filename: "graph-inline.js" });
  // 页面可能在 DOMContentLoaded 里做初始化
  if (document._h && document._h.DOMContentLoaded) document._h.DOMContentLoaded();
  if (sandbox._h && sandbox._h.load) sandbox._h.load();
} catch (e) {
  threw = e;
}

const frames = drain(600);

function evalIn(expr) {
  try { return vm.runInContext(expr, ctx, { timeout: 20000 }); } catch (e) { return undefined; }
}

const nodeCount = evalIn("(typeof DATA!=='undefined' && DATA.nodes) ? DATA.nodes.length : undefined");
const linkCount = evalIn("(typeof DATA!=='undefined' && DATA.links) ? DATA.links.length : undefined");
const simNodes = evalIn("(typeof sim!=='undefined' && sim.nodes) ? sim.nodes.length : undefined");
const finite = evalIn("(()=>{ if(typeof sim==='undefined'||!sim.nodes) return null; const bad=sim.nodes.filter(s=>!Number.isFinite(s.x)||!Number.isFinite(s.y)); return {bad:bad.length, total:sim.nodes.length, spread: Math.round(Math.max(...sim.nodes.map(s=>s.x))-Math.min(...sim.nodes.map(s=>s.x)))}; })()");

/* ---------- 5. 判定 ---------- */
let fail = 0;
function t(ok, label, detail) {
  console.log(`   ${ok ? "✓" : "✗"} ${label}${detail !== undefined ? " — " + detail : ""}`);
  if (!ok) fail++;
}

console.log(`\n=== 绘制路径测试：${file} ===`);
console.log(`   script 块 ${blocks.length} 个 / ${source.length} 字符 / 排空动画帧 ${frames}\n`);

t(threw === null, "页面 JS 在无浏览器环境下不抛异常",
  threw ? `${threw.name}: ${threw.message}` : "无异常");

console.log(`   录到的绘制调用：${JSON.stringify(calls)}`);
console.log(`   数据：DATA.nodes=${nodeCount} DATA.links=${linkCount} sim.nodes=${simNodes}\n`);

t((calls.clearRect || 0) > 0, "draw() 被执行过（有 clearRect）", String(calls.clearRect || 0));
t((calls.arc || 0) >= (simNodes || 0), "每个节点都被画了（arc 次数 ≥ 节点数）",
  `arc=${calls.arc || 0} 节点=${simNodes}`);
t((calls.lineTo || 0) > 0 || (calls.moveTo || 0) > 0, "边被画了（有 moveTo/lineTo）",
  `moveTo=${calls.moveTo || 0} lineTo=${calls.lineTo || 0}`);
t((calls.fillText || 0) > 0 || (calls.fill || 0) > 0, "有填充/文字绘制",
  `fill=${calls.fill || 0} fillText=${calls.fillText || 0}`);
t(badArgs.length === 0, "送进 canvas 的坐标没有 NaN/Infinity",
  badArgs.length ? `${badArgs.length} 处，例：${badArgs[0]}` : "全部有限");
t(!!finite && finite.bad === 0, "布局坐标全部有限",
  finite ? `${finite.total} 节点，越界 ${finite.bad}，横向跨度 ${finite.spread}px` : "读不到 sim");
t(!!finite && finite.spread > 50, "布局真的摊开了（不是全叠在一点）",
  finite ? `${finite.spread}px` : "n/a");

console.log();
if (fail) {
  console.log(`结果 / RESULT: FAIL — ${fail} 项未通过\n`);
  process.exit(1);
}
console.log("结果 / RESULT: PASS — 绘制路径在无浏览器环境下确认执行，坐标全部有限\n");
process.exit(0);

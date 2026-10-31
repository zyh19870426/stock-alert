/* 97_pagecheck.js —— 不开浏览器，把页面里的纯函数拿出来跑一遍
   目的：在没有可用截图环境时，仍然能验证「图表/卡片/建议」不出现 NaN、undefined、a[object Object]，
        以及九档时间范围都能出图。用法：node 97_pagecheck.js */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HERE = __dirname;
const html = fs.readFileSync(path.join(HERE, '..', 'index.html'), 'utf8');
const data = JSON.parse(fs.readFileSync(path.join(HERE, '..', 'data', 'data.json'), 'utf8'));

// 抽出所有内联 <script>
const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);

const store = {};
const el = () => ({ innerHTML: '', textContent: '', style: {}, classList: { add(){}, remove(){} },
                    setAttribute(){}, getAttribute(){ return null }, querySelector(){ return null },
                    querySelectorAll(){ return [] }, insertAdjacentHTML(){}, appendChild(){}, addEventListener(){} });
const sandbox = {
  console,
  document: { getElementById: el, querySelector: () => null, querySelectorAll: () => [],
              addEventListener(){}, createElement: el, body: el() },
  window: {}, localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v } },
  navigator: { userAgent: 'node' }, location: { search: '' },
  Math, JSON, Date, Number, String, Array, Object, isNaN, parseFloat, parseInt, encodeURIComponent, decodeURIComponent,
  setTimeout, clearTimeout,
  fetch: () => Promise.reject(new Error('no network in headless check')),
  parent: { postMessage(){} },
};
sandbox.window = sandbox; sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox);
let failed = 0;
blocks.forEach((b, i) => {
  try { vm.runInContext(b, ctx, { filename: `block${i + 1}.js` }) }
  catch (e) { console.log(`  FAIL script#${i + 1} 执行报错：${e.message}`); failed++ }
});

// 把数据直接塞进沙箱（页面是 fetch 来的，这里跳过网络）
vm.runInContext('var DATA=null, LIVE={}, BYCODE={}', ctx);
ctx.DATA = data;
data.picks.concat(data.shortlist || []).forEach(f => { ctx.BYCODE[f.code] = f });

function bad(s, label) {
  const hits = [];
  if (/NaN/.test(s)) hits.push('NaN');
  if (/undefined/.test(s)) hits.push('undefined');
  if (/\[object Object\]/.test(s)) hits.push('[object Object]');
  if (/Infinity/.test(s)) hits.push('Infinity');
  if (hits.length) { console.log(`  FAIL ${label} 含 ${hits.join(',')}`); failed++ }
  return hits.length === 0;
}

const RANGES = ['30d', '60d', '3m', '6m', '1y', '2y', '3y', '5y', 'all'];

console.log('== 九档时间范围 ==');
for (const r of RANGES) {
  const svg = ctx.svgChart(data.picks[0], 'k' + r, r);
  const pts = (svg.match(/L[\d.]+ [\d.]+/g) || []).length;
  const ok = bad(svg, `range ${r}`);
  console.log(`  ${ok ? 'PASS' : 'FAIL'} ${r.padEnd(4)} 折线点数 ${String(pts).padStart(5)}  SVG ${String(svg.length).padStart(6)} 字节`);
}

console.log('== 推荐卡 / 备选卡 / 建议区 ==');
for (const p of data.picks) {
  const card = ctx.pickCard(p, 0);
  const ok = bad(card, `pickCard ${p.code}`);
  const hasLine = /以下才算便宜|便宜/.test(card), hasMk = /▲|▼/.test(card) || true;
  console.log(`  ${ok ? 'PASS' : 'FAIL'} ${p.code} 卡片 ${String(card.length).padStart(6)} 字节 · 便宜线 ${hasLine ? '有' : '无'}`);
  const adv = ctx.calcAdv(p.m, p.series);
  console.log(`       建议：${adv.state} · pos60=${adv.pos60} · 低位线=${adv.buy_line} · 离场线=${adv.stop}`);
  // 文案必须交代两种人各自怎么办：系统不知道你有没有持仓，不能只说「持有」
  const isBuy = /^可以买/.test(adv.state);
  const twoParty = isBuy ? /这是买点/.test(adv.act)
                         : (/已经买了的/.test(adv.act) && /还没买的/.test(adv.act));
  const noHold = !/只能持有|不加仓|先别加/.test(adv.act + adv.state);
  console.log(`  ${twoParty ? 'PASS' : 'FAIL'} ${p.code} 建议文案分「已经买了的 / 还没买的」= ${twoParty} · 无「只能持有」类含糊说法 = ${noHold}`);
  if (!twoParty || !noHold) failed++;

  // 信号板必须渲染，且结论与状态一致（曾出现「状态说该走、信号板说未触发」）
  const box = ctx.sigBlock(adv);
  const sigOk = /class="sig"/.test(box) && (adv.sig || []).length === 3;
  const hit = (adv.sig || []).filter(x => x.hit).map(x => x.k);
  const mustHit = /^该走|^可以买/.test(adv.state) ? hit.length > 0 : true;
  console.log(`  ${sigOk ? 'PASS' : 'FAIL'} ${p.code} 信号板渲染 3 个信号 = ${(adv.sig || []).length} · 已触发 [${hit.join(',')}] · 与状态一致 = ${mustHit}`);
  if (!sigOk || !mustHit) failed++;
}
for (const f of data.shortlist.slice(0, 3)) {
  const card = ctx.rowCard(f, { suffix: 'p' });
  if (bad(card, `rowCard ${f.code}`)) console.log(`  PASS 备选卡 ${f.code} ${card.length} 字节`);
}
// 关注卡（带 opts.watch，会多出「低位线/离场」chip）
for (const f of data.picks.slice(0, 1)) {
  const card = ctx.rowCard(f, { watch: true, suffix: 'w' });
  const ok = bad(card, `watchCard ${f.code}`);
  // 关注卡必须同时给出「便宜线」和「60 日均线」，且不能把均线说成卖出线
  const hasCheap = /以下才算便宜/.test(card), hasMa = /60 日均线/.test(card);
  const noFakeSell = !/就该走|就离场/.test(card);
  console.log(`  ${ok && hasCheap && hasMa && noFakeSell ? 'PASS' : 'FAIL'} 关注卡 ${f.code} 便宜线=${hasCheap} · 60日均线=${hasMa} · 无假卖出线=${noFakeSell}`);
  if (!ok || !hasCheap || !hasMa || !noFakeSell) failed++;
}
// 抱团方向表（7 列，含动量分位）
{
  const row = ctx.renderHerd;
  console.log(`  ${typeof row === 'function' ? 'PASS' : 'FAIL'} renderHerd 存在`);
  const secOk = data.sectors.every(s => s.mom_rk == null || (s.mom_rk >= 0 && s.mom_rk <= 1));
  console.log(`  ${secOk ? 'PASS' : 'FAIL'} 板块动量分位都在 0~1`);
  if (!secOk) failed++;
}
console.log('== 买卖点分布（页面自己算的那一版，用于关注列表现场补抓） ==');
for (const p of data.picks) {
  const mk = ctx.calcMarks(p.series);
  const vs = p.series.map(x => x[1]);
  const lows = mk.filter(m => m.t === 'B').map(m => {
    const i = p.series.findIndex(x => x[0] === m.d);
    const seg = vs.slice(Math.max(0, i - 59), i + 1);
    return ((vs[i] - Math.min(...seg)) / (Math.max(...seg) - Math.min(...seg))).toFixed(2);
  });
  console.log(`  ${p.code} 信号 ${mk.length} 个，建仓信号位置 ${lows.join(', ') || '无'}`);
}

console.log('== 数据一致性 ==');
for (const p of data.picks) {
  const ds = p.marks.map(m => m.d);
  const sset = new Set(p.series.map(x => x[0]));
  const outside = ds.filter(d => !sset.has(d));
  console.log(`  ${outside.length ? 'FAIL' : 'PASS'} ${p.code} 标记都落在走势日期上（越界 ${outside.length}）`);
  if (outside.length) failed++;
}

console.log('== 信号可靠性标注（σ 决定卖点该不该当真） ==');
{
  const relOk = data.sectors.every(s => s.sig_rel == null ||
    (s.xsd >= 0.015 ? s.sig_rel === 'high' : s.xsd < 0.012 ? s.sig_rel === 'low' : s.sig_rel === 'mid'));
  console.log(`  ${relOk ? 'PASS' : 'FAIL'} 板块 sig_rel 与 σ 阈值一致`);
  if (!relOk) failed++;
  for (const k of ['high', 'low', 'mid', null]) {
    const html = ctx._relNote({ sig_rel: k, xsd: k ? 0.018 : null });
    const ok = typeof html === 'string' && html.length > 20;
    console.log(`  ${ok ? 'PASS' : 'FAIL'} _relNote(${k}) ${ok ? '有内容' : '空'}`);
    if (!ok) failed++;
  }
  const herd = ctx.renderHerd ? 'checked' : 'missing';
  const hasCol = /c8/.test(String(ctx.renderHerd));
  console.log(`  ${hasCol ? 'PASS' : 'FAIL'} 抱团榜含「分化σ」列 (${herd})`);
  if (!hasCol) failed++;
  for (const p of data.picks) {
    const a = p.adv || {};
    console.log(`  ${'sig_rel' in a ? 'PASS' : 'FAIL'} ${p.code} 建议带 sig_rel=${a.sig_rel} σ=${a.xsd}`);
    if (!('sig_rel' in a)) failed++;
  }
}

console.log('== 提醒状态机（离开建仓区不能漏） ==');
{
  // 序列 0.30 → 0.50 → 0.70。旧实现在 0.50 那步把 canBuy 抹成 false，
  // 到 0.70 时已经不记得「曾经在低位」，那条提醒永远发不出来。这里钉死行为。
  const f = { code: 'TST', name: '测试基金', live: false,
              m: { streak: 0, last: 1, r1m: 0, r3m: 0, mdd: 0, ma20: 1 },
              series: [['2026-01-01', 1], ['2026-02-02', 1.01]],
              adv: { pos60: 0.30, trend_up: true, dd120: 0, state: '可建仓', tone: 'ok', act: '' } };
  ctx.BYCODE['TST'] = f;
  ctx.localStorage.setItem('fr_watch_v1', JSON.stringify([{ code: 'TST' }]));
  ctx.localStorage.setItem('fr_snap_v1', '{}');
  const cb = () => {
    const s = JSON.parse(ctx.localStorage.getItem('fr_snap_v1'))['TST'];
    return s ? s.canBuy : undefined;
  };
  const alerts = () => (ctx.checkAlerts(f) || []).map(x => x.s || '').join(' | ');

  ctx.snapshot();
  const s1 = cb();
  console.log(`  ${s1 === true ? 'PASS' : 'FAIL'} 0.30（低位+趋势上）→ canBuy=true（得到 ${s1}）`);
  if (s1 !== true) failed++;

  f.adv.pos60 = 0.50;
  const a2 = alerts();
  ctx.snapshot();
  const s2 = cb();
  const ok2 = s2 === true && !/离开建仓区/.test(a2);
  console.log(`  ${ok2 ? 'PASS' : 'FAIL'} 0.50（中间地带）→ canBuy 保持 true，且此刻不提醒（得到 ${s2}）`);
  if (!ok2) failed++;

  f.adv.pos60 = 0.70;
  const a3 = alerts();
  const ok3 = /离开建仓区/.test(a3);
  console.log(`  ${ok3 ? 'PASS' : 'FAIL'} 0.70（升破 0.6）→ 必须提醒「已离开建仓区」（得到「${a3}」）`);
  if (!ok3) failed++;
  ctx.snapshot();
  const s3 = cb();
  console.log(`  ${s3 === false ? 'PASS' : 'FAIL'} 0.70 → canBuy 落回 false（得到 ${s3}）`);
  if (s3 !== false) failed++;
  delete ctx.BYCODE['TST'];
  ctx.localStorage.setItem('fr_watch_v1', '[]');
  ctx.localStorage.setItem('fr_snap_v1', '{}');
}

console.log('== 建议文案覆盖全部状态（源码级，不依赖当天数据） ==');
{
  // 每个非买点状态都必须交代「已经买了的」「还没买的」怎么办
  // act 是多段字符串拼接，用「按状态名切段」拿到该状态之后的整段代码
  const parts = html.split(/st='([^']+)'/);
  const stateActs = [];
  for (let i = 1; i < parts.length; i += 2) {
    const st = parts[i], body = parts[i + 1] || '';
    if (!/tone/.test(body)) continue;   // 只认建议区的那几处
    stateActs.push({ st, act: body.slice(0, 2000) });
  }
  console.log(`  扫描到 ${stateActs.length} 条状态文案`);
  if (stateActs.length < 5) { console.log(`  FAIL 状态文案少于 5 条`); failed++; }
  const badOnes = stateActs.filter(x => {
    if (/^可以买/.test(x.st)) return !/这是买点/.test(x.act);
    return !(/已经买了的/.test(x.act) && /还没买的/.test(x.act));
  }).map(x => x.st);
  const vagueOnes = stateActs.filter(x => /只能持有|不加仓|先别加/.test(x.st + x.act)).map(x => x.st);
  console.log(`  ${badOnes.length ? 'FAIL' : 'PASS'} 全部状态都写了两种人怎么办${badOnes.length ? ' → ' + badOnes.join(', ') : ''}`);
  if (badOnes.length) failed++;
  console.log(`  ${vagueOnes.length ? 'FAIL' : 'PASS'} 没有「只能持有 / 不加仓」类替用户决定仓位的说法${vagueOnes.length ? ' → ' + vagueOnes.join(', ') : ''}`);
  if (vagueOnes.length) failed++;
}

console.log(failed ? `\n==> ${failed} 项不通过` : '\n==> 全部通过');
process.exit(failed ? 1 : 0);

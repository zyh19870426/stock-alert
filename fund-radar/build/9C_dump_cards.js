/* 9C_dump_cards.js —— 把卡片渲染结果转成纯文本，用来人读一遍文案是否说人话
   不是校验（那归 97_pagecheck.js），而是"读起来通不通"的问题没法自动断言，只能读。
   用法：node 9C_dump_cards.js            # 三只推荐
         node 9C_dump_cards.js --all      # 连带备选池
*/
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HERE = __dirname;
const html = fs.readFileSync(path.join(HERE, '..', 'index.html'), 'utf8');
const data = JSON.parse(fs.readFileSync(path.join(HERE, '..', 'data', 'data.json'), 'utf8'));

const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const store = {};
const el = () => ({ innerHTML: '', textContent: '', style: {}, classList: { add() {}, remove() {} },
  setAttribute() {}, getAttribute() { return null }, querySelector() { return null },
  querySelectorAll() { return [] }, insertAdjacentHTML() {}, appendChild() {}, addEventListener() {} });
const sandbox = {
  console,
  document: { getElementById: el, querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, createElement: el, body: el() },
  window: {}, localStorage: { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v } },
  navigator: { userAgent: 'node' }, location: { search: '' },
  Math, JSON, Date, Number, String, Array, Object, isNaN, parseFloat, parseInt,
  encodeURIComponent, decodeURIComponent, setTimeout, clearTimeout,
  fetch: () => Promise.reject(new Error('no network')), parent: { postMessage() {} },
};
sandbox.window = sandbox; sandbox.globalThis = sandbox;
const ctx = vm.createContext(sandbox);
blocks.forEach((b, i) => {
  try { vm.runInContext(b, ctx) } catch (e) { console.log(`script#${i + 1}: ${e.message}`) }
});
vm.runInContext('var DATA=null, LIVE={}, BYCODE={}', ctx);
ctx.DATA = data;
data.picks.concat(data.shortlist || []).forEach(f => { ctx.BYCODE[f.code] = f });

/* 去标签、把连续竖线压成一个分隔符，尽量接近"眼睛看到的顺序" */
const txt = s => s.replace(/<[^>]+>/g, ' | ').replace(/(\s*\|\s*)+/g, ' | ').replace(/[ \t]+/g, ' ').trim();

const dump = (f, i) => {
  console.log('\n' + '='.repeat(78));
  console.log(`第 ${i + 1} 位  ${f.name}  (${f.code})`);
  console.log('='.repeat(78));
  console.log(txt(ctx.pickCard(f, i)));
};

data.picks.forEach(dump);
if (process.argv.includes('--all')) {
  (data.shortlist || []).forEach((f, i) => dump(f, i));
}
console.log('\n（以上为渲染文本，用于检查"读起来是否说人话"）');

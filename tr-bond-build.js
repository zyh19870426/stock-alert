/* 构建「股债相对强弱」数据文件 → tr-bond-data.json  —— 云端日更版
 *
 * ★唯一真源：本文件（stock-alert-cloud/tr-bond-build.js）。
 *   本地 `node _tests/tr-bond-build.js` 只是薄包装，会转调本文件并指定输出到
 *   stock-alert-console/；改算法只改这里，禁止另存副本。
 *
 * 输出（默认写脚本所在目录 = 真源仓根，由 GitHub Actions 提交回仓）：
 *   tr-bond-data.json  —— 全序列 + 历史触发点 + 阈值统计 + 当前状态
 *   可用环境变量 DATA_OUT_DIR 覆盖输出目录（本地跑时指向 stock-alert-console）。
 *
 * 序列：
 *   P = 沪深300全收益指数 H00300（中证官网，2004-12-31 起）
 *   B = 二级债基代理（4 只老牌二级债基复权净值等权，equityReturn 链乘）
 *   R = P/B（基期=1 时，R=1 即两条累计收益曲线"重合"）
 */
const https = require('https');
const fs = require('fs');
const path = require('path');

const DIR = process.env.DATA_OUT_DIR || __dirname;
const OUT = path.join(DIR, 'tr-bond-data.json');
const BASE_DATE = '2005-01-04';       /* 基期：两线在此设为 100 */
const THR = 1.05;                      /* 重合阈值（含）= R ≤ 1.05 视为进入重合带 */
const BAND = [1.00, 1.10];             /* 视觉重合带 */

const BOND_FUNDS = [
  ['202101', '南方宝元债券A'],
  ['240003', '华宝宝康债券'],
  ['110008', '易方达稳健收益债券B'],
  ['110017', '易方达增强回报债券A']
];

function getOnce(url, ref, timeout) {
  return new Promise((res, rej) => {
    const q = https.get(url, {
      timeout: timeout || 30000,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0',
        'Referer': ref
      }
    }, x => {
      const c = [];
      x.on('data', d => c.push(d));
      x.on('end', () => res({ s: x.statusCode, body: Buffer.concat(c).toString('utf8') }));
    });
    q.on('error', rej);
    q.on('timeout', () => { q.destroy(); rej(new Error('timeout')); });
  });
}
/* ★2026-11-28 根治：中证官网海外出口偶发 30s 超时（run #13 卡在①直接 FAIL，
   连带国家队 nt-data 已生成却无法提交）。带退避重试 3 次，偶发抖动自愈；
   3 次全败仍抛错 fail-fast，不静默放行。 */
async function get(url, ref, timeout) {
  let last = null;
  for (let i = 1; i <= 3; i++) {
    try { return await getOnce(url, ref, timeout); }
    catch (e) {
      last = e;
      if (i < 3) await new Promise(r => setTimeout(r, 2000 * i));
    }
  }
  throw last;
}

function extractArr(body, key) {
  const i = body.indexOf('var ' + key + ' =');
  if (i < 0) return null;
  const eq = body.indexOf('=', i);
  const end = body.indexOf(';', eq);
  if (eq < 0 || end < 0) return null;
  try { return JSON.parse(body.slice(eq + 1, end).trim()); } catch (e) { return null; }
}

const r2 = v => Math.round(v * 10000) / 10000;

(async () => {
  const log = s => console.log(s);
  log('① 沪深300全收益 H00300（中证官网）');
  /* 结束日取当日，避免写死导致序列永久停在旧日期 */
  const now = new Date(Date.now() + 8 * 3600e3);           /* 按北京时间取日期 */
  const endDate = now.toISOString().slice(0, 10).replace(/-/g, '');
  const rA = await get('https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=H00300&startDate=20041231&endDate=' + endDate, 'https://www.csindex.com.cn/');
  const jA = JSON.parse(rA.body);
  const A = {};
  jA.data.forEach(d => {
    if (!d.close) return;
    const t = String(d.tradeDate);
    A[t.length === 8 ? (t.slice(0, 4) + '-' + t.slice(4, 6) + '-' + t.slice(6, 8)) : t] = +d.close;
  });
  log('   rows=' + Object.keys(A).length);

  log('② 二级债基代理（4 只等权）');
  const Braw = {};
  for (const [code, name] of BOND_FUNDS) {
    const r = await get('https://fund.eastmoney.com/pingzhongdata/' + code + '.js', 'https://fund.eastmoney.com/');
    const nw = extractArr(r.body, 'Data_netWorthTrend');
    if (!nw || !nw.length) { log('   ' + code + ' 失败'); continue; }
    let lvl = 1;
    const ser = [];
    nw.forEach(o => {
      /* ★日期必须按北京时间还原（2026-11-17 修）：天天基金给的是「北京时间当日 00:00」
         对应的毫秒戳，直接 toISOString() 会按 UTC 取日期，整体**早一天**。
         后果不只是日期难看——每根债基净值都被错配到前一个交易日的指数上，
         而且与指数取交集时会丢掉最新几个交易日（表现为"数据一直停在几天前"）。 */
      const d = new Date(o.x + 8 * 3600e3).toISOString().slice(0, 10);
      if (typeof o.equityReturn === 'number') lvl *= (1 + o.equityReturn / 100);
      ser.push([d, lvl]);
    });
    Braw[code] = ser;
    log('   ' + code + ' ' + name + ' ' + ser.length + ' 期 ' + ser[0][0] + ' → ' + ser[ser.length - 1][0]);
  }

  /* 共同交易日 */
  const bd = {};
  Object.values(Braw).forEach(ser => ser.forEach(([d]) => { bd[d] = 1; }));
  const dates = Object.keys(A).filter(d => bd[d]).sort();
  /* 从基期起算 */
  const i0 = dates.indexOf(BASE_DATE);
  if (i0 < 0) throw new Error('基期 ' + BASE_DATE + ' 不是共同交易日');
  const dts = dates.slice(i0);

  /* 各债基归一化到基期 */
  const perFund = {};
  Object.keys(Braw).forEach(code => {
    const m = {};
    Braw[code].forEach(([d, v]) => { m[d] = v; });
    const b = m[BASE_DATE];
    perFund[code] = dts.map(d => (m[d] != null && b ? m[d] / b : null));
  });

  const P = [], B = [], R = [], nFund = [];
  const baseA = A[BASE_DATE];
  dts.forEach((d, i) => {
    const p = A[d] / baseA;
    let s = 0, n = 0;
    Object.keys(perFund).forEach(k => { const v = perFund[k][i]; if (v != null && isFinite(v)) { s += v; n++; } });
    const b = n ? s / n : null;
    P.push(r2(p));
    B.push(b == null ? null : r2(b));
    R.push(b ? r2(p / b) : null);
    nFund.push(n);
  });
  const N = dts.length;
  /* ★安全闸（2026-11-17）：债券腿末日可用数低于 2 直接失败，workflow 不提交、保留上一版。
     注意实际参与只数并不等于 BOND_FUNDS.length——110008(2005-09 起)、110017(2008-03 起)
     在基期 2005-01-04 没有净值，被归一化整列剔除，历次运行实际只有 2 只在参与（nf 恒定 2）。
     这是既有口径缺陷，本轮只做诚实标注、不改历史序列（改会让 R 与全部历史触发点失效，
     属独立议题）。 */
  const nfMed = (function () { const s = nFund.slice().sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : 0; })();
  if (nFund[N - 1] < 2) throw new Error('债券腿末日仅 ' + nFund[N - 1] + ' 只可用（阈值 2），拒绝写出');
  if (nFund[N - 1] < nfMed) log('   ⚠ 末日可用债基 ' + nFund[N - 1] + ' 只，低于历史中位 ' + nfMed + ' 只');
  /* 指数腿也必须有效：P 全 null 时 events/state 全部无意义 */
  if (!isFinite(P[N - 1]) || !(P[N - 1] > 0)) throw new Error('沪深300全收益指数末值无效（' + P[N - 1] + '），拒绝写出');
  log('   合成序列 ' + N + ' 个交易日 ' + dts[0] + ' → ' + dts[N - 1] + '，末值 P=' + P[N - 1] + ' B=' + B[N - 1] + ' R=' + R[N - 1]);
  log('   实际参与债基 ' + nFund[N - 1] + '/' + BOND_FUNDS.length + ' 只（历史中位 ' + nfMed + ' 只）');

  /* ---------- 事件：跌破阈值（含冷却 60 交易日） ---------- */
  function fwd(i, h) { const j = Math.min(N - 1, i + h); return j > i ? P[j] / P[i] - 1 : null; }
  function maxDD(i, h) { const j = Math.min(N - 1, i + h); let lo = P[i]; for (let k = i; k <= j; k++) if (P[k] < lo) lo = P[k]; return lo / P[i] - 1; }
  function trough(i, h) { const j = Math.min(N - 1, i + h); let lo = Infinity, at = i; for (let k = i; k <= j; k++) if (P[k] < lo) { lo = P[k]; at = k; } return at - i; }

  const evIdx = [];
  let last = -1e9;
  for (let i = 1; i < N; i++) {
    if (R[i] == null || R[i - 1] == null) continue;
    if (R[i] <= THR && R[i - 1] > THR && i - last >= 60) { evIdx.push(i); last = i; }
  }
  const events = evIdx.map(i => ({
    d: dts[i], r: R[i],
    f60: fwd(i, 60) == null ? null : r2(fwd(i, 60)),
    f120: fwd(i, 120) == null ? null : r2(fwd(i, 120)),
    f250: fwd(i, 250) == null ? null : r2(fwd(i, 250)),
    dd120: r2(maxDD(i, 120)),
    lag: trough(i, 120),
    ok: fwd(i, 120) != null && fwd(i, 120) > 0
  }));
  log('   触发 ' + events.length + ' 次：' + events.map(e => e.d + '(R' + e.r + ' ' + (e.f120 != null ? (e.f120 * 100).toFixed(0) + '%' : '-') + ')').join(', '));

  /* ---------- 阈值统计表 ---------- */
  function avg(a) { const v = a.filter(x => x != null && isFinite(x)); return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null; }
  const thrTable = [1.00, 1.05, 1.10, 1.15].map(thr => {
    const ev = []; let lst = -1e9;
    for (let i = 1; i < N; i++) {
      if (R[i] == null || R[i - 1] == null) continue;
      if (R[i] <= thr && R[i - 1] > thr && i - lst >= 60) { ev.push(i); lst = i; }
    }
    const f60 = ev.map(i => fwd(i, 60)), f120 = ev.map(i => fwd(i, 120)), dd = ev.map(i => maxDD(i, 120));
    return {
      thr: thr, n: ev.length,
      w60: f60.length ? r2(f60.filter(x => x != null && x > 0).length / f60.filter(x => x != null).length) : null,
      m60: f60.length ? r2(avg(f60)) : null,
      w120: f120.length ? r2(f120.filter(x => x != null && x > 0).length / f120.filter(x => x != null).length) : null,
      m120: f120.length ? r2(avg(f120)) : null,
      dd120: r2(avg(dd) || 0)
    };
  });
  /* 无条件基准 */
  const allIdx = [];
  for (let i = 0; i < N - 250; i++) allIdx.push(i);
  const baseF60 = allIdx.map(i => fwd(i, 60)), baseF120 = allIdx.map(i => fwd(i, 120)), baseDD = allIdx.map(i => maxDD(i, 120));
  const baseline = {
    n: allIdx.length,
    w60: r2(baseF60.filter(x => x > 0).length / baseF60.length),
    m60: r2(avg(baseF60)),
    w120: r2(baseF120.filter(x => x > 0).length / baseF120.length),
    m120: r2(avg(baseF120)),
    dd120: r2(avg(baseDD))
  };

  /* ---------- 当前状态 ---------- */
  const cur = R[N - 1];
  let state, dist;
  if (cur <= 1.00) { state = 'deep'; dist = 0; }
  else if (cur <= THR) { state = 'in'; dist = 0; }
  else { state = cur <= THR + 0.10 ? 'near' : 'safe'; dist = r2((cur - THR) / THR); }

  const out = {
    gen: new Date().toISOString().slice(0, 16).replace('T', ' ') + ' UTC',
    meta: {
      baseDate: BASE_DATE, thr: THR, band: BAND,
      aName: '沪深300全收益指数(H00300)', bName: '二级债基代理（等权）',
      funds: BOND_FUNDS.map(f => f[0] + ' ' + f[1]),
      /* 实际参与只数：上面 4 只里，起步晚于基期的会被归一化剔除，页面若写死"4 只等权"会失真 */
      fundsUsed: nFund[N - 1], fundsListed: BOND_FUNDS.length,
      fundsUsedNote: '实际参与合成 ' + nFund[N - 1] + ' 只（共列 ' + BOND_FUNDS.length + ' 只，起步晚于基期 ' + BASE_DATE + ' 者因无基期净值被剔除）',
      nDays: N
    },
    dates: dts,
    p: P,
    b: B,
    r: R,
    nf: nFund,
    events: events,
    thrTable: thrTable,
    baseline: baseline,
    current: { d: dts[N - 1], r: cur, state: state, dist: dist, p: P[N - 1], b: B[N - 1] }
  };
  fs.writeFileSync(OUT, JSON.stringify(out));
  log('③ 写出 ' + OUT + '  ' + (fs.statSync(OUT).size / 1024).toFixed(1) + ' KB');
  log('   当前 R=' + cur + ' 状态=' + state + ' 距阈值 ' + (dist * 100).toFixed(1) + '%');
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });

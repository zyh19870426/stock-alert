#!/usr/bin/env node
/**
 * 融资盘平仓风险日报 - 数据生成脚本
 *
 * 数据源（全部公开）：
 *  - 东财 datacenter-web RPTA_WEB_RZRQ_GGMX ：个股融资余额明细（T+1 披露）
 *  - 东财 datacenter-web RPTA_RZRQ_LSHJ    ：市场级融资余额历史（近12交易日）
 *  - 腾讯 web.ifzq.gtimg.cn                 ：前复权日K（算 MA20 成本价）
 *  - 东财 push2 clist                       ：申万/东财一级行业映射
 *
 * 维保比例口径（复刻原日报）：
 *   维保比例(%) = 180 × 现价 / 20日成本价
 *   其中 180 = 1.8 倍初始杠杆；成本价用前复权 MA20（原报告用融资买入额加权均价，差异约1%）
 *
 * 用法：node build-data.js [--days 25] [--limit 0] [--out margin-risk.json]
 */
'use strict';

const { execFile } = require('child_process');
const fs = require('fs');
const path = require('path');

const ARGS = parseArgs(process.argv.slice(2));
const OUT = path.resolve(__dirname, ARGS.out || 'margin-risk.json');
const KLINE_DAYS = ARGS.days || 25;      // 多取几天，保证截到披露日有20根
const LEV = 1.8;
const BASE = LEV * 100;                   // 180
const CONCURRENCY = ARGS.conc || 16;
const LIMIT = ARGS.limit || 0;            // >0 时只处理前 N 只（调试用）

const DC = 'https://datacenter-web.eastmoney.com/api/data/v1/get?';
// 新浪日K（本地稳定、无WAF）：不复权，但现价与成本同比缩放，维保比值不受影响
const SINA = 'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20k=/CN_MarketDataService.getKLineData?symbol=';
// 东财 push2his 日K（境外 GitHub Actions 已验证可用，与现有 margin-regime.js 同源）
const EMK = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?';

function parseArgs(a) {
  const o = {};
  for (let i = 0; i < a.length; i++) {
    if (a[i].startsWith('--')) {
      const k = a[i].slice(2);
      o[k] = a[i + 1] && !a[i + 1].startsWith('--') ? a[++i] : true;
    }
  }
  return o;
}

function httpJson(url, timeoutMs = 30000, withHeader = true) {
  return new Promise((resolve, reject) => {
    const args = ['-s', '--max-time', String(Math.ceil(timeoutMs / 1000)), '-A', 'Mozilla/5.0'];
    if (withHeader) args.push('-H', 'Referer: https://quote.eastmoney.com/');
    args.push(url);
    execFile('curl', args,
      { maxBuffer: 32 * 1024 * 1024, encoding: 'utf8' }, (err, stdout) => {
        if (err) return reject(err);
        try { resolve(JSON.parse(stdout)); }
        catch (e) { reject(new Error('bad json: ' + stdout.slice(0, 160))); }
      });
  });
}

async function httpRetry(url, tries = 3, ms = 30000, withHeader = true) {
  let last;
  for (let i = 0; i < tries; i++) {
    try { return await httpJson(url, ms, withHeader); }
    catch (e) { last = e; await sleep(400 * (i + 1)); }
  }
  throw last;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

// 新浪返回 JSONP: var k=([{day,open,high,low,close,volume},...]);  解析出数组
function parseSina(out) {
  const a = out.indexOf('([');
  const b = out.lastIndexOf('])');
  if (a < 0 || b < 0) return null;
  try { return JSON.parse(out.slice(a + 1, b + 1)); }
  catch (e) { return null; }
}

function httpText(url, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    execFile('curl', ['-s', '--max-time', String(Math.ceil(timeoutMs / 1000)), '-A', 'Mozilla/5.0', url],
      { maxBuffer: 8 * 1024 * 1024, encoding: 'utf8' }, (err, stdout) => {
        if (err) return reject(err);
        resolve(stdout);
      });
  });
}

async function sinaKline(code, days, tries = 3) {
  const sym = secidOf(code); // sh/sz/bj + code
  const url = SINA + sym + '&scale=240&ma=no&datalen=' + days;
  let last;
  for (let t = 0; t < tries; t++) {
    try {
      const txt = await httpText(url, 20000);
      const arr = parseSina(txt);
      if (arr && arr.length) return arr;
      last = new Error('empty kline');
    } catch (e) { last = e; }
    await sleep(300 * (t + 1));
  }
  throw last;
}

// 东财 push2his 日K：secid = 1.(沪) / 0.(深京)；fields2 f51日期 f53收盘
// 返回与新浪统一格式 [{day:'YYYY-MM-DD', close:'..'}, ...]
async function emKline(code, days, tries = 2) {
  const secid = (/^(6|9)/.test(code) ? '1.' : '0.') + code;
  const url = EMK + qstr({
    secid, fields1: 'f1,f2,f3', fields2: 'f51,f53',
    klt: '101', fqt: '1', lmt: String(days), end: '20500101'
  });
  let last;
  for (let t = 0; t < tries; t++) {
    try {
      const j = await httpJson(url, 20000, false);
      const ks = j && j.data && j.data.klines;
      if (ks && ks.length) return ks.map(s => { const p = s.split(','); return { day: p[0], close: p[1] }; });
      last = new Error('empty em kline');
    } catch (e) { last = e; }
    await sleep(300 * (t + 1));
  }
  throw last;
}

// K线源选择：本地默认新浪（快），云端用 KLINE_SRC=em 直接走东财 push2his
// 境外 GitHub 访问新浪不通，若"新浪优先"会每只空等超时，4491 只必然超时
const KLINE_SRC = process.env.KLINE_SRC || 'auto';
async function kline(code, days) {
  if (KLINE_SRC === 'em') return await emKline(code, days, 3);
  if (KLINE_SRC === 'sina') return await sinaKline(code, days, 3);
  try { return await sinaKline(code, days, 2); }
  catch (e) { return await emKline(code, days, 2); }
}

function qstr(obj) {
  return Object.keys(obj).map(k => k + '=' + encodeURIComponent(obj[k])).join('&');
}

async function pool(items, size, worker) {
  const ret = new Array(items.length);
  let idx = 0, done = 0;
  async function run() {
    while (idx < items.length) {
      const i = idx++;
      try { ret[i] = await worker(items[i], i); }
      catch (e) { ret[i] = null; }
      done++;
      if (done % 300 === 0) process.stderr.write(`  进度 ${done}/${items.length}\n`);
    }
  }
  await Promise.all(Array.from({ length: Math.min(size, items.length) }, run));
  return ret;
}

function secidOf(code) {
  if (/^(6|9)/.test(code)) return 'sh' + code;
  if (/^(8|4)/.test(code)) return 'bj' + code;
  return 'sz' + code;
}

function riskLevel(wb) {
  if (wb < 110) return { key: 'd110', label: '已破110%', color: '#c0392b' };
  if (wb < 130) return { key: 'w130', label: '逼近130%', color: '#e67e22' };
  if (wb < 150) return { key: 'w150', label: '跌破150%', color: '#c9a227' };
  if (wb < 180) return { key: 'ok', label: '正常', color: '#5a7d9a' };
  return { key: 'safe', label: '安全', color: '#2e8b57' };
}

function median(arr) {
  if (!arr.length) return 0;
  const s = arr.slice().sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

(async function main() {
  const t0 = Date.now();
  console.error('[1/6] 取最新披露日期 ...');
  const dateQ = await httpRetry(DC + qstr({
    reportName: 'RPTA_WEB_RZRQ_GGMX', columns: 'DATE',
    source: 'WEB', client: 'WEB', sortColumns: 'DATE', sortTypes: '-1', pageSize: 1, pageNumber: 1
  }));
  const tradeDate = (dateQ.result.data[0].DATE || '').slice(0, 10).replace(/-/g, '');
  const dispDate = tradeDate.slice(0, 4) + '-' + tradeDate.slice(4, 6) + '-' + tradeDate.slice(6);
  console.error('  最新披露日:', dispDate);

  console.error('[2/6] 拉全市场个股融资余额明细 ...');
  const cols = 'SCODE,SECNAME,RZYE,RZMRE,RZJME,RZCHE,RZYEZB,SZ,SPJ,ZDF,DATE,SECUCODE,TRADE_MARKET';
  const first = await httpRetry(DC + qstr({
    reportName: 'RPTA_WEB_RZRQ_GGMX', columns: cols,
    filter: `(DATE='${dispDate}')`, source: 'WEB', client: 'WEB',
    sortColumns: 'RZYE', sortTypes: '-1', pageSize: 500, pageNumber: 1
  }));
  const pages = first.result.pages;
  const all = first.result.data.concat();
  for (let p = 2; p <= pages; p++) {
    const j = await httpRetry(DC + qstr({
      reportName: 'RPTA_WEB_RZRQ_GGMX', columns: cols,
      filter: `(DATE='${dispDate}')`, source: 'WEB', client: 'WEB',
      sortColumns: 'RZYE', sortTypes: '-1', pageSize: 500, pageNumber: p
    }));
    all.push(...j.result.data);
    if (p % 2 === 0) console.error(`  明细 ${p}/${pages} 页, ${all.length} 只`);
  }
  let stocks = all.filter(x => x.RZYE > 0 && x.SCODE);
  if (LIMIT) stocks = stocks.slice(0, LIMIT);
  console.error('  有效融资标的:', stocks.length);

  console.error('[3/6] 拉行业映射 ...');
  const indMap = {};
  // fs 需整体编码：冒号→%3A、加号→%2B（逗号保留原样），否则 curl 会把 + 当特殊字符
  const fs_ = 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23';
  const fsEnc = fs_.replace(/:/g, '%3A').replace(/\+/g, '%2B');
  try {
    for (let pn = 1; pn <= 30; pn++) {
      const url = `https://push2.eastmoney.com/api/qt/clist/get?pn=${pn}&pz=200&po=1&invt=2&fltt=2&fid=f3&fs=${fsEnc}&fields=f12%2Cf14%2Cf100`;
      const j = await httpRetry(url, 5, 20000, false);
      const diff = j && j.data && j.data.diff;
      if (!diff) break;
      const arr = Array.isArray(diff) ? diff : Object.values(diff);
      arr.forEach(x => { if (x.f12) indMap[x.f12] = x.f100 || '其他'; });
      if (arr.length < 200) break;
    }
  } catch (e) { console.error('  行业映射降级为“其他”:', e.message.slice(0, 80)); }
  console.error('  行业映射:', Object.keys(indMap).length);

  console.error('[4/6] 拉市场融资余额历史(近12交易日) ...');
  const hist = await httpRetry(DC + qstr({
    reportName: 'RPTA_RZRQ_LSHJ', columns: 'DIM_DATE,RZYE,RZMRE,RZJME,RZCHE,LTSZ',
    source: 'WEB', client: 'WEB', sortColumns: 'DIM_DATE', sortTypes: '-1', pageSize: 16, pageNumber: 1
  }));
  const rzSeries = hist.result.data.reverse().map(x => ({
    date: x.DIM_DATE.slice(0, 10), rzye: x.RZYE, rzmre: x.RZMRE, rzjme: x.RZJME
  })).slice(-12);

  console.error('[5/6] 拉日K线算 MA20 成本（新浪源，失败转东财）...');
  const klines = await pool(stocks, CONCURRENCY, async (s) => {
    const arr = await kline(s.SCODE, KLINE_DAYS);
    // 只取到披露日（含）为止，保证股价与两融口径同日
    const upto = arr.filter(row => row.day.replace(/-/g, '') <= tradeDate);
    if (upto.length < 5) return null;
    const last20 = upto.slice(-20);
    const ma20 = last20.reduce((a, r) => a + parseFloat(r.close), 0) / last20.length;
    const close = parseFloat(upto[upto.length - 1].close);
    return { ma20, close, kd: upto[upto.length - 1].day };
  });
  const kOk = klines.filter(Boolean).length;
  console.error(`  K线成功 ${kOk}/${stocks.length}`);
  if (kOk < stocks.length * 0.85) console.error('  ⚠ K线成功率偏低，维保样本会不完整');

  console.error('[6/6] 计算维保比例与聚合 ...');
  const rows = [];
  for (let i = 0; i < stocks.length; i++) {
    const s = stocks[i], k = klines[i];
    if (!k || !k.ma20 || !k.close) continue;
    const wb = BASE * k.close / k.ma20;
    const flowMz = Number(s.SZ) || 0;
    rows.push({
      code: s.SCODE,
      name: s.SECNAME,
      secucode: s.SECUCODE || '',
      industry: indMap[s.SCODE] || '其他',
      market: s.TRADE_MARKET || '',
      rzye: Number(s.RZYE) || 0,          // 融资余额(元)
      rzmre: Number(s.RZMRE) || 0,        // 融资买入额(元)
      rzjme: Number(s.RZJME) || 0,        // 融资净买入(元)
      flowMz,                              // 流通市值(元)
      rzPct: Number(s.RZYEZB) || 0,       // 融资余额占流通市值比(%)
      close: +k.close.toFixed(2),
      ma20: +k.ma20.toFixed(2),
      chg: Number(s.ZDF) || 0,             // 当日涨跌幅(%)
      wb: +wb.toFixed(1),
      risk: riskLevel(wb).key,
      riskLabel: riskLevel(wb).label,
      riskColor: riskLevel(wb).color
    });
  }
  rows.sort((a, b) => a.wb - b.wb);

  const totalRzye = rows.reduce((a, r) => a + r.rzye, 0);
  const totalMz = rows.reduce((a, r) => a + r.flowMz, 0);
  const weightedWb = totalRzye ? rows.reduce((a, r) => a + r.wb * r.rzye, 0) / totalRzye : 0;

  // 区间分布
  const buckets = [
    { key: 'lt110', label: '<110%', lo: -1e9, hi: 110 },
    { key: 'b110', label: '110-130%', lo: 110, hi: 130 },
    { key: 'b130', label: '130-150%', lo: 130, hi: 150 },
    { key: 'b150', label: '150-180%', lo: 150, hi: 180 },
    { key: 'ge180', label: '≥180%', lo: 180, hi: 1e9 }
  ].map(b => {
    const g = rows.filter(r => r.wb >= b.lo && r.wb < b.hi);
    const amt = g.reduce((a, r) => a + r.rzye, 0);
    return { key: b.key, label: b.label, n: g.length, amt, pct: totalRzye ? +(amt / totalRzye * 100).toFixed(2) : 0 };
  });

  // 下跌敏感性：全市场再跌 X% 后，跌破 130/150 的余额占比与只数
  const sensitivity = [0, -3, -5, -8, -10, -15, -20].map(drop => {
    const f = 1 + drop / 100;
    const b130 = rows.filter(r => r.wb * f < 130);
    const b150 = rows.filter(r => r.wb * f < 150);
    return {
      drop,
      n130: b130.length, amt130: b130.reduce((a, r) => a + r.rzye, 0),
      pct130: totalRzye ? +(b130.reduce((a, r) => a + r.rzye, 0) / totalRzye * 100).toFixed(2) : 0,
      n150: b150.length, amt150: b150.reduce((a, r) => a + r.rzye, 0),
      pct150: totalRzye ? +(b150.reduce((a, r) => a + r.rzye, 0) / totalRzye * 100).toFixed(2) : 0
    };
  });

  // 分组聚合（行业 / 板块）
  function aggBy(field) {
    const m = new Map();
    rows.forEach(r => {
      const k = r[field] || '其他';
      if (!m.has(k)) m.set(k, []);
      m.get(k).push(r);
    });
    return Array.from(m.entries()).map(([name, g]) => {
      const amt = g.reduce((a, r) => a + r.rzye, 0);
      const b130 = g.filter(r => r.wb < 130), b150 = g.filter(r => r.wb < 150);
      return {
        name, n: g.length, rzye: amt,
        pct: totalRzye ? +(amt / totalRzye * 100).toFixed(2) : 0,
        wWb: amt ? +(g.reduce((a, r) => a + r.wb * r.rzye, 0) / amt).toFixed(1) : 0,
        medWb: +median(g.map(r => r.wb)).toFixed(1),
        n130: b130.length, amt130: b130.reduce((a, r) => a + r.rzye, 0),
        n150: b150.length
      };
    }).sort((a, b) => b.rzye - a.rzye);
  }
  const industries = aggBy('industry');
  const markets = aggBy('market');

  // 重点关注：维保<150 中，融资余额排名 × 维保排名 最小（体量大且脆弱）
  const under150 = rows.filter(r => r.wb < 150);
  const byAmt = under150.slice().sort((a, b) => b.rzye - a.rzye);
  const byWb = under150.slice().sort((a, b) => a.wb - b.wb);
  const rankAmt = new Map(), rankWb = new Map();
  byAmt.forEach((r, i) => rankAmt.set(r.code, i + 1));
  byWb.forEach((r, i) => rankWb.set(r.code, i + 1));
  const focus = under150.map(r => ({ ...r, score: rankAmt.get(r.code) * rankWb.get(r.code) }))
    .sort((a, b) => a.score - b.score).slice(0, 10);

  const b130 = rows.filter(r => r.wb < 130);
  const b150 = rows.filter(r => r.wb < 150);

  const data = {
    meta: {
      tradeDate: dispDate,
      generatedAt: new Date().toISOString(),
      source: '东方财富 datacenter-web + 腾讯前复权K线',
      formula: '维保比例(%) = 180 × 现价 / MA20成本价（前复权）',
      note: '两融数据T+1披露；维保为模拟测算口径，融资均价用MA20近似（原报告用融资买入额加权，差异约1%）',
      lev: LEV
    },
    kpi: {
      weightedWb: +weightedWb.toFixed(1),
      medWb: +median(rows.map(r => r.wb)).toFixed(1),
      n130: b130.length,
      amt130: b130.reduce((a, r) => a + r.rzye, 0),
      pct130: +(b130.length / rows.length * 100).toFixed(2),
      n150: b150.length,
      amt150: b150.reduce((a, r) => a + r.rzye, 0),
      pct150: +(b150.length / rows.length * 100).toFixed(2),
      totalRzye, totalMz,
      mzPct: totalMz ? +(totalRzye / totalMz * 100).toFixed(3) : 0,
      sample: rows.length
    },
    buckets, sensitivity, industries, markets,
    indMap,
    rzSeries,
    lowest: rows.slice(0, 200),
    focus
  };

  fs.writeFileSync(OUT, JSON.stringify(data));
  console.error(`完成: ${OUT} (${(fs.statSync(OUT).size / 1024).toFixed(0)} KB, ${rows.length} 只, 用时 ${((Date.now() - t0) / 1000).toFixed(0)}s)`);
  console.error(`加权维保 ${data.kpi.weightedWb}% | 破130 ${data.kpi.n130}只 | 破150 ${data.kpi.n150}只 | 融资余额 ${(totalRzye / 1e8).toFixed(0)}亿`);
})().catch(e => { console.error('FATAL', e); process.exit(1); });

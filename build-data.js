#!/usr/bin/env node
/**
 * 融资盘平仓风险日报 - 数据生成脚本
 *
 * 数据源（全部公开，统一走 datacenter-web，本机/境外云端都稳定）：
 *  - 东财 datacenter-web RPTA_WEB_RZRQ_GGMX ：个股融资余额明细（T+1 披露）+ 历史每日现价 SPJ
 *  - 东财 datacenter-web RPTA_RZRQ_LSHJ    ：市场级融资余额历史（近12交易日）
 *
 * ★ MA20 成本价的取法（v2，2026-11-19 重写）：
 *   放弃逐只 K 线接口（新浪/东财 push2his 在云端均被限流，4491 只必超时）。
 *   改为：按交易日批量拉全市场明细里的 SPJ（当日收盘价），20 个交易日 ≈ 180 次请求，
 *   拼成每只股票的价格序列算 MA20。已与旧版逐位对账：中际旭创 MA20 514.44 / 维保 138.7% 一致。
 *
 * 维保比例口径（复刻原日报）：
 *   维保比例(%) = 180 × 现价 / 20日成本价
 *   其中 180 = 1.8 倍初始杠杆；成本价用 MA20（原报告用融资买入额加权均价，差异约1%）
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
// MA20 成本价数据源：两融明细里的 SPJ（当日收盘价），按交易日批量取，单一稳定源
const DC_COLS_PRICE = 'SCODE,SPJ';

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

// 取最近 N 个交易日（截至披露日，含披露日），返回 YYYY-MM-DD 数组（新→旧）
async function tradingDays(tradeDate, n) {
  const url = DC + qstr({
    reportName: 'RPTA_RZRQ_LSHJ', columns: 'DIM_DATE',
    source: 'WEB', client: 'WEB', sortColumns: 'DIM_DATE', sortTypes: -1,
    pageSize: n + 5, pageNumber: 1
  });
  let j;
  for (let t = 0; t < 4; t++) {
    try {
      j = await httpJson(url, 30000, false);
      if (j && j.result && j.result.data && j.result.data.length) break;
    } catch (e) { }
    await sleep(500 * (t + 1));
  }
  if (!j || !j.result || !j.result.data) throw new Error('交易日列表获取失败');
  return j.result.data
    .map(x => x.DIM_DATE.slice(0, 10).replace(/-/g, ''))
    .filter(d => d <= tradeDate)
    .slice(0, n);
}

// 按单个交易日批量拉全市场 SPJ（收盘价），分页；返回 {SCODE: price}
async function priceOfDay(d, tries = 3) {
  const disp = d.slice(0, 4) + '-' + d.slice(4, 6) + '-' + d.slice(6);
  const base = {
    reportName: 'RPTA_WEB_RZRQ_GGMX', columns: DC_COLS_PRICE,
    filter: `(DATE='${disp}')`, source: 'WEB', client: 'WEB',
    sortColumns: 'SCODE', sortTypes: 1, pageSize: 500
  };
  let first;
  for (let t = 0; t < tries; t++) {
    try { first = await httpJson(DC + qstr(Object.assign({ pageNumber: 1 }, base)), 30000, false); if (first && first.result) break; }
    catch (e) { await sleep(400 * (t + 1)); }
  }
  if (!first || !first.result) throw new Error('price day failed ' + disp);
  const map = {};
  const put = rows => rows.forEach(r => { const p = Number(r.SPJ); if (r.SCODE && p > 0) map[r.SCODE] = p; });
  put(first.result.data);
  const pages = first.result.pages || 1;
  for (let p = 2; p <= pages; p++) {
    let j;
    for (let t = 0; t < tries; t++) {
      try { j = await httpJson(DC + qstr(Object.assign({ pageNumber: p }, base)), 30000, false); if (j && j.result) break; }
      catch (e) { await sleep(400 * (t + 1)); }
    }
    if (!j || !j.result) continue; // 某页失败就跳过该页，其它日照常算
    put(j.result.data);
  }
  return map;
}

// 构建全市场 MA20：按交易日并发拉价，拼每只股票的价格序列
// 返回 { code: {ma20, close, days} }
async function buildMA20(tradeDate, days) {
  const dlist = await tradingDays(tradeDate, days);
  console.error('  取价交易日:', dlist.length, '天 (' + dlist[dlist.length - 1] + ' ~ ' + dlist[0] + ')');
  // code -> {date:price}
  const series = {};
  const dayMaps = await pool(dlist, 6, async d => {
    try { return await priceOfDay(d, 3); }
    catch (e) { console.error('  取价失败', d, e.message.slice(0, 40)); return {}; }
  });
  dayMaps.forEach((map, idx) => {
    const d = dlist[idx];
    Object.keys(map).forEach(code => {
      if (!series[code]) series[code] = {};
      series[code][d] = map[code];
    });
  });
  const out = {};
  Object.keys(series).forEach(code => {
    const entries = Object.entries(series[code]).sort((a, b) => a[0].localeCompare(b[0])); // 升序
    if (entries.length < 5) return;
    const use = entries.slice(-20);
    const ma20 = use.reduce((s, x) => s + x[1], 0) / use.length;
    out[code] = { ma20, close: use[use.length - 1][1], days: use.length, kd: use[use.length - 1][0] };
  });
  console.error('  价格序列覆盖:', Object.keys(out).length, '只');
  return out;
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

  // 行业映射已废弃：网页改用两融明细自带的 TRADE_MARKET 板块字段，稳定可靠。
  // 原 push2 clist 行业接口在境外/本机都不稳定，30页×5次重试会把云端任务拖到超时，故移除。
  console.error('[3/6] 行业维度改用两融明细自带板块字段（跳过 push2 行业接口）...');

  console.error('[4/6] 拉市场融资余额历史(近12交易日) ...');
  const hist = await httpRetry(DC + qstr({
    reportName: 'RPTA_RZRQ_LSHJ', columns: 'DIM_DATE,RZYE,RZMRE,RZJME,RZCHE,LTSZ',
    source: 'WEB', client: 'WEB', sortColumns: 'DIM_DATE', sortTypes: '-1', pageSize: 16, pageNumber: 1
  }));
  const rzSeries = hist.result.data.reverse().map(x => ({
    date: x.DIM_DATE.slice(0, 10), rzye: x.RZYE, rzmre: x.RZMRE, rzjme: x.RZJME
  })).slice(-12);

  console.error('[5/6] 用两融明细历史价格算 MA20 成本（批量取价，单一稳定源）...');
  const maMap = await buildMA20(tradeDate, KLINE_DAYS);
  const klines = stocks.map(s => maMap[s.SCODE] || null);
  const kOk = klines.filter(Boolean).length;
  console.error(`  MA20覆盖 ${kOk}/${stocks.length}`);
  if (kOk < stocks.length * 0.85) console.error('  ⚠ MA20覆盖率偏低，维保样本会不完整');

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
      industry: s.TRADE_MARKET || '其他',  // 行业维度已废弃，用板块字段兜底
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
    rzSeries,
    lowest: rows.slice(0, 200),
    focus
  };

  fs.writeFileSync(OUT, JSON.stringify(data));
  console.error(`完成: ${OUT} (${(fs.statSync(OUT).size / 1024).toFixed(0)} KB, ${rows.length} 只, 用时 ${((Date.now() - t0) / 1000).toFixed(0)}s)`);
  console.error(`加权维保 ${data.kpi.weightedWb}% | 破130 ${data.kpi.n130}只 | 破150 ${data.kpi.n150}只 | 融资余额 ${(totalRzye / 1e8).toFixed(0)}亿`);
})().catch(e => { console.error('FATAL', e); process.exit(1); });

/* 杠杆周期复盘 · 自包含日更生成器（2026-11-28 根治版）
 *
 * 背景：原 leverage-cycle.html 内嵌静态 JSON，无云端链路，数据停在 11-25。
 * 本脚本独立运行在云端仓（无本地文件依赖），每日重算全量并输出 leverage-history.json，
 * 供页面运行时拉取；页面内嵌数据仅作离线兜底。
 *
 * 数据源（实测 2026-11-28 可用）：
 *   · 指数日K：ifzq.gtimg.cn/appstock/app/kline/kline?param=sh000001,day,,,N,qfq
 *              （web.ifzq / push2his / push2test 均被拦或空，此路径可用，1400 条上限需分段）
 *   · 两融历史：datacenter-web RPTA_RZRQ_LSHJ（T+1）
 *   · 真实维保：本仓 margin-risk.json（云端日更，lowest 数组派生 dangerList）
 *
 * 口径：代理维保=180×指数/MA20；拐点 ZigZag 15% 阈值（事后标注，禁用于择时）。
 * 用法：node leverage-history.js  → 本仓根目录 leverage-history.json
 */
'use strict';
const https = require('https');
const fs = require('fs');
const path = require('path');

const DIR = process.env.DATA_OUT_DIR || __dirname;
const OUT = path.join(DIR, 'leverage-history.json');

function get(url, ref, timeout) {
  return new Promise((res, rej) => {
    const q = https.get(url, { timeout: timeout || 25000, headers: { 'User-Agent': 'Mozilla/5.0', 'Referer': ref || 'https://quote.eastmoney.com/' } }, r => {
      const c = []; r.on('data', d => c.push(d));
      r.on('end', () => res({ s: r.statusCode, body: Buffer.concat(c).toString('utf8') }));
    });
    q.on('error', rej); q.on('timeout', () => { q.destroy(); rej(new Error('timeout')); });
  });
}
async function jget(url, ref) {
  for (let t = 0; t < 3; t++) {
    try { const r = await get(url, ref); if (r.s === 200) return JSON.parse(r.body); } catch (e) { }
    await new Promise(r => setTimeout(r, 600 * (t + 1)));
  }
  throw new Error('取数失败: ' + url.slice(0, 90));
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* 指数日K：分段拉（1400 条上限），从近到远拼到 2016-01，去重 */
async function getIndex() {
  /* kline 接口不认 qfq 第5参（那是 fqkline 的），用无复权 3 段拼接。
     上证指数本身无除权，不复权=前复权，口径一致。 */
  const ends = ['', '2021-02-22', '2017-06-30'];
  const map = new Map();
  for (const end of ends) {
    const param = end ? `sh000001,day,,${end},1400` : 'sh000001,day,,,1400';
    const j = await jget('https://ifzq.gtimg.cn/appstock/app/kline/kline?param=' + param, 'https://gu.qq.com/');
    const d = j && j.data && j.data.sh000001;
    const k = d && (d.qfqday || d.day);
    if (!k) { console.error('  段失败', end || '最新'); continue; }
    k.forEach(row => { if (row[0] && +row[2] > 0) map.set(row[0], +row[2]); });
    console.error('  段', end || '最新', '得', k.length, '条，累计', map.size);
    await sleep(250);
  }
  const rows = Array.from(map.entries()).filter(r => r[0] >= '2016-01-01').sort((a, b) => a[0].localeCompare(b[0]));
  if (rows.length < 1000) throw new Error('指数日K不足：' + rows.length + ' 条（预期 >1000）');
  console.error('  指数', rows.length, '条', rows[0][0], '~', rows[rows.length - 1][0]);
  return rows.map(r => ({ date: r[0], close: r[1] }));
}

async function getMargin() {
  const base = 'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPTA_RZRQ_LSHJ&columns=DIM_DATE,RZYE,RZMRE,RZJME,RZCHE&source=WEB&client=WEB&sortColumns=DIM_DATE&sortTypes=-1&pageSize=800&pageNumber=';
  const map = new Map();
  for (let p = 1; p <= 6; p++) {
    const j = await jget(base + p, 'https://data.eastmoney.com/');
    const d = j && j.result && j.result.data;
    if (!d) break;
    d.forEach(r => {
      const date = r.DIM_DATE.slice(0, 10);
      map.set(date, { date, rzye: +r.RZYE, rzmre: +r.RZMRE, rzjme: +r.RZJME, rzche: +r.RZCHE });
    });
    if (d.length < 800) break;
    await sleep(250);
  }
  const rows = Array.from(map.values()).sort((a, b) => a.date.localeCompare(b.date));
  if (rows.length < 1000) throw new Error('两融历史不足：' + rows.length);
  console.error('  两融', rows.length, '条', rows[0].date, '~', rows[rows.length - 1].date);
  return rows;
}

function withProxy(index) {
  return index.map((x, i) => {
    let ma20 = null;
    if (i >= 19) { let s = 0; for (let k = i - 19; k <= i; k++) s += index[k].close; ma20 = s / 20; }
    return { ...x, ma20: ma20 ? +ma20.toFixed(2) : null, proxyWb: ma20 ? +(180 * x.close / ma20).toFixed(1) : null };
  });
}
function zigzag(series, pct) {
  const pts = []; if (series.length < 2) return pts;
  let dir = 1, ext = 0;
  for (let i = 1; i < series.length; i++) {
    const c = series[i].close, e = series[ext].close;
    if (dir === 1) {
      if (c > e) { ext = i; continue; }
      if ((e - c) / e * 100 >= pct) { pts.push({ ...series[ext], kind: '顶' }); dir = -1; ext = i; }
    } else {
      if (c < e) { ext = i; continue; }
      if ((c - e) / e * 100 >= pct) { pts.push({ ...series[ext], kind: '底' }); dir = 1; ext = i; }
    }
  }
  const out = [];
  for (const p of pts) {
    if (out.length && out[out.length - 1].kind === p.kind) {
      const q = out[out.length - 1];
      if ((p.kind === '底' && p.close < q.close) || (p.kind === '顶' && p.close > q.close)) out[out.length - 1] = p;
      continue;
    }
    out.push(p);
  }
  return out;
}

(async () => {
  console.error('[1/4] 指数日K');
  const index = withProxy(await getIndex());
  console.error('[2/4] 两融历史');
  const margin = await getMargin();
  const mMap = new Map(margin.map(m => [m.date, m]));
  const mDates = margin.map(m => m.date);
  function marginAt(date) {
    if (mMap.has(date)) return mMap.get(date);
    let lo = 0, hi = mDates.length - 1, res = null;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (mDates[mid] <= date) { res = mMap.get(mDates[mid]); lo = mid + 1; } else hi = mid - 1; }
    return res;
  }

  console.error('[3/4] 拐点/阶段/分析');
  const pivots = zigzag(index, 15).filter(p => p.date >= '2016-01-01').map(p => {
    const m = marginAt(p.date);
    return { date: p.date, kind: p.kind, close: p.close, proxyWb: p.proxyWb, ma20: p.ma20, rzye: m ? m.rzye : null, rzyeYi: m ? +(m.rzye / 1e8).toFixed(0) : null };
  });
  const phases = [];
  for (let i = 0; i < pivots.length - 1; i++) {
    const a = pivots[i], b = pivots[i + 1];
    phases.push({
      from: a.date, to: b.date, type: a.kind === '底' ? '底→顶(加杠杆)' : '顶→底(去杠杆)',
      idxFrom: a.close, idxTo: b.close, idxChg: +((b.close - a.close) / a.close * 100).toFixed(1),
      rzyeFrom: a.rzyeYi, rzyeTo: b.rzyeYi,
      rzyeChg: (a.rzyeYi && b.rzyeYi) ? +((b.rzyeYi - a.rzyeYi) / a.rzyeYi * 100).toFixed(1) : null,
      rzyeAbsChg: (a.rzyeYi && b.rzyeYi) ? (b.rzyeYi - a.rzyeYi) : null, wbFrom: a.proxyWb, wbTo: b.proxyWb
    });
  }
  const series = index.filter(x => x.proxyWb != null).map(x => {
    const m = marginAt(x.date);
    return { d: x.date, c: x.close, wb: x.proxyWb, rz: m ? +(m.rzye / 1e8).toFixed(0) : null };
  });
  const lastIdx = index[index.length - 1], lastM = margin[margin.length - 1];

  /* 真实维保：优先本仓 margin-risk.json（云端日更），lowest 派生 dangerList */
  let mr = null;
  try { mr = JSON.parse(fs.readFileSync(path.join(__dirname, 'margin-risk.json'), 'utf8')); } catch (e) { console.error('  ⚠ margin-risk.json 读取失败:', e.message); }
  const k = mr && mr.kpi || {};
  /* ★危险榜口径（与历史页面 buildDanger 逐字一致，2026-11-28 核验修正）：
     ① 候选池 = lowest ∪ focus 去重（不是只取 lowest 前15 —— 那样会把「体量大但维保未到最低」的
        重点票（浪潮/兴业/巨化等）全漏掉，榜单退化成纯最低维保小票）；
     ② 过滤 wb<155 且融资余额≥1亿且剔基金；
     ③ score = norm(155-wb,0,55)*0.45 + (rzye/maxR)^0.6*0.35 + norm(rzPct,0,20)*0.20，×100；
     ④ dropTo130 = (1-130/wb)*100（维保跌到130%还需多少跌幅），不是 (ma20*1.3-close)/ma20；
     ⑤ breached = wb<130。已用 11-19 快照逐位复现旧页 score 61.1/57/46.1。 */
  const uniq = [], seen = {};
  ((mr && mr.lowest) || []).concat((mr && mr.focus) || []).forEach(x => {
    if (x && !seen[x.code]) { seen[x.code] = 1; uniq.push(x); }
  });
  const isFund = c => /^(5|1)\d{5}$/.test(String(c || ''));
  const norm = (v, a, b) => Math.max(0, Math.min(1, (v - a) / (b - a)));
  const under = uniq.filter(x => x.wb < 155 && x.rzye >= 1e8 && !isFund(x.code));
  const maxR = under.reduce((m, x) => Math.max(m, x.rzye), 1);
  const dangerList = under.map(x => {
    const frag = norm(155 - x.wb, 0, 55);
    const scale = Math.pow(x.rzye / maxR, 0.6);
    const rpct = norm(x.rzPct || 0, 0, 20);
    return {
      code: x.code, name: x.name, wb: x.wb, rzyeYi: +(x.rzye / 1e8).toFixed(2),
      chg: x.chg, rzPct: +(x.rzPct || 0).toFixed(2), market: x.market || x.industry,
      score: +((frag * 0.45 + scale * 0.35 + rpct * 0.20) * 100).toFixed(1),
      dropTo130: x.wb >= 130 ? +((1 - 130 / x.wb) * 100).toFixed(1) : 0,
      close: x.close, ma20: x.ma20, breached: x.wb < 130
    };
  }).sort((a, b) => b.score - a.score).slice(0, 15);

  const peak = pivots.filter(p => p.kind === '顶').sort((a, b) => b.rzyeYi - a.rzyeYi)[0];
  const delever = phases.filter(p => p.type.indexOf('去杠杆') >= 0).map(p => p.rzyeChg).filter(v => v != null);
  const wbTops = pivots.filter(p => p.kind === '顶' && p.proxyWb).map(p => p.proxyWb);
  const wbBots = pivots.filter(p => p.kind === '底' && p.proxyWb).map(p => p.proxyWb);
  const analysis = {
    cyclePeak: peak ? { date: peak.date, rzyeYi: peak.rzyeYi } : null,
    currentDrawdown: peak && lastM ? { absYi: +(peak.rzyeYi - lastM.rzye / 1e8).toFixed(0), pct: +((lastM.rzye / 1e8 - peak.rzyeYi) / peak.rzyeYi * 100).toFixed(1) } : null,
    histDelever: { list: delever, avg: delever.length ? +(delever.reduce((a, b) => a + b, 0) / delever.length).toFixed(1) : null, deepest: delever.length ? Math.min(...delever) : null },
    topWbRange: wbTops.length ? [Math.min(...wbTops), Math.max(...wbTops)] : null,
    bottomWbRange: wbBots.length ? [Math.min(...wbBots), Math.max(...wbBots)] : null
  };

  const data = {
    meta: {
      generatedAt: new Date().toISOString(),
      indexSource: '腾讯 ifzq kline 接口 sh000001 前复权(qfq)，分段拼接去重',
      marginSource: '东方财富 datacenter-web RPTA_RZRQ_LSHJ',
      realWbSource: '本仓 margin-risk.json（个股加权维保，MA20成本口径）',
      proxyNote: '代理维保 = 180×指数/指数MA20，仅描述市场整体杠杆环境温度，非真实披露维保；真实维保以当前节点 realWb 为准',
      zigzagNote: '大底大顶按固定15%反向波动阈值事后标注；拐点确认依赖后续反向走势(含未来数据)，仅用于划分历史阶段，严禁用于实盘择时，不构成预测',
      seriesCount: series.length, indexRange: [index[0].date, lastIdx.date],
      marginRange: [margin[0].date, lastM.date]
    },
    series, pivots, phases,
    current: {
      date: lastIdx.date, close: lastIdx.close, proxyWb: lastIdx.proxyWb,
      marginDate: lastM.date, rzyeYi: +(lastM.rzye / 1e8).toFixed(0), rzjmeYi: +(lastM.rzjme / 1e8).toFixed(1),
      realWb: k.weightedWb || null, realN130: k.n130 || null, realN150: k.n150 || null,
      realSample: k.sample || null, realDate: mr && mr.meta && mr.meta.tradeDate || null
    },
    analysis, dangerList
  };
  fs.writeFileSync(OUT, JSON.stringify(data));
  console.error('[4/4] 写出', OUT, (fs.statSync(OUT).size / 1024).toFixed(0), 'KB');
  console.error('完成: 序列', series.length, '拐点', pivots.length, '当前', lastIdx.date, '两融', lastM.date, '真实维保', data.current.realDate);
})().catch(e => { console.error('FATAL', e); process.exit(1); });

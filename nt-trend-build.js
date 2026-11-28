#!/usr/bin/env node
/* nt-trend-build.js — 国家队宽基 ETF 份额趋势 × 上证指数
 * 2026-11-28 新增
 *
 * 数据源（全部为官方/公开权威源，GitHub Actions 海外出口实测可达）：
 *   份额 : 上交所官方日频 ETF 规模披露 COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L
 *          支持历史回溯（已验证可回溯至 2023 年），T 日盘后披露
 *   指数 : 新浪日K sh000001（上证指数）
 *   价格 : 新浪日K sh510300 等（ETF 收盘价）
 *
 * 口径（页面必须原样展示）：
 *   ETF 份额变化 = 全市场净申赎。在宽基大 ETF 上，巨额份额变动主要由
 *   中央汇金等国家队资金主导，是市场公认的"国家队动向"代理指标，
 *   但不等于汇金账户精确持仓，也不代表单一机构行为。
 *   净申赎金额 = Σ(份额变化 × 当日收盘价)，按净值申赎近似，误差 <2%。
 *
 * 增量策略：历史份额不会变，读已有 JSON 复用；只补缺失交易日 +
 *   重拉最近 10 个交易日（交易所偶有事后修正）。全量重跑用 --full。
 */
'use strict';
const https = require('https');
const fs = require('fs');
const path = require('path');

const OUT = process.env.NT_TREND_OUT || path.join(__dirname, 'nt-trend.json');
const YEARS = 3;
const FULL = process.argv.includes('--full');
const REBASE_DAYS = 10;          // 最近 N 个交易日强制重拉（吸收交易所事后修正）
const CONCURRENCY = 5;

const ETFS = [
  { code: '510300', name: '沪深300ETF(华泰柏瑞)', grp: '大盘' },
  { code: '510310', name: '沪深300ETF(易方达)', grp: '大盘' },
  { code: '510330', name: '沪深300ETF(华夏)', grp: '大盘' },
  { code: '510050', name: '上证50ETF', grp: '大盘' },
  { code: '510500', name: '中证500ETF', grp: '中盘' },
  { code: '512100', name: '中证1000ETF', grp: '小盘' },
  { code: '588000', name: '科创50ETF(华夏)', grp: '科创' },
  { code: '588080', name: '科创板50ETF(易方达)', grp: '科创' }
];

function log(m) { console.log(new Date().toISOString().slice(11, 19) + ' ' + m); }

function httpGet(url, headers, timeout) {
  return new Promise((res, rej) => {
    const req = https.get(url, {
      timeout: timeout || 20000,
      headers: Object.assign({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0'
      }, headers || {})
    }, r => {
      const c = [];
      r.on('data', d => c.push(d));
      r.on('end', () => res({ s: r.statusCode, body: Buffer.concat(c).toString('utf8') }));
    });
    req.on('error', rej);
    req.on('timeout', () => { req.destroy(); rej(new Error('timeout')); });
  });
}

/* 带退避重试：交易所接口偶发 5xx / 连接重置 */
async function getRetry(url, headers, tries) {
  const n = tries || 3;
  let last;
  for (let i = 0; i < n; i++) {
    try {
      const r = await httpGet(url, headers, 20000);
      if (r.s === 200 && r.body) return r.body;
      last = new Error('HTTP ' + r.s);
    } catch (e) { last = e; }
    if (i < n - 1) await new Promise(r => setTimeout(r, 800 * (i + 1)));
  }
  throw last;
}

async function mapLimit(arr, limit, fn) {
  const out = new Array(arr.length);
  let idx = 0;
  const workers = Array.from({ length: Math.min(limit, arr.length) }, async () => {
    while (true) {
      const i = idx++;
      if (i >= arr.length) return;
      try { out[i] = await fn(arr[i], i); }
      catch (e) { out[i] = { __err: e.message }; }
    }
  });
  await Promise.all(workers);
  return out;
}

/* 日K：新浪优先，失败退腾讯 ifzq（双通道，云端出口两家可用性不同）
   返回 [{day, close}]，均为**不复权**价（份额折算复权由本脚本自己做，
   所以价格必须保持交易所原始口径，不能用前复权，否则折算检测会失效） */
async function sinaKline(symbol, datalen) {
  const u = 'https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_x=/CN_MarketDataService.getKLineData?symbol='
    + symbol + '&scale=240&ma=no&datalen=' + datalen;
  const body = await getRetry(u, {}, 2);
  const m = body.match(/\[\s*\{[\s\S]*\}\s*\]/);
  if (!m) throw new Error('新浪返回格式异常: ' + body.slice(0, 80));
  const arr = JSON.parse(m[0]);
  const out = arr.map(x => ({ day: x.day, close: +x.close })).filter(x => x.day && x.close > 0);
  if (!out.length) throw new Error('新浪空数据');
  return out;
}

async function txKline(symbol, datalen) {
  const u = 'https://ifzq.gtimg.cn/appstock/app/kline/kline?param=' + symbol + ',day,,,' + datalen;
  const body = await getRetry(u, { 'Referer': 'https://gu.qq.com/' }, 2);
  let j;
  try { j = JSON.parse(body); } catch (e) { throw new Error('腾讯非JSON'); }
  const node = j && j.data && j.data[symbol];
  const rows = node && (node.day || node.qfqday);
  if (!rows || !rows.length) throw new Error('腾讯空数据');
  return rows.map(r => ({ day: r[0], close: +r[2] })).filter(x => x.day && x.close > 0);
}

async function kline(symbol, datalen) {
  let e1;
  try { return await sinaKline(symbol, datalen); } catch (e) { e1 = e; }
  try { return await txKline(symbol, datalen); }
  catch (e2) { throw new Error('两家日K均失败 | 新浪: ' + e1.message + ' | 腾讯: ' + e2.message); }
}

/* 上交所官方日频份额：返回 {code: 亿份}；非交易/未披露返回 {} */
const SSE_SQL = 'https://query.sse.com.cn/commonQuery.do?sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L'
  + '&isPagination=true&pageHelp.pageSize=2000&pageHelp.pageNo=1&STAT_DATE=';
async function sseShares(day) {
  const body = await getRetry(SSE_SQL + day, { 'Referer': 'http://www.sse.com.cn/' }, 3);
  let j;
  try { j = JSON.parse(body); } catch (e) { throw new Error('SSE 非JSON: ' + body.slice(0, 60)); }
  const rows = (j.pageHelp && j.pageHelp.data) || [];
  const out = {};
  let hit = 0;
  rows.forEach(r => {
    const c = String(r.SEC_CODE || '').trim();
    if (!ETFS.some(e => e.code === c)) return;
    const v = Number(r.TOT_VOL);
    if (isFinite(v) && v > 0) { out[c] = +(v / 1e4).toFixed(2); hit++; }
  });
  return { map: out, hit: hit };
}

function md5ish(s) { return String(s).length + ':' + String(s).slice(0, 24); }

(async () => {
  const t0 = Date.now();
  log('=== 国家队份额趋势构建开始 ' + (FULL ? '(全量)' : '(增量)') + ' ===');

  /* ---- 1. 上证指数日K → 交易日历 ---- */
  log('1. 拉上证指数日K');
  const idxAll = await kline('sh000001', 820);
  if (!idxAll.length) throw new Error('上证指数日K为空');
  const cutoff = new Date(Date.now() - Math.round(YEARS * 365.25) * 86400000).toISOString().slice(0, 10);
  const idx = idxAll.filter(x => x.day >= cutoff);
  const dates = idx.map(x => x.day);
  log('   指数 ' + idxAll.length + ' 根，取近 ' + YEARS + ' 年 ' + dates.length + ' 个交易日 ('
    + dates[0] + ' ~ ' + dates[dates.length - 1] + ')');

  /* ---- 2. 读已有档案，确定要拉的日子 ---- */
  let prev = null;
  try { prev = JSON.parse(fs.readFileSync(OUT, 'utf8')); } catch (e) { prev = null; }
  const knownMap = {};   // day -> {code: shares}
  if (prev && prev.dates && prev.shares && !FULL) {
    prev.dates.forEach((d, i) => {
      const o = {}; let any = false;
      ETFS.forEach(e => {
        const arr = prev.shares[e.code];
        if (arr && arr[i] != null) { o[e.code] = arr[i]; any = true; }
      });
      if (any) knownMap[d] = o;
    });
    log('   已有档案 ' + Object.keys(knownMap).length + ' 天可复用');
  }
  const needRebase = new Set(dates.slice(-REBASE_DAYS));
  const todo = dates.filter(d => !knownMap[d] || needRebase.has(d));
  log('2. 需拉取份额 ' + todo.length + ' 天（复用 ' + (dates.length - todo.length) + ' 天）');

  /* ---- 3. 逐日拉份额 ---- */
  const fetched = {};
  let done = 0, emptyDays = [];
  if (todo.length) {
    log('3. 逐日拉上交所官方份额（并发 ' + CONCURRENCY + '）');
    const res = await mapLimit(todo, CONCURRENCY, async (d) => {
      const r = await sseShares(d);
      done++;
      if (done % 50 === 0) log('   ' + done + '/' + todo.length + ' … (' + d + ')');
      return { d: d, map: r.map, hit: r.hit };
    });
    res.forEach(r => {
      if (!r) return;
      if (r.__err) { log('   ⚠ ' + r.__err); return; }
      if (r.hit === 0) { emptyDays.push(r.d); return; }   // 非交易日或未披露
      fetched[r.d] = r.map;
    });
  }
  if (emptyDays.length) log('   ⚠ ' + emptyDays.length + ' 天无披露数据（未开盘/未披露），用前值填充');

  /* ---- 4. 合成份额矩阵（缺失 ffill） ---- */
  const shares = {}; ETFS.forEach(e => shares[e.code] = []);
  let fillCount = 0, nullCount = 0;
  const lastSeen = {}; ETFS.forEach(e => lastSeen[e.code] = null);
  dates.forEach(d => {
    const src = fetched[d] || knownMap[d] || null;
    ETFS.forEach(e => {
      let v = src && src[e.code] != null ? src[e.code] : null;
      if (v == null) {
        if (lastSeen[e.code] != null) { v = lastSeen[e.code]; fillCount++; }
        else { nullCount++; }
      } else lastSeen[e.code] = v;
      shares[e.code].push(v);
    });
  });
  log('4. 份额矩阵 ' + dates.length + '×' + ETFS.length + '，前值填充 ' + fillCount + ' 处，起点缺失 ' + nullCount + ' 处');

  /* ---- 5. ETF 收盘价 ---- */
  log('5. 拉 ' + ETFS.length + ' 只 ETF 收盘价');
  const priceRaw = await mapLimit(ETFS, 4, async (e) => {
    const k = await kline('sh' + e.code, 820);
    const m = {}; k.forEach(x => m[x.day] = x.close);
    return { code: e.code, map: m, n: k.length };
  });
  const price = {}; ETFS.forEach(e => price[e.code] = []);
  priceRaw.forEach(r => {
    if (!r || r.__err) { log('   ⚠ 价格失败 ' + (r && r.__err)); return; }
    dates.forEach(d => price[r.code].push(r.map[d] != null ? r.map[d] : null));
  });
  const priceMiss = ETFS.reduce((s, e) => s + price[e.code].filter(v => v == null).length, 0);
  log('   价格覆盖完成，缺失 ' + priceMiss + ' 格');

  /* ---- 6. 份额折算检测与复权 ----
     为什么必须做：ETF 做份额拆分/合并时，份额与价格反向跳变（2 合 1 = 份额减半、价格翻倍，规模不变）。
     实测 510310 在 2024-09-20 份额 1218.64→623.47（×0.512），价格 1.597→3.213 却在 09-23 才跳，
     两者错位 1 个交易日，直接用 Δshares×price 会造出「单日赎回 898 亿」的假信号。
     处理：检测「份额突变 + 邻近价格反向阶跃且乘积≈1」⇒ 判定折算，把折算前份额 ×f、价格 ÷f，
     全部统一到折算后口径（等价于前复权），使规模序列连续、流量计算不受污染。 */
  const splits = [];
  /* ★增量模式的关键一步：prev 里的份额已经复权过（旧折算），而价格每次都是重拉的原始值，
     必须先把「已记录的折算因子」补到价格上，否则份额与价格口径不一致、规模会算错。
     例：510310 2024-09-20 份额已由 1218.64 复权成 623.47，若价格仍取原始 1.597，
     规模会算成 995 亿（真值 1946 亿）。 */
  const prevSplits = (!FULL && prev && prev.meta && Array.isArray(prev.meta.splits)) ? prev.meta.splits : [];
  if (prevSplits.length) {
    prevSplits.forEach(sp => {
      const px = price[sp.code];
      if (!px) return;
      const pi = dates.indexOf(sp.priceAt);
      if (pi < 0) return;
      for (let j = 0; j < pi; j++) px[j] = +(px[j] / sp.f).toFixed(4);
      splits.push(sp);
    });
    log('   复用历史折算记录 ' + prevSplits.length + ' 条（已同步复权到价格）');
  }
  ETFS.forEach(e => {
    const sh = shares[e.code], px = price[e.code];
    for (let i = 1; i < sh.length; i++) {
      if (!(sh[i - 1] > 0) || !(sh[i] > 0)) continue;
      const fs = sh[i] / sh[i - 1];
      if (Math.abs(fs - 1) < 0.25) continue;          // 份额未突变
      let hitK = -1;
      for (let k = i; k <= Math.min(i + 5, px.length - 1); k++) {
        if (!(px[i - 1] > 0) || !(px[k] > 0)) continue;
        const fp = px[k] / px[i - 1];
        if (Math.abs(fp * fs - 1) < 0.12) { hitK = k; break; }   // 份额×价格 ≈ 不变 ⇒ 折算
      }
      if (hitK < 0) continue;                          // 价格未反向 ⇒ 真实大额申赎，不动
      for (let j = 0; j < i; j++) sh[j] = +(sh[j] * fs).toFixed(2);
      for (let j = 0; j < hitK; j++) px[j] = +(px[j] / fs).toFixed(4);
      splits.push({ code: e.code, day: dates[i], f: +fs.toFixed(4), priceAt: dates[hitK] });
      log('   ⇄ 检测到份额折算 ' + e.code + ' ' + dates[i] + ' 份额×' + fs.toFixed(3)
        + '（价格于 ' + dates[hitK] + ' 跳变），已前复权对齐');
    }
  });

  /* ---- 7. 聚合 ---- */
  const basketShares = [], basketVal = [], flow = [];
  for (let i = 0; i < dates.length; i++) {
    let s = 0, v = 0, f = 0, any = false;
    ETFS.forEach(e => {
      const sh = shares[e.code][i], px = price[e.code][i];
      if (sh == null) return;
      any = true;
      s += sh;
      if (px > 0) v += sh * px;
      const shPrev = i > 0 ? shares[e.code][i - 1] : null;
      if (shPrev != null && px > 0) f += (sh - shPrev) * px;
    });
    basketShares.push(any ? +s.toFixed(1) : null);
    basketVal.push(v > 0 ? +v.toFixed(0) : null);
    flow.push(i > 0 ? +f.toFixed(1) : null);
  }

  /* ---- 8. 自检：不能出现全空/负份额 ---- */
  const validShares = basketShares.filter(v => v != null);
  if (validShares.length < dates.length * 0.9) {
    throw new Error('份额覆盖不足：有效 ' + validShares.length + '/' + dates.length + ' 天，拒绝写出');
  }
  const lastShares = basketShares[basketShares.length - 1];
  if (!(lastShares > 0)) throw new Error('末日篮子份额无效 ' + lastShares + '，拒绝写出');

  /* 单日份额变动 >40% 打异常标记（真实大额申赎也可能触发，仅提示不拒写） */
  const jumps = [];
  for (let i = 1; i < dates.length; i++) {
    if (basketShares[i] == null || basketShares[i - 1] == null) continue;
    const chg = (basketShares[i] - basketShares[i - 1]) / basketShares[i - 1];
    if (Math.abs(chg) > 0.4) jumps.push({ d: dates[i], chg: +(chg * 100).toFixed(1) });
  }

  /* ---- 9. 写出 ---- */
  const doc = {
    meta: {
      generatedAt: new Date().toISOString(),
      asOf: dates[dates.length - 1],
      years: YEARS,
      days: dates.length,
      shareSource: '上交所官方日频 ETF 规模披露(COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L)，T 日盘后披露',
      indexSource: '新浪日K sh000001',
      priceSource: '新浪日K sh510xxx/588xxx 收盘价',
      caliber: '沪市 8 只宽基 ETF 份额合计。ETF 份额变化=全市场净申赎，宽基大 ETF 上由中央汇金等国家队主导，'
        + '是市场公认的"国家队动向"代理指标，不等于汇金账户精确持仓。深市 2 只(159919/159922)无官方历史份额接口，'
        + '为保证三年口径统一从起点即不纳入。净申赎金额=份额变化×当日收盘价(按净值申赎近似，误差<2%)。',
      fillNote: '非披露日用前值填充（' + fillCount + ' 处）；单日篮子份额变动>40% 已标记 ' + jumps.length + ' 处，'
        + '多为大额申赎或份额折算，需结合当日指数判断',
      jumps: jumps,
      splits: splits
    },
    etfs: ETFS,
    dates: dates,
    sse: idx.map(x => +x.close.toFixed(2)),
    shares: shares,
    price: price,
    basket: { shares: basketShares, val: basketVal, flow: flow }
  };

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  const tmp = OUT + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(doc));
  fs.renameSync(tmp, OUT);

  log('=== 完成 ' + ((Date.now() - t0) / 1000).toFixed(1) + 's ===');
  log('   输出 ' + OUT + '  ' + (fs.statSync(OUT).size / 1024).toFixed(1) + 'KB');
  log('   最新 ' + dates[dates.length - 1] + ' 篮子份额 ' + lastShares + ' 亿份 / 规模 '
    + basketVal[basketVal.length - 1] + ' 亿 / 当日净申赎 ' + flow[flow.length - 1] + ' 亿');
  if (jumps.length) log('   ⚠ 大额变动日: ' + jumps.slice(-5).map(j => j.d + '(' + j.chg + '%)').join(', '));
})().catch(e => { console.error('❌ 构建失败: ' + e.message); process.exit(1); });

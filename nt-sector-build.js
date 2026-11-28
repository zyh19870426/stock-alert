/**
 * 国家队 ETF 篮子 → 行业板块隐含敞口（穿透估算）
 * 生成 stock-alert-console/nt-sector.json
 *
 * 口径（务必随数据展示，禁止当成官方持仓披露）：
 *  1. 国家队持仓代理 = nt-trend.json 里 8 只沪市宽基 ETF 的最新份额×收盘价。
 *  2. 指数成分股无公开权重接口 → 用「全A股流通市值分层」近似：
 *     上证50=市值前50 / 沪深300=前300 / 中证500=301~800 / 中证1000=801~1800 / 科创50=科创板前50。
 *     这是市值排序近似，不是指数官方成分表；真实成分只在边界附近有出入。
 *  3. 单只股票在指数内的权重 = 其流通市值 / 该指数分层内流通市值合计（市值加权近似）。
 *  4. 某板块的隐含敞口 = Σ(每只ETF规模 × 其指数内该股票权重 × 归属系数)，
 *     归属系数：股票属1个板块记1，属2个板块各记0.5（board-members.json，东财行业127个）。
 *  5. 板块占比 = 板块隐含敞口 / 全部板块敞口合计。
 *
 * 数据源：新浪 Market_Center 全A股（sh_a+sz_a，含 nmc 流通市值，单位万元）；
 *         board-members.json（板块→成分股，云端日更）；nt-trend.json（ETF 规模）。
 * 更新：跟随 nt-trend.yml，每交易日盘后重算。
 */
const fs = require('fs');
const path = require('path');
const https = require('https');

const ROOT = __dirname;
const OUT_CONSOLE = path.join(ROOT, '..', 'stock-alert-console', 'nt-sector.json');
const OUT_CLOUD = path.join(ROOT, 'nt-sector.json');

function get(url) {
  return new Promise(function (resolve) {
    const req = https.get(url, { headers: { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn' } }, function (res) {
      let s = '';
      res.on('data', function (d) { s += d; });
      res.on('end', function () { try { resolve(JSON.parse(s)); } catch (e) { resolve(null); } });
    });
    req.on('error', function () { resolve(null); });
    req.setTimeout(12000, function () { req.destroy(); resolve(null); });
  });
}
async function pullAll(node) {
  let all = [], p = 1;
  while (p <= 70) {
    const u = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData?page=' + p + '&num=80&sort=symbol&asc=1&node=' + node;
    const j = await get(u);
    if (!Array.isArray(j) || !j.length) break;
    all = all.concat(j);
    p++;
  }
  return all;
}

// 分层规则：ETF code -> 指数分层键
const ETF_TIER = {
  '510050': 'sz50', '510300': 'hs300', '510310': 'hs300', '510330': 'hs300',
  '510500': 'zz500', '512100': 'zz1000', '588000': 'kcb50', '588080': 'kcb50'
};
const TIER_RANGE = { sz50: [0, 50], hs300: [0, 300], zz500: [300, 800], zz1000: [800, 1800], kcb50: null };

(async function () {
  const nt = JSON.parse(fs.readFileSync(path.join(ROOT, 'nt-trend.json'), 'utf8'));
  // board-members.json 只在 console 目录有一份（云端无此文件，走本地拷贝）
  const bmPath = fs.existsSync(path.join(ROOT, 'board-members.json'))
    ? path.join(ROOT, 'board-members.json')
    : path.join(ROOT, '..', 'stock-alert-console', 'board-members.json');
  const bm = JSON.parse(fs.readFileSync(bmPath, 'utf8'));
  const t = nt.dates.length - 1, asOf = nt.dates[t];

  // 1. 全A股 + 流通市值（万元 -> 亿元：/1e4）
  const raw = (await pullAll('sh_a')).concat(await pullAll('sz_a'));
  if (raw.length < 4000) throw new Error('全A股拉取不足: ' + raw.length);
  const stocks = raw.filter(function (x) { return x.code && +x.nmc > 0; })
    .map(function (x) { return { code: x.code, name: x.name, cap: +x.nmc / 1e4 }; }); // 亿元

  // 2. 分层
  const byCap = stocks.slice().sort(function (a, b) { return b.cap - a.cap; });
  const tiers = {};
  Object.keys(TIER_RANGE).forEach(function (k) {
    const r = TIER_RANGE[k];
    tiers[k] = r ? byCap.slice(r[0], r[1]) : byCap.filter(function (x) { return /^688/.test(x.code); }).slice(0, 50);
  });
  // 校验每层
  Object.keys(tiers).forEach(function (k) {
    if (tiers[k].length < 20) throw new Error('分层 ' + k + ' 成分不足: ' + tiers[k].length);
  });

  // 3. 板块归属系数
  const n2b = {};
  Object.keys(bm.members).forEach(function (b) {
    bm.members[b].forEach(function (c) { (n2b[c] = n2b[c] || []).push(b); });
  });
  const bname = {};
  bm.boards.forEach(function (b) { bname[b.c] = b.n; });

  // 4. 穿透：每只ETF规模 × 成分股权重 → 板块
  const boardAmt = {}, boardCnt = {}, stockAmt = {};
  const etfDetail = [];
  nt.etfs.forEach(function (e) {
    const sh = nt.shares[e.code] && nt.shares[e.code][t];
    const px = nt.price[e.code] && nt.price[e.code][t];
    if (!sh || !px) return;
    const scale = sh * px; // 亿元
    const tierKey = ETF_TIER[e.code];
    const pool = tiers[tierKey];
    if (!pool) return;
    const totalCap = pool.reduce(function (s, x) { return s + x.cap; }, 0);
    etfDetail.push({ code: e.code, name: e.name, grp: e.grp, tier: tierKey, scale: +scale.toFixed(1), n: pool.length });
    pool.forEach(function (x) {
      const w = x.cap / totalCap;
      const amt = scale * w;
      stockAmt[x.code] = (stockAmt[x.code] || 0) + amt;
      const bs = n2b[x.code] || [];
      const coef = bs.length ? 1 / bs.length : 0;
      bs.forEach(function (b) {
        boardAmt[b] = (boardAmt[b] || 0) + amt * coef;
        boardCnt[b] = (boardCnt[b] || 0) + 1;
      });
    });
  });

  const total = Object.values(boardAmt).reduce(function (s, x) { return s + x; }, 0);
  const boards = Object.keys(boardAmt).map(function (b) {
    return {
      code: b, name: bname[b] || b,
      pct: +(boardAmt[b] / total * 100).toFixed(2),
      amt: +boardAmt[b].toFixed(1),
      n: boardCnt[b]
    };
  }).sort(function (a, b) { return b.pct - a.pct; });

  const topStocks = Object.keys(stockAmt).map(function (c) {
    const s = stocks.find(function (x) { return x.code === c; });
    return { code: c, name: s ? s.name : c, amt: +stockAmt[c].toFixed(1) };
  }).sort(function (a, b) { return b.amt - a.amt; }).slice(0, 20);

  const out = {
    meta: {
      generatedAt: new Date().toISOString(), asOf: asOf,
      method: '8只宽基ETF规模 × 市值分层近似的指数成分权重 → 东财127行业板块归属',
      caveat: '★估算值，非官方披露。国家队只买宽基ETF，不披露板块持仓；指数成分股权重无公开接口，用全A股流通市值分层近似（前50/前300/301-800/801-1800/科创前50）。一只股票属2个板块时各记0.5。仅反映大盘宽基ETF的隐含行业偏向，不代表汇金真实持仓。',
      source: 'ETF规模=nt-trend.json(交易所官方份额×收盘价)；成分股与流通市值=新浪Market_Center(sh_a+sz_a)；板块归属=board-members.json(东财行业)',
      stockCount: stocks.length, boardCount: boards.length, totalScale: +total.toFixed(1),
      boardMembersGen: bm.gen || bm.date || null,
      tiers: Object.keys(tiers).map(function (k) { return { k: k, n: tiers[k].length }; })
    },
    boards: boards,
    topStocks: topStocks,
    etfs: etfDetail
  };
  const txt = JSON.stringify(out);
  fs.writeFileSync(OUT_CONSOLE, txt);
  fs.writeFileSync(OUT_CLOUD, txt);
  console.log('生成 nt-sector.json', txt.length + 'B', 'asOf', asOf, '板块', boards.length);
  console.log('Top10 板块:', boards.slice(0, 10).map(function (b) { return b.name + ' ' + b.pct + '%'; }).join(' | '));
  console.log('Top5 个股:', topStocks.slice(0, 5).map(function (s) { return s.name + ' ' + s.amt + '亿'; }).join(' | '));
})().catch(function (e) { console.error('FAIL', e.message); process.exit(1); });

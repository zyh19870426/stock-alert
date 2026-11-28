/* 国家队数据构建（Node 离线管线）v3 —— 云端日更版
 *
 * ★唯一真源：本文件（stock-alert-cloud/build-nt-data.js）。
 *   本地 `node _tests/build-nt-data.js` 只是薄包装，会转调本文件并指定输出到
 *   stock-alert-console/；改算法只改这里，禁止另存副本。
 *
 * 输出（默认写脚本所在目录 = 真源仓根，由 GitHub Actions 提交回仓）：
 *   nt-data.json   —— 季度长历史 + 直接持股行业聚合 + KPI
 *   nt-daily.json  —— ETF 份额日频快照存档（每日追加，页面日频曲线数据源）
 *   可用环境变量 DATA_OUT_DIR 覆盖输出目录（本地跑时指向 stock-alert-console）。
 *
 * 数据源（均已实测校准）：
 *  A. ETF 季度  : fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd&code={code}
 *                ⇒ 期末总份额(亿份)/期间申购/期间赎回/期末净资产(亿元)，2012 起（定期报告口径）
 *  B. ETF 当日  : push2test stock/get f116(规模=份额×价) + f43(价)
 *                ⇒ 与深交所 SZSE dqgm 官方值已交叉校准一致（159919: 107.6 亿份 ✓）
 *  C. 季报持股  : datacenter-web RPT_F10_EH_FREEHOLDERS（HOLDER_NAME ∈ 国家队名单，END_DATE 倒序翻页）
 *                ⇒ HOLDER_MARKET_CAP(万元)/HOLD_NUM_CHANGE（十大流通股东披露口径，仅前十、滞后）
 *  D. 股→行业   : push2test clist m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23 fields=f100（5563 只一次拿全）
 *  E. 两市市值  : qt.gtimg.cn GBK sh000001+sz399001 字段45=总市值(亿元)
 *
 * 口径诚实声明（写入 JSON，页面原样展示）：
 *   · ETF 份额变动 = 全投资者净申购，宽基篮子以汇金增持为主，非精确国家队账户
 *   · 季报 = 十大流通股东披露口径，仅前十、披露滞后
 * 用法: node _tests/build-nt-data.js
 */
const https = require('https');
const fs = require('fs');
const path = require('path');
const { TextDecoder } = require('util');

const DIR = process.env.DATA_OUT_DIR || __dirname;
const OUT = path.join(DIR, 'nt-data.json');
const DAILY = path.join(DIR, 'nt-daily.json');

/* 汇金重仓宽基篮子（2015 救市 + 2024-2025 公告增持主力品种） */
const NT_ETFS = [
  { code: '510300', name: '华泰柏瑞沪深300ETF', mkt: 1 },
  { code: '510310', name: '易方达沪深300ETF', mkt: 1 },
  { code: '510330', name: '华夏沪深300ETF', mkt: 1 },
  { code: '159919', name: '嘉实沪深300ETF', mkt: 0 },
  { code: '510050', name: '华夏上证50ETF', mkt: 1 },
  { code: '510500', name: '南方中证500ETF', mkt: 1 },
  { code: '159922', name: '嘉实中证500ETF', mkt: 0 },
  { code: '588000', name: '华夏科创50ETF', mkt: 1 },
  { code: '588080', name: '易方达科创50ETF', mkt: 1 },
  { code: '512100', name: '南方中证1000ETF', mkt: 1 }
];

/* 国家队名单（上市公司十大流通股东常见披露名） */
const NT_NAMES = [
  '中央汇金投资有限责任公司',
  '中央汇金资产管理有限责任公司',
  '中国证券金融股份有限公司',
  '梧桐树投资平台有限责任公司',
  '中国国新控股有限责任公司',
  '中国诚通控股集团有限公司',
  '国新投资有限公司',
  '北京凤山投资有限公司'
];

/* 东财二级行业(f100) → 口语化大类（正则优先，未命中保留原名单独成行） */
const SECTOR_RULES = [
  [/银行/, '银行'], [/证券|保险/, '大金融'], [/多元金融|信托/, '大金融'],
  [/酿酒|白酒/, '白酒'], [/食品|乳品|调味/, '食品饮料'],
  [/半导体|集成电路|芯片|电子化学品/, '半导体'],
  [/消费电子|电子元件|光学光电子|LED|面板/, '消费电子'],
  [/软件开发|信息技术/, '软件'], [/互联网服务|云计算|大数据|IT服务|计算机设备/, '人工智能'],
  [/通信设备|通信服务|光模块|光通信/, '通信(CPO/光模块)'],
  [/光伏/, '光伏'], [/电池|锂电|能源金属/, '锂电池'],
  [/电力|电网|发电/, '电力'], [/燃气|公用事业/, '电力'],
  [/航天航空|航空装备|航天装备|军工|船舶/, '军工'],
  [/轨交设备|专业工程|基础建设|工程建设|装修建材|水泥/, '建筑基建'],
  [/农化制品/, '化工'], [/专业服务/, '其他'],
  [/工业金属|贵金属|黄金|小金属|金属新材料|铜|铝|锌|铅|镍/, '有色'],
  [/化学制药|生物制品|医疗器械|医疗服务|医药商业|中药/, '医药'],
  [/汽车/, '汽车'], [/房地产开发|房地产服务/, '地产'],
  [/煤炭/, '煤炭'], [/钢铁|普钢|特钢/, '钢铁'],
  [/化学|塑料|橡胶|化纤/, '化工'],
  [/通用设备|专用设备|工程机械|仪器仪表|电机|自动化设备/, '机械设备'],
  [/机器人/, '机器人'], [/游戏|文化传媒|影视|广告|出版|教育/, '传媒游戏'],
  [/家电/, '家电'], [/航运港口|航空机场|铁路公路|物流/, '交通运输'],
  ['农牧饲渔', '农业'], ['种植业', '农业'], ['渔业', '农业'],
  ['纺织服装', '纺服'], ['纺织制造', '纺服'], ['服装家纺', '纺服'],
  ['美容护理', '美容护理'], ['化妆品', '美容护理'], ['环保', '环保'],
  ['贸易', '商贸零售'], ['零售', '商贸零售'], ['互联网电商', '商贸零售'],
  ['旅游酒店', '旅游酒店'], ['酒店餐饮', '旅游酒店'],
  ['造纸印刷', '造纸'], ['包装材料', '包装'],
  ['石油行业', '石油石化'], ['油服工程', '石油石化'], ['炼化及贸易', '石油石化'],
  ['油气开采', '石油石化'], ['油品贸易', '石油石化'], ['电源设备', '电力']
];

function get(url, timeout, ref) {
  return new Promise((r, rej) => {
    const q = https.get(url, { timeout: timeout || 15000, headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Referer': ref || 'https://fundf10.eastmoney.com/' } }, res => {
      const c = []; res.on('data', d => c.push(d));
      res.on('end', () => {
        const buf = Buffer.concat(c);
        r({ s: res.statusCode, body: ref && ref.indexOf('gtimg') >= 0 ? new TextDecoder('gbk').decode(buf) : buf.toString('utf8') });
      });
    });
    q.on('error', rej); q.on('timeout', () => { q.destroy(); rej(new Error('timeout ' + url)); });
  });
}
function jget(url, timeout, ref) { return get(url, timeout, ref).then(r => { if (r.s !== 200) throw new Error('HTTP ' + r.s); return JSON.parse(r.body); }); }
async function mapLimit(arr, limit, fn) {
  const ret = new Array(arr.length); let i = 0;
  async function worker() { while (i < arr.length) { const k = i++; try { ret[k] = await fn(arr[k], k); } catch (e) { ret[k] = { __err: e.message }; } } }
  await Promise.all(Array.from({ length: Math.min(limit, arr.length) }, worker));
  return ret;
}
const num = s => { const v = parseFloat(String(s).replace(/,/g, '')); return isFinite(v) ? v : 0; };
const yi = w => w / 1e8; /* HOLDER_MARKET_CAP 单位=元（CAP/HOLD=每股价格，已实证） */
const dstr = ts => { const d = new Date(ts); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };

(async () => {
  const t0 = Date.now();
  const log = m => console.log('[' + ((Date.now() - t0) / 1000).toFixed(1) + 's] ' + m);
  /* 一律按北京时间取"今天"：GitHub 运行器是 UTC，直接 new Date() 会在 UTC 00:00-08:00
     区间拿到前一天，导致日频档案追加错误日期 */
  const today = new Date(Date.now() + 8 * 3600e3);
  const todayStr = today.getUTCFullYear() + '-' + String(today.getUTCMonth() + 1).padStart(2, '0') + '-' + String(today.getUTCDate()).padStart(2, '0');

  /* ★交易日闸门（复用 trade-day.js 单一真源）—— 2026-11-28 修
     旧逻辑：g.isTrading===false 直接 return。事故：cron('10 14 * * 1-5'=北京22:10)
     在 GitHub 高负载时延迟到次日凌晨跑，脚本拿到的是「次日自然日」(周六/周日)，
     闸门判非交易日直接跳过，连续 11-25/26/27 三天 workflow 全绿但档案没写盘。
     新逻辑：以「行情数据日 qDate」为准。非交易日且行情日仍落后档案末日 ⇒ 跳过；
     非交易日但行情日已前进(如周六补周五数据) ⇒ 继续，并用行情日作采样日期。
     两个信号都取不到时 fail-open 继续，避免漏掉真实交易日。 */
  var dataDay = todayStr;
  if (!process.env.SKIP_TRADE_GATE) {
    const TD = require('./trade-day.js');
    const g = await TD.gate();
    if (g.qDate && /^\d{8}$/.test(g.qDate)) {
      dataDay = g.qDate.slice(0, 4) + '-' + g.qDate.slice(4, 6) + '-' + g.qDate.slice(6, 8);
    }
    if (g.isTrading === false && dataDay === todayStr) {
      log('非交易日且行情日未前进（' + TD.describe(g) + '），跳过本次构建'); return;
    }
    if (g.isTrading === false) log('非交易日但行情日已到 ' + dataDay + '（cron 延迟补采），继续');
    if (g.isTrading === null) log('⚠ 交易日无法判定（' + TD.describe(g) + '），按 fail-open 继续');
    else if (g.isTrading) log('交易日确认 ' + g.t + '，行情日 ' + dataDay);
  }

  /* ============ A. ETF 季度规模（长历史 + 绝对持仓） ============ */
  log('A. ETF 季度规模 (gmbd)');
  const qRows = await mapLimit(NT_ETFS, 3, async e => {
    let rows = [];
    /* 偶发返回空（限流）→ 重试 3 次，避免某只缺期导致聚合失真 */
    for (let attempt = 0; attempt < 3 && !rows.length; attempt++) {
      try {
        const r = await get('https://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=gmbd&code=' + e.code);
        const trs = String(r.body).match(/<tr>[\s\S]*?<\/tr>/g) || [];
        const rr = [];
        trs.forEach(tr => {
          const tds = (tr.match(/<td[^>]*>([\s\S]*?)<\/td>/g) || []).map(t => t.replace(/<[^>]+>/g, '').trim());
          if (tds.length >= 5 && /^\d{4}-\d{2}-\d{2}/.test(tds[0])) rr.push({ endDate: tds[0].slice(0, 10), sub: num(tds[1]), redeem: num(tds[2]), shares: num(tds[3]), nav: num(tds[4]) });
        });
        rows = rr;
      } catch (err) { }
      if (!rows.length) await new Promise(r => setTimeout(r, 700));
    }
    if (!rows.length) log('   ⚠ ' + e.code + ' 季度数据重试 3 次仍为空');
    return { code: e.code, name: e.name, rows: rows };
  });
  const qMap = {};
  qRows.forEach(e => e.rows.forEach(r => {
    if (!qMap[r.endDate]) qMap[r.endDate] = { shares: 0, nav: 0, sub: 0, redeem: 0, n: 0 };
    qMap[r.endDate].shares += r.shares; qMap[r.endDate].nav += r.nav; qMap[r.endDate].sub += r.sub; qMap[r.endDate].redeem += r.redeem;
    qMap[r.endDate].n++;
  }));
  const quarterly = Object.keys(qMap).sort().map(q => {
    const o = qMap[q];
    const avgNav = o.shares > 0 ? o.nav / o.shares : 0;          /* 当期篮子均价 元/份 */
    const netShares = +(o.sub - o.redeem).toFixed(1);            /* 当期净申购份额（亿份） */
    return {
      q: q.slice(0, 4) + 'Q' + (Math.floor(+q.slice(5, 7) / 3.2) + 1), endDate: q,
      shares: +o.shares.toFixed(1), val: +o.nav.toFixed(1),
      sub: +o.sub.toFixed(1), redeem: +o.redeem.toFixed(1),
      netShares: netShares,
      netVal: +(netShares * avgNav).toFixed(0),                  /* 当期净申购金额（亿元），流量指标 */
      n: o.n                                                      /* 当期覆盖 ETF 只数（可比性依据） */
    };
  });
  if (!quarterly.length) throw new Error('gmbd 季度数据为空');
  const qLast = quarterly[quarterly.length - 1];
  const fullN = NT_ETFS.length;
  const fullStart = (quarterly.find(q => q.n >= fullN) || {}).endDate || '-';
  log('   季度轴 ' + quarterly[0].endDate + ' → ' + qLast.endDate + '（' + quarterly.length + ' 期），最新净资产 ' + qLast.val + ' 亿 / 份额 ' + qLast.shares + ' 亿份');
  log('   篮子完整（' + fullN + ' 只全覆盖）起始于 ' + fullStart + '，此前各期覆盖只数递减，绝对值不可跨期直比');

  /* ============ B. 当日快照（f116 规模 + f43 价 → 份额） ============
     ★2026-11-28 修：push2test 的 f43 偶发返回 0（测试域名冻结/限流），
     份额=规模/价 会整体变 0 ⇒ 触发安全闸拒写 ⇒ 档案连续停更。
     加 qt.gtimg.cn 报价兜底：f43<=0 时用 qt 实时价（10 只一次拿全，实测 11-27 全可用）。 */
  log('B. ETF 当日快照 (push2test f116 + qt 价格兜底)');
  const pxFallback = {};
  try {
    const q = NT_ETFS.map(e => (e.mkt ? 'sh' : 'sz') + e.code).join(',');
    const qr = await get('https://qt.gtimg.cn/q=' + q, 10000, 'https://qt.gtimg.cn/');
    const re = /v_(s[hz]\d+)="([^"]*)"/g; let m;
    while ((m = re.exec(qr.body))) {
      const p = +m[2].split('~')[3];
      if (p > 0) pxFallback[m[1].slice(2)] = p;
    }
    log('   qt 价格兜底 ' + Object.keys(pxFallback).length + '/' + NT_ETFS.length + ' 只');
  } catch (e) { log('   ⚠ qt 价格兜底不可用：' + e.message); }
  const snap = await mapLimit(NT_ETFS, 5, async e => {
    const j = await jget('https://push2test.eastmoney.com/api/qt/stock/get?secid=' + (e.mkt ? '1.' : '0.') + e.code + '&fields=f43,f116', 10000, 'https://quote.eastmoney.com/');
    let price = (j.data.f43 || 0) / 1000; /* ETF 价 3 位小数 */
    if (!(price > 0) && pxFallback[e.code] > 0) price = pxFallback[e.code];
    const val = j.data.f116 || 0;
    return { code: e.code, name: e.name, price: price, val: val, shares: price > 0 ? val / price / 1e8 : 0 };
  });
  const snapOk = snap.filter(s => s.shares > 0);
  /* ★安全闸（2026-11-17）：云端取数一旦整体失败（风控必须是显式报错，不能静默放行），
     下面所有聚合都会变成 0，写回仓库就把页面变成全 0。这里直接 fail-fast，
     workflow 因非 0 退出而不提交，仓库保留上一版好数据。 */
  const MIN_SNAP = Math.ceil(NT_ETFS.length * 0.6);
  if (snapOk.length < MIN_SNAP) {
    const bad = snap.filter(s => s.__err).map(s => s.__err).slice(0, 3).join(' | ');
    throw new Error('ETF 当日快照仅 ' + snapOk.length + '/' + NT_ETFS.length + ' 只成功（低于阈值 ' + MIN_SNAP + '），拒绝写出全 0 数据。样本错误: ' + bad);
  }
  const snapTotalShares = snapOk.reduce((s, x) => s + x.shares, 0);
  const snapTotalVal = snapOk.reduce((s, x) => s + x.val, 0) / 1e8;
  log('   ' + snapOk.length + '/' + NT_ETFS.length + ' 只成功，篮子份额 ' + snapTotalShares.toFixed(1) + ' 亿份 / 规模 ' + snapTotalVal.toFixed(0) + ' 亿');

  /* 当日快照追加进 nt-daily.json（日频档案，页面曲线数据源）
     注意：这里只在内存里追加，真正落盘放到脚本末尾与 nt-data.json 一起写，
     避免"日频档案写了、KPI 没写"的半成品状态被 workflow 提交。 */
  let daily = { dates: [], shares: {}, val: {} };
  try { daily = JSON.parse(fs.readFileSync(DAILY, 'utf8')); } catch (e) { }
  let dailyDirty = false;
  if (daily.dates[daily.dates.length - 1] !== dataDay) {
    daily.dates.push(dataDay);
    NT_ETFS.forEach(e => {
      const s = snapOk.find(x => x.code === e.code);
      (daily.shares[e.code] = daily.shares[e.code] || []).push(s ? +s.shares.toFixed(2) : null);
      (daily.val[e.code] = daily.val[e.code] || []).push(s ? +(s.val / 1e8).toFixed(1) : null);
    });
    dailyDirty = true;
    log('   日频档案待追加 ' + dataDay + '（累计 ' + daily.dates.length + ' 天）');
  } else log('   日频档案已有 ' + dataDay + '，跳过');

  /* 本季至今净申购（当日份额 vs 上季末定期报告份额） */
  const qNetShares = +(snapTotalShares - qLast.shares).toFixed(1);
  const navAvg = snapOk.length ? (snapOk.reduce((s, x) => s + x.val, 0) / 1e8) / snapOk.reduce((s, x) => s + x.shares, 0) : 4;
  const qNetVal = +(qNetShares * navAvg).toFixed(0);
  log('   本季至今净申购 ' + qNetShares + ' 亿份 ≈ ' + qNetVal + ' 亿');

  /* ============ B2. 月度切分（季末锚 = 月末锚，逐月用快照切） ============
   * 口径要点：季报期末日恰为月末（3-31/6-30/9-30/12-31），故「上月末份额」在季末月可直接取定期报告值，
   * 非季末月取当月自采末快照。⇒ 月度净申购 = 当月末快照总份额 − 上月末锚份额，全程真实，无插值。
   * 首月（自采当月）锚 = 最近一份季报期末份额，因此第一个月点当天即可精确算出。 */
  const codesAll = NT_ETFS.map(e => e.code);
  const dayTotShares = daily.dates.map((dd, i) => {
    let s = 0, n = 0;
    codesAll.forEach(c => { const v = (daily.shares[c] || [])[i]; if (v != null) { s += v; n++; } });
    return { s: s, n: n };
  });
  const dayTotVal = daily.dates.map((dd, i) => {
    let s = 0; codesAll.forEach(c => { const v = (daily.val[c] || [])[i]; if (v != null) s += v; }); return s;
  });
  const curMonth = dataDay.slice(0, 7);
  const monthLast = {};                                   /* m → 该月最后一个快照索引 */
  daily.dates.forEach((dd, i) => { monthLast[dd.slice(0, 7)] = i; });
  const monthly = [];
  let mPrevShares = qLast.shares, mPrevEnd = qLast.endDate, mPrevSrc = '季报';
  Object.keys(monthLast).sort().forEach(m => {
    const i = monthLast[m];
    const cur = dayTotShares[i].s;
    const px = dayTotShares[i].s > 0 ? dayTotVal[i] / dayTotShares[i].s : 0;   /* 期末篮子均价 元/份 */
    const ds = +(cur - mPrevShares).toFixed(1);
    monthly.push({
      m: m, netShares: ds, netVal: +(ds * px).toFixed(0), px: +px.toFixed(3),
      n: dayTotShares[i].n,
      from: mPrevEnd, to: daily.dates[i],
      days: daily.dates.filter(x => x.slice(0, 7) === m).length,
      partial: m === curMonth,
      anchor: mPrevSrc
    });
    mPrevShares = cur; mPrevEnd = daily.dates[i]; mPrevSrc = '自采';
  });
  if (monthly.length) log('   月度切分 ' + monthly.length + ' 个月：' + monthly.map(x => x.m + '(' + (x.netVal > 0 ? '+' : '') + x.netVal + '亿)').join(' '));

  /* ============ C. 国家队季报持股（REPORT_DATE_NAME 过滤，每账户取各自最新披露期） ============ */
  log('C. 国家队季报十大流通股东');
  /* 报告期动态推算：不再写死年份（写死会在跨年后静默取不到新披露，页面停在上一年）。
     披露节奏：一季报 4 月底、中报 8 月底、三季报 10 月底、年报次年 4 月底。
     故 1-4 月锚定为上年三季报（年报尚未披露完，避免取空）。 */
  const REPORT_PERIODS = (function (n) {
    const names = ['一季报', '中报', '三季报', '年报'];
    let y = dataDay.slice(0, 4) | 0, m = dataDay.slice(5, 7) | 0, qs;
    if (m >= 11) qs = 2;
    else if (m >= 9) qs = 1;
    else if (m >= 5) qs = 0;
    else { y = y - 1; qs = 2; }
    const out = [];
    for (let i = 0; i < n; i++) {
      out.push(y + names[qs]);
      qs--; if (qs < 0) { qs = 3; y--; }
    }
    return out;
  })(8);
  log('   拟查报告期：' + REPORT_PERIODS.join(' > '));
  const holdings = [];
  const asOf = {}; /* 账户 → 报告期 */
  const fetchHold = (nm, rdn) => jget('https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_EH_FREEHOLDERS&columns=SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,END_DATE,HOLD_NUM,HOLDER_MARKET_CAP,HOLD_NUM_CHANGE&pageSize=500&pageNumber=1&filter=' + encodeURIComponent('(HOLDER_NAME="' + nm + '")(REPORT_DATE_NAME="' + rdn + '")'), 15000, 'https://data.eastmoney.com/');
  for (const nm of NT_NAMES) {
    let rows = [], used = '', idx = -1;
    for (let k = 0; k < REPORT_PERIODS.length; k++) {
      const j = await fetchHold(nm, REPORT_PERIODS[k]);
      if (j.result && j.result.data && j.result.data.length) { rows = j.result.data; used = REPORT_PERIODS[k]; idx = k; break; }
    }
    if (!rows.length) { log('   ' + nm.slice(0, 12) + ' → 无记录'); continue; }
    asOf[nm.slice(0, 12)] = used;
    /* 同账户上一期持仓 → 用于判定 增持/减持/新进（不用 HOLDER_NUM_CHANGE，该字段常为空） */
    const prev = {};
    if (idx + 1 < REPORT_PERIODS.length) {
      try {
        const jp = await fetchHold(nm, REPORT_PERIODS[idx + 1]);
        if (jp.result && jp.result.data) jp.result.data.forEach(d => { prev[d.SECURITY_CODE] = +d.HOLD_NUM || 0; });
      } catch (e) { }
    }
    rows.forEach(d => {
      const hn = +d.HOLD_NUM || 0, pv = prev[d.SECURITY_CODE];
      const chg = pv === undefined ? '新进' : (hn > pv ? '增持' : (hn < pv ? '减持' : '不变'));
      holdings.push({ code: d.SECURITY_CODE, name: d.SECURITY_NAME_ABBR, who: nm.slice(0, 12), cap: yi(+d.HOLDER_MARKET_CAP || 0), chg: chg, dNum: +((hn - (pv || 0)) / 1e4).toFixed(1) });
    });
    log('   ' + nm.slice(0, 12) + ' ' + used + ' → ' + rows.length + ' 只（上期可比 ' + Object.keys(prev).length + ' 只）');
  }
  const byCode = {};
  holdings.forEach(h => {
    if (!byCode[h.code]) byCode[h.code] = { code: h.code, name: h.name, cap: 0, chg: '', who: [], up: 0, dn: 0, nw: 0, dNum: 0 };
    byCode[h.code].cap += h.cap;
    byCode[h.code].dNum += h.dNum;
    if (h.chg === '增持') byCode[h.code].up++;
    else if (h.chg === '减持') byCode[h.code].dn++;
    else if (h.chg === '新进') byCode[h.code].nw++;
    if (byCode[h.code].who.indexOf(h.who) < 0) byCode[h.code].who.push(h.who);
  });
  Object.keys(byCode).forEach(k => {
    const h = byCode[k];
    h.chg = (h.up && h.dn) ? '分歧' : (h.up ? '增持' : (h.dn ? '减持' : (h.nw ? '新进' : '不变')));
    h.dNum = +h.dNum.toFixed(1);
  });
  const holdList = Object.values(byCode).sort((a, b) => b.cap - a.cap);
  /* 安全闸：季报持股全空 ⇒ 多半是接口不可达或期次推算错，写出空表会让"直接持股"归零 */
  if (!holdList.length) throw new Error('季报持股为空（报告期：' + REPORT_PERIODS.join('/') + '），拒绝写出');
  const directTotal = holdList.reduce((s, h) => s + h.cap, 0);
  log('   国家队直接持股合并 ' + holdList.length + ' 只，市值 ' + directTotal.toFixed(0) + ' 亿（各账户最新披露期：' + Object.values(asOf).join('/') + '）');

  /* ============ D. 股→行业映射 ============ */
  log('D. 股→行业映射 (f100)');
  const code2ind = {};
  let total = 1, fetched = 0;
  for (let p = 1; fetched < total; p++) {
    const j = await jget('https://push2test.eastmoney.com/api/qt/clist/get?pn=' + p + '&pz=200&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f100', 15000, 'https://quote.eastmoney.com/');
    if (!j.data || !j.data.diff) break;
    total = +j.data.total || 1;
    Object.keys(j.data.diff).forEach(k => { const d = j.data.diff[k]; if (d.f12 && d.f100) code2ind[d.f12] = d.f100; });
    fetched += Object.keys(j.data.diff).length;
  }
  log('   映射 ' + Object.keys(code2ind).length + ' 只');
  /* 映射失败不会让总额出错，但会把几乎所有票归到"其他"，行业分布失去意义 ⇒ 显式告警 */
  if (Object.keys(code2ind).length < 1000) log('   ⚠ 行业映射仅 ' + Object.keys(code2ind).length + ' 只（正常应 >5000），sectors 将以"其他"为主');

  /* ============ 行业聚合 → 大类 ============ */
  function toSector(ind) {
    if (!ind) return '其他';
    for (const [re, g] of SECTOR_RULES) { if (re instanceof RegExp ? re.test(ind) : ind.indexOf(re) >= 0) return g; }
    return ind;
  }
  const secAgg = {};
  holdList.forEach(h => {
    const g = toSector(code2ind[h.code] || '');
    if (!secAgg[g]) secAgg[g] = { name: g, cap: 0, n: 0, up: 0, dn: 0, new: 0 };
    secAgg[g].cap += h.cap; secAgg[g].n++;
    if (h.chg.indexOf('增') >= 0) secAgg[g].up++;
    else if (h.chg.indexOf('减') >= 0) secAgg[g].dn++;
    else if (h.chg.indexOf('新') >= 0) secAgg[g].new++;
  });
  const sectors = Object.values(secAgg).map(s => ({
    name: s.name, cap: +s.cap.toFixed(1), pct: +(s.cap / (directTotal || 1) * 100).toFixed(2), n: s.n, up: s.up, dn: s.dn, new: s.new
  })).sort((a, b) => b.cap - a.cap);
  log('   大类 ' + sectors.length + ' 个，Top5: ' + sectors.slice(0, 5).map(s => s.name + ' ' + s.pct + '%').join(' | '));

  /* ============ E. 两市总市值 ============ */
  log('E. 两市总市值');
  const r = await get('https://qt.gtimg.cn/q=sh000001,sz399001', 10000, 'https://qt.gtimg.cn/');
  const a = r.body.split('v_sz399001="')[0].split('~');
  const b = r.body.split('v_sz399001="')[1].split('~');
  const mktCap = +((num(a[45]) || 0) + (num(b[45]) || 0)).toFixed(1);
  log('   沪深总市值 ' + mktCap + ' 亿');

  /* ============ 汇总 ============ */
  const dir = qNetVal > 100 ? '进场' : (qNetVal < -100 ? '离场' : '拉锯');
  const out = {
    gen: new Date().toISOString().slice(0, 16) + 'Z',
    caliber: {
      etf: '宽基ETF篮子（汇金重仓10只）份额变动×净值=净申购金额，全投资者口径（宽基ETF以汇金增持为主，非精确国家队账户）',
      quarterly: '季度规模源自基金定期报告（东财F10 gmbd）。netVal=当期(期间申购−期间赎回)×当期篮子均价=净申购金额（亿元，流量指标，不受篮子成员增减影响）；净申购份额为负=净赎回。n=当期覆盖ETF只数，n<10 的早期期次存量份额/净资产不可与后期直比',
      direct: '季报十大流通股东披露口径（仅前十、披露滞后），含汇金/证金/梧桐树/国新/诚通系；各账户取各自最新披露期：' + JSON.stringify(asOf),
      mktCap: '沪深总市值=沪(字段45)+深(字段45)，qt.gtimg.cn 实时口径',
      daily: '日频档案 nt-daily.json 自 2026-10-29 起逐日累积（交易所日频份额历史已不可批量获取：上交所 403/深交所限流）',
      monthly: '月度净申购 = 当月末快照篮子总份额 − 上月末锚份额，× 期末均价。季末月的「上月末锚」直接取定期报告值（季末日即月末），非季末月取当月自采末快照 ⇒ 无插值。partial=当月未走完（读数随日更新）'
    },
    meta: { fullN: fullN, fullStart: fullStart },
    kpi: {
      basketVal: +snapTotalVal.toFixed(0), basketShares: +snapTotalShares.toFixed(1),
      qLastVal: qLast.val, qLastEnd: qLast.endDate,
      qNetShares: qNetShares, qNetVal: qNetVal, dir: dir,
      monthNetShares: monthly.length ? monthly[monthly.length - 1].netShares : qNetShares,
      monthNetVal: monthly.length ? monthly[monthly.length - 1].netVal : qNetVal,
      monthLabel: monthly.length ? monthly[monthly.length - 1].m : '-',
      directVal: +directTotal.toFixed(1), mktCap: mktCap,
      basketPct: +(snapTotalVal / mktCap * 100).toFixed(2),
      directPct: +(directTotal / mktCap * 100).toFixed(2)
    },
    live: {
      asOf: dataDay, basketShares: +snapTotalShares.toFixed(1),
      anchorEnd: qLast.endDate, anchorShares: qLast.shares,
      netShares: qNetShares, netVal: qNetVal
    },
    monthly: monthly,
    quarterly: quarterly,
    sectors: sectors,
    holdings: holdList.map(h => ({
      code: h.code, name: h.name, cap: +h.cap.toFixed(1),
      pct: +(h.cap / (directTotal || 1) * 100).toFixed(2),
      chg: h.chg, dNum: h.dNum, who: h.who
    })),
    basket: NT_ETFS.map(e => e.name + '(' + e.code + ')'),
    names: NT_NAMES,
    dailyFile: 'nt-daily.json'
  };
  /* ★两文件一起落盘：先主数据，再日频档案（顺序故意如此——若第二步失败，
     页面 KPI 已是新的、曲线少一个点，比"曲线有今天但 KPI 还是昨天"更好恢复）。
     全部计算都在上面完成，任一环节 throw 都不会留下半成品。 */
  fs.writeFileSync(OUT, JSON.stringify(out));
  log('✅ ' + OUT + ' (' + (fs.statSync(OUT).size / 1024).toFixed(1) + ' KB)');
  if (dailyDirty) {
    fs.writeFileSync(DAILY, JSON.stringify(daily));
    log('✅ ' + DAILY + ' 追加 ' + dataDay + '（累计 ' + daily.dates.length + ' 天）');
  }
  log('   方向: ' + dir + '（本季至今 ' + (qNetVal > 0 ? '+' : '') + qNetVal + ' 亿）；宽基篮子 ' + snapTotalVal.toFixed(0) + ' 亿占总市值 ' + out.kpi.basketPct + '%；直接持股 ' + directTotal.toFixed(0) + ' 亿占 ' + out.kpi.directPct + '%');
})().catch(e => { console.error('❌', e); process.exit(1); });

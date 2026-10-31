#!/usr/bin/env node
/* 散户结构（拉萨系/家人们）—— 板块参与度 + 个股拥堵，单一实现
 *
 * 两个已经过检验的用途（2026-10-31，全样本 2021-01~2026-10）：
 *
 * ①【个股级 · 强】share = 拉萨买入额 / 个股当日成交额
 *    持仓 20 日绝对收益五档单调：Bottom20% −2.66%(t−9.4) → Top5% −11.31%(t−21.8)；
 *    6 个分年全部同向；随机标签安慰剂实测落 0.0% 分位；阈值 p50→p99 单调（−7.4%→−13.7%）；
 *    Top5% 次日跳空 −3.91% ⇒ **T 日盘后出信号，T+1 开盘集合竞价是唯一可执行卖点**。
 *
 * ②【板块级 · 只作背景】part = 拉萨买入额 / 该大类「拉萨进买入榜」个股的成交额合计
 *    ⚠️ 它**不是择时信号**（实测分位→后续板块收益不单调、t 值低），只用来看「散户在往哪挤」：
 *    参与度 + 历史分位高 = 该大类散户接盘密集（例：2026-10-30 农业 5.29%/87% 分位）；
 *    分位低 = 散户冷清（例：制造 1.07%/6% 分位）。方向性判断仍由 rotation 八态承担。
 *
 * 口径关键（换一个数就全变，勿改）：
 *   - 只统计「拉萨出现在**买入榜**」的个股；把只上卖出榜的股票计入分母会把参与度压低 2~4 倍。
 *   - 各大类当前值取「该大类最近一个有数据的交易日」（小类当天可能无上榜股），并标 stale。
 *   - 分位 = 过去 250 个有效值中 ≤ 当前值的比例（不含当日；样本 <40 不出分位）。
 *   - 分母是「上榜股成交额」而非板块总成交额 ⇒ 数值天然高于小单口径（科技约 2.6% 而非 0.1%），
 *     千分位比较只在同一口径内成立。
 */
'use strict'
const fs = require('fs')
const path = require('path')

const LASA_KEY = ['拉萨', '山南香曲东路', '昌都两江大道']
const isLasa = n => LASA_KEY.some(k => String(n || '').includes(k))
const THR_HOT = 0.055
const THR_WARN = 0.0437
const MIN_AMT = 5e7
const UA = { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/' }
const REPORTS = ['RPT_BILLBOARD_DAILYDETAILSBUY', 'RPT_BILLBOARD_DAILYDETAILSSELL']

function url (rep, page, size, date) {
  const f = date ? `&filter=${encodeURIComponent(`(TRADE_DATE='${date}')`)}` : ''
  return 'https://datacenter-web.eastmoney.com/api/data/v1/get'
    + '?reportName=' + rep + '&columns=ALL&pageNumber=' + page + '&pageSize=' + size
    + '&sortColumns=ACCUM_AMOUNT&sortTypes=-1&source=WEB&client=WEB' + f
}
async function get (u) {
  let last
  for (let t = 0; t < 3; t++) {
    try {
      const r = await fetch(u, { headers: UA, signal: AbortSignal.timeout(20000) })
      if (!r.ok) throw new Error('http ' + r.status)
      return await r.json()
    } catch (e) { last = e; await new Promise(s => setTimeout(s, 800 * (t + 1))) }
  }
  throw last
}
async function fetchReport (rep, date) {
  const first = await get(url(rep, 1, 500, date))
  const res = first && first.result
  if (!res || !Array.isArray(res.data)) return { rows: [], date: null }
  let rows = res.data.slice()
  const pages = Math.min(res.pages || 1, 6)
  for (let p = 2; p <= pages; p++) {
    const j = await get(url(rep, p, 500, date))
    if (j && j.result && Array.isArray(j.result.data)) rows = rows.concat(j.result.data)
  }
  const d = rows.length ? String(rows[0].TRADE_DATE || '').slice(0, 10) : null
  return { rows, date: d }
}
async function fetchNames (date) {
  const f = date ? `&filter=${encodeURIComponent(`(TRADE_DATE='${date}')`)}` : ''
  const u = 'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_DAILYBILLBOARD_DETAILSNEW'
    + '&columns=SECURITY_CODE,SECURITY_NAME_ABBR&pageNumber=1&pageSize=500&source=WEB&client=WEB' + f
  try {
    const j = await get(u)
    const m = Object.create(null)
    for (const r of (j.result && j.result.data) || []) m[r.SECURITY_CODE] = r.SECURITY_NAME_ABBR
    return m
  } catch (e) { return Object.create(null) }
}

/* 抓当日龙虎榜 → 个股聚合 + 按大类聚合（大类需外部 gmap） */
async function fetchToday (date) {
  const NAMES = await fetchNames(date)
  let all = []
  let dataDate = date
  for (const rep of REPORTS) {
    const r = await fetchReport(rep, date)
    if (r.date && !dataDate) dataDate = r.date
    all = all.concat(r.rows.map(x => Object.assign({ _side: rep.endsWith('BUY') ? 'B' : 'S' }, x)))
  }
  if (!all.length) throw new Error('龙虎榜无数据（非交易日或接口变更）')

  const M = new Map()
  for (const r of all) {
    const code = r.SECURITY_CODE
    if (!M.has(code)) M.set(code, { code, name: r.SECURITY_NAME_ABBR || NAMES[code] || '', amt: 0, amtB: 0, lsB: 0, lsS: 0, chg: null, close: null, lsSeat: new Set() })
    const e = M.get(code)
    /* ★ 必须取**最小**成交额：同一股票同一天可能有多条上榜原因，其中「连续三个交易日」
       「严重异常期间」这类给出的是**多日累计**成交额（2026-10-30 大业股份：单日 21.4 亿 /
       三日累计 123.7 亿）。取 max 会把分母放大 3~6 倍、参与度系统性压低（实测制造 0.46% vs 1.07%）。 */
    { const a = +r.ACCUM_AMOUNT || 0; if (a > 0) e.amt = e.amt > 0 ? Math.min(e.amt, a) : a }
    /* ★ amtB = 只取**买入榜**记录里的成交额。板块参与度的分母必须用它 ——
       与 lasa-board-base.json 的生成口径一致；若混入卖出榜记录（同一股票可能对应
       「连续三日累计」这类多日口径，ACCUM_AMOUNT 会大 2~3 倍），参与度会被系统性压低
       （2026-10-30 实测：制造 0.46% vs 正确 1.07%）。个股 share 仍用 amt（max）。 */
    if (r._side === 'B') { const a = +r.ACCUM_AMOUNT || 0; if (a > 0) e.amtB = e.amtB > 0 ? Math.min(e.amtB, a) : a }
    if (r.CHANGE_RATE != null) e.chg = +r.CHANGE_RATE
    if (r.CLOSE_PRICE != null) e.close = +r.CLOSE_PRICE
    if (isLasa(r.OPERATEDEPT_NAME)) {
      if (r._side === 'B') { e.lsB += +r.BUY || 0; e.lsSeat.add(r.OPERATEDEPT_NAME) }
      else { e.lsS += +r.SELL || 0; e.lsSeat.add(r.OPERATEDEPT_NAME) }
    }
  }
  const stocks = [...M.values()].map(e => ({
    code: e.code, name: e.name, amt: e.amt, amtB: e.amtB || e.amt,
    share: e.amt > 0 ? e.lsB / e.amt : 0,
    net: e.amt > 0 ? (e.lsB - e.lsS) / e.amt : 0,
    chg: e.chg, close: e.close, seat: e.lsSeat.size
  }))
  return { date: String(dataDate || '').replace(/-/g, ''), stocks }
}

/* 按大类聚合当日参与度（只取买入榜口径） */
function aggByGroup (stocks, gmap, groups) {
  const acc = {}
  for (const s of stocks) {
    const gi = gmap[s.code]
    if (gi == null || !groups[gi]) continue
    const g = groups[gi]
    if (s.share <= 0) continue          // 未进买入榜
    if (!acc[g]) acc[g] = { buy: 0, amt: 0, n: 0 }
    acc[g].buy += s.share * s.amtB
    acc[g].amt += s.amtB
    acc[g].n++
  }
  const o = {}
  for (const g in acc) if (acc[g].amt > 0) o[g] = { part: +(acc[g].buy / acc[g].amt).toFixed(5), n: acc[g].n }
  return o
}

/* 把今日值并入历史序列并算分位 */
function mergeSeries (base, todayAgg, todayLedger) {
  const WIN = 250
  /* 兼容两种历史形态：base 文件（groups 是名字数组）与上一份 lasa（groups 是对象数组）。
     传错格式时降级为空历史，绝不抛错把整页拖垮。 */
  const src = Array.isArray(base && base.groups) ? base.groups : []
  const names = src.filter(x => typeof x === 'string')
  const gs = names.length ? names.slice() : src.map(x => (x && x.g) || '').filter(Boolean)
  const out = { v: 1, date: todayLedger, groups: gs, series: {}, cur: {} }
  const gmap = (base && base.gmap) || {}
  for (const g of out.groups) {
    let s = (base.series && base.series[g]) ? base.series[g].slice() : []
    const t = todayAgg[g]
    if (t) {
      const i = s.findIndex(x => x[0] === todayLedger)
      if (i >= 0) s[i] = [todayLedger, t.part, t.n, null]
      else s.push([todayLedger, t.part, t.n, null])
    }
    if (s.length > 260) s = s.slice(-260)
    // 重算最后一个点的分位（其余保持 base 里已算好的）
    const last = s.length - 1
    if (last >= 0) {
      const w = []
      for (let k = Math.max(0, last - WIN); k < last; k++) if (s[k][1] != null) w.push(s[k][1])
      if (w.length >= 40) {
        let c = 0; for (const x of w) if (x <= s[last][1]) c++
        s[last] = [s[last][0], s[last][1], s[last][2], +(c / w.length).toFixed(3)]
      } else s[last] = [s[last][0], s[last][1], s[last][2], null]
    }
    out.series[g] = s
    const L = s[s.length - 1]
    if (!L) { out.cur[g] = null; continue }
    out.cur[g] = { d: L[0], part: L[1], pct: L[3], nStk: L[2], stale: L[0] === todayLedger ? 0 : 1 }
  }
  out.gmap = gmap
  return out
}

/* 对外：给定历史（base 或上一份 rotation.lasa）+ 当日数据，产出 rotation.json 的 lasa 段 */
function buildLasa (hist, today, ledger) {
  const merged = mergeSeries(hist, aggByGroup(today.stocks, hist.gmap || {}, hist.groups), ledger)
  const list = today.stocks.filter(s => s.amt >= MIN_AMT && !/退$/.test(String(s.name || '')))
  const hot = list.filter(s => s.share >= THR_HOT).sort((a, b) => b.share - a.share)
    .map(s => ({ code: s.code, name: s.name, share: +s.share.toFixed(4), net: +s.net.toFixed(4), chg: s.chg }))
  const warn = list.filter(s => s.share >= THR_WARN && s.share < THR_HOT).sort((a, b) => b.share - a.share)
    .map(s => ({ code: s.code, name: s.name, share: +s.share.toFixed(4), net: +s.net.toFixed(4), chg: s.chg }))
  return {
    v: 1,
    date: ledger,
    thr: { hot: THR_HOT, warn: THR_WARN, minAmt: MIN_AMT },
    groups: merged.groups.map(g => (merged.cur[g] ? Object.assign({ g }, merged.cur[g]) : { g, d: null, part: null, pct: null, nStk: null, stale: 1 })),
    stat: { listed: list.length, hot: hot.length, warn: warn.length, lasa: list.filter(s => s.share > 0).length },
    stocks: { hot, warn },
    /* 序列随文件下发，前端画趋势/前端重算都不用再请求；9 组 × 260 点 ≈ 100KB */
    series: merged.series,
    basis: {
      win: '2021-01~2026-10',
      stock: 'Top5% 持有20日 −11.31%(t−21.8) / Bottom20% −2.66% / 6年同向 / 安慰剂0.0%分位 / 次日跳空−3.91%',
      board: '仅作背景（分位→后续收益不单调，非择时）',
      hint: 'T日盘后出信号，T+1开盘集合竞价为唯一可执行卖点'
    }
  }
}

function loadBase (file) {
  const p = file || path.join(__dirname, 'lasa-board-base.json')
  return JSON.parse(fs.readFileSync(p, 'utf8'))
}

/* ---------- 滚动存档（★ 免去「每季度本地重跑」） ----------
 * lasa-board-base.json 是**一次性种子**：9 大类 × 260 日历史 + 5580 只个股→大类映射（gmap，占 128KB 的绝大部分）。
 * 上线后每天把当日值 append 进 lasa-series.json（只含 groups/series，约 30KB），Actions 提交回仓库；
 * 次次run 优先读它 ⇒ 250 日分位窗口始终贴着今天走，不需要任何人工重跑。
 * 若 rolling 文件缺失/损坏/被删 ⇒ 自动退回种子（fail-open，绝不因为存档问题算不出结果）。
 * 只有一种情况仍需人工介入：**新增/重命名大类**（gmap 与种子在 base 里），此时重跑 _tmp_build_lasa_board.js。
 */
const SERIES_FILE = path.join(__dirname, 'lasa-series.json')

function loadHist (baseFile) {
  const base = loadBase(baseFile)
  try {
    const s = JSON.parse(fs.readFileSync(SERIES_FILE, 'utf8'))
    const okG = Array.isArray(s && s.groups) && s.groups.length === (base.groups || []).length
    const okS = s && s.series && Object.keys(s.series).length > 0
    if (okG && okS) {
      return { groups: s.groups, series: s.series, gmap: base.gmap || {}, date: s.date, src: 'rolling' }
    }
  } catch (e) { /* 不存在或损坏 → 落回种子 */ }
  return Object.assign({ src: 'seed' }, base)
}

function saveHist (lasa) {
  try {
    const o = { v: 1, date: lasa.date, groups: (lasa.groups || []).map(g => g.g), series: lasa.series || {} }
    fs.writeFileSync(SERIES_FILE, JSON.stringify(o))
    return true
  } catch (e) { return false }
}

module.exports = { fetchToday, buildLasa, aggByGroup, mergeSeries, loadBase, loadHist, saveHist, SERIES_FILE, THR_HOT, THR_WARN, MIN_AMT, isLasa }

/* CLI：node lasa-board.js [--date YYYY-MM-DD] [--dry] */
if (require.main === module) {
  (async () => {
    const argv = process.argv.slice(2)
    const i = argv.indexOf('--date'); const DATE = i >= 0 ? argv[i + 1] : ''
    const DRY = argv.includes('--dry')
    const hist = loadHist()
    console.log('历史来源：' + (hist.src === 'rolling' ? '滚动存档 ' + hist.date : '种子 ' + hist.date))
    const today = await fetchToday(DATE)
    const out = buildLasa(hist, today, today.date)
    console.log(`数据日 ${out.date} · 到榜 ${out.stat.listed} 只 · 拉萨参与 ${out.stat.lasa} · 触警 ${out.stat.hot} · 警示 ${out.stat.warn}`)
    out.groups.forEach(g => console.log(`  ${g.g.padEnd(5)} ${g.part == null ? 'NA' : (g.part * 100).toFixed(2) + '%'} 分位 ${g.pct == null ? 'NA' : (g.pct * 100).toFixed(0) + '%'}  ${g.nStk == null ? '' : g.nStk + '只'} ${g.d}${g.stale ? '(滞后)' : ''}`))
    out.stocks.hot.forEach(s => console.log(`  ★ ${s.code} ${s.name} 占比 ${(s.share * 100).toFixed(1)}% 当日 ${(s.chg || 0).toFixed(1)}%`))
    if (DRY) { console.log('[dry] 不写盘'); return }
    fs.writeFileSync(path.join(__dirname, 'lasa.json'), JSON.stringify(out))
    saveHist(out)
    console.log('已写 lasa.json + lasa-series.json ' + (JSON.stringify(out).length / 1024).toFixed(0) + 'KB')
  })().catch(e => { console.error('失败：' + e.message); process.exit(1) })
}

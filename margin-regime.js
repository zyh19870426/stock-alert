#!/usr/bin/env node
/* margin-regime.js — 两融 · 大盘阶段雷达（每日收盘后，云端生成）
 * ---------------------------------------------------------------------------
 * 解决什么：融资余额页要回答「当前两融处于什么阶段、历史上同态之后怎么走、可信度多少」。
 * 数据源（全部为公开、可直连、无需 token）：
 *   · 两融全历史：datacenter-web RPTA_RZRQ_LSHJ（2010-03 起；本脚本只用 2013-01 起）
 *       字段：DIM_DATE / RZYE 融资余额 / RZMRE 融资买入额 / LTSZ 流通市值 / NEW 沪深300收盘 / ZDF
 *   · 两市成交额：push2his K线 上证(1.000001)+深证(0.399001) 的 f57 成交额
 * 三项状态指标（均为「状态量」而非日信号）：
 *   L = 融资余额 / 流通市值        （杠杆水位）
 *   T = 融资买入额 / 融资余额      （两融成交额 / 两融余额 = 周转率）
 *   P = 融资买入额 / 两市成交额    （两融成交额 / 大盘成交额 = 参与度）
 *   B60 = 融资余额 60 交易日变化率 （余额趋势）
 *
 * ★ 口径（预注册、冻结，不做参数寻优）：
 *   · 分位 = 250 交易日**滚动**分位（因果）。不用全历史分位作主口径：
 *     融资余额/流通市值的绝对水平跨年代不可比（2010-2019 的"低水位"阈值在 2020 后不再出现）。
 *   · 样本起点 2013-01-01（两融标的扩容后结构稳定；2010-2012 试点期余额不足千亿，与今日不可比）。
 *   · 入场点 = 信号日**次一交易日收盘**（两融为当日盘后披露，当日收盘价买入不现实 ⇒ 严格因果）。
 *   · 统计单元 = **事件段**（连段命中日聚合，间隔 >10 交易日为新段），每段 1 个观测
 *     —— 否则相邻日的重叠前瞻收益会造成伪重复、把显著性抬高数倍。
 *   · 显著性 = 置换检验 2000 次（打乱组标签，自动吸收重叠）；Bonferroni 由脚本内实际检验数决定。
 *   · 可信度 = 段数 + 置换 p + 跨期（2013-2019 / 2020-至今）中位数同号，三者共同决定。
 *
 * 产出 margin-regime.json（供 console.html「两融·杠杆水位」页渲染）。
 * 失败策略：取数失败 → 退出码 1（由工作流决定是否阻断）；不写半成品。
 * 用法：node margin-regime.js [--dry]
 */
'use strict'
const fs = require('fs')
const path = require('path')

const OUT_PATH = path.join(__dirname, 'margin-regime.json')
const DRY = process.argv.includes('--dry')

/* ===== 冻结口径（改动必须同步更新 console.html 的说明与 _tests/margin-regime-test.js）===== */
const SPEC = {
  sampStart: '2013-01-01',
  splitAt: '2020-01-01',
  winq: 250,          // 滚动分位窗口（交易日）
  barWindow: 60,      // 顶/底标注：±N 交易日极值
  barAmp: 0.15,       // 顶/底标注：与前一个反向极值的最小幅度
  gap: 10,            // 事件段聚合：命中日间隔 >N 交易日 视为新段
  horizons: [20, 60, 120, 250],
  entry: 'T+1 收盘',
  permR: 2000,
  minSeg: { mid: 25, high: 30 },
  pMid: 0.05, pWeak: 0.10,
  /* 多重比较：分两组阈值，先注册后执行 ——
   *   主要假设 = 4 对「情绪/杠杆两端对比」 × 2 个期限（20/60 日）= 8 次检验 → 0.05/8
   *   其余（探索性）= 12 层 × 4 期限 = 48 次检验 → 0.05/48
   * 阈值写死在这里，不允许按结果回改。 */
  bonfPrimary: 0.05 / 8,
  bonfExplore: 0.05 / 48,
  /* ===== 决策打分（2026-10-18 新增，★预注册，不允许按结果回改）=====
   * 结构：总分 = 位置分(0/1/2) + 出清分(0/1)，范围 0~3。
   * 依据：沪深300 日频 2010-03~2026-10（4016 日），事件段(间隔>20 交易日)为统计单元。
   *   0 分 −2.39%(n14,t−1.83,胜29%) · 1 分 −0.22%(n16) · 2 分 +2.67%(n20,t+3.30,胜70%) · 3 分 +4.15%(n11,t+2.27,胜82%)
   *   2 分档置换检验 p=0.0040(2000 次)；位置阈值 −15%~−25% 全程为正且 t≥3.0；出清区间 5 个变体全在 +2.1~+2.7%。
   * ★ 为什么位置分要 0/1/2 而不是布尔：距 250 日高越深期望越高（−15%→+2.16% / −20%→+2.67% / −25%→+3.98%），单调。
   * ★ 为什么出清要卡「2%~4%」区间而不是「降得越多越好」：>4% 为踩踏，20 日 −0.13%（已验证）。
   * ★ 缩量（成交额/20 日均额<0.7）未纳入：22 个行业指数口径 t=7.9 很强，但沪深300 单指数口径无法复现，
   *   且两市成交额与沪深300 成交量量比相关性仅 −0.07（口径不同）⇒ 如实降级为「参考项」，不计分。 */
  score: {
    deep: -0.20,      // 位置 2 分：距 250 日最高收盘 ≤ −20%
    mid: -0.08,       // 位置 1 分：−20% < 距高 ≤ −8%；0 分：> −8%
    dd60: -0.08,      // 出清前置：距 60 日高 ≤ −8%（低位）
    db5Hi: -0.02,     // 5 日融资余额变化上界（降 2%）
    db5Lo: -0.04,     // 下界（降 4%，超过算踩踏不计分）
    gap: 20,          // 事件段聚合间隔（交易日）
    hist: [20, 60]    // 输出这两个期限的历史条件分布
  }
}
/* 预注册的「主要假设」：情绪/杠杆两端对比（冰点 vs 沸腾） */
const PRIMARY_PAIRS = [
  { id: 'P', a: 'P_ice', b: 'P_boil', label: '两融参与度：冰点 vs 沸腾', horizons: [20, 60] },
  { id: 'T', a: 'T_ice', b: 'T_boil', label: '两融周转率：冰点 vs 沸腾', horizons: [20, 60] },
  { id: 'B60', a: 'delev_fast', b: 'lev_fast', label: '余额趋势：急速去杠杆 vs 快速加杠杆', horizons: [20, 60] },
  { id: 'L', a: 'L_low', b: 'L_high', label: '杠杆水位：低位 vs 高位', horizons: [20, 60] }
]
/* 参与度/周转率/水位 的分位阈值（冰点/沸腾），余额趋势的分档（%）——全部预注册，不做搜索 */
const LAYER_DEFS = [
  { id: 'P_ice', label: '参与度冰点（P 250日分位 ≤5%）', test: r => r.P_q != null && r.P_q <= 0.05 },
  { id: 'P_boil', label: '参与度沸腾（P 250日分位 ≥95%）', test: r => r.P_q != null && r.P_q >= 0.95 },
  { id: 'T_ice', label: '周转率冰点（T 250日分位 ≤5%）', test: r => r.T_q != null && r.T_q <= 0.05 },
  { id: 'T_boil', label: '周转率沸腾（T 250日分位 ≥95%）', test: r => r.T_q != null && r.T_q >= 0.95 },
  { id: 'both_ice', label: '双冰点（P≤5% 且 T≤5%）', test: r => r.P_q != null && r.T_q != null && r.P_q <= 0.05 && r.T_q <= 0.05 },
  { id: 'L_high', label: '杠杆水位高位（L 250日分位 ≥85%）', test: r => r.L_q != null && r.L_q >= 0.85 },
  { id: 'L_low', label: '杠杆水位低位（L 250日分位 ≤15%）', test: r => r.L_q != null && r.L_q <= 0.15 },
  { id: 'delev_fast', label: '急速去杠杆（余额60日 ≤-8%）', test: r => r.B60 != null && r.B60 <= -8 },
  { id: 'delev_mild', label: '温和去杠杆（余额60日 -8~-3%）', test: r => r.B60 != null && r.B60 >= -8 && r.B60 < -3 },
  { id: 'lev_fast', label: '快速加杠杆（余额60日 ≥8%）', test: r => r.B60 != null && r.B60 >= 8 },
  { id: 'sign_top', label: '顶部特征（P≥85% 且 T≥67% 且 余额60日>0）', test: r => r.P_q != null && r.T_q != null && r.B60 != null && r.P_q >= 0.85 && r.T_q >= 0.67 && r.B60 > 0 },
  { id: 'sign_bot', label: '底部特征（P≤5% 且 T≤10% 且 余额60日<0）', test: r => r.P_q != null && r.T_q != null && r.B60 != null && r.P_q <= 0.05 && r.T_q <= 0.10 && r.B60 < 0 }
]
const UA = { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/' }

/* ============================ 工具 ============================ */
const num = v => { if (v == null || v === '') return null; const n = Number(v); return isFinite(n) ? n : null }
const mean = a => a.length ? a.reduce((p, c) => p + c, 0) / a.length : null
function median(a) {
  if (!a || !a.length) return null
  const s = a.slice().sort((x, y) => x - y), n = s.length
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }
async function getText(u, h, tries) {
  tries = tries || 5
  for (let i = 0; i < tries; i++) {
    try {
      const r = await fetch(u, { headers: h || UA })
      const t = await r.text()
      if (r.status !== 200) throw new Error('HTTP ' + r.status)
      return t
    } catch (e) { if (i === tries - 1) throw e; await sleep(800) }
  }
}
function jsonp(text) {
  return JSON.parse(String(text).replace(/^[^(]*\(/, '').replace(/\)\s*;?\s*$/, ''))
}

/* ============================ 取数 ============================ */
async function fetchMarginAll() {
  const all = []
  let page = 1, total = null
  for (;;) {
    const u = 'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPTA_RZRQ_LSHJ' +
      '&columns=DIM_DATE,NEW,ZDF,LTSZ,RZYE,RZMRE,RQYE&source=WEB&client=WEB' +
      '&sortColumns=DIM_DATE&sortTypes=-1&pageSize=500&pageNumber=' + page + '&_=' + Date.now()
    const j = jsonp(await getText(u))
    const d = (j.result && j.result.data) || []
    total = j.result ? j.result.count : 0
    all.push.apply(all, d)
    console.log('  两融第 ' + page + ' 页 +' + d.length + ' → ' + all.length + '/' + total)
    if (!d.length || all.length >= total || page > 20) break
    page++
  }
  return all
}
async function fetchIndexAmount() {
  const amt = {}
  for (const sec of ['1.000001', '0.399001']) {
    const u = 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=' + sec +
      '&fields1=f1&fields2=f51,f57&klt=101&fqt=0&lmt=6000&end=20500101'
    const j = JSON.parse(await getText(u, { 'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/' }))
    const k = (j.data && j.data.klines) || []
    console.log('  ' + sec + ' 日线 ' + k.length + ' 根')
    for (const row of k) {
      const p = String(row).split(','), d = p[0], v = Number(p[p.length - 1])
      if (d && isFinite(v)) amt[d] = (amt[d] || 0) + v
    }
  }
  return amt
}

/* ============================ 建序列 ============================ */
function buildSeries(raw, amt) {
  const rows = [], seen = {}
  for (const r of raw) {
    const d = String(r.DIM_DATE || '').slice(0, 10)
    if (!d || seen[d] || d < SPEC.sampStart) continue
    const rzye = num(r.RZYE), rzmre = num(r.RZMRE), ltsz = num(r.LTSZ), hs = num(r.NEW), A = amt[d]
    if (rzye == null || ltsz == null || hs == null || !A || rzye <= 0 || ltsz <= 0) continue
    seen[d] = 1
    rows.push({ d, hs, rzye, ltsz, L: rzye / ltsz, T: rzmre != null ? rzmre / rzye : null, P: rzmre != null ? rzmre / A : null })
  }
  rows.sort((a, b) => a.d < b.d ? -1 : 1)
  for (let i = 0; i < rows.length; i++) rows[i].B60 = i >= 60 ? (rows[i].rzye / rows[i - 60].rzye - 1) * 100 : null
  for (const key of ['L', 'T', 'P']) {
    for (let i = 0; i < rows.length; i++) {
      const v = rows[i][key]
      if (!isFinite(v)) { rows[i][key + '_q'] = null; continue }
      const a = []
      for (let j = Math.max(0, i - SPEC.winq + 1); j <= i; j++) if (isFinite(rows[j][key])) a.push(rows[j][key])
      if (a.length < Math.floor(SPEC.winq * 0.6)) { rows[i][key + '_q'] = null; continue }
      let n = 0
      for (const x of a) if (x <= v) n++
      rows[i][key + '_q'] = n / a.length
    }
  }
  return rows
}
/* 前瞻收益：信号日 i → 入场 i+1 收盘，持有 H 交易日 */
function fwd(rows, i, H) { const e = i + 1; return (e + H >= rows.length) ? null : rows[e + H].hs / rows[e].hs - 1 }

/* ============================ 事件段 ============================ */
function episodes(rows, test, gap) {
  const eps = []
  for (let i = 0; i < rows.length; i++) {
    if (!test(rows[i])) continue
    const L = eps[eps.length - 1]
    if (L && i - L.end <= gap) { L.end = i; L.idx.push(i) } else eps.push({ start: i, end: i, idx: [i] })
  }
  for (const e of eps) { e.from = rows[e.start].d; e.to = rows[e.end].d; e.days = e.end - e.start + 1 }
  return eps
}
/* 每段 1 个观测：段内所有日的 H 日前瞻收益均值 */
function segObs(rows, test, H, gap) {
  const out = []
  for (const e of episodes(rows, test, gap)) {
    const vals = []
    for (const i of e.idx) { const f = fwd(rows, i, H); if (f != null) vals.push(f) }
    if (vals.length) out.push({ from: e.from, to: e.to, days: e.days, v: mean(vals), n: vals.length, startD: rows[e.start].d })
  }
  return out
}
function permCompare(A, B, R) {
  if (!A.length || !B.length) return { diff: null, p: null }
  const obs = Math.abs(median(A) - median(B))
  const all = A.concat(B), na = A.length
  let ge = 0
  for (let it = 0; it < R; it++) {
    for (let i = all.length - 1; i > 0; i--) { const j = (Math.random() * (i + 1)) | 0; const t = all[i]; all[i] = all[j]; all[j] = t }
    if (Math.abs(median(all.slice(0, na)) - median(all.slice(na))) >= obs - 1e-12) ge++
  }
  return { diff: median(A) - median(B), p: (ge + 1) / (R + 1) }
}
/* 可信度四级：段数 + 置换 p（按所属检验族的 Bonferroni 阈值）+ 跨期段中位数同号
 *   高 = 过 Bonferroni + 段数够 + 跨期同向
 *   中 = 未过 Bonferroni 但 p<0.05 + 段数够 + 跨期同向
 *   弱 = 方向暗示（p<0.10 + 段数够，或 p<0.05 但跨期不同向）
 *   低 = 其余（含样本不足）
 * ★ 校级声明：若某期限下无任何层达「高」，页面必须如实显示「无一项通过多重比较校正」。 */
function gradeOf(nSeg, p, trMed, teMed, thr) {
  const sameSign = trMed != null && teMed != null && Math.sign(trMed) === Math.sign(teMed) && Math.sign(teMed) !== 0
  if (nSeg >= SPEC.minSeg.high && p != null && p < thr && sameSign) return '高'
  if (nSeg >= SPEC.minSeg.mid && p != null && p < SPEC.pMid && sameSign) return '中'
  if (nSeg >= SPEC.minSeg.mid && p != null && p < SPEC.pWeak) return '弱'
  if (nSeg >= SPEC.minSeg.high && p != null && p < SPEC.pMid) return '弱'
  return '低'
}

/* ============================ 顶 / 底 画像（描述性） ============================ */
function markBars(rows) {
  const raw = []
  for (let i = 0; i < rows.length; i++) {
    const lo = Math.max(0, i - SPEC.barWindow), hi = Math.min(rows.length - 1, i + SPEC.barWindow)
    let mn = true, mx = true
    for (let j = lo; j <= hi; j++) { if (rows[j].hs < rows[i].hs) mn = false; if (rows[j].hs > rows[i].hs) mx = false }
    if (mn) raw.push({ i, type: '底' }); else if (mx) raw.push({ i, type: '顶' })
  }
  const mg = []
  for (const e of raw) {
    const L = mg[mg.length - 1]
    if (L && L.type === e.type) { if ((e.type === '底' && rows[e.i].hs < rows[L.i].hs) || (e.type === '顶' && rows[e.i].hs > rows[L.i].hs)) mg[mg.length - 1] = e; continue }
    if (L && Math.abs(rows[e.i].hs / rows[L.i].hs - 1) < SPEC.barAmp) { if ((L.type === '底' && rows[e.i].hs < rows[L.i].hs) || (L.type === '顶' && rows[e.i].hs > rows[L.i].hs)) mg[mg.length - 1] = e; continue }
    mg.push(e)
  }
  return mg
}
/* 逐项比较：当前读数更接近「底画像」还是「顶画像」（不做加权、不引入参数） */
function matchProfile(cur, bottom, top) {
  const items = [
    { k: 'L分位', cur: cur.Lq, b: bottom.Lq, t: top.Lq, unit: 'q' },
    { k: 'T分位', cur: cur.Tq, b: bottom.Tq, t: top.Tq, unit: 'q' },
    { k: 'P分位', cur: cur.Pq, b: bottom.Pq, t: top.Pq, unit: 'q' },
    { k: '余额60日', cur: cur.B60, b: bottom.B60, t: top.B60, unit: 'pct' }
  ]
  let nearBottom = 0, nearTop = 0
  const detail = []
  for (const it of items) {
    if (it.cur == null || it.b == null || it.t == null) { detail.push({ k: it.k, cur: it.cur, b: it.b, t: it.t, closer: '—', ext: null }); continue }
    const db = Math.abs(it.cur - it.b), dt = Math.abs(it.cur - it.t)
    const closer = db <= dt ? '底' : '顶'
    if (closer === '底') nearBottom++; else nearTop++
    /* 是否比历史底部均值更极端（同侧且更远） */
    const ext = (closer === '底') ? ((it.cur - it.b) * (it.b - it.t) > 0 ? '更极端' : null) : null
    detail.push({ k: it.k, cur: it.cur, b: it.b, t: it.t, closer, ext, db, dt, unit: it.unit })
  }
  return { nearBottom, nearTop, total: items.length, detail, closerTo: nearBottom >= nearTop ? '底部' : '顶部' }
}

/* ============================ 决策打分（2026-10-18） ============================
 * 纯函数：入参 rows（buildSeries 产物，含 hs/rzye），返回结构化打分结果。
 * ★ 与上面 L/T/P/B60 体系相互独立：那套回答「处于什么阶段」，这套回答「三条条件命中几条、历史同分档之后怎么走」。
 * ★ 不含任何前瞻信息：dd250/dd60/db5 只用当日及以前，f20/f60 只用于回算历史分布。
 */
function computeScore(rows) {
  const S = SPEC.score, n = rows.length
  const H = {}
  H[20] = 'f20'; H[60] = 'f60'
  for (let i = 0; i < n; i++) {
    const r = rows[i]
    let h250 = -Infinity, h60 = -Infinity
    for (let j = Math.max(0, i - 249); j <= i; j++) if (rows[j].hs > h250) h250 = rows[j].hs
    for (let j = Math.max(0, i - 59); j <= i; j++) if (rows[j].hs > h60) h60 = rows[j].hs
    r.dd250 = i >= 249 ? r.hs / h250 - 1 : null
    r.h250 = i >= 249 ? h250 : null
    r.dd60 = i >= 59 ? r.hs / h60 - 1 : null
    r.db5 = (i >= 5 && rows[i - 5].rzye) ? r.rzye / rows[i - 5].rzye - 1 : null
    r.f20 = i + 20 < n ? rows[i + 20].hs / r.hs - 1 : null
    r.f60 = i + 60 < n ? rows[i + 60].hs / r.hs - 1 : null
    r.pos = r.dd250 == null ? null : (r.dd250 <= S.deep ? 2 : r.dd250 <= S.mid ? 1 : 0)
    r.clr = (r.dd60 == null || r.db5 == null) ? null
      : ((r.dd60 <= S.dd60 && r.db5 <= S.db5Hi && r.db5 >= S.db5Lo) ? 1 : 0)
    r.score = (r.pos == null || r.clr == null) ? null : r.pos + r.clr
  }
  /* 段级统计：连段命中日聚合，每段 1 个观测（否则重叠前瞻会把显著性抬高数倍） */
  function statOf(pred, hz) {
    const key = H[hz], hit = []
    for (let i = 0; i < n; i++) if (rows[i][key] != null && pred(rows[i])) hit.push(i)
    const eps = []
    for (const i of hit) {
      const L = eps[eps.length - 1]
      if (L && i - L.end <= S.gap) { L.end = i; L.idx.push(i) } else eps.push({ start: i, end: i, idx: [i] })
    }
    const v = eps.map(e => mean(e.idx.map(i => rows[i][key])))
    if (!v.length) return { n: 0, med: null, mean: null, t: null, win: null }
    const m = mean(v)
    const sd = v.length > 1 ? Math.sqrt(v.reduce((s, x) => s + (x - m) * (x - m), 0) / (v.length - 1)) : null
    return {
      n: v.length, med: median(v), mean: m,
      t: (sd != null && v.length > 2) ? m / (sd / Math.sqrt(v.length)) : null,
      win: v.filter(x => x > 0).length / v.length
    }
  }
  const tiers = {}
  for (let s = 0; s <= 3; s++) {
    tiers[s] = {}
    for (const hz of S.hist) tiers[s]['h' + hz] = statOf(r => r.score === s, hz)
  }
  /* 单条规则的独立效应（页面用来交代「哪条在起作用」） */
  const singles = {
    pos2: statOf(r => r.pos === 2, 20),
    pos1: statOf(r => r.pos === 1, 20),
    pos0: statOf(r => r.pos === 0, 20),
    clr1: statOf(r => r.clr === 1, 20),
    clr0: statOf(r => r.clr === 0, 20)
  }
  const L = rows[n - 1]
  return {
    spec: S,
    latest: {
      d: L.d, hs: L.hs, dd250: L.dd250, dd60: L.dd60, db5: L.db5,
      pos: L.pos, clr: L.clr, score: L.score, h250: L.h250,
      /* 下一档触发价位：位置 2 分线（距 250 日高 −20%） */
      tgtDeep: L.h250 == null ? null : L.h250 * (1 + S.deep),
      /* 出清失效线：单周再降超 4% 即踩踏（历史 20 日 −0.13%） */
      db5Break: S.db5Lo
    },
    tiers: tiers,
    singles: singles,
    sample: { n: n, from: rows[0].d, to: L.d },
    note: '总分 = 位置分(0/1/2) + 出清分(0/1)。统计单元为事件段（间隔 >20 交易日为新段，每段 1 个观测），' +
      '前瞻收益从信号日收盘起算；分档历史值为段级均值，t 值为段级 t。只描述历史同分档的条件分布，不代表未来必然重复。'
  }
}

/* ============================ 主流程 ============================ */
async function main() {
  console.log('抓取两融全历史 + 两市成交额 …')
  const raw = await fetchMarginAll()
  const amt = await fetchIndexAmount()
  const rows = buildSeries(raw, amt)
  if (rows.length < 1200) throw new Error('样本不足（' + rows.length + ' 日），拒绝写档')
  const last = rows[rows.length - 1]
  console.log('样本 ' + rows.length + ' 日：' + rows[0].d + ' ~ ' + last.d)

  /* ---- 阶段分层：事件段统计 ---- */
  const segCache = {}
  function segsOf(id, test, H, not) {
    const key = (not ? 'not:' : '') + id + ':' + H
    if (!segCache[key]) segCache[key] = segObs(rows, not ? (r => !test(r)) : test, H, SPEC.gap)
    return segCache[key]
  }
  const layers = []
  for (const def of LAYER_DEFS) {
    const rec = { id: def.id, label: def.label, on: !!def.test(last), h: {} }
    for (const H of SPEC.horizons) {
      const A = segsOf(def.id, def.test, H, false)
      const B = segsOf(def.id, def.test, H, true)
      const va = A.map(x => x.v), vb = B.map(x => x.v)
      const cmp = permCompare(va, vb, SPEC.permR)
      const tr = A.filter(x => x.startD < SPEC.splitAt).map(x => x.v)
      const te = A.filter(x => x.startD >= SPEC.splitAt).map(x => x.v)
      rec.h[H] = {
        n: va.length, med: median(va), win: va.length ? va.filter(x => x > 0).length / va.length : null,
        restMed: median(vb), restWin: vb.length ? vb.filter(x => x > 0).length / vb.length : null, restN: vb.length,
        diff: cmp.diff, p: cmp.p, trMed: median(tr), trN: tr.length, teMed: median(te), teN: te.length,
        grade: null, pPair: null, basis: '探索性（vs 非命中日）'
      }
    }
    layers.push(rec)
  }
  const layerById = {}
  for (const l of layers) layerById[l.id] = l

  /* ---- 预注册主要假设：情绪/杠杆「两端对比」---- */
  const pairs = []
  for (const pd of PRIMARY_PAIRS) {
    const A = layerById[pd.a], B = layerById[pd.b]
    if (!A || !B) continue
    const rec = { id: pd.id, label: pd.label, a: pd.a, b: pd.b, horizons: pd.horizons, h: {} }
    for (const H of SPEC.horizons) {
      const va = segsOf(A.id, LAYER_DEFS.find(d => d.id === A.id).test, H, false).map(x => x.v)
      const vb = segsOf(B.id, LAYER_DEFS.find(d => d.id === B.id).test, H, false).map(x => x.v)
      const cmp = permCompare(va, vb, SPEC.permR)
      const primary = pd.horizons.indexOf(H) >= 0
      rec.h[H] = {
        aN: va.length, aMed: median(va), aWin: va.length ? va.filter(x => x > 0).length / va.length : null,
        bN: vb.length, bMed: median(vb), bWin: vb.length ? vb.filter(x => x > 0).length / vb.length : null,
        diff: cmp.diff, p: cmp.p, primary: primary, thr: primary ? SPEC.bonfPrimary : SPEC.bonfExplore,
        sig: cmp.p != null && cmp.p < (primary ? SPEC.bonfPrimary : SPEC.bonfExplore)
      }
      /* 主要假设的 p 回填到所在两层的同期限（更锐利的检验优先），并按对应阈值评级 */
      if (primary) {
        for (const l of [A, B]) {
          l.h[H].pPair = cmp.p
          l.h[H].basis = '主要假设（两端对比，Bonferroni 0.05/8）'
          l.h[H].grade = gradeOf(l.h[H].n, cmp.p, l.h[H].trMed, l.h[H].teMed, SPEC.bonfPrimary)
        }
      }
    }
    pairs.push(rec)
    const r = rec.h[20]
    console.log('  [主要假设] ' + pd.label + ' → 20日 差' + fmtPct(r.diff) + ' p=' + (r.p == null ? '—' : r.p.toFixed(4)) + (r.sig ? ' ★显著' : ''))
  }
  for (const l of layers) for (const H of SPEC.horizons) if (!l.h[H].grade) l.h[H].grade = gradeOf(l.h[H].n, l.h[H].p, l.h[H].trMed, l.h[H].teMed, SPEC.bonfExplore)
  for (const l of layers) {
    const h60 = l.h[60]
    console.log('  ' + (l.on ? '★' : ' ') + ' ' + l.label + ' → 60日 中位' + fmtPct(h60.med) + ' 胜率' + fmtPct(h60.win) + ' 段数' + h60.n + ' 可信度' + h60.grade)
  }
  /* 校级披露：各期限是否存在「高」可信层 */
  const gradeSummary = {}
  for (const H of SPEC.horizons) {
    gradeSummary[H] = { high: 0, mid: 0, weak: 0, low: 0 }
    for (const l of layers) {
      const g = l.h[H].grade
      if (g === '高') gradeSummary[H].high++
      else if (g === '中') gradeSummary[H].mid++
      else if (g === '弱') gradeSummary[H].weak++
      else gradeSummary[H].low++
    }
    console.log('  维度披露 ' + H + ' 日：高 ' + gradeSummary[H].high + ' / 中 ' + gradeSummary[H].mid + ' / 弱 ' + gradeSummary[H].weak + ' / 低 ' + gradeSummary[H].low)
  }
  const anyHigh = SPEC.horizons.some(H => gradeSummary[H].high > 0)
  const primarySig = pairs.some(pd => pd.h[20] && pd.h[20].sig)
  console.log('  ★ 结论强度：' + (anyHigh ? '存在通过多重比较校正的层' : '无任何层通过多重比较校正（全部为弱证据）') + '；主要假设 20 日层面 ' + (primarySig ? '有显著项' : '无显著项'))

  /* ---- 顶 / 底 画像 ---- */
  const bars = markBars(rows)
  const pick = t => bars.filter(e => e.type === t).map(e => rows[e.i])
  const avgOf = (a, k) => { const v = a.map(x => x[k]).filter(isFinite); return v.length ? mean(v) : null }
  const profile = {
    bottom: { n: pick('底').length, Lq: avgOf(pick('底'), 'L_q'), Tq: avgOf(pick('底'), 'T_q'), Pq: avgOf(pick('底'), 'P_q'), B60: avgOf(pick('底'), 'B60') },
    top: { n: pick('顶').length, Lq: avgOf(pick('顶'), 'L_q'), Tq: avgOf(pick('顶'), 'T_q'), Pq: avgOf(pick('顶'), 'P_q'), B60: avgOf(pick('顶'), 'B60') },
    bars: bars.map(e => { const r = rows[e.i]; return { d: r.d, type: e.type, hs: Math.round(r.hs), Lq: r.L_q, Tq: r.T_q, Pq: r.P_q, B60: r.B60 } })
  }
  const cur = { d: last.d, hs: last.hs, L: last.L, T: last.T, P: last.P, Lq: last.L_q, Tq: last.T_q, Pq: last.P_q, B60: last.B60, rzye: last.rzye, amt: last.amt }
  const match = matchProfile(cur, profile.bottom, profile.top)

  /* ---- 最近序列（供页面小图；260 个交易日足够） ---- */
  const series = rows.slice(-260).map(r => [r.d, r.L == null ? null : +(r.L * 100).toFixed(3), r.T == null ? null : +(r.T * 100).toFixed(3), r.P == null ? null : +(r.P * 100).toFixed(3),
    r.L_q == null ? null : Math.round(r.L_q * 100), r.T_q == null ? null : Math.round(r.T_q * 100), r.P_q == null ? null : Math.round(r.P_q * 100), r.B60 == null ? null : +r.B60.toFixed(1)])

  /* ---- 决策打分（位置 + 出清）---- */
  const scoreCard = computeScore(rows)
  if (scoreCard.latest.score != null) {
    const t = scoreCard.tiers[scoreCard.latest.score].h20
    console.log('  打分 ' + scoreCard.latest.score + '/3（位置' + scoreCard.latest.pos + '+出清' + scoreCard.latest.clr + '）' +
      ' → 历史 20 日 ' + fmtPct(t && t.mean) + '（' + (t ? t.n : 0) + ' 段）')
  } else {
    console.log('  打分：数据不足（缺少 250 日历史或融资余额）')
  }

  const out = {
    date: last.d,
    generatedAt: new Date().toISOString(),
    spec: SPEC,
    sample: { n: rows.length, from: rows[0].d, to: last.d },
    latest: cur,
    profile: profile,
    match: match,
    layers: layers,
    pairs: pairs,
    gradeSummary: gradeSummary,
    strength: {
      anyHigh: anyHigh,
      primarySig20: primarySig,
      text: (anyHigh ? '存在通过多重比较校正的层' : '无任何层通过多重比较校正（全部为弱证据）') +
        '；预注册主要假设（情绪两端对比，20/60 日）' + (primarySig ? '有显著项' : '无显著项')
    },
    series: series,
    score: scoreCard,
    note: '两融状态为「当日盘后披露」→ 前瞻收益从次一交易日收盘起算；统计单元为事件段（非交易日），' +
      '显著性用置换检验；本页只给历史条件分布与可信度，不构成买卖建议。'
  }
  const body = JSON.stringify(out)
  const onLayers = layers.filter(l => l.on).map(l => l.id).join(',') || '无'
  const summary = last.d + '  L分位' + fmtPct(last.L_q) + ' T分位' + fmtPct(last.T_q) + ' P分位' + fmtPct(last.P_q) + ' 余额60日' + (last.B60 == null ? '—' : last.B60.toFixed(1) + '%') +
    ' | 命中层: ' + onLayers + ' | ' + layers.length + ' 层 | ' + Math.round(body.length / 1024) + 'KB' +
    ' | 打分 ' + (scoreCard.latest.score == null ? '—' : scoreCard.latest.score + '/3')
  if (DRY) { console.log('[dry] ' + summary); return out }
  fs.writeFileSync(OUT_PATH, body)
  console.log('已写 margin-regime.json：' + summary)
  return out
}
function fmtPct(v) { return v == null ? '—' : (v * 100).toFixed(1) + '%' }

if (require.main === module) {
  main().catch(e => { console.error('margin-regime 失败: ' + (e && e.message ? e.message : e)); process.exit(1) })
} else {
  module.exports = { SPEC, LAYER_DEFS, buildSeries, episodes, segObs, permCompare, gradeOf, markBars, matchProfile, median, mean, fwd, computeScore }
}

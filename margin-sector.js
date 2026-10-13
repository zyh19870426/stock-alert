#!/usr/bin/env node
/* margin-sector.js — 板块/题材消耗两融占比（每日收盘后，云端生成）
 * ---------------------------------------------------------------
 * 解决什么：首页「两融·杠杆水位」第二步 = 板块/题材消耗两融占比。
 *   板块两融没有现成报表（datacenter-web 无板块级两融、RPTA_WEB_RZRQ_GGMX 无可用代码列），
 *   只能「逐只聚合个股融资余额 → 按板块成员求和 → 占全市场比」。
 * 数据源（datacenter-web 域，与 push2 不同风控域）：
 *   · 个股融资余额：datacenter-web RPTA_WEB_RZRQ_GGMX（字段 SCODE/SECNAME/RZYE，单位 元，T+1）
 *     ⚠️ 2026-10-13：原用 push2 clist fid=f207，**该字段已失效** —— 四个域名全 HTTP 200
 *        但 f207 值全为占位符 "-"，被 Number() 判成 NaN 全丢 ⇒「未取到任何个股融资余额」。
 *        改用本报表；实测当日全市场 4455 只 / 25066 亿元。
 *   · 板块成员：datacenter-web RPT_BOARD_CONSTITUENT（复用 flow-daily.fetchBoardMembers）
 *   · 板块名：push2delay clist（复用 flow-daily.fetchBoards，type=2 行业 / 3 概念）
 *   · 数据日：RPTA_WEB_RZRQ_GGMX 自身的 DATE（T+1）；取不到时兜底 RPTA_RZRQ_LSHJ 的 DIM_DATE
 * 产出 margin-sector-history.json：{date, total, sectors:[{bk,name,kind,rzye,sharePct,members,top}], others}
 * 口径：板块两融 = 成分股**融资余额**之和（融券占比极小，先只算融资余额并注明）；T+1。
 * 失败策略：取数失败 → 沿用上一日存档（fail-open，不生成脏数据）。
 */
'use strict'
const fs = require('fs')
const path = require('path')
const { fetchBoardMembers, fetchBoards, dcGet } = require('./flow-daily')

const HIST_PATH = path.join(__dirname, 'margin-sector-history.json')
const DRY = process.argv.includes('--dry')
const NOMARGIN = process.argv.includes('--nomargin')  // 跳过 push2（仅测试聚合用）
const START = Date.now()
const BUDGET_MS = 11 * 60 * 1000
function outOfBudget() { return Date.now() - START > BUDGET_MS }
function wait(ms) { return new Promise(r => setTimeout(r, ms)) }
function bjToday() { return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10) }

/* ---- datacenter-web 抓全市场个股融资余额（RPTA_WEB_RZRQ_GGMX）----
 * ★ 2026-10-13 换源：原 push2 clist fid=f207 字段已失效（四域名全 200 但值全为占位符 "-"）。
 *   本报表按 DATE 分页，pageSize 硬上限 500（传 1000 也只回 500），实测全市场约 9 页。*/
async function fetchAllStockMargin() {
  const map = {}
  let total = 0
  /* 1. 取最新数据日（T+1） */
  const dj = await dcGet({
    reportName: 'RPTA_WEB_RZRQ_GGMX', columns: 'DATE',
    pageSize: 1, pageNumber: 1, sortColumns: 'DATE', sortTypes: '-1',
    source: 'WEB', client: 'WEB'
  }, 2, 20000)
  const d0 = dj && dj.result && dj.result.data && dj.result.data[0]
  if (!d0 || !d0.DATE) throw new Error('RPTA_WEB_RZRQ_GGMX 取最新日期失败')
  const dateStr = String(d0.DATE).slice(0, 10)

  /* 2. 按数据日分页取全市场 */
  const PS = 500
  let count = 0
  for (let pn = 1; pn <= 30; pn++) {
    if (outOfBudget()) { console.log('⚠️ 预算到点，已抓 ' + Object.keys(map).length + ' 只'); break }
    let j
    try {
      j = await dcGet({
        reportName: 'RPTA_WEB_RZRQ_GGMX', columns: 'SCODE,SECNAME,RZYE,DATE',
        filter: "(DATE='" + dateStr + "')",
        pageSize: PS, pageNumber: pn, sortColumns: 'RZYE', sortTypes: '-1',
        source: 'WEB', client: 'WEB'
      }, 2, 20000)
    } catch (e) { console.log('⚠️ RZRQ_GGMX 第 ' + pn + ' 页失败：' + e.message + '（停止分页）'); break }
    const rows = (j && j.result && j.result.data) || []
    if (!rows.length) break
    count = Number(j.result.count) || count
    for (const d of rows) {
      const code = String(d.SCODE || '').trim()
      if (!code) continue
      const rzye = Number(d.RZYE)
      if (!isFinite(rzye) || rzye <= 0) continue
      map[code] = { name: String(d.SECNAME || '').trim(), rzye: rzye }
      total += rzye
    }
    if (rows.length < PS || (count && Object.keys(map).length >= count)) break
    await wait(300)
  }
  if (!Object.keys(map).length) throw new Error('datacenter 未取到任何个股融资余额')
  console.log('个股融资余额：' + Object.keys(map).length + ' 只（数据日 ' + dateStr + '），全市场合计 ' + (total / 1e8).toFixed(0) + ' 亿元')
  return { map, total, date: dateStr }
}

/* ---- 数据日（T+1）：取市场汇总两融最新 DIM_DATE ---- */
async function getMarginDate() {
  try {
    const mj = await dcGet({
      reportName: 'RPTA_RZRQ_LSHJ',
      columns: 'DIM_DATE,RZYE', pageSize: 1, pageNumber: 1,
      sortColumns: 'DIM_DATE', sortTypes: '-1',
      source: 'WEB', client: 'WEB'
    }, 2, 20000)
    const d = mj && mj.result && mj.result.data && mj.result.data[0]
    if (d && d.DIM_DATE) return String(d.DIM_DATE).slice(0, 10)
  } catch (e) { console.log('⚠️ 取数据日失败，用本地日期：' + e.message) }
  return bjToday()
}

/* ★ 2026-10-13：东财概念池里混有「非题材」的标签板（指数成分 / 通道 / 持仓 / 风格 / 行情特征），
 * 其成分覆盖全市场或大比例 ⇒ 占比无分析意义且会霸榜（实测 TOP5 全是这类：融资融券 95.7% 等）。
 * 处置 = 用「显式前缀名单」排除：可审计、无自由参数、可回归。
 * 依据 = 2026-10-13 存档全量 160 个概念板块逐条核对（非拍脑袋）；main() 会打印本轮实际排除项。
 * 若东财新增同类标签板，在此补一行即可。 */
const TAG_BOARD_PREFIXES = [
  /* 指数 / 通道 / 标的类 */
  '融资融券', '沪股通', '深股通', 'MSCI', '富时罗素', '标准普尔', '标普',
  'HS300', '沪深300', '上证50', '上证180', '上证380', '中证500', '中证1000',
  '深成500', '深证100', '创业成份', '央视50', '茅指数', '宁组合', 'AH股',
  /* 持仓 / 榜单类 */
  'QFII重仓', '基金重仓', '机构重仓', '社保重仓', '券商重仓', '券商金股',
  '养老金', '东方财富热股', '独角兽',
  /* 市值 / 风格类 */
  '小盘股', '小盘成长', '小盘价值', '中盘股', '中盘成长', '中盘价值',
  '大盘股', '大盘成长', '大盘价值', '权重股', '高市净率',
  '科技风格', '周期股', '金融地产风格', '先进制造风格', '医药医疗风格',
  /* 财务 / 行情特征类 */
  '破净股', '长期破净', '破增发价股', '微利股', '高成长股',
  '2026中报预减', '2026一季报预增', '2026三季报预增',
  '昨日涨停', '昨日高振幅', '最近多板', '题材股', '行业龙头', '股权集中'
]
function isTagBoard(name) {
  if (!name) return false
  for (let i = 0; i < TAG_BOARD_PREFIXES.length; i++) {
    if (name.indexOf(TAG_BOARD_PREFIXES[i]) === 0) return true
  }
  return false
}

/* ★ 纯函数（无网络，可单测）：板块成员 × 个股融资余额 → 各板块占比，降序。
 * 不在此折叠「其他」：云端存全量，前端按 行业/题材 各自取 TOP + 折叠，口径更清晰。
 * 非题材标签板（isTagBoard）在此剔除 ⇒ 存档即干净，前端零改。 */
function buildSectorShares(members, stockMap, names, total) {
  const rows = []
  const dropped = []
  stockMap = stockMap || {}   /* --nomargin / 取数失败分支：无个股两融时按空集处理，不崩 */
  for (const bk of Object.keys(members)) {
    const nm = names[bk]
    const nmv = nm ? String(nm[0] || bk) : bk
    if (isTagBoard(nmv)) { dropped.push(nmv); continue }
    const cs = members[bk] || []
    let sum = 0, n = 0
    const local = []
    for (const c of cs) {
      const s = stockMap[c]
      if (!s) continue
      sum += s.rzye; n++
      local.push({ code: c, name: s.name, rzye: s.rzye })
    }
    if (n === 0) continue
    local.sort((a, b) => b.rzye - a.rzye)
    rows.push({
      bk: bk,
      name: nmv,
      kind: nm ? Number(nm[1]) || 0 : 0,
      rzye: sum,
      sharePct: total > 0 ? sum / total : 0,
      members: n,
      top: local.slice(0, 8)
    })
  }
  rows.sort((a, b) => b.rzye - a.rzye)
  return { sectors: rows, dropped: dropped }
}

async function main() {
  /* 1. 板块名（行业+概念）+ 成员（datacenter-web），任一失败降级不阻断 */
  let ind = {}, con = {}
  try { ind = await fetchBoards(2, 100) } catch (e) { console.log('⚠️ 行业板块名失败：' + e.message) }
  try { con = await fetchBoards(3, 80) } catch (e) { console.log('⚠️ 概念板块名失败：' + e.message) }
  const names = {}
  for (const bk in ind) names[bk] = [ind[bk][0], 2]
  for (const bk in con) if (!names[bk]) names[bk] = [con[bk][0], 3]
  if (!Object.keys(names).length) throw new Error('行业+概念板块名均取不到')

  let members = {}
  try { members = await fetchBoardMembers() } catch (e) { console.log('⚠️ 板块成员失败：' + e.message) }
  if (!Object.keys(members).length) throw new Error('板块成员取不到')

  /* 2. 个股融资余额（datacenter-web RPTA_WEB_RZRQ_GGMX，T+1）*/
  let stock = null, total = 0, dataDate = ''
  if (NOMARGIN) {
    console.log('[--nomargin] 跳过个股两融取数，仅测聚合')
  } else {
    try {
      const r = await fetchAllStockMargin()
      stock = r.map; total = r.total; dataDate = r.date || ''
    } catch (e) {
      console.log('⚠️ 个股融资余额失败：' + e.message)
      if (fs.existsSync(HIST_PATH)) {
        console.log('→ 沿用上一日存档（fail-open）')
        return   /* 不覆盖旧存档 */
      }
      throw e
    }
  }

  /* 3. 数据日（优先用个股两融自身的 DATE，兜底市场汇总）*/
  const date = dataDate || await getMarginDate()

  /* 4. 聚合 */
  const { sectors, dropped } = buildSectorShares(members, stock, names, total)
  if (dropped.length) console.log('已排除非题材标签板 ' + dropped.length + ' 个：' + dropped.join(' / '))
  const out = {
    date: date,
    total: total,
    sectors: sectors,
    note: '板块两融 = 成分股融资余额之和（融券占比极小，先只计融资余额）；T+1 披露；由个股两融聚合，不构成方向判断。',
    generatedAt: new Date().toISOString()
  }
  const body = JSON.stringify(out)
  const top3 = sectors.slice(0, 3).map(s => s.name).join(' / ')
  const summary = date + '  板块 ' + (sectors.length + 1) + ' 项（含其他）  全市场融资余额 ' +
    (total / 1e8).toFixed(0) + ' 亿元  TOP3: ' + top3 + '  ' + Math.round(body.length / 1024) + 'KB'
  if (DRY) { console.log('[dry] ' + summary); return }
  fs.writeFileSync(HIST_PATH, body)
  console.log('已写 margin-sector-history.json：' + summary)
}

if (require.main === module) {
  main().catch(function (e) {
    console.error('margin-sector 失败: ' + (e && e.message ? e.message : e))
    process.exit(1)
  })
} else {
  module.exports = { buildSectorShares: buildSectorShares, fetchAllStockMargin: fetchAllStockMargin, isTagBoard: isTagBoard, TAG_BOARD_PREFIXES: TAG_BOARD_PREFIXES }
}

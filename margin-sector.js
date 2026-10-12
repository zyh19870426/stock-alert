#!/usr/bin/env node
/* margin-sector.js — 板块/题材消耗两融占比（每日收盘后，云端生成）
 * ---------------------------------------------------------------
 * 解决什么：首页「两融·杠杆水位」第二步 = 板块/题材消耗两融占比。
 *   板块两融没有现成报表（datacenter-web 无板块级两融、RPTA_WEB_RZRQ_GGMX 无可用代码列），
 *   只能「逐只聚合个股融资余额 → 按板块成员求和 → 占全市场比」。
 * 数据源（全部在云端 GitHub Actions 出口可用；本机 push2 被 IP 封禁）：
 *   · 个股融资余额：push2 clist fid=f207（融资余额，单位 元）
 *   · 板块成员：datacenter-web RPT_BOARD_CONSTITUENT（复用 flow-daily.fetchBoardMembers）
 *   · 板块名：push2delay clist（复用 flow-daily.fetchBoards，type=2 行业 / 3 概念）
 *   · 数据日：datacenter-web RPTA_RZRQ_LSHJ 的 DIM_DATE（T+1）
 * 产出 margin-sector-history.json：{date, total, sectors:[{bk,name,kind,rzye,sharePct,members,top}], others}
 * 口径：板块两融 = 成分股**融资余额**之和（融券占比极小，先只算融资余额并注明）；T+1。
 * 失败策略：push2 偶发 UND_ERR_SOCKET → 沿用上一日存档（fail-open，不生成脏数据）。
 */
'use strict'
const fs = require('fs')
const path = require('path')
const https = require('https')
const { fetchBoardMembers, fetchBoards, dcGet } = require('./flow-daily')

const HIST_PATH = path.join(__dirname, 'margin-sector-history.json')
const DRY = process.argv.includes('--dry')
const NOMARGIN = process.argv.includes('--nomargin')  // 跳过 push2（仅测试聚合用）
const START = Date.now()
const BUDGET_MS = 11 * 60 * 1000
function outOfBudget() { return Date.now() - START > BUDGET_MS }
function wait(ms) { return new Promise(r => setTimeout(r, ms)) }
function bjToday() { return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10) }

/* ---- 本地 HTTP 工具（flow-daily 的 httpsGetJson 未导出，这里自带一份最小实现）---- */
function httpsGetJson(url, ms) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, {
      headers: { 'User-Agent': 'Mozilla/5.0' },
      timeout: ms || 20000
    }, res => {
      let body = ''; res.setEncoding('utf8')
      res.on('data', d => (body += d))
      res.on('end', () => {
        try { resolve(JSON.parse(body)) } catch (e) { reject(new Error('JSON 解析失败: ' + e.message)) }
      })
    })
    req.on('error', e => reject(e))
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')) })
  })
}

/* ---- push2 抓全市场个股融资余额（fid=f207）---- */
async function fetchAllStockMargin() {
  const map = {}
  let total = 0
  const pz = 5000
  for (let pn = 1; pn <= 30; pn++) {
    if (outOfBudget()) { console.log('⚠️ 预算到点，已抓 ' + Object.keys(map).length + ' 只'); break }
    const url = 'https://push2.eastmoney.com/api/qt/clist/get?pn=' + pn + '&pz=' + pz +
      '&po=1&np=1&fltt=2&invt=2&fid=f207&fs=m:0+t:3,m:1+t:2' +
      '&fields=f12,f14,f207&ut=b2884a393a59ad64002292a3e90d46a5'
    let j
    try { j = await httpsGetJson(url, 20000) }
    catch (e) { console.log('⚠️ push2 第 ' + pn + ' 页失败：' + e.message + '（停止分页）'); break }
    const diff = (j && j.data && j.data.diff) || []
    if (!diff.length) break
    for (const d of diff) {
      const code = String(d.f12 || '')
      if (!code) continue
      const rzye = Number(d.f207)
      if (!isFinite(rzye) || rzye <= 0) continue
      map[code] = { name: String(d.f14 || '').trim(), rzye: rzye }
      total += rzye
    }
    if (diff.length < pz) break
    await wait(300)
  }
  if (!Object.keys(map).length) throw new Error('push2 未取到任何个股融资余额')
  console.log('个股融资余额：' + Object.keys(map).length + ' 只，全市场合计 ' + (total / 1e8).toFixed(0) + ' 亿元')
  return { map, total }
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

/* ★ 纯函数（无网络，可单测）：板块成员 × 个股融资余额 → 各板块占比，降序。
 * 不在此折叠「其他」：云端存全量，前端按 行业/题材 各自取 TOP + 折叠，口径更清晰。 */
function buildSectorShares(members, stockMap, names, total) {
  const rows = []
  for (const bk of Object.keys(members)) {
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
    const nm = names[bk]
    rows.push({
      bk: bk,
      name: nm ? String(nm[0] || bk) : bk,
      kind: nm ? Number(nm[1]) || 0 : 0,
      rzye: sum,
      sharePct: total > 0 ? sum / total : 0,
      members: n,
      top: local.slice(0, 8)
    })
  }
  rows.sort((a, b) => b.rzye - a.rzye)
  return { sectors: rows }
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

  /* 2. 个股融资余额（push2，云端可用；本机被封）*/
  let stock = null, total = 0
  if (NOMARGIN) {
    console.log('[--nomargin] 跳过 push2，仅测聚合')
  } else {
    try {
      const r = await fetchAllStockMargin()
      stock = r.map; total = r.total
    } catch (e) {
      console.log('⚠️ push2 个股融资余额失败：' + e.message)
      if (fs.existsSync(HIST_PATH)) {
        console.log('→ 沿用上一日存档（fail-open）')
        return   /* 不覆盖旧存档 */
      }
      throw e
    }
  }

  /* 3. 数据日 */
  const date = await getMarginDate()

  /* 4. 聚合 */
  const { sectors } = buildSectorShares(members, stock, names, total)
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
  module.exports = { buildSectorShares: buildSectorShares, fetchAllStockMargin: fetchAllStockMargin }
}

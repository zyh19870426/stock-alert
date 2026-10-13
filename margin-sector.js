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
 *   · 板块名：push2delay clist（复用 flow-daily.fetchBoards(type,pz,allPages)，type=2 行业 / 3 概念）
 *     ⚠️ 2026-10-13：原只取 pn=1 两侧（pz 硬上限 100）⇒ 名字仅覆盖约 1/3，榜单混入大批无名板块
 *        （BKxxxx）。改为 allPages 翻页取满（东财实际 行业≈496 / 概念≈506）。
 *   · 数据日：RPTA_WEB_RZRQ_GGMX 自身的 DATE（T+1）；取不到时兜底 RPTA_RZRQ_LSHJ 的 DIM_DATE
 * 产出 margin-sector-history.json：{date, total, sectors:[{bk,name(口语名),full(原名),kind,rzye,sharePct,members,top}]}
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
  '深成500', '深证100', '创业成份', '创业板', '央视50', '茅指数', '宁组合', 'AH股',
  /* 持仓 / 榜单类 */
  'QFII重仓', '基金重仓', '机构重仓', '社保重仓', '券商重仓', '券商金股',
  '养老金', '东方财富热股', '独角兽', '证金持股',
  /* 市值 / 风格类 */
  '小盘股', '小盘成长', '小盘价值', '中盘股', '中盘成长', '中盘价值',
  '大盘股', '大盘成长', '大盘价值', '权重股', '高市净率',
  '科技风格', '周期股', '金融地产风格', '先进制造风格', '医药医疗风格',
  /* 财务 / 行情 / 资质特征类 */
  '破净股', '长期破净', '破增发价股', '微利股', '高成长股',
  '昨日', '最近多板', '题材股', '行业龙头', '股权集中',
  '百元股', '低价股', '高价股', '绩优股', '次新股', '专精特新'
]
/* ★ 2026-10-13 第二类：关键词**包含**匹配 —— 前缀匹配覆盖不到的写法。
 * 依据 = 全量 443 个概念板块逐条扫描（业绩/价格/标的/股权/估值/持仓/打板/指数/资质/参股 共 33 项命中）。 */
const TAG_BOARD_WORDS = [
  '破净', '破发', '预增', '预减', '预盈', '预亏', '扭亏', '首亏', '续盈',
  '股权激励', '股权分散', '股权转让', '参股', '举牌', '转债标的', '高送转', '送转',
  '市净率', '市盈率', '贬值受益', '壳资源', '退市',
  '含B股', '含H股', 'AB股', '员工持股', '龙虎榜'
]
/* ★ 含年份的业绩快报（2026中报预增 / 2025三季报扭亏 …）*/
const TAG_BOARD_RE = /^\d{4}(中报|一季报|三季报|半年报|年报)/
function isTagBoard(name) {
  if (!name) return false
  const n = String(name)
  for (let i = 0; i < TAG_BOARD_PREFIXES.length; i++) {
    if (n.indexOf(TAG_BOARD_PREFIXES[i]) === 0) return true
  }
  for (let i = 0; i < TAG_BOARD_WORDS.length; i++) {
    if (n.indexOf(TAG_BOARD_WORDS[i]) >= 0) return true
  }
  return TAG_BOARD_RE.test(n)
}

/* ★ 2026-10-13 第三刀：名称口语化 —— 东财行业/概念名偏书面（「印制电路板」「通信网络设备及器件」
 * 「光通信模块」），股民口语是「PCB」「通信设备」「光模块」。用**显式别名表**做展示名替换：
 * 可审计、无自由参数、未命中即用原名。原名存 full 字段供悬停溯源（前端 tooltip）。
 * 通用规则：末尾罗马数字（Ⅰ/Ⅱ/Ⅲ）一律剥离；题材名「XX概念」的冗余后缀剥离。
 * 同 tab 内若替换后重名 → 双方回退原名，避免两个不同板块显示成一模一样。 */
const BOARD_ALIAS = {
  /* 行业（东财各级行业名 → 股民口语） */
  '证券': '券商',
  '股份制银行': '股份行',
  '国有大型银行': '国有大行',
  '数字芯片设计': '芯片设计',
  '集成电路制造': '芯片制造',
  '集成电路封测': '芯片封测',
  '印制电路板': 'PCB',
  '通信网络设备及器件': '通信网络设备',
  '消费电子零部件及组装': '消费电子零部件',
  '垂直应用软件': '应用软件',
  '横向通用软件': '通用软件',
  '工程咨询服务': '工程咨询',
  '调味发酵品': '调味品',
  '其他电源设备': '电源设备',
  '元件': '电子元件',
  '家用电器': '家电',
  /* 题材（口语化） */
  '光通信模块': '光模块',
  '车联网(车路云)': '车联网',
  '混合现实': 'MR',
  '虚拟数字人': '数字人'
}
function normBoardName(n) { return String(n == null ? '' : n).replace(/[ⅠⅡⅢⅣⅤ]+$/, '').trim() }
function aliasOf(name) {
  const base = normBoardName(name)
  if (BOARD_ALIAS[base]) return BOARD_ALIAS[base]
  if (/概念$/.test(base) && base.length > 2) return base.slice(0, -2)   /* 「CPO概念」→「CPO」 */
  return base
}
function applyAlias(rows) {
  const cnt = {}
  for (const r of rows) {
    r.full = r.name
    r.name = aliasOf(r.name)
    const k = String(r.kind) + '|' + r.name
    cnt[k] = (cnt[k] || 0) + 1
  }
  for (const r of rows) {
    if (cnt[String(r.kind) + '|' + r.name] > 1) r.name = r.full   /* 同 tab 重名 → 回退原名，保持区分 */
  }
  return rows
}

/* ★ 2026-10-13 第二刀：东财行业板块会同时挂二级(Ⅱ)与三级(Ⅲ)两份 —— 名称只差一个罗马数字后缀，
 * 成分股与融资余额完全相同 ⇒ 榜单出现「证券Ⅱ / 证券Ⅲ」这类同值双行（实测前 50 名里占约 12 席，
 * 纯噪声，把真行业挤下去）。规则（可审计、无自由参数）：归一键 = 名称去掉末尾罗马数字；
 * 同键 + 同 kind + 同成分数 + 同融资余额（= 同一批股票）→ 只留一条，优先名称更短者
 * （如「银行」优于「银行Ⅱ」）。成分 < MIN_BOARD_MEMBERS 的零星小板块一并剔除。 */
const MIN_BOARD_MEMBERS = 5
function boardKeyOf(r) {
  return String(r.kind) + '|' + String(r.name || '').replace(/[ⅠⅡⅢⅣⅤ]+$/, '').trim()
}
function dedupeHierarchy(rows) {
  const kept = {}, out = [], dup = []
  for (const r of rows) {
    const k = boardKeyOf(r)
    const p = kept[k]
    if (p && p.members === r.members && Math.abs(Number(p.rzye) - Number(r.rzye)) < 1) {
      if (String(r.name).length < String(p.name).length) {
        dup.push(String(p.name))
        const i = out.indexOf(p)
        if (i >= 0) out[i] = r
        kept[k] = r
      } else {
        dup.push(String(r.name))
      }
      continue
    }
    kept[k] = r
    out.push(r)
  }
  return { out: out, dup: dup }
}

/* ★ 纯函数（无网络，可单测）：板块成员 × 个股融资余额 → 各板块占比，降序。
 * 不在此折叠「其他」：云端存全量，前端按 行业/题材 各取 TOP50。
 * 三道剔除都在此完成（存档即干净、前端零改）：非题材标签板 isTagBoard → 层级重复 dedupeHierarchy
 * → 零星小板块（成分 < MIN_BOARD_MEMBERS）。 */
function buildSectorShares(members, stockMap, names, total) {
  const rows = []
  const dropped = []
  let unnamed = 0
  stockMap = stockMap || {}   /* --nomargin / 取数失败分支：无个股两融时按空集处理，不崩 */
  for (const bk of Object.keys(members)) {
    const nm = names[bk]
    if (!nm) { unnamed++; continue }   /* 不在行业/概念分类里的板块（无名称、无 kind）→ 不产出，避免 BKxxxx 噪声混入 */
    const nmv = String(nm[0] || bk)
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
      kind: Number(nm[1]) || 0,
      rzye: sum,
      sharePct: total > 0 ? sum / total : 0,
      members: n,
      top: local.slice(0, 8)
    })
  }
  rows.sort((a, b) => b.rzye - a.rzye)
  const ded = dedupeHierarchy(rows)
  const kept = ded.out.filter(r => r.members >= MIN_BOARD_MEMBERS)
  const thin = ded.out.filter(r => r.members < MIN_BOARD_MEMBERS).map(r => r.name)
  const sectors = applyAlias(kept)   /* 展示名口语化（原名存 full 供悬停溯源） */
  return { sectors: sectors, dropped: dropped, dupDropped: ded.dup, thinDropped: thin, unnamed: unnamed }
}

async function main() {
  /* 1. 板块名（行业+概念）+ 成员（datacenter-web），任一失败降级不阻断 */
  let ind = {}, con = {}
  try { ind = await fetchBoards(2, 100, true) } catch (e) { console.log('⚠️ 行业板块名失败：' + e.message) }
  try { con = await fetchBoards(3, 100, true) } catch (e) { console.log('⚠️ 概念板块名失败：' + e.message) }
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
  const { sectors, dropped, dupDropped, thinDropped, unnamed } = buildSectorShares(members, stock, names, total)
  if (dropped.length) console.log('已排除非题材标签板 ' + dropped.length + ' 个：' + dropped.join(' / '))
  if (unnamed) console.log('已跳过无名板块（不在行业/概念分类）' + unnamed + ' 个')
  if (dupDropped.length) console.log('已合并层级重复板块 ' + dupDropped.length + ' 个：' + dupDropped.join(' / '))
  if (thinDropped.length) console.log('已排除零星小板块（成分<' + MIN_BOARD_MEMBERS + '）' + thinDropped.length + ' 个：' + thinDropped.join(' / '))
  const out = {
    date: date,
    total: total,
    sectors: sectors,
    note: '板块两融 = 成分股融资余额之和（融券占比极小，先只计融资余额）；T+1 披露；由个股两融聚合，不构成方向判断。',
    generatedAt: new Date().toISOString()
  }
  const body = JSON.stringify(out)
  const top3 = sectors.slice(0, 3).map(s => s.name).join(' / ')
  const summary = date + '  板块 ' + sectors.length + ' 项  全市场融资余额 ' +
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
  module.exports = {
    buildSectorShares: buildSectorShares, fetchAllStockMargin: fetchAllStockMargin,
    isTagBoard: isTagBoard, TAG_BOARD_PREFIXES: TAG_BOARD_PREFIXES, TAG_BOARD_WORDS: TAG_BOARD_WORDS,
    dedupeHierarchy: dedupeHierarchy, MIN_BOARD_MEMBERS: MIN_BOARD_MEMBERS,
    aliasOf: aliasOf, BOARD_ALIAS: BOARD_ALIAS
  }
}

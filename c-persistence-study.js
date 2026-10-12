'use strict'
/* ============================================================================
 * c-persistence-study.js —— 方案 C：大盘「持续性」指标研究（预注册，先写死口径再跑）
 * ============================================================================
 * 目的：验证「持续性」指标在近端样本上是否有区分度，**不预测涨跌**。
 * 数据边界（2026-10-10 实测）：
 *   - 涨停池 push2ex：可回溯到 ~09-14（约 10+ 交易日）
 *   - 全市场快照 push2test clist：date 参数被忽略 → 只能取当天，历史宽度不可回溯
 *   - 指数日线 腾讯 ifzq：800 根（2023-06 起）
 * 预注册口径（跑之前写死，不许事后调参）：
 *   P1 封板率 = 涨停数 /（涨停数 + 炸板数）          [0,1]，越高承接越强
 *   P2 最高连板高度 = 当日最高 lbc                    [1,∞)
 *   P3 梯队完整度 = 高度板下方连续梯队层数 / 高度      [0,1]，断层→低
 *   P4 涨停家数分位 = 当日涨停数在近 N 日中的秩         [0,1]
 *   P5 量能比 = 当日成交量 / 近 5 日均量              [0,∞)
 * 合成「持续性分」= P1/P3/P4 归一后等权均值（P2/P5 作方向参考，不进合成分）
 * 判读：**只输出状态分布与描述**，不做「预测次日涨跌」的检验（样本不足，做了也是过拟合）。
 * ============================================================================ */
const https = require('https')
const http = require('http')
const em = require('./em-resilient')

function get(url, ms){
  return new Promise(resolve => {
    let done = false
    const lib = url.indexOf('https:') === 0 ? https : http
    const req = lib.get(url, { timeout: ms || 12000, headers: { 'User-Agent': 'Mozilla/5.0' } }, res => {
      let b = ''; res.setEncoding('utf8')
      res.on('data', d => b += d)
      res.on('end', () => { if(!done){ done = true; resolve({ status: res.statusCode, body: b }) } })
    })
    req.on('error', () => { if(!done){ done = true; resolve({ status: 0, body: '' }) } })
    req.on('timeout', () => { if(!done){ done = true; req.destroy(); resolve({ status: 0, body: '' }) } })
  })
}
function parse(body){
  if(!body) return null
  let s = String(body).trim()
  const i = s.indexOf('('), j = s.lastIndexOf(')')
  if(i >= 0 && j > i && !s.slice(0, i).includes(':')) s = s.slice(i + 1, j)
  try { return JSON.parse(s) } catch(e){ return null }
}
function fmtDay(d){ return d }

/* ⚠️ 关键：push2ex 对节假日/无交易日返回 tc:0，需与「真·0涨停」区分 —— 用周边日交叉验证 */
async function ztPool(date){
  const u = 'https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989' +
    '&dpt=wz.ztzt&Pageindex=0&pagesize=200&sort=fbt%3Aasc&date=' + date
  const r = await get(u)
  const j = parse(r.body)
  if(!j || !j.data) return null
  return { tc: j.data.tc || 0, pool: j.data.pool || [] }
}
async function zbPool(date){
  const u = 'https://push2ex.eastmoney.com/getTopicZBPool?ut=7eea3edcaed734bea9cbfc24409ed989' +
    '&dpt=wz.ztzt&Pageindex=0&pagesize=200&sort=fbt%3Aasc&date=' + date
  const r = await get(u)
  const j = parse(r.body)
  if(!j || !j.data) return null
  return { tc: j.data.tc || 0, pool: j.data.pool || [] }
}
/* 指数日线（成交量原料） */
async function indexKline(sym, n){
  const k = n || 60
  const r = await get('https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=' + sym + ',day,,,' + k + ',qfq')
  const j = parse(r.body)
  const node = j && j.data && j.data[sym]
  const rows = node ? (node.day || node.qfqday) : null
  if(!Array.isArray(rows)) return []
  return rows.map(x => ({ d: String(x[0]), vol: Number(x[5]) })).filter(x => x.d && isFinite(x.vol))
}

/* ---- 指标计算（纯函数，可单测）---- */
/* 梯队完整度：给定连板数数组（如 [4,3,3,2,2,2,1...]），算最高板到 1 板之间有多少层存在 */
function ladderIntegrity(lbcs){
  if(!lbcs || !lbcs.length) return null
  const max = Math.max.apply(null, lbcs)
  if(max <= 1) return 1
  const levels = {}
  lbcs.forEach(x => { if(x >= 1) levels[x] = 1 })
  let present = 0
  for(let h = 1; h <= max; h++) if(levels[h]) present++
  return present / max
}
function persistScore(p){
  const parts = []
  if(p.sealRate != null) parts.push(p.sealRate)
  if(p.ladder != null) parts.push(p.ladder)
  if(p.ztRank != null) parts.push(p.ztRank)
  if(!parts.length) return null
  return parts.reduce((a,b) => a+b, 0) / parts.length
}
function rank(arr, v){
  if(!arr.length) return null
  let n = 0; for(const x of arr) if(x <= v) n++
  return n / arr.length
}

async function main(){
  const DRY = process.argv.includes('--dry')
  console.log('==== 方案 C：大盘「持续性」指标研究（预注册）====\n')

  /* 1. 拿近端交易日序列：从涨停池能回的最远日开始（用日线日期为日历基准） */
  const kl = await indexKline('sh000001', 60)
  console.log('[日线] 取到 ' + kl.length + ' 根，最新=' + (kl[kl.length-1] || {}).d + ' 最早=' + (kl[0] || {}).d)
  /* 用日线日期作为候选交易日（近 20 个），再逐个探涨停池是否有数据 */
  const cand = kl.slice(-20).map(x => x.d.replace(/-/g, ''))
  console.log('[候选交易日] ' + cand.length + ' 个：' + cand.join(' '))

  const rows = []
  for(const d of cand){
    const zt = await ztPool(d)
    if(!zt) { rows.push({ d: d, miss: true }); continue }
    if(zt.tc === 0){ rows.push({ d: d, zero: true }); continue }
    const zb = await zbPool(d)
    const lbcs = zt.pool.map(x => x.lbc || 1)
    const maxLb = lbcs.length ? Math.max.apply(null, lbcs) : 0
    const sealRate = (zb && zb.tc != null) ? zt.tc / (zt.tc + zb.tc) : null
    rows.push({
      d: d, zt: zt.tc, zb: (zb ? zb.tc : null), maxLb: maxLb,
      ladder: ladderIntegrity(lbcs), sealRate: sealRate,
      pool: zt.pool
    })
    await new Promise(z => setTimeout(z, 200))
  }

  const valid = rows.filter(r => !r.miss && !r.zero && r.zt > 0)
  console.log('\n[有效样本] ' + valid.length + ' 个交易日\n')
  if(!valid.length){ console.log('无有效样本，终止。'); return }

  /* 涨停家数分位（在有效样本内） */
  const zts = valid.map(r => r.zt)
  valid.forEach(r => { r.ztRank = rank(zts, r.zt) })
  /* 量能比：当日量 / 近5日均量 */
  const volMap = {}
  kl.forEach((x, i) => { volMap[x.d.replace(/-/g, '')] = i })
  valid.forEach(r => {
    const i = volMap[r.d]
    if(i != null && i >= 5){
      const w = kl.slice(i-5, i).map(x => x.vol)
      const mean = w.reduce((a,b)=>a+b,0)/w.length
      r.volRatio = mean > 0 ? kl[i].vol / mean : null
    }
  })
  valid.forEach(r => { r.persist = persistScore(r) })

  /* 打印表 */
  console.log('日期     涨停  炸板  封板率  最高板  梯队   涨停分位  量能比  持续性分')
  valid.forEach(r => {
    console.log(
      r.d + '  ' +
      String(r.zt).padStart(4) + '  ' +
      String(r.zb).padStart(4) + '  ' +
      (r.sealRate != null ? (r.sealRate*100).toFixed(0)+'%' : ' — ').padStart(6) + '  ' +
      String(r.maxLb).padStart(5) + '  ' +
      (r.ladder != null ? r.ladder.toFixed(2) : ' — ').padStart(5) + '  ' +
      (r.ztRank != null ? r.ztRank.toFixed(2) : ' — ').padStart(7) + '  ' +
      (r.volRatio != null ? r.volRatio.toFixed(2) : ' — ').padStart(5) + '  ' +
      (r.persist != null ? r.persist.toFixed(3) : ' — ')
    )
  })

  /* 分布统计（描述性，不做预测检验） */
  const P = valid.map(r => r.persist).filter(x => x != null)
  if(P.length){
    const mean = P.reduce((a,b)=>a+b,0)/P.length
    const sd = Math.sqrt(P.reduce((a,b)=>a+(b-mean)*(b-mean),0)/P.length)
    const min = Math.min.apply(null,P), max = Math.max.apply(null,P)
    console.log('\n[持续性分分布] n=' + P.length + ' 均值=' + mean.toFixed(3) + ' 标准差=' + sd.toFixed(3) +
      ' 区间=[' + min.toFixed(3) + ', ' + max.toFixed(3) + ']')
    console.log('[区分度初判] 标准差/均值 = ' + (sd/mean).toFixed(3) +
      (sd/mean > 0.15 ? '  → 有离散度，值得继续' : '  → 离散度低，指标可能无信息'))
  }
  console.log('\n⚠️ 样本仅 ' + valid.length + ' 日，且涨停池不可回溯 → **只作定性观察，不足以下统计结论**。')
  console.log('⚠️ 本脚本只输出状态分布，不做「次日涨跌」检验（那样必然过拟合）。')
}

if(require.main === module) main().catch(e => { console.error('FATAL', e); process.exit(1) })
module.exports = { ladderIntegrity, persistScore, rank }

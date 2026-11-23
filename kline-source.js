#!/usr/bin/env node
/* 统一指数/个股日线取数（2026-11-23）
 *
 * 为什么需要：腾讯 web.ifzq / ifzq 两个域名都被 WAF 501（IP 级，且会**随时翻转**——
 * 同一域名上午 200、下午 501），导致恐贪 / 两融 / 板块轮动 / 筹码 / 信号 / 风格 /
 * 筛选 / 交易日闸门 8 条数据流轮流停更。单域名兜底靠不住。
 *
 * 设计：对外返回与腾讯 ifzq **完全相同**的 JSON 结构
 *   { data: { [sym]: { day: [[日期,开,收,高,低,量], ...] } } }
 * 内部按「数据末日最新」在多源间选胜（不是谁先通就用谁——旧快照也会 200）。
 * 源：腾讯 ifzq → 腾讯 web.ifzq → 东财 push2his（前复权 fqt=1，与腾讯 qfq 同口径）。
 *
 * 用法（替换原来的 fetch 调用，解析逻辑一行不用改）：
 *   const KS = require('./kline-source.js')
 *   const j = await KS.fetchKlineJson(sym, n)     // j.data[sym].day
 */
'use strict'

const UA = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Referer': 'https://quote.eastmoney.com/' }

function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

async function getJson(url, ms) {
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), ms || 12000)
  try {
    const r = await fetch(url, { headers: UA, signal: ctl.signal })
    if (!r.ok) throw new Error('HTTP ' + r.status)
    return await r.json()
  } finally { clearTimeout(timer) }
}

function secid(sym) {
  const s = String(sym)
  const code = s.replace(/^(sh|sz)/i, '')
  return (/^6/.test(code) || /^sh/i.test(s) ? '1.' : '0.') + code
}

/* 东财 klines 字段顺序：f51日期,f52开,f53收,f54高,f55低,f56量,f57额
 * → 对齐腾讯 day[] 的 [d,o,c,h,l,v]（前 6 项），后续字段多带无妨（各脚本只取前 6）。 */
function emRowsToTx(klines) {
  const out = []
  for (const line of (klines || [])) {
    const a = String(line).split(',')
    if (!/^\d{4}-\d{2}-\d{2}/.test(a[0] || '')) continue
    out.push([a[0], Number(a[1]), Number(a[2]), Number(a[3]), Number(a[4]), Number(a[5])])
  }
  return out
}

/* 从各源响应里抽出统一的 rows 数组 */
function rowsFrom(src, j, sym) {
  if (src === 'em') {
    const k = j && j.data && j.data.klines
    return Array.isArray(k) ? emRowsToTx(k) : []
  }
  const node = j && j.data && j.data[sym]
  const rows = node && (node.qfqday || node.day || node.qfq_day)
  return Array.isArray(rows) ? rows : []
}

function lastDay(rows) {
  return rows.length && rows[rows.length - 1] && rows[rows.length - 1][0]
    ? String(rows[rows.length - 1][0]).slice(0, 10) : ''
}

/* 多源取数：并发跑，按「末日最新 → 条数最多」选胜；全失败抛错。
 * 不设单源粘性：WAF 会随时翻转，每次都重新竞速才不会卡死在一个刚被封的源上。 */
async function fetchKlineJson(sym, n) {
  const bars = n || 130
  const srcs = [
    { name: 'tx-ifzq', url: 'https://ifzq.gtimg.cn/appstock/app/fqkline/get?param=' + sym + ',day,,,' + bars + ',qfq&_=' + Date.now() },
    { name: 'tx-web', url: 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=' + sym + ',day,,,' + bars + ',qfq&_=' + Date.now() },
    { name: 'em-his', url: 'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=' + secid(sym) +
        '&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt=' + bars + '&_=' + Date.now() }
  ]
  const results = await Promise.all(srcs.map(async s => {
    try {
      const j = await getJson(s.url)
      const rows = rowsFrom(s.name === 'em-his' ? 'em' : 'tx', j, sym)
      return { name: s.name, rows, err: rows.length ? '' : '空数据' }
    } catch (e) { return { name: s.name, rows: [], err: (e && e.message) || '请求失败' } }
  }))
  results.sort((a, b) => {
    const d = lastDay(b.rows).localeCompare(lastDay(a.rows))
    return d !== 0 ? d : b.rows.length - a.rows.length
  })
  const best = results[0]
  if (!best || !best.rows.length) {
    throw new Error('日线全源不可用：' + results.map(r => r.name + '=' + r.err).join(' / '))
  }
  return { data: { [sym]: { day: best.rows } }, _src: best.name, _asof: lastDay(best.rows), _sources: results.map(r => ({ name: r.name, asof: lastDay(r.rows), n: r.rows.length, err: r.err })) }
}

module.exports = { fetchKlineJson, secid, lastDay, rowsFrom }

if (require.main === module) {
  const sym = process.argv[2] || 'sh000985'
  const n = Number(process.argv[3] || 20)
  fetchKlineJson(sym, n).then(j => {
    console.log('胜出源=' + j._src + ' 末日=' + j._asof + ' 条数=' + j.data[sym].day.length)
    console.log('各源:', JSON.stringify(j._sources))
    console.log('最后2条:', JSON.stringify(j.data[sym].day.slice(-2)))
  }).catch(e => { console.error('FAIL', e.message); process.exit(1) })
}

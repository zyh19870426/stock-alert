#!/usr/bin/env node
/* 东财接口「被封」实测 —— 在 GitHub Actions 的干净出口 IP 上跑一次，回答两件事：
 *
 *  Q1 个股级 clist（板块成分股 `fs=b:BK…`）从 Actions IP 到底能不能抓？
 *     —— 决定方案 B「云端预生成 · 板块前5个股」是否可行。
 *     （2026-09-30 旧结论：个股级 502、板块级可用；需复测，风控策略会变。）
 *
 *  Q2 是「频率风控」还是「IP 级封禁」？
 *     —— 同一接口按固定间隔连打 N 次：
 *        前几次 200、之后变 502/断连  ⇒ 频率风控，降频 + 退避有效；
 *        **第一次就挂**                ⇒ IP 级封禁，只能换出口（预生成 / 代理），重试无用。
 *
 * 用法：node probe-em.js
 *      ROUNDS=12 GAP=1500 node probe-em.js
 */
'use strict'
const https = require('https')

const ROUNDS = Number(process.env.ROUNDS || 6)
const GAP = Number(process.env.GAP || 700)
const UA = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Referer': 'https://quote.eastmoney.com/',
  'Accept': '*/*',
}
const UT = 'b2884a393a59ad64002292a3e90d46a5'
const sleep = ms => new Promise(r => setTimeout(r, ms))

/* 北京今日（YYYYMMDD），涨停池要带日期 */
function ymd() {
  return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10).replace(/-/g, '')
}

function get(url, ms) {
  return new Promise(resolve => {
    const t0 = Date.now()
    const req = https.get(url, { headers: UA, timeout: ms || 12000 }, res => {
      let b = ''
      res.setEncoding('utf8')
      res.on('data', d => (b += d))
      res.on('end', () => {
        let note = ''
        if (res.statusCode === 200 && b) {
          try { const j = JSON.parse(b); note = (j && j.data && j.data.diff) ? ('diff=' + j.data.diff.length) : (j && j.data ? 'data' : '') } catch (e) { note = b.slice(0, 40) }
        }
        resolve({ code: res.statusCode, ms: Date.now() - t0, len: b.length, note })
      })
    })
    req.on('timeout', () => { req.destroy(); resolve({ code: 'TIMEOUT', ms: Date.now() - t0, len: 0, note: '' }) })
    req.on('error', e => resolve({ code: 'ERR:' + (e.code || e.message), ms: Date.now() - t0, len: 0, note: '' }))
  })
}

const TARGETS = [
  ['A 板块级 clist(push2delay)', 'https://push2delay.eastmoney.com/api/qt/clist/get?pn=1&pz=100&po=1&np=1&fltt=2&invt=2&fid=f62&fs=m:90+t:3&fields=f12,f14,f2,f3,f62&ut=' + UT],
  ['B 个股级 clist·净额(push2)', 'https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=1&np=1&fltt=2&invt=2&fid=f62&fs=b:BK1033+f:!50&fields=f12,f14,f2,f3,f62&ut=' + UT],
  ['C 个股级 clist·涨幅(push2his)', 'https://push2his.eastmoney.com/api/qt/clist/get?pn=1&pz=5&po=1&np=1&fltt=2&invt=2&fid=f3&fs=b:BK1033+f:!50&fields=f12,f3&ut=' + UT],
  ['D 板块月累计 daykline(push2his)', 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?lmt=5&klt=101&secid=90.BK1033&fields1=f1,f2,f3,f7&fields2=f51,f52&ut=' + UT],
  ['E 涨停池(push2ex)', 'https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=20&sort=fbt%3Aasc&date=' + ymd()],
  ['F datacenter-web(两融)', 'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPTA_WEB_RZRQ_GGMX&columns=ALL&pageSize=3&source=WEB&client=WEB'],
  ['G 新浪·板块成分股资金流', 'https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_bkzj_ssggzj?page=1&num=5&sort=netamount&asc=0&bankuai=new_blhy'],
]

;(async () => {
  console.log('出口 IP 探测（Actions runner）')
  console.log('ROUNDS=' + ROUNDS + '  GAP=' + GAP + 'ms')
  console.log('='.repeat(72))
  const summary = []
  for (const [name, url] of TARGETS) {
    const seq = []
    for (let i = 0; i < ROUNDS; i++) {
      const r = await get(url)
      seq.push(String(r.code))
      if (i === 0 && r.note) console.log('  · ' + name + ' 首次返回: ' + r.note)
      await sleep(GAP)
    }
    const okN = seq.filter(c => c === '200').length
    summary.push({ name, seq, okN })
    console.log(name.padEnd(30) + ' ' + seq.join(',') + '   → 200×' + okN + '/' + ROUNDS)
  }
  console.log('='.repeat(72))
  console.log('判定：')
  for (const s of summary) {
    const first = s.seq[0]
    let verdict
    if (s.okN === s.seq.length) verdict = '✅ 全程可用'
    else if (s.okN === 0) verdict = '❌ 完全不可用（IP 级封禁 ⇒ 降频/重试无效）'
    else if (first === '200') verdict = '⚠️ 前段可用后段失效（频率风控 ⇒ 降频+退避有效）'
    else verdict = '⚠️ 首次即失败但偶有成功（不稳定）'
    console.log('  ' + s.name.padEnd(30) + verdict)
  }
  console.log('')
  console.log('结论怎么用：')
  console.log('  若 B/C 行 =「全程可用」 ⇒ 方案 B（云端预生成 板块前5个股）成立，前端零东财请求。')
  console.log('  若 B/C 行 =「完全不可用」 ⇒ 只能走方案 C（代理中转）或换源（新浪 ssl_bkzj_ssggzj）。')
})()

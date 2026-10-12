'use strict'
/* ============================================================================
 * em-resilient.js —— 东财「出口风控」终极解决方案（单一真源）
 * ============================================================================
 * 背景（2026-10-10 实测钉死）：
 *   东财对 clist（全市场列表）接口做的是【域名+路径】级风控，**不是 IP 封禁**。
 *   同一台机、同一出口 IP：
 *     - https://push2.eastmoney.com/api/qt/clist/get      → http=000（主域名被标记）
 *     - https://push2test.eastmoney.com/api/qt/clist/get  → http=200（连续 6 次全通）
 *     - https://push2.eastmoney.com/api/qt/stock/get      → 时通时不通
 *     - push2ex / datacenter-web                          → 稳定 200（未被标记）
 *   结论：**换域名即破，不需要换 IP。** 换 IP（代理池/VPN）成本高、易再被封，已被否决。
 *
 * 本模块提供三层兜底（按优先级）：
 *   1) 域名池轮换：push2test → push2test1 → push2wtest → ... → push2（主域名垫底）
 *   2) 同域名多路径：clist / ulist.np（部分节点只放行其一）
 *   3) 冷却记忆：某域名失败即冷 10 分钟，避免每轮都撞墙
 *
 * 用法：
 *   const em = require('./em-resilient')
 *   const j = await em.clist('m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23', 'f12,f14,f3', 1, 500)
 *   const all = await em.clistAll(fs, 'f12,f14,f3')   // 自动翻页取全量
 * ============================================================================ */
const https = require('https')
const http = require('http')

/* 域名池：按实测可用性排序。主域名 push2 垫底（它最常被标记）。 */
const HOSTS = [
  'push2test.eastmoney.com',
  'push2test1.eastmoney.com',
  'push2wtest.eastmoney.com',
  'push2delaytest.eastmoney.com',
  'push2web.eastmoney.com',
  'push2api.eastmoney.com',
  'push2trade.eastmoney.com',
  'push2delay.eastmoney.com',
  'push2.eastmoney.com'
]
/* 路径池：同域名不同路径，风控粒度可能不同 */
const PATHS = ['/api/qt/clist/get', '/api/qt/ulist.np/get']

const COOL_MS = 10 * 60 * 1000        // 失败域名冷却 10 分钟
const _cool = {}                       // host → 解禁时间戳
let _cursor = 0                        // 轮换起点

function _now(){ return Date.now() }
function _alive(host){ return !(_cool[host] && _cool[host] > _now()) }
function _markDead(host){ _cool[host] = _now() + COOL_MS }

/* 单次 GET（带超时 + UA），返回 {status, body}；网络异常 → status:0 */
function get(url, ms){
  return new Promise(resolve => {
    let done = false
    const lib = url.indexOf('https:') === 0 ? https : http
    const req = lib.get(url, {
      timeout: ms || 9000,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36',
        'Referer': 'https://quote.eastmoney.com/'
      }
    }, res => {
      let b = ''
      res.setEncoding('utf8')
      res.on('data', d => b += d)
      res.on('end', () => { if(!done){ done = true; resolve({ status: res.statusCode, body: b }) } })
    })
    req.on('error', () => { if(!done){ done = true; resolve({ status: 0, body: '' }) } })
    req.on('timeout', () => { if(!done){ done = true; req.destroy(); resolve({ status: 0, body: '' }) } })
  })
}

/* 带 JSONP 剥离的 JSON 解析 */
function _parse(body){
  if(!body) return null
  let s = String(body).trim()
  const i = s.indexOf('('), j = s.lastIndexOf(')')
  if(i >= 0 && j > i && !s.slice(0, i).includes(':')) s = s.slice(i + 1, j)
  try { return JSON.parse(s) } catch(e){ return null }
}

/* 核心：clist 多域名 × 多路径 轮换 + 冷却记忆 */
async function clistRaw(query, ms){
  const n = HOSTS.length
  let lastErr = ''
  for(let step = 0; step < n * PATHS.length; step++){
    const host = HOSTS[(_cursor + step) % n]
    if(!_alive(host)){ continue }
    const path = PATHS[step % PATHS.length]
    const url = 'https://' + host + path + '?' + query
    const r = await get(url, ms)
    if(r.status === 200){
      const j = _parse(r.body)
      if(j && j.data){ _cursor = (_cursor + step) % n; return { json: j, host: host, path: path } }
      lastErr = host + ' 200 但无 data'
    } else {
      if(r.status === 0) _markDead(host)     // 连接被拒 → 该域名冷却
      lastErr = host + ' http=' + r.status
    }
  }
  throw new Error('所有东财域名均不可用：' + lastErr)
}

/* 组装 clist query。fields 逗号串；pn 页码；pz 每页；fid/po 排序 */
function clistQuery(fs, fields, pn, pz, fid, po){
  let q = 'pn=' + (pn || 1) + '&pz=' + (pz || 100) + '&fs=' + encodeURIComponent(fs) +
          '&fields=' + fields + '&source=WEB&client=WEB'
  if(fid) q += '&fid=' + fid
  if(po) q += '&po=' + po
  return q
}

/* 取一页，返回 {total, list}。list 为 data.diff 的值数组（兼容 dict/array） */
async function clist(fs, fields, pn, pz, fid, po, ms){
  const r = await clistRaw(clistQuery(fs, fields, pn, pz, fid, po), ms)
  const d = r.json.data || {}
  const diff = d.diff || {}
  const list = Array.isArray(diff) ? diff : Object.keys(diff).map(k => diff[k])
  return { total: d.total || 0, list: list, host: r.host }
}

/* 自动翻页取全量（每页 1000，最多 maxPages 页） */
async function clistAll(fs, fields, fid, po, maxPages){
  const PZ = 1000, LIMIT = maxPages || 12
  let all = [], total = 0, host = ''
  for(let p = 1; p <= LIMIT; p++){
    const r = await clist(fs, fields, p, PZ, fid, po)
    total = r.total; host = r.host
    all = all.concat(r.list)
    if(all.length >= total || !r.list.length) break
    await new Promise(z => setTimeout(z, 250))
  }
  return { total: total, list: all, host: host }
}

/* 自检：逐个域名探活，返回可用清单（供云端/本机诊断） */
async function probeHosts(){
  const out = []
  for(const h of HOSTS){
    const r = await get('https://' + h + '/api/qt/clist/get?pn=1&pz=1&fs=' + encodeURIComponent('m:90+t:2') + '&fields=f12', 6000)
    out.push({ host: h, status: r.status, ok: r.status === 200 })
  }
  return out
}

module.exports = {
  HOSTS, PATHS,
  get, clist, clistAll, clistRaw, clistQuery, probeHosts, _parse,
  /* 供测试注入：手动清空/设置冷却 */
  _cool, _resetCool(){ for(const k in _cool) delete _cool[k] }, _markDead, _alive
}

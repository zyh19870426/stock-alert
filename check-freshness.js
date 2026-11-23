#!/usr/bin/env node
/* 存档新鲜度硬校验（2026-11-23）
 *
 * 背景：日更 workflow 出现「闸门跳过 / 数据源被封」时，脚本 early return、没有 commit，
 * 但 workflow 仍显示 success（13 秒空跑）——数据静默停更 17 天才被用户发现。
 * 本校验在每次日更末尾运行：存档末日落后行情日即 exit(1)，让失败显式报红。
 *
 * 用法：node check-freshness.js <file.json> <dateField> [maxLagDays]
 *   dateField 支持点路径，如 days / series / data.days；自动识别末项为数组或对象。
 *   maxLagDays 默认 1（行情日到存档日允许 1 天滞后，覆盖跨日调度延迟）。
 */
'use strict'
const fs = require('fs')

const [, , file, field, lagArg] = process.argv
if (!file || !field) {
  console.error('用法: node check-freshness.js <file.json> <dateField> [maxLagDays]')
  process.exit(2)
}
const MAX_LAG = Number(lagArg || 1)

function lastDate(obj, path) {
  /* 顶层标量日期（如 rotation.json 的 updated="20261116"） */
  if (path === 'updated' || path === 'date') {
    const v = obj && obj[path]
    if (!v) return ''
    const s = String(v).slice(0, 10)
    return /^\d{8}$/.test(s) ? (s.slice(0, 4) + '-' + s.slice(4, 6) + '-' + s.slice(6, 8)) : s
  }
  let cur = obj
  for (const k of path.split('.')) {
    if (!cur || typeof cur !== 'object') return ''
    cur = cur[k]
  }
  if (!Array.isArray(cur) || !cur.length) return ''
  const last = cur[cur.length - 1]
  const v = Array.isArray(last) ? last[0] : (last && (last.d || last.date || last.dimDate || last.DIM_DATE))
  return v ? String(v).slice(0, 10) : ''
}

function bjToday() {
  return new Date(Date.now() + 8 * 3600 * 1000).toISOString().slice(0, 10)
}

async function marketDay() {
  try {
    const KS = require('./kline-source.js')
    const j = await KS.fetchKlineJson('sh000985', 5)
    return KS.lastDay(j.data.sh000985.day) || bjToday()
  } catch (e) { return bjToday() }
}

;(async () => {
  const mkt = await marketDay()
  if (!fs.existsSync(file)) { console.error('存档不存在: ' + file); process.exit(1) }
  let j
  try { j = JSON.parse(fs.readFileSync(file, 'utf8')) } catch (e) { console.error('存档解析失败: ' + e.message); process.exit(1) }
  const last = lastDate(j, field)
  const lag = (new Date(mkt + 'T00:00:00Z') - new Date(last + 'T00:00:00Z')) / 86400000
  console.log('新鲜度: 行情日=' + mkt + ' 存档末日=' + (last || '空') + ' 滞后=' + (isFinite(lag) ? lag.toFixed(0) : '?') + ' 天（上限 ' + MAX_LAG + '）')
  if (!last || !isFinite(lag) || lag < 0) { console.error('校验失败：存档日期无效或超前'); process.exit(1) }
  if (lag > MAX_LAG) { console.error('校验失败：存档落后行情 ' + lag.toFixed(0) + ' 天 > 上限 ' + MAX_LAG); process.exit(1) }
  console.log('新鲜度校验通过')
})()

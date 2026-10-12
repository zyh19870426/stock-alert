#!/usr/bin/env node
/* eslint-disable */
/**
 * 核验「云端仓库里的代码」与「本地 stock-alert-cloud/」是否逐字节一致（维护用）
 *
 * 为什么需要：上传走的是 Contents API / Git Data API，成功返回不代表内容对
 * （比如传错文件、缓存、分支写错）。这个脚本按 md5 逐文件比对，是上传后的必做一步。
 *
 * 用法：
 *   node tools/verify-cloud.js                # 匿名读（仓库是公开的，够用）
 *   GH_TOKEN=ghp_xxx node tools/verify-cloud.js   # 带 token 读（避免匿名限流）
 *
 * 可选环境变量：GH_OWNER / GH_REPO / GH_BRANCH（默认 zyhaaad / stock-alert / main）
 */

const fs = require('fs')
const path = require('path')
const crypto = require('crypto')

const TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN || ''
const OWNER = process.env.GH_OWNER || 'zyhaaad'
const REPO = process.env.GH_REPO || 'stock-alert'
const BRANCH = process.env.GH_BRANCH || 'main'
const API = 'https://api.github.com/repos/' + OWNER + '/' + REPO
const SRC = path.join(__dirname, '..')

/* 与 upload.js 同一份清单：只核验「上传过的那些」，运行期数据文件不参与
   ⚠️ 改了 upload.js 的清单，这里必须同步（否则新文件上传后不会被核验到）。 */
const FILES = [
  'push.js',
  'fng-core.js',
  'position-core.js',
  'signal-core.js',
  'chip-core.js',
  'fng.js',
  'heartbeat.js',
  'alert.js',
  'signals.js',
  'screener.js',
  'style.js',
  'review.js',
  'chips.js',
  '.github/workflows/fng.yml',
  '.github/workflows/heartbeat.yml',
  '.github/workflows/monitor.yml',
  '.github/workflows/signals.yml',
  '.github/workflows/style.yml'
]

const md5 = b => crypto.createHash('md5').update(b).digest('hex')

async function fetchCloud(rel) {
  const url = API + '/contents/' + rel.split('/').map(encodeURIComponent).join('/') + '?ref=' + BRANCH
  const headers = { 'Accept': 'application/vnd.github+json', 'User-Agent': 'stock-alert-verifier' }
  if (TOKEN) headers['Authorization'] = 'Bearer ' + TOKEN
  const res = await fetch(url, { headers })
  if (!res.ok) return { ok: false, status: res.status }
  const j = await res.json()
  if (!j || !j.content) return { ok: false, status: res.status, msg: '返回里没有 content' }
  const buf = Buffer.from(String(j.content).replace(/\n/g, ''), 'base64')
  return { ok: true, buf: buf, sha: j.sha, size: j.size }
}

async function main() {
  console.log('核验 ' + OWNER + '/' + REPO + '@' + BRANCH + (TOKEN ? '（带 token）' : '（匿名）'))
  console.log('')
  let bad = 0
  for (const rel of FILES) {
    const abs = path.join(SRC, ...rel.split('/'))
    if (!fs.existsSync(abs)) { console.log('  ❓ ' + rel + '  本地不存在，跳过'); continue }
    const local = fs.readFileSync(abs)
    const cloud = await fetchCloud(rel)
    if (!cloud.ok) { console.log('  ❌ ' + rel + '  云端读取失败 HTTP ' + cloud.status); bad++; continue }
    const same = md5(local) === md5(cloud.buf)
    console.log((same ? '  ✅ ' : '  ❌ ') + rel.padEnd(32) +
      (same ? '一致' : '不一致！本地 ' + md5(local).slice(0, 8) + ' vs 云端 ' + md5(cloud.buf).slice(0, 8)) +
      '  ' + Math.round(local.length / 1024) + ' KB')
    if (!same) bad++
  }
  console.log('')
  console.log(bad ? '有 ' + bad + ' 个文件不一致' : '云端与本地逐字节一致')
  process.exit(bad ? 1 : 0)
}

main().catch(e => { console.error('出错: ' + (e && e.message ? e.message : e)); process.exit(1) })

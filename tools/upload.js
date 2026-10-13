#!/usr/bin/env node
/* eslint-disable */
/**
 * 把本地 stock-alert-cloud/ 的代码文件上传到 GitHub 仓库（维护用）
 *
 * 用法（token 只从环境变量读，不落盘、不写进任何文件）：
 *   Windows PowerShell：  $env:GH_TOKEN='ghp_xxx'; node tools/upload.js
 *   macOS / Linux：       GH_TOKEN=ghp_xxx node tools/upload.js
 *
 * 可选环境变量：GH_OWNER（默认 zyh19870426）、GH_REPO（默认 stock-alert）、GH_BRANCH（默认 main）
 *
 * ⚠️ 故意**不**上传 config.json / state.json / fng-history.json：
 *   - config.json / state.json 是运行期数据，手机控制台随时在改，覆盖会丢用户配置
 *   - fng-history.json 由云端 fng.js 自己维护（本地覆盖会把云端新增的日期冲掉）
 *   需要单独传这三个时，请用带明确参数的一次性脚本，不要动本文件的清单。
 *
 * 说明：
 *   - 普通文件走 Contents API
 *   - .github/workflows/* 走 Git Data API（classic token 必须带 workflow scope，
 *     否则写 workflow 路径会返回 404 而不是 422 —— 这是踩过的坑）
 *   - **幂等**：上传前先比对云端 md5，内容一致的文件直接跳过，
 *     不会为"没改过的文件"产生只有时间戳变化的噪音提交（与存档幂等同一个原则）
 */

const fs = require('fs')
const path = require('path')
const crypto = require('crypto')
const md5 = b => crypto.createHash('md5').update(b).digest('hex')

const TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN
const OWNER = process.env.GH_OWNER || 'zyh19870426'
const REPO = process.env.GH_REPO || 'stock-alert'
const BRANCH = process.env.GH_BRANCH || 'main'
const API = 'https://api.github.com/repos/' + OWNER + '/' + REPO
const SRC = path.join(__dirname, '..')

if (!TOKEN) {
  console.error('缺少 GH_TOKEN 环境变量')
  process.exit(1)
}

const HEADERS = {
  'Authorization': 'Bearer ' + TOKEN,
  'Accept': 'application/vnd.github+json',
  'User-Agent': 'stock-alert-uploader',
  'X-GitHub-Api-Version': '2022-11-28'
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method: method,
    headers: Object.assign({ 'Content-Type': 'application/json' }, HEADERS),
    body: body ? JSON.stringify(body) : undefined
  })
  let json = null
  try { json = await res.json() } catch (e) {}
  return { status: res.status, ok: res.ok, json: json, scopes: res.headers.get('x-oauth-scopes') }
}

/* ---------------- Contents API：普通文件 ---------------- */
async function putFile(relPath, absPath) {
  const content = fs.readFileSync(absPath)
  const b64 = content.toString('base64')
  const url = API + '/contents/' + relPath.split('/').map(encodeURIComponent).join('/')

  let sha = null
  const head = await api('GET', url + '?ref=' + BRANCH)
  if (head.ok && head.json && head.json.sha) sha = head.json.sha
  else if (head.status !== 404) {
    return { file: relPath, ok: false, msg: '读取现有文件失败 HTTP ' + head.status + ' scopes=' + head.scopes }
  }

  // 幂等：云端内容与本地一致就不写，避免噪音提交
  if (sha && head.json && head.json.content) {
    const cloud = Buffer.from(String(head.json.content).replace(/\n/g, ''), 'base64')
    if (md5(cloud) === md5(content)) return { file: relPath, ok: true, skipped: true, msg: '内容一致，跳过' }
  }

  const body = { message: 'chore(cloud): upsert ' + relPath, content: b64, branch: BRANCH }
  if (sha) body.sha = sha
  const put = await api('PUT', url, body)
  if (put.ok) {
    return { file: relPath, ok: true, msg: (sha ? '更新' : '新建') + ' 成功，' + Math.round(content.length / 1024) + ' KB' }
  }
  return { file: relPath, ok: false, msg: 'HTTP ' + put.status + ' ' + JSON.stringify(put.json).slice(0, 200) + ' scopes=' + put.scopes }
}

/* ---------------- Git Data API：workflow 文件 ---------------- */
async function putWorkflow(relPath, absPath) {
  const content = fs.readFileSync(absPath, 'utf8')

  // 幂等：云端内容与本地一致就不写（读走 Contents API 即可，写才需要 workflow scope）
  {
    const url = API + '/contents/' + relPath.split('/').map(encodeURIComponent).join('/') + '?ref=' + BRANCH
    const head = await api('GET', url)
    if (head.ok && head.json && head.json.content) {
      const cloud = Buffer.from(String(head.json.content).replace(/\n/g, ''), 'base64').toString('utf8')
      if (cloud === content) return { file: relPath, ok: true, skipped: true, msg: '内容一致，跳过' }
    }
  }

  const ref = await api('GET', API + '/git/ref/heads/' + BRANCH)
  if (!ref.ok) return { file: relPath, ok: false, msg: '取分支失败 HTTP ' + ref.status + ' scopes=' + ref.scopes }
  const parentSha = ref.json.object.sha

  const parent = await api('GET', API + '/git/commits/' + parentSha)
  if (!parent.ok) return { file: relPath, ok: false, msg: '取父提交失败 HTTP ' + parent.status }
  const baseTree = parent.json.tree.sha

  const blob = await api('POST', API + '/git/blobs', { content: content, encoding: 'utf-8' })
  if (!blob.ok) return { file: relPath, ok: false, msg: '建 blob 失败 HTTP ' + blob.status + ' ' + JSON.stringify(blob.json).slice(0, 160) }

  const tree = await api('POST', API + '/git/trees', {
    base_tree: baseTree,
    tree: [{ path: relPath, mode: '100644', type: 'blob', sha: blob.json.sha }]
  })
  if (!tree.ok) return { file: relPath, ok: false, msg: '建 tree 失败 HTTP ' + tree.status + ' ' + JSON.stringify(tree.json).slice(0, 160) }

  const commit = await api('POST', API + '/git/commits', {
    message: 'chore(cloud): upsert ' + relPath,
    tree: tree.json.sha,
    parents: [parentSha]
  })
  if (!commit.ok) return { file: relPath, ok: false, msg: '建提交失败 HTTP ' + commit.status + ' ' + JSON.stringify(commit.json).slice(0, 160) }

  const upd = await api('PATCH', API + '/git/refs/heads/' + BRANCH, { sha: commit.json.sha, force: false })
  if (!upd.ok) return { file: relPath, ok: false, msg: '更新分支失败 HTTP ' + upd.status + ' ' + JSON.stringify(upd.json).slice(0, 160) }

  return { file: relPath, ok: true, msg: '写入成功（Git Data API），commit ' + String(commit.json.sha).slice(0, 7) }
}

/* ---------------- 上传清单 ----------------
 * 顺序有意义：push.js 必须最先上传 —— alert.js / fng.js 都 require 它，
 * 若先更新调用方而后传被依赖模块，中间这段时间线上会直接报错。
 */
const NORMAL = [
  ['push.js', path.join(SRC, 'push.js')],
  ['fng-core.js', path.join(SRC, 'fng-core.js')],
  ['position-core.js', path.join(SRC, 'position-core.js')],
  ['signal-core.js', path.join(SRC, 'signal-core.js')],
  ['chip-core.js', path.join(SRC, 'chip-core.js')],
  // stage-core.js（趋势阶段引擎+有效性统计单一真源，2026-09-25）；无运行期依赖，无 history 存档
  ['stage-core.js', path.join(SRC, 'stage-core.js')],
  ['fng.js', path.join(SRC, 'fng.js')],
  ['heartbeat.js', path.join(SRC, 'heartbeat.js')],
  ['alert.js', path.join(SRC, 'alert.js')],
  ['signals.js', path.join(SRC, 'signals.js')],
  ['screener.js', path.join(SRC, 'screener.js')],
  // style.js（大盘风格驾驶舱 S1，2026-09-19）依赖 screener.js 的数据管道，排在它之后
  ['style.js', path.join(SRC, 'style.js')],
  ['review.js', path.join(SRC, 'review.js')],
  // trade-day.js（交易日判定单一真源，2026-10-07）；chips.js / flow-daily.js 都 require 它，
  // 必须排在它们**之前**上传，否则中间窗口线上会 require 失败
  ['trade-day.js', path.join(SRC, 'trade-day.js')],
  // chips.js 依赖 chip-core.js + signal-core.js，所以排在它们之后
  ['chips.js', path.join(SRC, 'chips.js')],
  // flow-daily.js（板块资金流日更存档，2026-09-29）；无本地依赖，flow-history.json 由云端自维护不上传
  ['flow-daily.js', path.join(SRC, 'flow-daily.js')],
  // daykline-probe.js（一次性诊断：板块月累计 + 个股 TOP5 重复性，2026-09-30）
  ['daykline-probe.js', path.join(SRC, 'daykline-probe.js')],
  // ---- 板块轮动（2026-10-07）----
  // 顺序有硬约束：rotation.js require state/report/boards + trade-day.js，
  // 所以三个被依赖模块必须排在它**之前**，否则中间窗口线上会 require 失败。
  // rotation-state.js / rotation-report.js = 八态判定与文案口径的单一真源；
  // rotation-boards.json = 板块代码表（代码扫描建表，不是运行期数据，必须上传）。
  // ⚠️ rotation.json 是运行期存档，由云端自维护（成功率<60% 不覆盖），**故意不上传**。
  ['rotation-state.js', path.join(SRC, 'rotation-state.js')],
  ['rotation-report.js', path.join(SRC, 'rotation-report.js')],
  ['rotation-boards.json', path.join(SRC, 'rotation-boards.json')],
  ['rotation.js', path.join(SRC, 'rotation.js')]
]

const WORKFLOWS = [
  ['.github/workflows/fng.yml', path.join(SRC, '.github', 'workflows', 'fng.yml')],
  ['.github/workflows/heartbeat.yml', path.join(SRC, '.github', 'workflows', 'heartbeat.yml')],
  ['.github/workflows/monitor.yml', path.join(SRC, '.github', 'workflows', 'monitor.yml')],
  ['.github/workflows/signals.yml', path.join(SRC, '.github', 'workflows', 'signals.yml')],
  ['.github/workflows/style.yml', path.join(SRC, '.github', 'workflows', 'style.yml')],
  ['.github/workflows/flow.yml', path.join(SRC, '.github', 'workflows', 'flow.yml')],
  ['.github/workflows/probe.yml', path.join(SRC, '.github', 'workflows', 'probe.yml')],
  // rotation.yml（板块轮动状态日更，北京 17:30 / UTC 9:30，2026-10-07）
  ['.github/workflows/rotation.yml', path.join(SRC, '.github', 'workflows', 'rotation.yml')]
]

async function main() {
  console.log('仓库：' + OWNER + '/' + REPO + '  分支：' + BRANCH)

  const me = await api('GET', 'https://api.github.com/user')
  if (!me.ok) {
    console.error('token 无效或无法访问 GitHub：HTTP ' + me.status)
    process.exit(1)
  }
  console.log('token 属于：' + (me.json.login || '?') + '   scopes=' + (me.scopes || '(未返回)'))
  const scopes = String(me.scopes || '')
  if (scopes && scopes.indexOf('workflow') < 0 && scopes.indexOf('repo') >= 0) {
    console.log('⚠️  token 没带 workflow scope，写 .github/workflows/ 会 404，请重新生成（勾 repo + workflow）')
  }

  /* 可选：GH_ONLY=<正则> 只上传文件名匹配的项（分批上线时用，避免把无关改动一起推上去） */
  const ONLY = process.env.GH_ONLY ? new RegExp(process.env.GH_ONLY) : null
  const pick = a => (ONLY ? a.filter(x => ONLY.test(x[0])) : a)
  if (ONLY) console.log('⚠️  只上传匹配 /' + process.env.GH_ONLY + '/ 的项')

  const results = []
  for (const [rel, abs] of pick(NORMAL)) {
    if (!fs.existsSync(abs)) { results.push({ file: rel, ok: false, msg: '本地文件不存在' }); continue }
    results.push(await putFile(rel, abs))
  }
  for (const [rel, abs] of pick(WORKFLOWS)) {
    if (!fs.existsSync(abs)) { results.push({ file: rel, ok: false, msg: '本地文件不存在' }); continue }
    results.push(await putWorkflow(rel, abs))
  }

  console.log('')
  console.log('================ 上传结果 ================')
  let bad = 0, skipped = 0
  for (const r of results) {
    const mark = !r.ok ? '  ❌ ' : (r.skipped ? '  ➖ ' : '  ✅ ')
    console.log(mark + r.file + '  ' + r.msg)
    if (!r.ok) bad++
    else if (r.skipped) skipped++
  }
  console.log(bad ? '\n有 ' + bad + ' 个文件失败'
    : '\n全部一致（' + skipped + ' 个跳过、' + (results.length - skipped) + ' 个已上传）')
  process.exit(bad ? 1 : 0)
}

main().catch(function (e) {
  console.error('出错: ' + (e && e.message ? e.message : e))
  process.exit(1)
})

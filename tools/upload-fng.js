#!/usr/bin/env node
/* eslint-disable */
/**
 * 把恐贪指数相关文件上传到 GitHub 仓库（一次性/维护用）
 *
 * 用法（token 只从环境变量读，不落盘、不写进任何文件）：
 *   Windows PowerShell：
 *     $env:GH_TOKEN='ghp_xxx'; node tools/upload-fng.js
 *   macOS / Linux：
 *     GH_TOKEN=ghp_xxx node tools/upload-fng.js
 *
 * 可选环境变量：GH_OWNER（默认 zyh19870426）、GH_REPO（默认 stock-alert）、GH_BRANCH（默认 main）
 *
 * 说明：
 *   - fng-core.js / fng.js / fng-history.json 走 Contents API
 *   - .github/workflows/fng.yml 走 Git Data API（classic token 必须带 workflow scope，
 *     否则写 workflow 路径会返回 404 而不是 422 —— 这是踩过的坑）
 */

const fs = require('fs')
const path = require('path')

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
  'User-Agent': 'stock-alert-fng-uploader',
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

  const body = { message: 'feat(fng): upsert ' + relPath, content: b64, branch: BRANCH }
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
    message: 'feat(fng): add daily fear & greed workflow',
    tree: tree.json.sha,
    parents: [parentSha]
  })
  if (!commit.ok) return { file: relPath, ok: false, msg: '建提交失败 HTTP ' + commit.status + ' ' + JSON.stringify(commit.json).slice(0, 160) }

  const upd = await api('PATCH', API + '/git/refs/heads/' + BRANCH, { sha: commit.json.sha, force: false })
  if (!upd.ok) return { file: relPath, ok: false, msg: '更新分支失败 HTTP ' + upd.status + ' ' + JSON.stringify(upd.json).slice(0, 160) }

  return { file: relPath, ok: true, msg: '写入成功（通过 Git Data API），commit ' + String(commit.json.sha).slice(0, 7) }
}

/* ---------------- 主流程 ---------------- */
const NORMAL = [
  ['fng-core.js', path.join(SRC, 'fng-core.js')],
  ['fng.js', path.join(SRC, 'fng.js')],
  ['fng-history.json', path.join(SRC, 'fng-history.json')]
]

async function main() {
  console.log('仓库：' + OWNER + '/' + REPO + '  分支：' + BRANCH)

  // 先验一下 token 与权限
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

  const results = []
  for (const [rel, abs] of NORMAL) {
    if (!fs.existsSync(abs)) { results.push({ file: rel, ok: false, msg: '本地文件不存在' }); continue }
    results.push(await putFile(rel, abs))
  }
  results.push(await putWorkflow('.github/workflows/fng.yml', path.join(SRC, '.github', 'workflows', 'fng.yml')))

  console.log('')
  console.log('================ 上传结果 ================')
  let bad = 0
  for (const r of results) {
    console.log((r.ok ? '  ✅ ' : '  ❌ ') + r.file + '  ' + r.msg)
    if (!r.ok) bad++
  }
  console.log(bad ? '\n有 ' + bad + ' 个文件失败' : '\n全部上传成功')

  // 回读校验
  const chk = await api('GET', API + '/contents/fng-history.json?ref=' + BRANCH)
  if (chk.ok && chk.json) {
    console.log('回读校验：fng-history.json 在线大小 ' + Math.round((chk.json.size || 0) / 1024) + ' KB（本地 ' +
      Math.round(fs.statSync(path.join(SRC, 'fng-history.json')).size / 1024) + ' KB）')
  }
  const wf = await api('GET', API + '/contents/.github/workflows/fng.yml?ref=' + BRANCH)
  console.log('回读校验：workflow ' + (wf.ok ? '已在线' : '未找到（HTTP ' + wf.status + '）'))
  process.exit(bad ? 1 : 0)
}

main().catch(function (e) {
  console.error('出错: ' + (e && e.message ? e.message : e))
  process.exit(1)
})

// 续传：用 Git Data API 上传 workflow 文件，然后存 Secret、触发、轮询（v2 token）
const fs = require('fs')
const path = require('path')

/* 凭据一律只从环境变量读，**不落盘、不写进任何文件**（本项目硬约定）：
     GH_TOKEN —— classic token，勾 repo + workflow
     SENDKEY  —— Server酱 SendKey（本脚本把它写进仓库 Secret，所以运行时需要传一次）
   用法：GH_TOKEN=ghp_xxx SENDKEY=SCTxxx node tools/gh-fix.js */
const TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN
const SENDKEY = process.env.SENDKEY
if (!TOKEN) { console.error('缺少 GH_TOKEN 环境变量'); process.exit(1) }
if (!SENDKEY) { console.error('缺少 SENDKEY 环境变量'); process.exit(1) }
const SRC = 'D:/mywork/stock-alert-cloud'
const API = 'https://api.github.com'

const log = []
function p(step, msg) { log.push('[' + step + '] ' + msg); console.log('[' + step + '] ' + msg) }

async function gh(method, url, body) {
  const res = await fetch(API + url, {
    method,
    headers: {
      Authorization: 'Bearer ' + TOKEN,
      Accept: 'application/vnd.github+json',
      'User-Agent': 'stock-alert-setup',
      'Content-Type': 'application/json'
    },
    body: body ? JSON.stringify(body) : undefined
  })
  const text = await res.text()
  let json = null
  try { json = text ? JSON.parse(text) : null } catch (e) {}
  return { status: res.status, ok: res.ok, json, text }
}

async function main() {
  const repoUrl = '/repos/zyh19870426/stock-alert'

  // 0. scope 确认
  const probe = await fetch(API + '/user', { headers: { Authorization: 'Bearer ' + TOKEN, 'User-Agent': 'setup' } })
  p('scopes', 'X-OAuth-Scopes: ' + (probe.headers.get('x-oauth-scopes') || '(none)'))

  // 1. Git Data API：blob -> tree -> commit -> 更新 main
  const yml = fs.readFileSync(path.join(SRC, '.github', 'workflows', 'monitor.yml'), 'utf8')
  const blob = await gh('POST', repoUrl + '/git/blobs', {
    content: Buffer.from(yml, 'utf8').toString('base64'),
    encoding: 'base64'
  })
  if (!blob.ok) throw new Error('blob 失败: HTTP ' + blob.status + ' ' + blob.text.slice(0, 200))
  p('blob', 'blob 创建成功: ' + blob.json.sha.slice(0, 7))

  const ref = await gh('GET', repoUrl + '/git/ref/heads/main')
  if (!ref.ok) throw new Error('取 main 分支失败: HTTP ' + ref.status + ' ' + ref.text.slice(0, 200))
  const baseCommitSha = ref.json.object.sha
  const baseCommit = await gh('GET', repoUrl + '/git/commits/' + baseCommitSha)
  if (!baseCommit.ok) throw new Error('取 commit 失败: HTTP ' + baseCommit.status)
  const baseTreeSha = baseCommit.json.tree.sha
  p('ref', 'main @ ' + baseCommitSha.slice(0, 7) + ', tree ' + baseTreeSha.slice(0, 7))

  const tree = await gh('POST', repoUrl + '/git/trees', {
    base_tree: baseTreeSha,
    tree: [{ path: '.github/workflows/monitor.yml', mode: '100644', type: 'blob', sha: blob.json.sha }]
  })
  if (!tree.ok) throw new Error('tree 失败: HTTP ' + tree.status + ' ' + tree.text.slice(0, 200))
  p('tree', 'tree 创建成功: ' + tree.json.sha.slice(0, 7))

  const commit = await gh('POST', repoUrl + '/git/commits', {
    message: 'add workflow',
    tree: tree.json.sha,
    parents: [baseCommitSha]
  })
  if (!commit.ok) throw new Error('commit 失败: HTTP ' + commit.status + ' ' + commit.text.slice(0, 200))
  p('commit', 'commit 创建成功: ' + commit.json.sha.slice(0, 7))

  const upd = await gh('PATCH', repoUrl + '/git/refs/heads/main', { sha: commit.json.sha })
  if (!upd.ok) throw new Error('更新分支失败: HTTP ' + upd.status + ' ' + upd.text.slice(0, 200))
  p('push', 'workflow 文件已推送到 main')

  // 2. Secret（libsodium sealed box）
  const sodium = require('libsodium-wrappers')
  await sodium.ready
  const pk = await gh('GET', repoUrl + '/actions/secrets/public-key')
  if (!pk.ok) throw new Error('拿公钥失败: HTTP ' + pk.status + ' ' + pk.text.slice(0, 200))
  const sealed = sodium.crypto_box_seal(
    sodium.from_string(SENDKEY),
    sodium.from_base64(pk.json.key, sodium.base64_variants.ORIGINAL)
  )
  const sec = await gh('PUT', repoUrl + '/actions/secrets/SENDKEY', {
    encrypted_value: sodium.to_base64(sealed, sodium.base64_variants.ORIGINAL),
    key_id: pk.json.key_id
  })
  if (!sec.ok) throw new Error('存 Secret 失败: HTTP ' + sec.status + ' ' + sec.text.slice(0, 200))
  p('secret', 'SENDKEY 已存入仓库 Secret')

  // 3. 触发
  const dis = await gh('POST', repoUrl + '/actions/workflows/monitor.yml/dispatches', { ref: 'main' })
  if (dis.status !== 204) throw new Error('触发失败: HTTP ' + dis.status + ' ' + dis.text.slice(0, 300))
  p('dispatch', 'workflow 已触发，等待运行…')

  // 4. 轮询
  let conclusion = null
  for (let i = 0; i < 24; i++) {
    await new Promise(function (r) { setTimeout(r, 10000) })
    const runs = await gh('GET', repoUrl + '/actions/runs?per_page=3')
    const list = (runs.json && runs.json.workflow_runs) || []
    if (!list.length) { p('poll', (i + 1) + '/24: 暂无记录'); continue }
    const run = list[0]
    if (run.status === 'completed') {
      conclusion = run.conclusion
      p('poll', '运行完成: ' + run.conclusion + ' (run_id=' + run.id + ')')
      break
    }
    p('poll', (i + 1) + '/24: ' + run.status)
  }

  if (conclusion && conclusion !== 'success') {
    const jobs = await gh('GET', repoUrl + '/actions/runs/latest/jobs')
    for (const j of (jobs.json && jobs.json.jobs) || []) {
      for (const s of j.steps || []) {
        if (s.conclusion && s.conclusion !== 'success' && s.conclusion !== 'skipped') {
          p('fail', 'step=' + s.name + ' -> ' + s.conclusion)
        }
      }
    }
  }

  fs.writeFileSync('D:/mywork/_gh_result3.txt', log.join('\n'), 'utf8')
  p('done', 'conclusion=' + conclusion)
}

main().catch(function (e) {
  log.push('[FATAL] ' + (e && e.message ? e.message : String(e)))
  try { fs.writeFileSync('D:/mywork/_gh_result3.txt', log.join('\n'), 'utf8') } catch (e2) {}
  console.log('[FATAL]', e.message)
})

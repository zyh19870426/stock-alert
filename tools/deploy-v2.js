// 部署：合并远端 config、上传 alert.js / config.json / workflow，然后触发一次模拟运行验证
//
// ⚠️ 一次性/维护脚本。凭据只从环境变量读，不落盘（本项目硬约定）：
//      GH_TOKEN=ghp_xxx node tools/deploy-v2.js     ← classic token，勾 repo + workflow
// ⚠️ 它会**上传 config.json**。正常情况下别用它覆盖云端 config —— 手机控制台随时在改，
//    覆盖会丢用户配置（上传代码请用 tools/upload.js，它不碰 config.json）。
const fs = require('fs')
const path = require('path')

const TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN
const SRC = 'D:/mywork/stock-alert-cloud'
const REPO = '/repos/zyhaaad/stock-alert'
const API = 'https://api.github.com'

if (!TOKEN) {
  console.error('缺少 GH_TOKEN 环境变量')
  process.exit(1)
}
const log = []
function p(s, m) { log.push('[' + s + '] ' + m); console.log('[' + s + '] ' + m) }

async function gh(method, url, body) {
  const res = await fetch(API + url, {
    method,
    headers: {
      Authorization: 'Bearer ' + TOKEN,
      Accept: 'application/vnd.github+json',
      'User-Agent': 'deploy',
      'Content-Type': 'application/json'
    },
    body: body ? JSON.stringify(body) : undefined
  })
  const text = await res.text()
  let json = null
  try { json = text ? JSON.parse(text) : null } catch (e) {}
  return { status: res.status, ok: res.ok, json, text }
}

async function getFile(p2) {
  const r = await gh('GET', REPO + '/contents/' + p2)
  if (!r.ok) return null
  return { sha: r.json.sha, text: Buffer.from(r.json.content, 'base64').toString('utf8') }
}

async function putFile(p2, content, message, sha) {
  const body = { message: message, content: Buffer.from(content, 'utf8').toString('base64') }
  if (sha) body.sha = sha
  const r = await gh('PUT', REPO + '/contents/' + p2, body)
  if (!r.ok) throw new Error('上传 ' + p2 + ' 失败: HTTP ' + r.status + ' ' + r.text.slice(0, 250))
  return r.json.content.sha
}

async function main() {
  // ---- 1. config.json：先取远端最新，合并迁移后再写回 ----
  const remoteCfg = await getFile('config.json')
  if (!remoteCfg) throw new Error('读不到远端 config.json')
  const cfg = JSON.parse(remoteCfg.text)
  if (!Array.isArray(cfg.stocks)) cfg.stocks = []
  if (!Array.isArray(cfg.deleted)) cfg.deleted = []
  const today = '2026-09-15'
  let migrated = 0
  for (const s of cfg.stocks) {
    if (!s.addedAt) { s.addedAt = today; migrated++ }
  }
  cfg._说明 = '云端(GitHub Actions)版配置。sendKey 留空即可——推送钥匙存在仓库的 Secret（名字叫 SENDKEY）里。手机端用网页控制台编辑：stocks 是正在监控的，deleted 是删除记录（含触发价，供查看触发后涨跌幅）。'
  const newCfgText = JSON.stringify(cfg, null, 2)
  p('config', '读数：stocks=' + cfg.stocks.length + '，deleted=' + cfg.deleted.length + '，补 addedAt 的股票 ' + migrated + ' 只')
  await putFile('config.json', newCfgText, 'feat: 支持删除记录与监控天数', remoteCfg.sha)
  p('config', '已写回')
  fs.writeFileSync(path.join(SRC, 'config.json'), newCfgText)

  // ---- 2. alert.js ----
  const alertJs = fs.readFileSync(path.join(SRC, 'alert.js'), 'utf8')
  const remoteAlert = await getFile('alert.js')
  await putFile('alert.js', alertJs, 'feat: 记录首次触发价 + 新增 --simulate', remoteAlert ? remoteAlert.sha : undefined)
  p('alert.js', '已上传（含首次触发价记录）')

  // ---- 3. workflow（Git Data API，需要 workflow 权限）----
  const yml = fs.readFileSync(path.join(SRC, '.github', 'workflows', 'monitor.yml'), 'utf8')
  const blob = await gh('POST', REPO + '/git/blobs', { content: Buffer.from(yml, 'utf8').toString('base64'), encoding: 'base64' })
  if (!blob.ok) throw new Error('blob 失败: HTTP ' + blob.status)
  const ref = await gh('GET', REPO + '/git/ref/heads/main')
  const base = ref.json.object.sha
  const bc = await gh('GET', REPO + '/git/commits/' + base)
  const tree = await gh('POST', REPO + '/git/trees', {
    base_tree: bc.json.tree.sha,
    tree: [{ path: '.github/workflows/monitor.yml', mode: '100644', type: 'blob', sha: blob.json.sha }]
  })
  if (!tree.ok) throw new Error('tree 失败: HTTP ' + tree.status + ' ' + tree.text.slice(0, 200))
  const commit = await gh('POST', REPO + '/git/commits', { message: 'fix: state.json 提交逻辑（未跟踪文件检测）+ 测试运行入口', tree: tree.json.sha, parents: [base] })
  if (!commit.ok) throw new Error('commit 失败: HTTP ' + commit.status + ' ' + commit.text.slice(0, 200))
  const upd = await gh('PATCH', REPO + '/git/refs/heads/main', { sha: commit.json.sha })
  if (!upd.ok) throw new Error('更新 main 失败: HTTP ' + upd.status + ' ' + upd.text.slice(0, 200))
  p('workflow', '已上传（修复状态提交 bug，新增测试运行入口）')

  // ---- 4. 触发一次"测试运行"（忽略交易时段、不推送）验证全链路 ----
  const dis = await gh('POST', REPO + '/actions/workflows/monitor.yml/dispatches', { ref: 'main', inputs: { simulate: 'true' } })
  if (dis.status !== 204) throw new Error('触发失败: HTTP ' + dis.status + ' ' + dis.text.slice(0, 250))
  p('dispatch', '已触发测试运行（simulate），等待结果…')

  let conclusion = null
  for (let i = 0; i < 24; i++) {
    await new Promise(function (r) { setTimeout(r, 10000) })
    const runs = await gh('GET', REPO + '/actions/runs?per_page=3')
    const list = (runs.json && runs.json.workflow_runs) || []
    if (!list.length) { p('poll', (i + 1) + '/24 暂无记录'); continue }
    const run = list[0]
    if (run.status === 'completed') { conclusion = run.conclusion; p('poll', '完成: ' + run.conclusion + ' (run_id=' + run.id + ')'); break }
    p('poll', (i + 1) + '/24: ' + run.status)
  }
  p('result', 'conclusion=' + conclusion)

  // ---- 5. 验证 state.json 是否被写回仓库（这是这次的修复重点）----
  const st = await getFile('state.json')
  if (st) {
    p('state', 'state.json 已在仓库中（状态持久化修复生效）')
    p('state', st.text.slice(0, 600))
  } else {
    p('state', 'state.json 仍不存在——需要排查 Save state 步骤')
  }

  fs.writeFileSync('D:/mywork/_deploy.txt', log.join('\n'), 'utf8')
}

main().catch(function (e) {
  log.push('[FATAL] ' + (e && e.message ? e.message : String(e)))
  try { fs.writeFileSync('D:/mywork/_deploy.txt', log.join('\n'), 'utf8') } catch (e2) {}
  console.log('[FATAL]', e.message)
})

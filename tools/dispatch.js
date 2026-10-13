#!/usr/bin/env node
/* eslint-disable */
/**
 * 手动触发云端 workflow 并等待结果（维护用）
 *
 * 为什么需要：新加的 workflow 文件光"上传成功"不算数——要确认
 *   ① GitHub 认了这个 workflow（能被 dispatch，说明 YAML 合法、在默认分支上）
 *   ② 云端 runner 真能跑成功（而不是像上次 raw 黑洞那样卡住/失败）
 * 这个脚本把"触发 → 等结果 → 打印结论"一次做完。
 *
 * 用法：
 *   GH_TOKEN=ghp_xxx node tools/dispatch.js heartbeat.yml --input force=true
 *   GH_TOKEN=ghp_xxx node tools/dispatch.js fng.yml
 *   GH_TOKEN=ghp_xxx node tools/dispatch.js fng.yml --no-wait     # 只触发不等结果
 *
 * 可选环境变量：GH_OWNER / GH_REPO / GH_BRANCH（默认 zyh19870426 / stock-alert / main）
 */

const TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN
const OWNER = process.env.GH_OWNER || 'zyh19870426'
const REPO = process.env.GH_REPO || 'stock-alert'
const BRANCH = process.env.GH_BRANCH || 'main'
const API = 'https://api.github.com/repos/' + OWNER + '/' + REPO

if (!TOKEN) { console.error('缺少 GH_TOKEN 环境变量'); process.exit(1) }

const argv = process.argv.slice(2)
const file = argv[0]
if (!file) { console.error('用法：node tools/dispatch.js <workflow 文件名> [--input k=v]... [--no-wait]'); process.exit(1) }
const noWait = argv.includes('--no-wait')
const inputs = {}
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === '--input' && argv[i + 1]) {
    const kv = argv[i + 1].split('=')
    inputs[kv[0]] = kv.slice(1).join('=')
    i++
  }
}

const HEADERS = {
  'Authorization': 'Bearer ' + TOKEN,
  'Accept': 'application/vnd.github+json',
  'User-Agent': 'stock-alert-dispatcher',
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
  return { status: res.status, ok: res.ok, json: json }
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

async function main() {
  console.log('触发 ' + file + ' @ ' + OWNER + '/' + REPO + ':' + BRANCH +
    (Object.keys(inputs).length ? '  参数 ' + JSON.stringify(inputs) : ''))

  const startedAt = Date.now()
  const dis = await api('POST', API + '/actions/workflows/' + file + '/dispatches', { ref: BRANCH, inputs: inputs })
  if (dis.status !== 204 && !dis.ok) {
    console.error('触发失败 HTTP ' + dis.status + ' ' + JSON.stringify(dis.json).slice(0, 300))
    console.error('提示：404 通常是「workflow 文件不在默认分支」或「YAML 不合法」')
    process.exit(1)
  }
  console.log('✅ 已接受触发（HTTP ' + dis.status + '）')
  if (noWait) return

  // 等这次运行出现
  let run = null
  for (let i = 0; i < 20; i++) {
    await sleep(3000)
    const r = await api('GET', API + '/actions/workflows/' + file + '/runs?per_page=10')
    if (!r.ok) { console.error('查询运行记录失败 HTTP ' + r.status); process.exit(1) }
    const list = (r.json && r.json.workflow_runs) || []
    run = list.find(x => new Date(x.created_at).getTime() >= startedAt - 15000)
    if (run) break
  }
  if (!run) { console.error('❌ 没等到这次运行出现'); process.exit(1) }
  console.log('  运行 #' + run.run_number + ' id=' + run.id + ' ' + run.html_url)

  // 等它跑完
  for (let i = 0; i < 90; i++) {
    if (run.status === 'completed') break
    await sleep(6000)
    const r = await api('GET', API + '/actions/runs/' + run.id)
    if (r.ok) run = r.json
    process.stdout.write('  ' + run.status + (run.conclusion ? '/' + run.conclusion : '') + '\r')
  }
  console.log('')
  if (run.status !== 'completed') { console.error('❌ 超时未跑完，去页面上看：' + run.html_url); process.exit(1) }
  console.log((run.conclusion === 'success' ? '✅' : '❌') + ' 结论：' + run.conclusion + '   用时 ' +
    Math.round((new Date(run.updated_at) - new Date(run.run_started_at)) / 1000) + 's')
  console.log('   详情：' + run.html_url)

  // 失败时把日志尾部拉出来，省得再点网页
  if (run.conclusion !== 'success') {
    const jobs = await api('GET', API + '/actions/runs/' + run.id + '/jobs')
    const job = jobs.json && jobs.json.jobs && jobs.json.jobs[0]
    if (job) {
      console.log('   失败步骤：' + (job.steps || []).filter(s => s.conclusion === 'failure').map(s => s.name).join('、'))
    }
    process.exit(1)
  }
}

main().catch(e => { console.error('出错: ' + (e && e.message ? e.message : e)); process.exit(1) })

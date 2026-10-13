#!/usr/bin/env node
/* 手动触发一次 rotation-daily 工作流，并轮询到结束打印结论（token 只从环境变量读）
   用法：GH_TOKEN=ghp_xxx node tools/dispatch-rotation.js [workflow.yml] [轮询次数]
   例：GH_TOKEN=ghp_xxx node tools/dispatch-rotation.js flow.yml 40（等待约 4 分钟）
   ⚠️ 注意：非交易日脚本会被交易日闸门拦截 → 结论 success 但日志里是「非交易日…跳过，不写盘」，
      这是**预期行为**，本脚本只用来验证 workflow 能启动、依赖能 require、脚本能跑到底。 */
const TOKEN = process.env.GH_TOKEN
if (!TOKEN) { console.error('缺少 GH_TOKEN'); process.exit(1) }
const API = 'https://api.github.com/repos/zyh19870426/stock-alert'
const H = {
  'Authorization': 'Bearer ' + TOKEN,
  'Accept': 'application/vnd.github+json',
  'User-Agent': 'rotation-dispatch',
  'X-GitHub-Api-Version': '2022-11-28'
}
const sleep = ms => new Promise(r => setTimeout(r, ms))
async function api (m, u, body) {
  const res = await fetch(u, { method: m, headers: H, body: body ? JSON.stringify(body) : undefined })
  let j = null
  try { j = await res.json() } catch (e) { }
  return { status: res.status, json: j }
}
;(async () => {
  const wf = process.argv[2] || 'rotation.yml'
  const POLL = Number(process.argv[3] || 40)      /* 轮询次数（每次 6 秒） */
  const d = await api('POST', API + '/actions/workflows/' + wf + '/dispatches', { ref: 'main' })
  console.log('触发 dispatch ' + wf + '：HTTP ' + d.status + (d.status === 204 ? '（成功）' : ' ' + JSON.stringify(d.json)))
  if (d.status !== 204) process.exit(1)

  let runId = 0
  for (let i = 0; i < POLL; i++) {
    await sleep(6000)
    const r = await api('GET', API + '/actions/workflows/' + wf + '/runs?per_page=1')
    const runs = (r.json && r.json.workflow_runs) || []
    if (runs.length) {
      const r0 = runs[0]
      if (r0.status === 'completed') { runId = r0.id; console.log('结论：' + r0.conclusion + '  （run ' + r0.id + '）'); break }
      console.log('运行中… status=' + r0.status)
    }
  }
  if (runId) {
    await sleep(1500)
    const j = await api('GET', API + '/actions/runs/' + runId + '/jobs')
    const jobs = (j.json && j.json.jobs) || []
    for (const jb of jobs) {
      console.log('--- job: ' + jb.name + ' → ' + jb.conclusion)
      for (const st of (jb.steps || [])) {
        if (/Update sector rotation|Commit archive|Set up/.test(st.name)) console.log('    ' + st.name + ' → ' + st.conclusion)
      }
    }
  }
})()

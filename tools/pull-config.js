// 拉取远端 config.json 与 state.json 的当前内容
// 凭据只从环境变量读，不落盘：GH_TOKEN=ghp_xxx node tools/pull-config.js
const fs = require('fs')
const TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN
const API = 'https://api.github.com'
const REPO = '/repos/zyhaaad/stock-alert'
if (!TOKEN) { console.error('缺少 GH_TOKEN 环境变量'); process.exit(1) }
async function get(p) {
  const res = await fetch(API + REPO + '/contents/' + p, {
    headers: { Authorization: 'Bearer ' + TOKEN, Accept: 'application/vnd.github+json', 'User-Agent': 'pull' }
  })
  const j = await res.json()
  if (!j.content) return { path: p, error: 'HTTP ' + res.status + ' ' + JSON.stringify(j).slice(0, 200) }
  return { path: p, sha: j.sha, text: Buffer.from(j.content, 'base64').toString('utf8') }
}
async function main() {
  const out = []
  for (const f of ['config.json', 'state.json']) {
    const r = await get(f)
    out.push('=== ' + r.path + ' (sha ' + (r.sha || '-') + ') ===')
    out.push(r.text || r.error)
    out.push('')
  }
  // 仓库文件列表
  const ls = await fetch(API + REPO + '/contents/', { headers: { Authorization: 'Bearer ' + TOKEN, 'User-Agent': 'pull' } })
  const lj = await ls.json()
  out.push('=== repo files ===')
  out.push((Array.isArray(lj) ? lj.map(function (x) { return x.name }) : lj).join(', '))
  fs.writeFileSync('D:/mywork/_remote.txt', out.join('\n'), 'utf8')
}
main().catch(function (e) { fs.writeFileSync('D:/mywork/_remote.txt', 'FATAL ' + e.message, 'utf8') })

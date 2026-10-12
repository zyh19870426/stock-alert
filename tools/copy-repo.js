#!/usr/bin/env node
/* eslint-disable */
/**
 * copy-repo.js · 云端引擎仓「搬家器」（旧仓 → 新仓）
 * ------------------------------------------------------------
 * 用途：把旧 GitHub 仓库（zyhaaad/stock-alert）的**全部内容**搬到新账号下的同名仓库。
 *
 * ★★ 安全基线（用户 2026-10-12 明确要求）★★
 *   1. **旧仓库全程只读**：本脚本对旧仓只发 GET（tree + raw），**绝不 PUT/POST/PATCH/DELETE**，
 *      不改文件、不建分支、不动 workflow、不删任何东西。旧仓库保持原样可回退。
 *   2. 新仓写入走 **Git Data API 一次性提交**（blob → tree → commit → ref），
 *      不依赖 git https 传输（本沙盒 github.com 的 git 通道被代理拦为 502，api.github.com 通畅）。
 *   3. token 只从环境变量读，**不落盘、不进 .git/config、不拼进任何 URL**。
 *
 * 文件取值规则（`--apply` 前会打印明细表，可逐条核对）：
 *   - `.js` / `.yml` / `.yaml`  → **本地 `stock-alert-cloud/` 优先**（本地是最新，含积压未上传的改动）
 *   - `.json` / `.md` / 其它    → **旧仓优先**（存档 / 配置 / state 是运行期数据，
 *                                必须原样搬过来，否则用户手机改过的监控清单会丢）
 *   - 两边都有的重复项取上面规则；只有一边有的直接取那一边（并集）
 *
 * 用法（默认 dry-run，只打印不动手）：
 *   GH_TOKEN=xxx node tools/copy-repo.js --dst-owner=<新用户名>
 *   GH_TOKEN=xxx node tools/copy-repo.js --dst-owner=<新用户名> --apply
 *   GH_TOKEN=xxx node tools/copy-repo.js --dst-owner=<新用户名> --apply --create-repo
 *
 * 参数：
 *   --src-owner  默认 zyhaaad
 *   --src-repo   默认 stock-alert
 *   --dst-owner  必填（新账号用户名）
 *   --dst-repo   默认 stock-alert（与源同名）
 *   --src-branch / --dst-branch  默认 main
 *   --create-repo 新仓不存在时用 API 创建（**public**，控制台要匿名读；不 auto_init，
 *                 这样可以直接建一个无父提交的初始提交）
 *   --apply      真正执行（不带就是 dry-run）
 *   --verify     只做校验：比对旧仓与新仓的 blob 数量/大小
 */

const fs = require('fs')
const path = require('path')
const HERE = __dirname
const SRC_DIR = path.join(HERE, '..')

const TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN

/* ---------- 参数 ---------- */
const args = {}
for (const a of process.argv.slice(2)) {
  const m = /^--([^=]+)(?:=(.*))?$/.exec(a)
  if (m) args[m[1]] = m[2] === undefined ? true : m[2]
}
const SRC_OWNER = args['src-owner'] || 'zyhaaad'
const SRC_REPO = args['src-repo'] || 'stock-alert'
const DST_OWNER = args['dst-owner']
const DST_REPO = args['dst-repo'] || 'stock-alert'
const SRC_BRANCH = args['src-branch'] || 'main'
const DST_BRANCH = args['dst-branch'] || 'main'
const APPLY = !!args.apply
const VERIFY = !!args.verify
const CREATE_REPO = !!args['create-repo']

if (!DST_OWNER) {
  console.error('缺少 --dst-owner=<新账号用户名>')
  process.exit(1)
}
if (!TOKEN && (APPLY || VERIFY || CREATE_REPO)) {
  console.error('缺少 GH_TOKEN 环境变量（只从环境变量读，不落盘）')
  process.exit(1)
}
/* dry-run 也要读旧仓（GET），但读公开仓库不需要 token */

const H = () => {
  const h = {
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'stock-alert-repo-mover',
    'X-GitHub-Api-Version': '2022-11-28'
  }
  // 无 token 时**不带 Authorization**（旧仓是公开库，dry-run 的只读 GET 不需要凭据；
  // 带一个 `Bearer undefined` 反而会被判 401 Bad credentials）
  if (TOKEN) h['Authorization'] = 'Bearer ' + TOKEN
  return h
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: Object.assign({ 'Content-Type': 'application/json' }, H()),
    body: body ? JSON.stringify(body) : undefined
  })
  let json = null
  try { json = await res.json() } catch (e) {}
  return { status: res.status, ok: res.ok, json, scopes: res.headers.get('x-oauth-scopes') }
}

/* ---------- 1. 读旧仓清单（只读） ---------- */
async function srcTree() {
  const r = await api('GET', 'https://api.github.com/repos/' + SRC_OWNER + '/' + SRC_REPO +
    '/git/trees/' + SRC_BRANCH + '?recursive=1')
  if (!r.ok) throw new Error('读取旧仓 tree 失败 HTTP ' + r.status + ' ' + JSON.stringify(r.json).slice(0, 200))
  return r.json.tree.filter(x => x.type === 'blob')
}

async function srcBlob(sha) {
  const r = await api('GET', 'https://api.github.com/repos/' + SRC_OWNER + '/' + SRC_REPO + '/git/blobs/' + sha)
  if (!r.ok) throw new Error('读旧仓 blob 失败 HTTP ' + r.status)
  return r.json.encoding === 'base64'
    ? Buffer.from(String(r.json.content).replace(/\n/g, ''), 'base64')
    : Buffer.from(r.json.content, 'utf8')
}

/* ---------- 2. 本地文件清单 ---------- */
const SKIP_RE = /(^|[\\/])(node_modules|\.git)([\\/]|$)/
const SKIP_NAME = /^_[\s\S]*\.txt$|\.log$|^\.env$/

function localFiles() {
  const out = {}
  const walk = (dir, rel) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
      const r = rel ? rel + '/' + ent.name : ent.name
      if (SKIP_RE.test(r)) continue
      const abs = path.join(dir, ent.name)
      if (ent.isDirectory()) walk(abs, r)
      else if (!SKIP_NAME.test(ent.name)) out[r] = abs
    }
  }
  walk(SRC_DIR, '')
  return out
}

const isCode = p => /\.(js|mjs|cjs|yml|yaml)$/i.test(p)

/* ---------- 3. 决定每个路径取哪边 ---------- */
async function buildPlan() {
  const remote = await srcTree()
  const local = localFiles()
  const paths = Array.from(new Set(remote.map(x => x.path).concat(Object.keys(local)))).sort()

  const plan = []
  for (const p of paths) {
    const r = remote.find(x => x.path === p)
    const l = local[p]
    let from, why
    if (isCode(p)) {
      from = l ? 'local' : 'remote'
      why = l ? (r ? '代码：本地最新优先' : '代码：仅本地有（积压未上传）') : '代码：仅旧仓有'
    } else {
      from = r ? 'remote' : 'local'
      why = r ? (l ? '数据：旧仓存档优先（运行期数据，不能丢）' : '数据：仅旧仓有') : '数据：仅本地有'
    }
    plan.push({ path: p, from, why, remoteSize: r ? r.size : null, remoteSha: r ? r.sha : null, localAbs: l || null })
  }
  return plan
}

/* ---------- 4. 取内容 ---------- */
async function contentOf(item) {
  if (item.from === 'local') return fs.readFileSync(item.localAbs)
  return await srcBlob(item.remoteSha)
}

/* ---------- 5. 写入新仓（一次性提交） ---------- */
const dstAPI = () => 'https://api.github.com/repos/' + DST_OWNER + '/' + DST_REPO

async function ensureRepo() {
  let state
  const dst = await api('GET', dstAPI())
  if (dst.ok) {
    state = '已存在'
  } else if (dst.status === 404) {
    if (!CREATE_REPO) throw new Error('新仓 ' + DST_OWNER + '/' + DST_REPO + ' 不存在（加 --create-repo 让脚本建）')
    const c = await api('POST', 'https://api.github.com/user/repos', {
      name: DST_REPO, private: false, auto_init: false,
      description: 'A股监控云端引擎（迁移自 ' + SRC_OWNER + '/' + SRC_REPO + '）'
    })
    if (!c.ok) throw new Error('创建新仓失败 HTTP ' + c.status + ' ' + JSON.stringify(c.json).slice(0, 200))
    state = '已新建（public）'
  } else {
    throw new Error('查询新仓失败 HTTP ' + dst.status)
  }

  // ★ 空仓陷阱（实测踩过）：Git Data API 在**完全没有提交**的仓库上建 blob 会返回
  //   409 {message:"Git Repository is empty."}。必须先落一次提交。
  //   用 Contents API 写一个占位文件来初始化 —— 之后正式提交的 tree 不带 base_tree，
  //   是「只含计划内路径」的完整树，占位文件会自动从新树里消失，不会残留。
  const ref = await api('GET', dstAPI() + '/git/ref/heads/' + DST_BRANCH)
  if (!ref.ok) {
    const b = await api('PUT', dstAPI() + '/contents/.migration-bootstrap', {
      message: 'init: bootstrap（迁移占位，随后由正式提交覆盖）',
      content: Buffer.from('bootstrap\n').toString('base64')
    })
    if (!b.ok) throw new Error('初始化空仓失败 HTTP ' + b.status + ' ' + JSON.stringify(b.json).slice(0, 200))
    if (!b.json || !b.json.commit) throw new Error('初始化空仓失败：返回无 commit')
    const info = await api('GET', dstAPI())
    const defBranch = (info.json && info.json.default_branch) || DST_BRANCH
    if (defBranch !== DST_BRANCH) {
      // 账号默认分支不是 main（老账号可能是 master）⇒ 建 main、改默认、删旧分支
      const mk = await api('POST', dstAPI() + '/git/refs', { ref: 'refs/heads/' + DST_BRANCH, sha: b.json.commit.sha })
      if (!mk.ok) throw new Error('建 ' + DST_BRANCH + ' 分支失败 HTTP ' + mk.status + ' ' + JSON.stringify(mk.json).slice(0, 200))
      await api('PATCH', dstAPI(), { default_branch: DST_BRANCH })
      await api('DELETE', dstAPI() + '/git/refs/heads/' + defBranch)
      state += '，已初始化（默认分支 ' + defBranch + ' → ' + DST_BRANCH + '）'
    } else {
      state += '，已初始化首个提交'
    }
  }
  return state
}

async function pushAll(plan) {
  const dstAPI = 'https://api.github.com/repos/' + DST_OWNER + '/' + DST_REPO
  const tree = []
  let i = 0
  for (const item of plan) {
    i++
    const buf = await contentOf(item)
    const blob = await api('POST', dstAPI + '/git/blobs', { content: buf.toString('base64'), encoding: 'base64' })
    if (!blob.ok) throw new Error('建 blob 失败 ' + item.path + ' HTTP ' + blob.status + ' ' + JSON.stringify(blob.json).slice(0, 160))
    tree.push({ path: item.path, mode: '100644', type: 'blob', sha: blob.json.sha })
    process.stdout.write('\r  写入中 ' + i + '/' + plan.length + '  ' + item.path.slice(0, 48).padEnd(50))
  }
  process.stdout.write('\r' + ' '.repeat(80) + '\r')
  console.log('  已建 ' + tree.length + ' 个 blob')

  const t = await api('POST', dstAPI + '/git/trees', { tree })
  if (!t.ok) throw new Error('建 tree 失败 HTTP ' + t.status + ' ' + JSON.stringify(t.json).slice(0, 200))

  // 新仓可能有初始提交（auto_init）→ 取其父；空仓则建无父提交
  const ref = await api('GET', dstAPI + '/git/ref/heads/' + DST_BRANCH)
  const parents = ref.ok && ref.json && ref.json.object ? [ref.json.object.sha] : []

  const c = await api('POST', dstAPI + '/git/commits', {
    message: 'init: 迁移自 ' + SRC_OWNER + '/' + SRC_REPO + '（' + tree.length + ' 个文件，存档原样搬运）',
    tree: t.json.sha, parents
  })
  if (!c.ok) throw new Error('建提交失败 HTTP ' + c.status + ' ' + JSON.stringify(c.json).slice(0, 200))

  if (parents.length) {
    const u = await api('PATCH', dstAPI + '/git/refs/heads/' + DST_BRANCH, { sha: c.json.sha, force: false })
    if (!u.ok) throw new Error('更新分支失败 HTTP ' + u.status)
  } else {
    const u = await api('POST', dstAPI + '/git/refs', { ref: 'refs/heads/' + DST_BRANCH, sha: c.json.sha })
    if (!u.ok) throw new Error('建分支失败 HTTP ' + u.status + ' ' + JSON.stringify(u.json).slice(0, 200))
  }
  return c.json.sha
}

/* ---------- 6. 校验 ---------- */
async function verify(plan) {
  const r = await api('GET', 'https://api.github.com/repos/' + DST_OWNER + '/' + DST_REPO +
    '/git/trees/' + DST_BRANCH + '?recursive=1')
  if (!r.ok) throw new Error('读新仓 tree 失败 HTTP ' + r.status)
  const dst = r.json.tree.filter(x => x.type === 'blob')
  const miss = plan.filter(p => !dst.find(x => x.path === p.path))
  console.log('  新仓文件数 ' + dst.length + ' / 计划 ' + plan.length + (miss.length ? '  ❌ 缺 ' + miss.length : '  ✅ 齐'))
  // 抽查 raw 可读性（控制台就是匿名读 raw，这条必须通）
  for (const probe of ['fng-history.json', 'rotation.json', 'flow-history.json']) {
    if (!plan.find(p => p.path === probe)) continue
    const url = 'https://raw.githubusercontent.com/' + DST_OWNER + '/' + DST_REPO + '/' + DST_BRANCH + '/' + probe
    const res = await fetch(url, { headers: { 'User-Agent': 'probe' } })
    console.log('  raw ' + probe + ' → HTTP ' + res.status + (res.ok ? ' ✅ 匿名可读' : ' ❌'))
  }
  return miss.length === 0
}

/* ---------- main ---------- */
;(async () => {
  console.log('源仓（只读）：' + SRC_OWNER + '/' + SRC_REPO + '@' + SRC_BRANCH)
  console.log('目标仓：      ' + DST_OWNER + '/' + DST_REPO + '@' + DST_BRANCH)
  console.log('模式：        ' + (VERIFY ? '仅校验' : APPLY ? '★ 执行写入' : 'dry-run（只打印，不加 --apply 不会写入）'))
  console.log('')

  const plan = await buildPlan()
  console.log('================ 文件取值计划（' + plan.length + ' 项）================')
  for (const p of plan) {
    const side = p.from === 'local' ? '本地' : '旧仓'
    const sz = p.remoteSize === null ? '   —    ' : (p.remoteSize / 1024).toFixed(1).padStart(7) + 'K'
    console.log('  ' + side + '  ' + sz + '  ' + p.path.padEnd(46) + ' ' + p.why)
  }
  const nLocal = plan.filter(p => p.from === 'local').length
  console.log('\n  取本地 ' + nLocal + ' 项 / 取旧仓 ' + (plan.length - nLocal) + ' 项')

  if (!APPLY && !VERIFY) {
    console.log('\n（dry-run 结束。确认无误后加 --apply 执行）')
    return
  }

  if (VERIFY) {
    const ok = await verify(plan)
    process.exit(ok ? 0 : 1)
  }

  console.log('')
  console.log('新仓状态：' + await ensureRepo())
  const sha = await pushAll(plan)
  console.log('  提交：' + String(sha).slice(0, 7))
  console.log('')
  await verify(plan)
  console.log('\n★ 别忘了两步手工操作：')
  console.log('  1) 新仓 Settings → Secrets and variables → Actions，补 SENDKEY / PUSHPLUS_TOKEN / WECOM_WEBHOOK')
  console.log('  2) 新仓 Actions 标签页点一次启用（新建仓库的定时任务默认不跑）')
})().catch(e => {
  console.error('\n出错：' + (e && e.message ? e.message : e))
  process.exit(1)
})

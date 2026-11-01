# -*- coding: utf-8 -*-
"""
9D · 系统审计（2026-11-01）
------------------------------------------------------------
三件事，都是「先量再改」，不凭直觉调参：

【A】主战场口径对齐
  现状两个定义并存：
    arena      = 持仓第一大行业（页面文案「押的 XX 板块」用它）
    main_sector= 60 日相关性最高的板块（抱团分的健康门 gate 用它）
  两者不同时，gate 判的是另一个板块。这里量化：不一致多少只、
  改用 arena 后抱团分/排名怎么变。

【B】「好价位」分档
  现规则 pos60 <= 0.40 是拍的。这里把 pos60 分 5 档、并对比
  pos250（250 日区间分位），量各档之后 20/60 日收益与胜率，
  训练段 <=2024-12-31 / 验证段 >=2025-01-01 分开看。
  额外对照：只按低价位 vs 低价位+趋势向上（现 B 信号三条件），
  验证「趋势过滤」到底有没有加分。

【C】打分权重扰动
  四维权重从未验证。对权重做 ±20% / ±40% 随机扰动各 500 次，
  看 Top3 名单重合率与名次波动。若 Top3 频繁换人 -> 排序不稳，
  页面不该把第 3 名当「优」。

输出：控制台报告（不写盘，纯审计）
"""
import json, os, math, random, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
TRAIN_END = '2024-12-31'


def load(n):
    return json.load(open(os.path.join(HERE, n), encoding='utf-8'))


def ma_at(vs, n, i):
    if i < n - 1:
        return None
    return sum(vs[i - n + 1:i + 1]) / n


def pct_rank(vals):
    """值 -> 0..1 分位"""
    idx = [i for i, v in enumerate(vals) if v is not None]
    order = sorted(idx, key=lambda i: vals[i])
    out = {}
    n = max(len(order) - 1, 1)
    for k, i in enumerate(order):
        out[i] = k / n
    return out


def med(xs):
    return statistics.median(xs) if xs else None


def win(xs):
    return (sum(1 for x in xs if x > 0) / float(len(xs))) if xs else None


# ============ A. 主战场口径 ============
def part_a():
    print('\n' + '=' * 74)
    print('【A】主战场口径：arena(持仓最大行业) vs main_sector(相关性最高板块)')
    print('=' * 74)
    d = load('../data/data.json')
    secs_raw = load('sectors.json')
    sec_k = {s['label']: s['k'] for s in secs_raw}
    all_f = (d.get('picks') or []) + (d.get('shortlist') or [])

    def rets(series):
        return {series[i][0]: series[i][1] / series[i - 1][1] - 1
                for i in range(1, len(series)) if series[i - 1][1]}

    def corr60(fr, sr):
        ks = sorted(set(fr) & set(sr))[-60:]
        if len(ks) < 30:
            return None
        x = [fr[k] for k in ks]; y = [sr[k] for k in ks]
        n = len(x); mx = sum(x) / n; my = sum(y) / n
        sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
        sxx = sum((x[i] - mx) ** 2 for i in range(n))
        syy = sum((y[i] - my) ** 2 for i in range(n))
        if sxx <= 0 or syy <= 0:
            return 0.0
        return sxy / math.sqrt(sxx * syy)

    print('%-8s %-10s %-10s %6s %6s %6s %6s %6s' % (
        '代码', '押的(arena)', '相关最高', 'corr_main', 'corr_arena', 'gate旧', 'gate新', '抱团分旧->新'))
    changed = []
    for f in all_f:
        sc = (f.get('sector_corr') or [])
        main = sc[0]['label'] if sc else ''
        arena = f.get('arena') or main
        fr = rets(f['series'])
        c_main = sc[0]['corr'] if sc else None
        c_arena = None
        if arena in sec_k:
            sr = rets(sec_k[arena])
            c_arena = corr60(fr, sr)
        # gate：现版用 main 板块 healthy；新版用 arena 板块 healthy（拿不到就回落 main）
        g_old = f.get('herd_gate', 1.0)
        g_new = g_old
        if arena in sec_k:
            # 用 arena ETF 最近 20 日均线判健康
            vs = [x[1] for x in sec_k[arena]]
            m20 = sum(vs[-20:]) / 20
            m20p = sum(vs[-25:-5]) / 20
            g_new = 1.0 if (vs[-1] > m20 and m20 > m20p) else 0.5
        # 抱团分：动量部分不变(hold_mom)，相关强度改用 corr_arena；gate 用新版
        hold_mom = f.get('hold_mom', 0.5)
        s_old = f.get('s_herd', 0)
        strength = max(0.0, min(1.0, ((c_arena if c_arena is not None else (c_main or 0)) - 0.55) / 0.40))
        s_new = round(30 * (0.60 * hold_mom + 0.40 * strength) * g_new, 2)
        flag = '' if arena == main else ' <<不一致'
        print('%-8s %-10s %-10s %6s %6s %6.1f %6.1f  %5.1f -> %5.1f%s' % (
            f['code'], arena, main,
            ('%.3f' % c_main) if c_main is not None else '  n/a',
            ('%.3f' % c_arena) if c_arena is not None else '  n/a',
            g_old, g_new, s_old, s_new, flag))
        if abs(s_new - s_old) >= 0.5:
            changed.append((f['code'], s_old, s_new, g_old, g_new, arena, main))
    print('\n不一致: %d/%d 只；抱团分变化 >=0.5 分: %d 只' % (
        sum(1 for f in all_f if (f.get('arena') or '') != ((f.get('sector_corr') or [{}])[0].get('label') or '')),
        len(all_f), len(changed)))
    for c in changed:
        print('   %s 抱团分 %.1f -> %.1f （gate %.1f->%.1f，押 %s / 旧判 %s）' % c)


# ============ B. 好价位分档 ============
def part_b():
    print('\n' + '=' * 74)
    print('【B】「好价位」分档：净值的相对位置 -> 之后 20/60 日收益')
    print('=' * 74)
    d = load('../data/data.json')
    all_f = (d.get('picks') or []) + (d.get('shortlist') or [])
    STEP = 5  # 每 5 个交易日采样一次，降低重叠

    def pos_at(vs, i, n):
        if i < n:
            return None
        win = vs[i - n + 1:i + 1]
        lo, hi = min(win), max(win)
        return (vs[i] - lo) / (hi - lo) if hi > lo else 0.5

    for nwin, tag in ((60, 'pos60（现规则）'), (250, 'pos250（长区间）')):
        buckets = [(0.0, 0.2, 'A 极低 <20%'), (0.2, 0.4, 'B 偏低 20-40%'),
                   (0.4, 0.6, 'C 中间 40-60%'), (0.6, 0.8, 'D 偏高 60-80%'),
                   (0.8, 1.01, 'E 高位 >80%')]
        stats_t = {b[2]: [] for b in buckets}
        stats_v = {b[2]: [] for b in buckets}
        for f in all_f:
            s = f['series']
            ds = [x[0] for x in s]; vs = [x[1] for x in s]
            for i in range(nwin, len(vs) - 60, STEP):
                p = pos_at(vs, i, nwin)
                if p is None:
                    continue
                fwd20 = vs[i + 20] / vs[i] - 1 if i + 20 < len(vs) else None
                fwd60 = vs[i + 60] / vs[i] - 1 if i + 60 < len(vs) else None
                if fwd60 is None:
                    continue
                for lo, hi, name in buckets:
                    if lo <= p < hi:
                        rec = (fwd20, fwd60)
                        (stats_t if ds[i] <= TRAIN_END else stats_v)[name].append(rec)
                        break
        print('\n--- %s ---' % tag)
        print('%-14s %18s %18s' % ('档位', '训练(<=2024) n/20日/60日', '验证(>=2025) n/20日/60日'))
        for _, _, name in buckets:
            t = stats_t[name]; v = stats_v[name]
            t20 = med([x[0] for x in t if x[0] is not None])
            t60 = med([x[1] for x in t])
            v20 = med([x[0] for x in v if x[0] is not None])
            v60 = med([x[1] for x in v])
            print('%-14s %6d %7s %7s   %6d %7s %7s' % (
                name, len(t),
                ('%+.2f%%' % (t20 * 100)) if t20 is not None else '   -',
                ('%+.2f%%' % (t60 * 100)) if t60 is not None else '   -',
                len(v),
                ('%+.2f%%' % (v20 * 100)) if v20 is not None else '   -',
                ('%+.2f%%' % (v60 * 100)) if v60 is not None else '   -'))

    # 趋势过滤到底有没有用：低位 + 趋势向上 vs 光低价位
    print('\n--- 「低价位」是否需要「趋势向上」这个过滤 ---')
    for cond_name, use_trend in (('仅 pos60<=0.40', False), ('pos60<=0.40 且趋势向上', True)):
        tr, va = [], []
        for f in all_f:
            s = f['series']; ds = [x[0] for x in s]; vs = [x[1] for x in s]
            for i in range(60, len(vs) - 60, STEP):
                p = pos_at(vs, i, 60)
                if p is None or p > 0.40:
                    continue
                if use_trend:
                    m20 = ma_at(vs, 20, i); m60 = ma_at(vs, 60, i)
                    m60p = ma_at(vs, 60, i - 20)
                    if not (m20 and m60 and m60p and m20 > m60 and m60 > m60p):
                        continue
                fwd60 = vs[i + 60] / vs[i] - 1
                (tr if ds[i] <= TRAIN_END else va).append(fwd60)
        print('%-40s 训练 n=%4d 中位 %s 胜率 %s | 验证 n=%4d 中位 %s 胜率 %s' % (
            cond_name, len(tr),
            ('%+.2f%%' % (med(tr) * 100)) if tr else '-',
            ('%.1f%%' % (win(tr) * 100)) if tr else '-',
            len(va),
            ('%+.2f%%' % (med(va) * 100)) if va else '-',
            ('%.1f%%' % (win(va) * 100)) if va else '-'))


# ============ C. 权重扰动 ============
def part_c():
    print('\n' + '=' * 74)
    print('【C】打分权重扰动：Top3 名单稳不稳')
    print('=' * 74)
    sc = load('scored.json')
    pool = sc['scored']
    print('打分池: %d 只' % len(pool))

    def score(f, w):
        m = f['m']
        # 收益 25：三周期百分位（用池内重算）
        return None  # 占位，实际在下面统一算

    n = len(pool)
    # 预取原始特征
    feats = []
    for f in pool:
        m = f['m']
        feats.append(dict(
            code=f['code'], name=f['name'],
            r3m=m.get('r3m'), r1m=m.get('r1m'), r1y=m.get('r1y'),
            vol=m.get('vol'), mdd=m.get('mdd'), sharpe=m.get('sharpe'),
            gap=m.get('ma20_gap'), above20=m.get('above20'),
            ma20=m.get('ma20'), ma60=m.get('ma60'), ma20up=m.get('ma20_up'),
            main_corr=f.get('main_corr', 0), main_mom=f.get('main_mom', 0),
            hold_mom=f.get('hold_mom'), gate=f.get('herd_gate', 1.0),
        ))
    # 百分位（池内）
    def pct(key):
        vals = [f[key] for f in feats]
        idx = [i for i, v in enumerate(vals) if v is not None]
        order = sorted(idx, key=lambda i: vals[i])
        out = {}
        nn = max(len(order) - 1, 1)
        for k, i in enumerate(order):
            out[i] = k / nn
        return out
    P3, P1, PY = pct('r3m'), pct('r1m'), pct('r1y')

    def calc(w_ret, w_trend, w_herd, w_risk):
        """w_ret=(a,b,c) 收益三周期；w_trend=(t1,t2,t3,t4) 趋势四项；w_herd=(h1,h2)；w_risk=(r1,r2,r3)"""
        out = []
        for i, f in enumerate(feats):
            s_ret = (w_ret[0] * P3.get(i, 0) + w_ret[1] * P1.get(i, 0) + w_ret[2] * PY.get(i, 0)) * 25
            t = 0.0
            t += w_trend[0] if f['above20'] else 0
            t += w_trend[1] if (f['ma20'] and f['ma60'] and f['ma20'] > f['ma60']) else 0
            t += w_trend[2] if f['ma20up'] else 0
            g = f['gap']
            t += w_trend[3] if (g is not None and 0 <= g <= 0.04) else (
                w_trend[3] * 0.6 if (g is not None and g <= 0.08) else 0)
            strength = max(0.0, min(1.0, (f['main_corr'] - 0.55) / 0.40))
            hm = f['hold_mom'] if f['hold_mom'] is not None else f['main_mom']
            base = w_herd[0] * hm + w_herd[1] * strength
            s_herd = 30 * base * f['gate']
            vol_s = max(0.0, min(1.0, (0.60 - (f['vol'] or 0)) / 0.45))
            mdd_s = max(0.0, min(1.0, (0.45 + (f['mdd'] or 0)) / 0.40))
            sh_s = max(0.0, min(1.0, ((f['sharpe'] if f['sharpe'] is not None else -1) + 0.5) / 2.0))
            s_risk = w_risk[0] * vol_s + w_risk[1] * mdd_s + w_risk[2] * sh_s
            out.append((f['code'], f['name'], round(s_ret + t + s_herd + s_risk, 2)))
        out.sort(key=lambda x: -x[2])
        return out

    BASE = calc((0.45, 0.35, 0.20), (8, 7, 5, 5), (0.60, 0.40), (7, 7, 6))
    print('\n基准 Top10:')
    for i, (c, nm, s) in enumerate(BASE[:10], 1):
        print('  %2d. %-8s %-20s %.1f' % (i, c, nm[:18], s))

    random.seed(20261101)
    for pert, label in ((0.20, '±20%'), (0.40, '±40%')):
        top1 = top3 = top10 = 0
        rank_dev = []
        base_rank = {c: i for i, (c, _, _) in enumerate(BASE)}
        N = 500
        for _ in range(N):
            def jit(x):
                return x * (1 + random.uniform(-pert, pert))
            r = (jit(0.45), jit(0.35), jit(0.20))
            t = (jit(8), jit(7), jit(5), jit(5))
            h = (jit(0.60), jit(0.40))
            k = (jit(7), jit(7), jit(6))
            ssum = sum(r); r = tuple(x / ssum * 1.0 for x in r)  # 收益权重归一
            hs = sum(h); h = (h[0] / hs, h[1] / hs)
            ts = sum(t); t = tuple(x / ts * 25 for x in t)
            ks = sum(k); kk = tuple(x / ks * 20 for x in k)
            cur = calc(r, t, h, kk)
            cur_rank = {c: i for i, (c, _, _) in enumerate(cur)}
            if cur[0][0] == BASE[0][0]:
                top1 += 1
            b3 = {c for c, _, _ in BASE[:3]}
            c3 = {c for c, _, _ in cur[:3]}
            top3 += len(b3 & c3) / 3.0
            b10 = {c for c, _, _ in BASE[:10]}
            c10 = {c for c, _, _ in cur[:10]}
            top10 += len(b10 & c10) / 10.0
            rank_dev.append(sum(abs(cur_rank[c] - base_rank[c]) for c in base_rank) / len(base_rank))
        print('\n扰动 %s（%d 次）: 第1名不变 %.0f%% | Top3 平均重合 %.0f%% | Top10 平均重合 %.0f%% | 平均名次偏移 %.1f 位' % (
            label, N, top1 / N * 100, top3 / N * 100, top10 / N * 100, statistics.mean(rank_dev)))




# ============ B2. 细档与板块过滤 ============
def part_b2():
    print('\n' + '=' * 74)
    print('【B2】pos60<=0.40 内部再分档 + 板块未破位过滤')
    print('=' * 74)
    d = load('../data/data.json')
    all_f = (d.get('picks') or []) + (d.get('shortlist') or [])
    STEP = 5

    def pos_at(vs, i, n):
        if i < n:
            return None
        w = vs[i - n + 1:i + 1]
        lo, hi = min(w), max(w)
        return (vs[i] - lo) / (hi - lo) if hi > lo else 0.5

    print('%-26s %6s %10s %8s | %6s %10s %8s' % ('条件', '训练n', '训练60日中位', '胜率', '验证n', '验证60日中位', '胜率'))
    for lo_b, hi_b, name, use_trend in (
            (0.0, 0.10, 'pos60 <10%', False),
            (0.10, 0.20, 'pos60 10-20%', False),
            (0.20, 0.40, 'pos60 20-40%', False),
            (0.0, 0.40, 'pos60<40% + 趋势向上', True),
            (0.0, 0.10, 'pos60<10% + 趋势向上', True),
            (0.10, 0.20, 'pos60 10-20% + 趋势向上', True)):
        tr, va = [], []
        for f in all_f:
            s = f['series']; ds = [x[0] for x in s]; vs = [x[1] for x in s]
            for i in range(60, len(vs) - 60, STEP):
                p = pos_at(vs, i, 60)
                if p is None or not (lo_b <= p < hi_b):
                    continue
                if use_trend:
                    m20 = ma_at(vs, 20, i); m60 = ma_at(vs, 60, i); m60p = ma_at(vs, 60, i - 20)
                    if not (m20 and m60 and m60p and m20 > m60 and m60 > m60p):
                        continue
                fwd = vs[i + 60] / vs[i] - 1
                (tr if ds[i] <= TRAIN_END else va).append(fwd)
        print('%-26s %6d %10s %8s | %6d %10s %8s' % (
            name, len(tr),
            ('%+.2f%%' % (med(tr) * 100)) if tr else '-',
            ('%.1f%%' % (win(tr) * 100)) if tr else '-',
            len(va),
            ('%+.2f%%' % (med(va) * 100)) if va else '-',
            ('%.1f%%' % (win(va) * 100)) if va else '-'))

if __name__ == '__main__':
    part_a()
    part_b()
    part_b2()
    part_c()

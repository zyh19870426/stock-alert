# -*- coding: utf-8 -*-
"""
04 · 板块相关 + 综合打分
------------------------------------------------------------
核心问题：怎么量化「踩中抱团」？
  抱团 = 资金持续涌入、涨幅在全部板块里排前列、且技术上没破位的方向。
  所以先给 39 个板块打「动量分」，再算每只基金与各板块的日收益相关性，
  取相关性最高的板块作为它的「主战场」，最后：
      抱团分 = 30 * (0.55 * 主战场动量百分位 + 0.45 * 相关性强度) * 板块健康门
  板块健康门：主战场若跌破 MA20，抱团分 × 0.5（抱团已经开始瓦解，不能算踩中）

否证条件：若某基金 相关性 < 0.6，则「主战场」只是统计噪声，
不参与抱团加分（抱团分只给 0.3 权重的基础分），避免把无关基金硬贴标签。
输出：scored.json
"""
import json, os, math, statistics

HERE = os.path.dirname(os.path.abspath(__file__))


def load(n):
    return json.load(open(os.path.join(HERE, n), encoding='utf-8'))


def rets(d):
    """{date: value} -> {date: pct}，只对相邻都有值的日子算"""
    ds = sorted(d)
    return {ds[i]: d[ds[i]] / d[ds[i - 1]] - 1 for i in range(1, len(ds)) if d[ds[i - 1]]}


def corr(x, y):
    n = len(x)
    if n < 20:
        return 0.0
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    sxx = sum((x[i] - mx) ** 2 for i in range(n))
    syy = sum((y[i] - my) ** 2 for i in range(n))
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def sector_metrics(s):
    k = s['k']
    vs = [x[1] for x in k]
    n = len(vs)

    def r(days):
        return vs[-1] / vs[-1 - days] - 1 if n > days else None

    ma20 = sum(vs[-20:]) / 20
    ma60 = sum(vs[-60:]) / 60
    ma20_prev = sum(vs[-25:-5]) / 20
    ma60_prev = sum(vs[-80:-20]) / 60
    return dict(
        label=s['label'], code=s['code'], etf=s['etf'], last=k[-1][0], close=vs[-1],
        r5=r(5), r20=r(20), r60=r(60), r120=r(120),
        ma20=ma20, ma60=ma60, above20=vs[-1] > ma20, above60=vs[-1] > ma60,
        ma20_up=ma20 > ma20_prev, ma60_up=ma60 > ma60_prev,
        dd60=vs[-1] / max(vs[-60:]) - 1,
        # ★ 2026-10-31 加：120 日高点回撤，卖点规则「极端回撤」与页面「抱团状态」都要用
        dd120=vs[-1] / max(vs[-120:]) - 1 if n >= 120 else None,
        n=n, src=s.get('src', ''),
    )


def pct_map(vals):
    """值 -> 0..1 百分位（越大越好）"""
    idx = [i for i, v in enumerate(vals) if v is not None]
    order = sorted(idx, key=lambda i: vals[i])
    out = {}
    n = max(len(order) - 1, 1)
    for k, i in enumerate(order):
        out[i] = k / n
    return out


def main():
    nav = load('nav.json')
    sec_raw = load('sectors.json')
    secs = [sector_metrics(s) for s in sec_raw]

    # ---- 板块动量分 ----
    p5 = pct_map([s['r5'] for s in secs])
    p20 = pct_map([s['r20'] for s in secs])
    p60 = pct_map([s['r60'] for s in secs])
    for i, s in enumerate(secs):
        s['mom'] = round(0.30 * p5.get(i, 0) + 0.40 * p20.get(i, 0) + 0.30 * p60.get(i, 0), 4)
        s['healthy'] = bool(s['above20'] and s['ma20_up'])
    secs.sort(key=lambda x: -x['mom'])
    print('板块动量 Top10（抱团方向候选）:')
    for s in secs[:10]:
        print('  %-12s %-16s 5d=%6.1f%% 20d=%6.1f%% 60d=%6.1f%% 动量%.3f 站上MA20=%s' % (
            s['label'], s['etf'], s['r5'] * 100, s['r20'] * 100, s['r60'] * 100, s['mom'], s['above20']))
    print('板块动量 Bottom5:')
    for s in secs[-5:]:
        print('  %-12s %-16s 5d=%6.1f%% 20d=%6.1f%% 60d=%6.1f%%' % (
            s['label'], s['etf'], s['r5'] * 100, s['r20'] * 100, s['r60'] * 100))

    sec_ret = {}
    for s in secs:
        sec_ret[s['label']] = rets({x[0]: x[1] for x in
                                    next(x for x in sec_raw if x['label'] == s['label'])['k']})

    # ---- 基金 vs 板块 相关性 ----
    COLS = 60
    for f in nav:
        fr = rets({d: v for d, v in f['series']})
        common_all = sorted(fr)[-COLS * 2:]
        picked = []
        for s in secs:
            sr = sec_ret[s['label']]
            common = [d for d in common_all if d in sr][-COLS:]
            if len(common) < 30:
                continue
            c = corr([fr[d] for d in common], [sr[d] for d in common])
            picked.append((c, s['label'], s))
        picked.sort(key=lambda x: -x[0])
        top = picked[:3]
        f['sector_corr'] = [dict(label=l, corr=round(c, 3), etf=s['etf'],
                                 mom=s['mom'], r20=s['r20'], r60=s['r60'],
                                 r5=s['r5'], above20=s['above20']) for c, l, s in top]
        f['main_sector'] = top[0][1] if top else ''
        f['main_corr'] = round(top[0][0], 3) if top else 0
        f['main_mom'] = top[0][2]['mom'] if top else 0
        f['main_healthy'] = bool(top[0][2]['above20'] and top[0][2]['ma20_up']) if top else False

    # ---- 基金综合分 ----
    ok = [f for f in nav if f['m']['above20'] and (f['scale'] or 0) >= 2.0]
    print('\n趋势+规模门槛后：%d / %d 只' % (len(ok), len(nav)))

    pool = ok if len(ok) >= 12 else nav
    m3r = pct_map([f['m']['r3m'] for f in pool])
    m1r = pct_map([f['m']['r1m'] for f in pool])
    y1r = pct_map([f['m']['r1y'] for f in pool])
    for i, f in enumerate(pool):
        m = f['m']
        # 收益 25
        f['s_ret'] = round((0.45 * m3r.get(i, 0) + 0.35 * m1r.get(i, 0) + 0.20 * y1r.get(i, 0)) * 25, 2)
        # 趋势 25
        t = 0.0
        t += 8 if m['above20'] else 0
        t += 7 if m['ma20'] > m['ma60'] else 0
        t += 5 if m['ma20_up'] else 0
        # 位置：贴着 MA20 最好，追高扣分
        g = m['ma20_gap']
        t += 5 if g is not None and 0 <= g <= 0.04 else (3 if g is not None and g <= 0.08 else 0)
        f['s_trend'] = round(t, 2)
        # 抱团 30
        strength = max(0.0, min(1.0, (f['main_corr'] - 0.55) / 0.40))
        base = 0.55 * f['main_mom'] + 0.45 * strength
        gate = 1.0 if f['main_healthy'] else 0.5
        f['s_herd'] = round(30 * base * gate, 2)
        f['herd_gate'] = gate
        # 风险 20
        vol_s = max(0.0, min(1.0, (0.60 - m['vol']) / 0.45))
        mdd_s = max(0.0, min(1.0, (0.45 + m['mdd']) / 0.40))
        sh_s = max(0.0, min(1.0, ((m['sharpe'] or -1) + 0.5) / 2.0))
        f['s_risk'] = round(7 * vol_s + 7 * mdd_s + 6 * sh_s, 2)
        f['score'] = round(f['s_ret'] + f['s_trend'] + f['s_herd'] + f['s_risk'], 1)

    pool.sort(key=lambda x: -x['score'])
    print('\n综合评分 Top15:')
    print('%-8s %-22s %5s %5s %5s %5s %6s | %-12s corr  %s' % ('代码', '名称', '收益', '趋势', '抱团', '风险', '总分', '主战场', ''))
    for f in pool[:15]:
        print('%-8s %-22s %5.1f %5.1f %5.1f %5.1f %6.1f | %-12s %.2f  1m=%5.1f%% 3m=%5.1f%%' % (
            f['code'], f['name'][:20], f['s_ret'], f['s_trend'], f['s_herd'], f['s_risk'], f['score'],
            f['main_sector'], f['main_corr'], f['m']['r1m'] * 100, f['m']['r3m'] * 100))

    out = dict(scored=pool, sectors=secs, all=nav)
    json.dump(out, open(os.path.join(HERE, 'scored.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print('\n-> scored.json')


main()

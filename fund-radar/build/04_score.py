# -*- coding: utf-8 -*-
"""
04 · 板块相关 + 综合打分（质量分口径，2026-11-01 重写）
------------------------------------------------------------
核心修正：把「质量」与「时机」彻底拆开，解决“好基金一跌到好价格就不再是好基金”的矛盾。

旧口径（矛盾根源）：
    抱团分 = 30 * base * gate，gate = 0.5 if 主战场跌破 MA20。
    这个 gate 同时压低「分数」和「推荐门槛」——基金短期回踩、板块 ETF 跌破 MA20
    就会从 1.0 变 0.5，质量分被对折、且被踢出推荐。结果是：
    “好基金跌到好价格 → 系统眼里它就不是好基金了”，自相矛盾。

新口径 —— 质量分 Q（持续性，0~100，不随短期涨跌掉档）：
    收益(长期偏置)  30：近1年 / 近3年 / 成立年化（偏长期，不押近期动量）
    基金经理        25：任职年限 + 星级 + 东财综合评分 + 抗风险/稳定性能力雷达
    行业能力        25：长期贴合其主战场（相关性强度，不乘健康门，不随板块冷热掉档）
    风险            20：低波动 + 可控回撤 + 正夏普
    ★ 全程不引入「板块当前是否跌破某条均线」这类瞬时状态。

时机（单独判断，不进质量分）：
    在 06_final 里用「慢尺（年线/主线）未断」+「近60日价位分档」判定，
    买点 = 质量基金 + 好价位 + 慢尺未断。
    —— 好基金短期回踩（板块跌破 MA20 但仍在年线上方）仍然是好基金，跌到便宜区就是买点。
输出：scored.json（字段名向下兼容：score=质量分、s_herd 留作行业能力分、herd_gate 在 06 重算为慢尺状态）
"""
import json, os, re, math, statistics

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


def parse_tenure(wt):
    """'8年又108天' -> 8.30（年）"""
    if not wt:
        return 0.0
    y = re.search(r'(\d+)年', wt)
    d = re.search(r'(\d+)天', wt)
    yy = float(y.group(1)) if y else 0.0
    yy += (float(d.group(1)) / 365.0) if d else 0.0
    return round(yy, 2)


def mgr_quality(mgr):
    """基金经理质量（持续性因子，不随短期涨跌掉档）。返回 (0~1 综合, 明细dict)。

    构成（权重）：
        任职年限 15%  ·  星级 10%  ·  东财综合评分avr 25%  ·
        抗风险 20%  ·  稳定性 15%  ·  收益率 10%  ·  经验值 5%
    理由：质量基金看的是“能不能长期稳定把钱管好”，而非某一波行情赚多少。
    所以抗风险/稳定性权重高于收益率；任职年限越长越经得起周期检验。
    """
    if not mgr:
        return 0.0, dict(tenure=0.0, star=0, avr=0.0, anti=0.0, stab=0.0,
                         retm=0.0, exp=0.0, name='', has=False)
    g = mgr[0]
    ten = min(parse_tenure(g.get('workTime', '')) / 10.0, 1.0)   # 封顶 10 年
    star = (g.get('star') or 0) / 5.0
    try:
        avr = float(g.get('avr') or 0) / 100.0
    except Exception:
        avr = 0.0
    pw = {p.get('k'): (p.get('v') or 0) for p in (g.get('power') or [])}
    anti = pw.get('抗风险', 0) / 100.0
    stab = pw.get('稳定性', 0) / 100.0
    retm = pw.get('收益率', 0) / 100.0
    exp = pw.get('经验值', 0) / 100.0
    q = (0.15 * ten + 0.10 * star + 0.25 * avr +
         0.20 * anti + 0.15 * stab + 0.10 * retm + 0.05 * exp)
    return round(q, 4), dict(tenure=round(ten * 10, 1), star=(g.get('star') or 0),
                             avr=round(avr * 100, 1), anti=round(anti * 100, 1),
                             stab=round(stab * 100, 1), retm=round(retm * 100, 1),
                             exp=round(exp * 100, 1), name=g.get('name', ''), has=True)


def ann_since(m):
    """成立来年化收益（用于长期收益分，避免只押近期动量）"""
    n, s = m.get('n'), m.get('since')
    if n and n > 250 and s is not None:
        try:
            return (1 + s) ** (250.0 / n) - 1
        except Exception:
            return None
    return None


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

    # ---- 候选池：流动性门槛（规模>=2亿），不再用「站上MA20」卡候选 ----
    # ★ 关键修正：旧版池子要求 above20，等于先把“短期回踩的好基金”整个排除出评分，
    #   矛盾在池子层就已经发生。质量分只认持续性，候选池不该用短期趋势卡。
    pool = [f for f in nav if (f['scale'] or 0) >= 2.0]
    print('\n规模门槛后候选池：%d / %d 只' % (len(pool), len(nav)))
    pool = pool if len(pool) >= 12 else nav

    # 长期收益百分位（近1年 / 近3年 / 成立年化，偏长期）
    r1y_l = [f['m']['r1y'] for f in pool]
    r3y_l = [f['m']['r3y'] for f in pool]
    sin_l = [ann_since(f['m']) for f in pool]
    r1y_p = pct_map(r1y_l)
    r3y_p = pct_map(r3y_l)
    sin_p = pct_map(sin_l)

    for i, f in enumerate(pool):
        m = f['m']
        # 收益 30（长期偏置：1年30% + 3年40% + 成立年化30%）—— 不押近期动量
        a = r1y_p.get(i, 0)
        b = r3y_p.get(i, 0)
        c = sin_p.get(i, 0)
        f['s_ret'] = round((0.30 * a + 0.40 * b + 0.30 * c) * 30, 2)

        # 基金经理 25（持续性核心因子）
        mq, md = mgr_quality(f.get('mgr'))
        f['mgr_q'] = mq
        f['mgr_score'] = round(25 * mq, 2)
        f['mgr_sum'] = md

        # 行业能力 25：长期贴合其主战场（相关性强度，不乘健康门，不随板块冷热掉档）
        #   main_corr >= 0.8 → 1.0；<= 0.4 → 0（非赛道/分散型，不加分也不减分）
        skill_raw = max(0.0, min(1.0, (f['main_corr'] - 0.40) / 0.40))
        f['s_skill'] = round(25 * skill_raw, 2)
        f['s_herd'] = f['s_skill']   # 字段名兼容（旧页面引用的“抱团分”实际是行业能力分）

        # 风险 20（波动率 / 回撤 / 夏普）
        vol_s = max(0.0, min(1.0, (0.60 - m['vol']) / 0.45))
        mdd_s = max(0.0, min(1.0, (0.45 + m['mdd']) / 0.40))
        sh_s = max(0.0, min(1.0, ((m['sharpe'] or -1) + 0.5) / 2.0))
        f['s_risk'] = round(7 * vol_s + 7 * mdd_s + 6 * sh_s, 2)
        f['s_trend'] = 0.0   # 趋势/时机不再进质量分

        # 质量分 Q = 持续性四因子之和（0~100），这是“它好不好”的唯一判据
        f['score'] = round(f['s_ret'] + f['mgr_score'] + f['s_skill'] + f['s_risk'], 1)
        # herd_gate 在 06 重算为「慢尺(年线/主线)状态」，这里先给 1.0 占位
        f['herd_gate'] = 1.0

    pool.sort(key=lambda x: -x['score'])
    print('\n质量分(综合) Top15:')
    print('%-8s %-22s %6s %6s %6s %6s %6s | %-12s 经理质量' % (
        '代码', '名称', '收益', '经理', '行业', '风险', '总分', '主战场'))
    for f in pool[:15]:
        print('%-8s %-22s %6.1f %6.1f %6.1f %6.1f %6.1f | %-12s %.2f' % (
            f['code'], f['name'][:20], f['s_ret'], f['mgr_score'], f['s_skill'],
            f['s_risk'], f['score'], f['main_sector'], f['mgr_q']))

    out = dict(scored=pool, sectors=secs, all=nav)
    json.dump(out, open(os.path.join(HERE, 'scored.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print('\n-> scored.json')


main()

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
    # ★ 2026-11-02 加：250 日（年线）维度 —— 区分「山顶」和「半山腰」必须用它。
    #   只看 60/120 日位置无法判断一段跌了 40% 的行情是「跌透」还是「刚开始跌」。
    ma250 = sum(vs[-250:]) / 250 if n >= 250 else None
    ma120 = sum(vs[-120:]) / 120 if n >= 120 else None
    ma250_prev = sum(vs[-260:-10]) / 250 if n >= 260 else None
    ma120_prev = sum(vs[-130:-10]) / 120 if n >= 130 else None
    hi250 = max(vs[-250:]) if n >= 250 else max(vs)
    return dict(
        label=s['label'], code=s['code'], etf=s['etf'], last=k[-1][0], close=vs[-1],
        r5=r(5), r20=r(20), r60=r(60), r120=r(120), r250=r(250),
        ma20=ma20, ma60=ma60, ma120=ma120, ma250=ma250,
        above20=vs[-1] > ma20, above60=vs[-1] > ma60,
        above120=(vs[-1] > ma120) if ma120 else None,
        above250=(vs[-1] > ma250) if ma250 else None,
        ma20_up=ma20 > ma20_prev, ma60_up=ma60 > ma60_prev,
        ma120_up=(ma120 > ma120_prev) if (ma120 and ma120_prev) else None,
        ma250_up=(ma250 > ma250_prev) if (ma250 and ma250_prev) else None,
        dd60=vs[-1] / max(vs[-60:]) - 1,
        # ★ 2026-10-31 加：120 日高点回撤，卖点规则「极端回撤」与页面「抱团状态」都要用
        dd120=vs[-1] / max(vs[-120:]) - 1 if n >= 120 else None,
        # ★ 2026-11-02 加：250 日高点回撤 = 「距年内高位跌了多少」，判断山顶/半山腰的核心
        dd250=vs[-1] / hi250 - 1 if n >= 250 else None,
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


# =====================================================================================
#  ★ 2026-11-02 新增（一）：市场情境识别
#  为什么需要：用户要「灵活选当下情境下的最优基金经理」。这句话的前提是系统先能说清
#  「当下是什么情境」——否则只能永远推荐最近涨得最多的那批，也就是追涨。
#  这里只用板块 ETF 价格，不含任何主观判断，且全部可复现。
# =====================================================================================
STYLE_PROXY = dict(
    # 成长/进攻端
    growth=['科创50', '创业板指', '半导体', '人工智能', '计算机', '消费电子',
            '芯片设计', '通信/光模块', '云计算/IDC', '传媒', '游戏'],
    # 价值/防守端
    value=['银行', '煤炭', '食品消费', '白酒', '基建', '电力公用', '房地产', '钢铁'],
)


def _avg(vals):
    v = [x for x in vals if x is not None]
    return sum(v) / len(v) if v else 0.0


def build_context(secs):
    """输出当下市场情境：风格钟摆 / 大小盘 / 温度（广度） / 一句话标签"""
    by = {s['label']: s for s in secs}

    def agg(labels, key):
        return _avg([by[l].get(key) for l in labels if l in by])

    g20, g60 = agg(STYLE_PROXY['growth'], 'r20'), agg(STYLE_PROXY['growth'], 'r60')
    v20, v60 = agg(STYLE_PROXY['value'], 'r20'), agg(STYLE_PROXY['value'], 'r60')
    # 钟摆 = 成长-价值 相对强弱。60 日为主（更稳），20 日为辅（更灵敏）
    swing = 0.6 * (g60 - v60) + 0.4 * (g20 - v20)
    style = 'growth' if swing > 0.06 else ('value' if swing < -0.06 else 'balanced')

    l20 = (by.get('沪深300') or {}).get('r20') or 0
    s20 = (by.get('中证1000') or {}).get('r20') or 0
    size = 'small' if s20 - l20 > 0.03 else ('large' if l20 - s20 > 0.03 else 'flat')

    breadth = sum(1 for s in secs if s['above20']) / max(len(secs), 1)
    breadth60 = sum(1 for s in secs if s['above60']) / max(len(secs), 1)
    up_cnt = sum(1 for s in secs if (s['r20'] or 0) > 0) / max(len(secs), 1)
    med20 = statistics.median([s['r20'] or 0 for s in secs])
    deep = sum(1 for s in secs if (s.get('dd250') or 0) <= -0.35) / max(len(secs), 1)

    # 温度：0~1，越低越冷
    temp = round(0.5 * breadth + 0.3 * up_cnt + 0.2 * (1 - deep), 3)

    # 一句话标签（人话，不堆术语）
    if style == 'value' and breadth < 0.30:
        label = '防守抱团 · 只有红利/银行这类在撑，成长普遍走弱'
    elif style == 'value':
        label = '价值占优 · 资金偏向低估值、高分红'
    elif style == 'growth' and breadth > 0.60:
        label = '成长进攻 · 多数板块同步走强'
    elif style == 'growth':
        label = '成长占优 · 但只有部分方向在涨，分化明显'
    elif breadth < 0.30:
        label = '普跌无主线 · 大多数板块跌破 20 日线'
    elif breadth > 0.65:
        label = '普涨 · 多数板块同步上行'
    else:
        label = '震荡分化 · 没有统一下跌或统一上涨'

    if deep >= 0.35:
        label += '；三成以上板块距年内高点已跌超 35%（超跌区很深）'

    return dict(
        style=style, size=size, breadth=round(breadth, 3), breadth60=round(breadth60, 3),
        up_ratio=round(up_cnt, 3), med_r20=round(med20, 4), temp=temp,
        deep_ratio=round(deep, 3), swing=round(swing, 4),
        growth_r20=round(g20, 4), growth_r60=round(g60, 4),
        value_r20=round(v20, 4), value_r60=round(v60, 4),
        label=label, last_date=(secs[0].get('last') if secs else ''),
    )


# =====================================================================================
#  ★ 2026-11-02 新增（二）：板块四象限
#  对用户那句「流入 7 月的科技，别买到半山腰」的直接回答：
#    · 只看「跌了多少」不够 —— 跌 40% 可能是半山腰，也可能已经跌透。
#    · 所以同时看两件事：① 长期趋势还在不在（年线 + 120 日线）
#                        ② 已从年内高点回撤多少 + 最近是不是在急涨
#  四象限：
#    sweet    甜点区 —— 趋势在、有回调没急涨 → 这才是买点（不追高、也不是下跌）
#    strong   强势追高 —— 趋势在但贴着新高或刚急涨 → 追进去就是山顶
#    rebound  超跌待转 —— 趋势刚破又深跌，但已站回 20 日线且 20 日线翘头 → 右侧信号初现
#    falling  下跌中继 —— 趋势已破且没有企稳迹象 → 就是「半山腰」，碰不得
# =====================================================================================
QUAD_TEXT = dict(
    sweet='趋势在·有回调（甜点区）',
    strong='趋势在·贴着高位（追高风险）',
    turn='底部转折（中短期修复，年线未收复）',
    pullback='长期在·短期走弱（等企稳）',
    rebound='深跌刚企稳（右侧初现）',
    falling='趋势已破（下跌中继）',
)


def quadrant(s):
    """返回 (象限key, 中文说明, 是否可买, 是否山顶, 是否半山腰)

    ★ 2026-11-02 修正（第一版把「底部转折」误杀成 falling）：
      第一版要求「必须站上年线」才算趋势在，结果白酒/食品消费这类
      「MA20/MA60/MA120 全部站上且 20/60 线上行、只是年线还没收复」的板块
      被判成 falling（下跌中继）。但它们恰恰是**底部刚转折、位置还很低**的最好买点。
      实测：白酒 MA20/60/120=T、20/60线双上行、距年内高点 -28.8% —— 这是右侧早期，不是下跌中继。
    现在按「长/中/短三层趋势」分档：
      sweet    三层全在 + 有回调没急涨        → 最好，可买
      strong   三层全在 + 贴新高/刚急涨        → 山顶，别追
      turn     中短期修复(MA60↑且站上MA120) + 年线未收复 + 位置不高 → 底部转折，可买（右侧早期）
      pullback 长期在但短期走弱                → 回调途中，等企稳
      rebound  深跌超35% + 刚站回MA20且MA20↑    → 右侧初现，观察仓
      falling  其余                            → 下跌中继（半山腰），别碰
    """
    above20, above60 = s['above20'], s['above60']
    above120, above250 = s.get('above120'), s.get('above250')
    up20, up60 = s.get('ma20_up'), s.get('ma60_up')
    dd250 = s.get('dd250')
    r20 = s.get('r20') or 0

    short_ok = bool(above20 and up20)
    mid_ok = bool(above60 and up60)
    long_ok = bool(above120 and above250)

    if long_ok and short_ok and mid_ok:
        if (dd250 is not None and dd250 > -0.03) or r20 >= 0.12:
            return 'strong', QUAD_TEXT['strong'], False, True, False
        return 'sweet', QUAD_TEXT['sweet'], True, False, False

    # 底部转折：中短期已修复（站上60日线且60日线上行）、站上120日线，但年线还没收复
    if mid_ok and short_ok and above120 and not above250:
        if dd250 is None or dd250 <= -0.08:      # 位置不高才叫转折，贴近高位就不是了
            return 'turn', QUAD_TEXT['turn'], True, False, False

    # 长期趋势还在，只是短期走弱 → 回调途中，等它自己站回来
    if long_ok and not short_ok:
        return 'pullback', QUAD_TEXT['pullback'], False, False, True

    # 深跌够狠且刚企稳
    if dd250 is not None and dd250 <= -0.35 and above20 and up20:
        return 'rebound', QUAD_TEXT['rebound'], True, False, True

    return 'falling', QUAD_TEXT['falling'], False, False, True


# =====================================================================================
#  ★ 2026-11-02 新增（三）：经理风格画像 + 情境匹配
#  「最优基金经理」不是绝对概念，而是「在这个情境下最优」。
#  用基金自身历史净值反推策略特征（不依赖任何主观标签），可复现、可被推翻：
#    · 攻守分解：把历史按自然年切开，算每年相对沪深300的超额
#        上涨年超额均值 = 进攻力；下跌年超额均值 = 防守力
#    · 风格暴露：与成长代理 / 价值代理的日收益相关性之差
#    · 稳定度：年际超额的离散度（越小越不靠单一年份）
# =====================================================================================
def year_rets(series):
    """[(date,val[,vol])] -> {year: 年度涨幅}"""
    out = {}
    ys = {}
    for x in series:
        ys.setdefault(x[0][:4], []).append(x[1])
    for y, vs in ys.items():
        if len(vs) >= 20 and vs[0]:
            out[y] = vs[-1] / vs[0] - 1
    return out


def series_ret(series):
    """日收益序列 [(date, pct)]"""
    ds = sorted(series, key=lambda x: x[0])
    return [(ds[i][0], ds[i][1] / ds[i - 1][1] - 1) for i in range(1, len(ds)) if ds[i - 1][1]]


def align_corr(a, b, days=120):
    """两个 [(date, pct)] 对齐后算相关性"""
    db = dict(b)
    common = [d for d, _ in a if d in db][-days:]
    if len(common) < 40:
        return 0.0
    da = dict(a)
    return corr([da[d] for d in common], [db[d] for d in common])


def manager_profile(f, bench_year, growth_ret, value_ret, hs300_ret):
    """基金/经理策略画像。返回 dict"""
    fr = series_ret(f['series'])
    fy = year_rets(f['series'])

    ups, dns = [], []
    for y, r in fy.items():
        b = bench_year.get(y)
        if b is None:
            continue
        ex = r - b
        (ups if b > 0 else dns).append(ex)

    attack = round(_avg(ups), 4) if ups else None       # 上涨年超额
    defend = round(_avg(dns), 4) if dns else None       # 下跌年超额
    # 稳定度：年际超额标准差（用全部年份）
    exs = [fy[y] - bench_year[y] for y in fy if y in bench_year]
    if len(exs) >= 3:
        mu = sum(exs) / len(exs)
        sd = (sum((x - mu) ** 2 for x in exs) / (len(exs) - 1)) ** 0.5
        stable = round(max(0.0, min(1.0, (0.35 - sd) / 0.35)), 3)
    else:
        stable = 0.4

    cg = align_corr(fr, growth_ret, 120)
    cv = align_corr(fr, value_ret, 120)
    tilt = cg - cv
    style = 'growth' if tilt > 0.08 else ('value' if tilt < -0.08 else 'balanced')

    # 经理任职连续性：现任经理任职 < 3 年 → 历史业绩不能算在他头上
    mgr = f.get('mgr') or []
    tenure = 0.0
    if mgr:
        tenure = parse_tenure(mgr[0].get('workTime', ''))
    inherit_risk = bool(tenure and tenure < 3.0)

    return dict(attack=attack, defend=defend, stable=stable, tilt=round(tilt, 3),
                style=style, corr_growth=round(cg, 3), corr_value=round(cv, 3),
                tenure=tenure, inherit_risk=inherit_risk,
                n_up=len(ups), n_dn=len(dns))


STYLE_FIT = {   # 情境 × 经理风格 → 匹配度
    'growth': dict(growth=1.0, balanced=0.65, value=0.20),
    'value': dict(growth=0.20, balanced=0.65, value=1.0),
    'balanced': dict(growth=0.70, balanced=1.0, value=0.70),
}


def fit_score(prof, ctx, quad):
    """情境匹配分 0~1：这经理「在这个环境下」是不是好选择。
    两部分：
      fit_style  = 风格匹配（当前钟摆 vs 他的暴露）45% + 攻守匹配（当前温度 vs 他的攻/守）55%
      fit_sector = 他能力圈所在板块的象限（甜点1.0 / 深跌刚企稳0.55 / 追高0.35 / 下跌中继0）
    最终 0.45*风格 + 0.55*板块 —— 板块权重更高：
      因为「能力圈正在下跌」时，再强的经理也不该现在买（这正是用户说的别买半山腰）。
    """
    sf = STYLE_FIT.get(ctx['style'], STYLE_FIT['balanced']).get(prof['style'], 0.5)

    # 攻守匹配：市场弱（广度低）看防守力，市场强看进攻力
    atk = prof.get('attack')
    dfn = prof.get('defend')
    if ctx['breadth'] < 0.40:            # 弱势 → 要能扛
        core = dfn if dfn is not None else (atk if atk is not None else 0)
    else:                                 # 强势 → 要能攻
        core = atk if atk is not None else (dfn if dfn is not None else 0)
    atk_def = max(0.0, min(1.0, (core + 0.05) / 0.25))   # 超额 -5%~+20% 映射到 0~1

    fit_style = 0.45 * sf + 0.55 * atk_def

    qk = quad[0] if isinstance(quad, (tuple, list)) else quad
    fit_sector = dict(sweet=1.0, turn=0.78, rebound=0.55,
                      pullback=0.35, strong=0.30, falling=0.0).get(qk, 0.3)

    # 任职不足 3 年：业绩不可继承，整体打 9 折（不是否决，只是折价）
    risk_pen = 0.90 if prof.get('inherit_risk') else 1.0

    return round((0.45 * fit_style + 0.55 * fit_sector) * risk_pen, 4)


def main():
    nav = load('nav.json')
    sec_raw = load('sectors.json')
    secs = [sector_metrics(s) for s in sec_raw]

    # ---- ★ 市场情境 + 板块四象限（2026-11-02）----
    ctx = build_context(secs)
    for s in secs:
        q, qt, cb, top, mid = quadrant(s)
        s['quad'], s['quad_text'] = q, qt
        s['can_buy'], s['is_top'], s['is_mid'] = cb, top, mid
    print('=' * 96)
    print('市场情境：%s' % ctx['label'])
    print('  风格=%-8s 大小盘=%-6s 广度(站上MA20)=%.0f%% 温度=%.2f 深跌板块占比=%.0f%%'
          % (ctx['style'], ctx['size'], ctx['breadth'] * 100, ctx['temp'], ctx['deep_ratio'] * 100))
    from collections import Counter as _C
    print('  板块象限分布：%s' % dict(_C(s['quad'] for s in secs)))
    print('  甜点区板块：%s' % '、'.join(s['label'] for s in secs if s['quad'] == 'sweet'))
    print('  追高区板块：%s' % '、'.join(s['label'] for s in secs if s['quad'] == 'strong'))
    print('=' * 96)

    # 基准与风格代理（供经理画像用）
    hs = next((x for x in sec_raw if x['label'] == '沪深300'), sec_raw[0])
    bench_year = year_rets(hs['k'])
    hs300_ret = series_ret(hs['k'])

    def proxy_ret(labels):
        acc, cnt = {}, {}
        for s in sec_raw:
            if s['label'] not in labels:
                continue
            for d, r in series_ret(s['k']):
                acc[d] = acc.get(d, 0.0) + r
                cnt[d] = cnt.get(d, 0) + 1
        return [(d, acc[d] / cnt[d]) for d in sorted(acc) if cnt[d] >= 3]

    growth_ret = proxy_ret(STYLE_PROXY['growth'])
    value_ret = proxy_ret(STYLE_PROXY['value'])

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
    quad_by = {s['label']: (s['quad'], s['quad_text']) for s in secs}
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
                                 r5=s['r5'], above20=s['above20'],
                                 quad=s['quad'], quad_text=s['quad_text']) for c, l, s in top]
        f['main_sector'] = top[0][1] if top else ''
        f['main_corr'] = round(top[0][0], 3) if top else 0
        # ★ 2026-11-02 修：相关性不够就别叫「主战场」。
        #   实测 519712 被判成「主战场=人工智能」，而它与该板块的相关性是 **-0.333**（负相关），
        #   实际持仓是银行 26.3% + 医药 9.9% + 电力 8.7% —— 是一只低估值分散配置。
        #   把「相关性排第一但为负」标成主战场，会让用户完全误解它在押什么。
        if f['main_corr'] < 0.50:
            f['main_sector_raw'] = f['main_sector']
            f['main_sector'] = '分散配置'
        f['main_mom'] = top[0][2]['mom'] if top else 0
        f['main_healthy'] = bool(top[0][2]['above20'] and top[0][2]['ma20_up']) if top else False

        # ---- ★ 经理风格画像 + 情境匹配（2026-11-02）----
        aq = quad_by.get(f['main_sector'], ('falling', '无板块数据'))
        f['arena_quad'], f['arena_quad_text'] = aq
        prof = manager_profile(f, bench_year, growth_ret, value_ret, hs300_ret)
        f['prof'] = prof

        # 基金自身象限：分散型基金（主战场是沪深300 这类宽基）不能拿宽基判死，
        # 它自己涨得好就是涨得好。强赛道基金（相关性>=0.75）才以板块为准。
        m = f['m']
        self_shape = dict(above20=m.get('above20'), above60=m.get('above60'),
                          above120=m.get('above120'), above250=m.get('above250'),
                          ma20_up=m.get('ma20_up'), ma60_up=m.get('ma60_up'),
                          dd250=m.get('dd250'), r20=m.get('r1m'))
        sq = quadrant(self_shape)
        f['self_quad'], f['self_quad_text'] = sq[0], sq[1]

        if f['main_corr'] >= 0.75:
            # 强赛道：以板块为准。★ 但必须防「板块转了、这只没跟上」——
            #   实测 005970(白酒 corr0.851)/009852(食品 corr0.781) 板块是 turn，
            #   而基金自己 MA60 没站上、60日线还在下行、近1年 -18%/-26%，属掉队。
            #   板块转 ≠ 这只基金能买，买的是基金不是板块。
            if aq[0] in ('sweet', 'turn', 'rebound') and sq[0] == 'falling':
                use, f['quad_src'] = ('pullback', '板块已转·这只没跟上（等它自己转）'), 'lag'
            else:
                use, f['quad_src'] = aq, 'sector'
        else:
            use, f['quad_src'] = (sq[0], sq[1]), 'self'
        f['quad'], f['quad_text'] = use[0], use[1]
        f['fit'] = fit_score(prof, ctx, use)

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
    print('%-8s %-22s %6s %6s %6s %6s %6s | %-10s %-9s %5s %5s' % (
        '代码', '名称', '收益', '经理', '行业', '风险', '总分', '主战场', '象限', '匹配', '风格'))
    for f in pool[:15]:
        pr = f.get('prof') or {}
        print('%-8s %-22s %6.1f %6.1f %6.1f %6.1f %6.1f | %-10s %-9s %5.2f %5s' % (
            f['code'], f['name'][:20], f['s_ret'], f['mgr_score'], f['s_skill'],
            f['s_risk'], f['score'], f['main_sector'][:9], f.get('quad', ''),
            f.get('fit', 0), pr.get('style', '')))

    from collections import Counter as _C2
    print('\n按象限统计（全池 %d）：%s' % (len(pool), dict(_C2(f.get('quad') for f in pool))))
    buy = [f for f in pool if (f.get('quad') in ('sweet', 'turn', 'rebound'))]
    buy.sort(key=lambda x: -(x['fit'] * 0.5 + x['score'] / 100.0))
    print('可买象限内按「匹配+质量」排序 Top10：')
    for f in buy[:10]:
        print('  %-8s %-20s 质量%5.1f 匹配%.2f 象限%-8s 主战场%-10s 风格%-8s 攻%+.1f%%/守%+.1f%%' % (
            f['code'], f['name'][:18], f['score'], f['fit'], f.get('quad'),
            f['main_sector'][:9], (f.get('prof') or {}).get('style', ''),
            ((f.get('prof') or {}).get('attack') or 0) * 100,
            ((f.get('prof') or {}).get('defend') or 0) * 100))

    out = dict(scored=pool, sectors=secs, all=nav, context=ctx)
    json.dump(out, open(os.path.join(HERE, 'scored.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print('\n-> scored.json')


main()

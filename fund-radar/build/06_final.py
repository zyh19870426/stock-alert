# -*- coding: utf-8 -*-
"""
06 · 定榜：持仓为主的抱团分 + 三只推荐 + 买卖点
------------------------------------------------------------
抱团分（30 分）口径（这是本系统最核心的判断，写清楚以便被推翻）：
   持仓加权板块动量 = Σ(前十大权重占比 × 该行业代表ETF的动量百分位)
   抱团分 = 30 × (0.60 × 持仓加权动量 + 0.40 × 相关强度) × 板块健康门
     · 相关强度 = clamp((净值与主战场板块60日相关性 - 0.55) / 0.40, 0, 1)
     · 板块健康门：主战场 ETF 跌破 MA20 → ×0.5（抱团已瓦解，不算踩中）
   若该基金拿不到持仓，退回用主战场动量代替持仓加权动量（页面上标注「按净值推断」）

★ 已知偏差（必须告诉用户）：ETF 是板块代理，不是基金的真实持仓。
  实测：中航趋势领航等基金重仓的是「汽零转型机器人」小票（福赛/斯菱/新泉），
  与中证机器人指数（汇川/埃斯顿）成分差别很大 → 这类基金的抱团分被系统性低估。

三只推荐规则：按总分排序，但**主战场去重**——同一板块只取一只。
  理由：三只如果都是银行，等于买了一只，分散不了。
输出：数据/data.json
"""
import json, os, math

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'data', 'data.json')


def load(n):
    return json.load(open(os.path.join(HERE, n), encoding='utf-8'))


def clamp(v, a=0.0, b=1.0):
    return max(a, min(b, v))


def ma(vs, n):
    out, s = [], 0.0
    for i, v in enumerate(vs):
        s += v
        if i >= n:
            s -= vs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def load_sec_state():
    """板块代表 ETF 的历史状态（按日期索引），买卖点规则要用：
         above60   ETF 收盘 > 自己的 MA60        —— 买点的「别逆势」过滤
         m60_dn    ETF MA60 低于 20 日前         —— 板块中期趋势是否掉头
         dd120     ETF 距 120 日高点回撤
         mom_rk    ETF 20 日动量在近 250 个观测里的分位  ← 卖点主信号用它
         mom20     ETF 20 日动量
       为什么卖点用「动量分位拐头」而不是 MA60 破位：
       97_scan_rules.py 在 22 只基金 / 8.5 年历史上把 11 条候选卖点全测了一遍，
       **没有任何一条能做到「信号后 60 日收益为负」**——技术信号判不了顶。
       其中 S8（动量分位从 >0.85 掉到 <0.55）是 11 条里唯一中位数≈0 的（-0.04%），
       对上涨的让出最小，所以选它，并明确它在页面上叫「减仓/离场信号」不叫「顶部」。
    """
    raw = json.load(open(os.path.join(HERE, 'sectors.json'), encoding='utf-8'))
    out = {}
    for s in raw:
        ds = [x[0] for x in s['k']]
        vs = [x[1] for x in s['k']]
        m60 = ma(vs, 60)
        mom20 = {}
        for i in range(20, len(vs)):
            mom20[ds[i]] = vs[i] / vs[i - 20] - 1
        # 动量分位：在最近 250 个观测里的排名（避免用全历史，防止早期样本主导）
        keys = [d for d in ds if d in mom20]
        rk, buf = {}, []
        for d in keys:
            buf.append(mom20[d])
            tail = buf[-250:]
            rk[d] = sum(1 for x in tail if x <= buf[-1]) / float(len(tail))
        st = {}
        for i in range(60, len(vs)):
            d = ds[i]
            if m60[i] is None or m60[i - 20] is None:
                continue
            st[d] = dict(
                above60=vs[i] > m60[i],
                m60_dn=m60[i] < m60[i - 20],
                dd120=vs[i] / max(vs[max(0, i - 119):i + 1]) - 1,
                mom_rk=rk.get(d),
                mom20=mom20.get(d),
            )
        out[s['label']] = (st, sorted(st))
    return out


def sec_at(SECS, label, date):
    """取 <= date 的最近一个板块状态；板块数据比基金短就返回 None（该条规则不参与判断）"""
    if not label or label not in SECS:
        return None
    st, keys = SECS[label]
    import bisect
    j = bisect.bisect_right(keys, date) - 1
    if j < 0:
        return None
    return st[keys[j]]


# ---- 规则参数（改这里就等于改规则，自检脚本会盯着这两个数）----
RULE = dict(
    b_pos60_max=0.40,     # B1 不追高：净值在过去 60 日区间的分位上限
    b_cool=30,            # B2 信号冷却（交易日）
    s_rank_hi=0.85,       # S1 板块动量分位近期曾达到的高位
    s_rank_lo=0.55,       # S2 板块动量分位掉到这个值以下算「拐头」
    s_look=40,            # S3 回看多少日内出现过高位
    s_cool=30,            # S4 信号冷却（交易日）
    s_dd120=-0.18,        # S5 硬止损：距 120 日高点回撤
)


def marks(series, arena='', SECS=None, cap=40):
    """买卖点 v3（2026-10-31 定稿，规则由 97_scan_rules.py 的扫描结果决定）

       买 B —— 低位 + 趋势在 + 板块没坏（三条件全满足，冷却 20 日）
         ① pos60 <= 0.40      净值在过去 60 日区间的下 40% 分位（这是「不追高」的硬约束）
         ② MA20 > MA60 且 MA60 高于 20 日前（趋势向上）
         ③ 主战场 ETF 收盘 > 其 MA60（板块没破位）
       实测：B 位置中位 0.35（旧规则 0.75），后 20 日收益训练段 +0.37% / 验证段 +2.57%，两段一致。

       卖 S —— 抱团动量拐头（冷却 20 日），另加一条硬止损
         ① 主战场 ETF 的 20 日动量分位，近 40 日内曾 >0.85，当前 <0.55
         ② 或 基金距 120 日高点回撤 >= 18%
       实测：S 后 60 日中位 -0.04%（勉强能算「卖了不亏」），
             但 5% 分位 -14.25% —— 卖出后继续跌的尾部确实避开了，代价是让出平均涨幅。
    """
    ds = [x[0] for x in series]
    vs = [x[1] for x in series]
    n = len(vs)
    m20, m60 = ma(vs, 20), ma(vs, 60)
    out = []
    holding, last_i = 0, -999
    for i in range(61, n):
        if m20[i] is None or m60[i] is None or m60[i - 20] is None:
            continue
        seg = vs[max(0, i - 59):i + 1]
        lo, hi = min(seg), max(seg)
        pos60 = (vs[i] - lo) / (hi - lo) if hi > lo else 0.5
        dd120 = vs[i] / max(vs[max(0, i - 119):i + 1]) - 1
        s3 = sec_at(SECS, arena, ds[i]) if SECS else None

        if holding:
            why = ''
            rk = s3.get('mom_rk') if s3 else None
            if rk is not None and rk < RULE['s_rank_lo']:
                past = []
                for j in range(max(0, i - RULE['s_look']), i + 1):
                    q = sec_at(SECS, arena, ds[j]) if SECS else None
                    if q and q.get('mom_rk') is not None:
                        past.append(q['mom_rk'])
                if past and max(past) > RULE['s_rank_hi']:
                    why = ('抱团动量拐头：%s 的 20 日动量分位从 %.2f 掉到 %.2f'
                           % (arena, max(past), rk))
            if not why and dd120 <= RULE['s_dd120']:
                why = '硬止损：距 120 日高点回撤 %.1f%%' % (dd120 * 100)
            if why and i - last_i >= RULE['s_cool']:
                out.append(dict(d=ds[i], v=round(vs[i], 4), t='S', why=why,
                                ma20=round(m20[i], 4), ma60=round(m60[i], 4),
                                pos60=round(pos60, 2), dd120=round(dd120, 3)))
                holding, last_i = 0, i
        else:
            trend_up = m20[i] > m60[i] and m60[i] > m60[i - 20]
            sec_ok = bool(s3['above60']) if s3 else True
            if (trend_up and pos60 <= RULE['b_pos60_max'] and sec_ok
                    and i - last_i >= RULE['b_cool']):
                out.append(dict(d=ds[i], v=round(vs[i], 4), t='B',
                                why='低位建仓：60 日区间 %.0f%% 分位，趋势向上，板块未破位' % (pos60 * 100),
                                ma20=round(m20[i], 4), ma60=round(m60[i], 4),
                                pos60=round(pos60, 2), dd120=round(dd120, 3)))
                holding, last_i = 1, i
    return out[-cap:] if cap else out


def cur_pos(series):
    """当前净值在过去 60 日区间的分位 + 距 120 日高点回撤"""
    vs = [x[1] for x in series]
    seg = vs[-60:]
    lo, hi = min(seg), max(seg)
    pos60 = (vs[-1] - lo) / (hi - lo) if hi > lo else 0.5
    dd120 = vs[-1] / max(vs[-120:]) - 1
    return round(pos60, 2), round(dd120, 3)


def advice(f, SECS=None):
    """当前该做什么。判定顺序：先看离场条件，再看能不能低位建仓。
       注意：所有文案都只说条件，不说涨跌。"""
    m = f['m']
    c, a20, a60 = m['last'], m['ma20'], m['ma60']
    vs = [x[1] for x in f['series']]
    m60 = ma(vs, 60)
    a60_dn = bool(m60[-1] is not None and m60[-21] is not None and m60[-1] < m60[-21])
    pos60, dd120 = cur_pos(f['series'])
    arena = f.get('arena', '')
    s3 = sec_at(SECS, arena, m['last_date']) if SECS else None
    rk = s3.get('mom_rk') if s3 else None

    seg60 = vs[-60:]
    lo60, hi60 = min(seg60), max(seg60)
    buy_line = round(lo60 + RULE['b_pos60_max'] * (hi60 - lo60), 4)   # 60日区间 40% 分位对应的净值
    stop = round(a60, 4)          # 60 日均线。它只是买点的趋势条件，**不是卖出信号**
    trend_up = a20 > a60 and not a60_dn
    sec_ok = bool(s3['above60']) if s3 else True

    # ---------------- 信号板：规则里只有 3 个信号，逐条给当前值 / 是否满足 / 差多少 ----------------
    # 这是页面「明确操作信号」的唯一来源。文字结论必须由它推出，不能另写一套。
    hi120 = max(vs[-120:])
    stop_price = round(hi120 * (1 + RULE['s_dd120']), 4)   # 触发硬止损的净值
    rank_max40 = None
    if SECS:
        past = [q['mom_rk'] for q in
                (sec_at(SECS, arena, d) for d in [x[0] for x in f['series']][-RULE['s_look']:])
                if q and q.get('mom_rk') is not None]
        if past:
            rank_max40 = max(past)

    trend_ok = bool(a20 > a60 and not a60_dn)
    s1_ok = dd120 <= RULE['s_dd120']
    s2_ok = bool(rk is not None and rk < RULE['s_rank_lo']
                 and rank_max40 is not None and rank_max40 > RULE['s_rank_hi'])
    b_ok = bool(trend_ok and pos60 <= RULE['b_pos60_max'] and sec_ok)

    sig = [
        dict(k='B', label='买入信号', hit=b_ok, rows=[
            dict(t='近 60 日位置 ≤ 40%',
                 now='%.0f%%' % (pos60 * 100), ok=pos60 <= RULE['b_pos60_max'],
                 need='要跌到 %.4f（%+.1f%%）' % (buy_line, (buy_line / c - 1) * 100)),
            dict(t='20 日均线在 60 日均线上方，且 60 日均线在上行',
                 now='%.4f %s %.4f，%s' % (a20, '>' if a20 > a60 else '<', a60,
                                          '上行' if not a60_dn else '下行'), ok=trend_ok, need=''),
            dict(t='押的板块 ETF 没跌破 60 日均线',
                 now=(arena + ('：站上' if sec_ok else '：已跌破')) if s3 else '无板块数据',
                 ok=sec_ok, need=''),
        ], n_bad=sum(1 for x in (pos60 <= RULE['b_pos60_max'], trend_ok, sec_ok) if not x)),
        dict(k='S1', label='卖出信号 · 硬止损', hit=s1_ok, rows=[
            dict(t='距 120 日最高点回撤 ≥ 18%',
                 now='%.1f%%' % (dd120 * 100), ok=s1_ok,
                 need='要跌到 %.4f（%+.1f%%）' % (stop_price, (stop_price / c - 1) * 100)),
        ], n_bad=0 if s1_ok else 1),
        dict(k='S2', label='卖出信号 · 板块动能拐头', hit=s2_ok, rows=[
            dict(t='板块动能现在 < 0.55',
                 now=('%.2f' % rk) if rk is not None else '无数据',
                 ok=bool(rk is not None and rk < RULE['s_rank_lo']),
                 need=''),
            dict(t='且近 40 个交易日内曾 > 0.85',
                 now=('否（最高只到 %.2f）' % rank_max40
                      if rank_max40 is not None and rank_max40 <= RULE['s_rank_hi']
                      else ('是（最高 %.2f）' % rank_max40 if rank_max40 is not None else '无数据')),
                 ok=bool(rank_max40 is not None and rank_max40 > RULE['s_rank_hi']),
                 need=''),
        ], n_bad=0 if s2_ok else sum(1 for x in (
            rk is not None and rk < RULE['s_rank_lo'],
            rank_max40 is not None and rank_max40 > RULE['s_rank_hi']) if not x)),
    ]
    for x in sig:
        if x['hit']:
            x['verdict'] = '已触发'
        elif x['k'] == 'B':
            x['verdict'] = '未触发（3 条里差 %d 条）' % x['n_bad']
        elif x['k'] == 'S2':
            if rank_max40 is not None and rank_max40 <= RULE['s_rank_hi']:
                x['verdict'] = '不会触发：这个板块动能最高只到过 %.2f，没到过 0.85' % rank_max40
            else:
                x['verdict'] = '未触发（动能还在高位，没拐头）'
        else:
            x['verdict'] = '未触发（还差 %.1f 个百分点）' % abs(dd120 * 100 - RULE['s_dd120'] * 100)
    # 下一个会触发的信号 + 触发价，让「等什么」变成具体数字
    nxt = None
    if not s1_ok:
        nxt = dict(k='S1', what='硬止损', price=stop_price, gap=(stop_price / c - 1) * 100)
    if not b_ok and pos60 > RULE['b_pos60_max']:
        cand = dict(k='B', what='买入', price=buy_line, gap=(buy_line / c - 1) * 100)
        if nxt is None or abs(cand['gap']) < abs(nxt['gap']):
            nxt = cand


    # 文案铁律：系统不知道你有没有持仓，所以每条都说清「已经买了的」「还没买的」各自怎么办，
    # 绝不出现「只能持有」「别加仓」这种替用户决定仓位的说法。
    # 卖出信号的真实口径（页面文案不得超出这两条，绝不能编「跌破均线就卖」）
    s_line = ('按规则，卖出只有两条：距 120 日最高点回撤 18%%（对应净值 %.4f）、或它押的板块动能拐头。'
              % stop_price)

    if dd120 <= RULE['s_dd120']:
        state, tone = '该走 · 已跌 18%', 'bad'
        act = ('这只基金从最近 120 个交易日的最高点已经跌了 %.1f%%，触发 18%% 硬止损 —— 这是规则里两条卖出信号之一，'
               '已经成立。已经买了的：按规则卖出，别在下跌里抢反弹。'
               '还没买的：跌得多不等于便宜，现在不碰。' % (abs(dd120) * 100))
    elif s2_ok:
        state, tone = '该走 · 押的板块动能拐头', 'bad'
        act = ('它主要押的是「%s」板块。这个板块的动能分位从近 40 日内的最高 %.2f 掉到 %.2f，'
               '触发「板块动能拐头」—— 这是规则里两条卖出信号之一，已经成立。'
               '已经买了的：按规则卖出，别等它跌透。还没买的：现在不是进场的时候。'
               % (arena, rank_max40 or 0, rk))
    elif not sec_ok:
        state, tone = '先别买 · 板块已破位', 'warn'
        act = ('它押的「%s」板块，代表 ETF 已经跌破 60 日均线。买入信号要求「板块没破位」，'
               '这一条不满足，所以现在不给买点（跟这只基金自己涨不涨无关）。'
               '已经买了的：%s 都没触发，规则上没有卖出动作。'
               '还没买的：等这个板块重新站上 60 日均线再考虑。' % (arena, s_line))
    elif trend_up and pos60 <= RULE['b_pos60_max']:
        state, tone = '可以买 · 位置偏低', 'ok'
        act = ('三个条件同时满足：价格在近 60 个交易日区间的 %.0f%% 位置（0%% 是最低、100%% 是最高，规则要求 ≤%.0f%%）、'
               '基金自己的走势向上、它押的板块也没破位。这是买点。'
               '买进去之后，%s 注意 60 日均线（%s）不是卖出线，它只是买点的趋势条件。'
               % (pos60 * 100, RULE['b_pos60_max'] * 100, s_line, stop))
    elif trend_up:
        state, tone = '先别买 · 已经涨高了', 'ok'
        act = ('走势向上，但价格已经在近 60 日区间的 %.0f%% 位置，买入信号要求的「位置 ≤%.0f%%」这条不满足 —— 现在不是买点。'
               '已经买了的：%s 这两条现在都没触发，所以规则上没有卖出动作，继续拿的理由是趋势还在、不是因为它便宜。'
               '还没买的：不追，等跌到 %.4f 以下（%+.1f%%）再重新判断，那时还要确认趋势和板块没坏。'
               % (pos60 * 100, RULE['b_pos60_max'] * 100, s_line,
                  buy_line, (buy_line / c - 1) * 100))
    elif c < a60 and a60_dn:
        state, tone = '先别买 · 它自己在跌', 'bad'
        act = ('这只基金「自己」的净值在 60 日均线（%s）下方，而且这条均线本身还在往下走。'
               '买入信号要求「20 日均线在 60 日均线上方且 60 日均线上行」，这条不成立。'
               '注意：这里说的是「基金自己」，跟它押的板块是两回事。'
               '已经买了的：%s 都没触发，规则上不卖出，但也不会在这里加。'
               '还没买的：等它自己站回 60 日均线上方。' % (stop, s_line))
    else:
        state, tone = '先别买 · 方向没定', 'warn'
        act = ('基金自己的走势还没转强（20 日均线 %s 还没上穿 60 日均线 %s），也没触发 18%% 硬止损。'
               '买入信号和卖出信号都不成立。已经买了的：%s 都没触发，规则上没有动作。'
               '还没买的：先不动，等方向出来。' % (round(a20, 4), stop, s_line))

    return dict(state=state, tone=tone, act=act,
                buy_line=buy_line, buy_lo=round(lo60, 4), buy_hi=round(hi60, 4),
                stop=stop, trigger_up=round(a20, 4), pos60=pos60, dd120=dd120,
                sec_ok=sec_ok, mom_rk=(round(rk, 2) if rk is not None else None),
                a60_dn=a60_dn, trend_up=trend_up, rule=RULE,
                sig=sig, next=nxt, stop_price=stop_price, hi120=round(hi120, 4),
                rank_max40=(round(rank_max40, 2) if rank_max40 is not None else None))
    # 注：曾有一个 line_crossed 字段（"离场线高于低位线"），已删除。理由是它不成立：
    #   1) 它只是「60日均线 > 60日区间40%分位」，上涨后必然为真，不是异常信号；
    #   2) 实测「可以买」状态里 89.9% 同时为真 → 九成买点旁边跟着一句"这里不是买点"；
    #   3) 曾据它推断「跌到便宜区时趋势早就破了」，5 万个样本里 61.2% 到那天趋势仍在 → 推断是错的。


def pack_series(series, n):
    """n=None → 全量历史（推荐 3 只要支持「3年/5年/全部」档位）；否则取最后 n 个交易日"""
    seg = series if n is None else series[-n:]
    return [[d, round(v, 4)] for d, v in seg]


XSD = {}


def load_xsd():
    """板块个股分化度 σ（93_robust_g1.py 验证过的那个变量）

    结论怎么来的（20 只基金 / 2020-08 起 / 训练段 ≤2024-12-31、验证段 ≥2025-01-01）：
      卖出信号之后 60 日收益的中位数，按板块个股分化度分层后差异极大：
        高σ（≥0.015，机器人/半导体/AI/通信）：训练 -4.8%、验证 -10.4%（bootstrap 区间不跨 0）
        低σ（<0.012，银行/电力/证券）：       训练 -1.0%、验证 +0.6%（区间跨 0，等于噪声）
      而且把「动量拐头」和「个股广度」分别套上同一个 σ 开关，两者变成一样好 ——
      说明**决定信号可靠性的是板块类型，不是信号类型**。
    所以 σ 不进买卖点公式，只作为「这个信号在这个板块该不该当回事」的标注。
    """
    f = os.path.join(HERE, 'breadth.json')
    if not os.path.exists(f):
        return {}
    d = json.load(open(f, encoding='utf-8'))['sectors']
    return {k: v.get('xsd_med') for k, v in d.items()}


def sig_rel(x):
    """按 σ 给信号可靠性分档（阈值 0.012 / 0.015 来自训练段分层，不是全样本最优）"""
    if x is None:
        return None
    if x >= 0.015:
        return 'high'      # 个股各走各的：信号之后 60 日下跌最明显
    if x >= 0.012:
        return 'mid'
    return 'low'           # 同步涨跌：广度是噪声，动量信号也不显著


def _sector_row(s, SECS):
    """板块行（给页面「抱团方向」用）：动量 + 状态。mom_rk 是卖点规则的核心输入，
    页面必须能看到它，否则「为什么现在提示减仓」没法解释。"""
    q = sec_at(SECS, s['label'], s['last']) or {}
    xsd = XSD.get(s['label'])
    return dict(label=s['label'], etf=s['etf'], code=s['code'],
                xsd=(round(xsd, 5) if xsd is not None else None),
                sig_rel=sig_rel(xsd),
                r5=round(s['r5'] * 100, 2), r20=round(s['r20'] * 100, 2),
                r60=round(s['r60'] * 100, 2), mom=s['mom'],
                above20=s['above20'], ma20_up=s['ma20_up'],
                above60=s.get('above60'), ma60_up=s.get('ma60_up'),
                m60_dn=q.get('m60_dn'), mom_rk=(round(q['mom_rk'], 2) if q.get('mom_rk') is not None else None),
                dd120=(round(s['dd120'] * 100, 2) if s.get('dd120') is not None else None),
                healthy=s['healthy'], last=s['last'])


def _adv_with_sigma(f, SECS):
    """在建议里补上「这个板块的信号可不可信」——信号可靠性由板块个股分化度决定，
    不是由信号本身决定（93_robust_g1.py 的对照实验结论）。"""
    a = advice(f, SECS)
    x = XSD.get(f.get('arena', ''))
    a['xsd'] = round(x, 5) if x is not None else None
    a['sig_rel'] = sig_rel(x)
    return a


def main():
    global XSD
    SECS = load_sec_state()
    XSD = load_xsd()
    sc = load('scored.json')
    hold = load('holdings.json')
    secs = {s['label']: s for s in sc['sectors']}
    mom_of = {k: v['mom'] for k, v in secs.items()}
    uni = {d['code']: d for d in load('universe.json')}

    pool = sc['scored']
    for f in pool:
        h = hold.get(f['code']) or {}
        f['has_hold'] = bool(h.get('expo'))
        f['new_in'], f['dropped'], f['prev_label'] = [], [], ''
        if h.get('expo'):
            lst = h['quarters'][0]['list']
            tot = sum(x['pct'] for x in lst) or 1
            # 持仓加权板块动量（只用能对上 ETF 的行业，其他按中位数 0.5 计并降权）
            num, den = 0.0, 0.0
            for x in lst:
                w = x['pct']
                mv = mom_of.get(x['sector'])
                if mv is None:
                    mv, w = 0.5, w * 0.5
                num += w * mv
                den += w
            f['hold_mom'] = round(num / den, 4) if den else 0.5
            f['expo'] = h['expo']
            f['top10'] = h['quarters'][0]['list']
            f['q_label'] = h['quarters'][0]['label']
            f['top10_sum'] = round(tot, 2)
            if len(h['quarters']) > 1:
                prev = {x['code'] for x in h['quarters'][1]['list']}
                cur = {x['code'] for x in h['quarters'][0]['list']}
                f['new_in'] = [x['name'] for x in h['quarters'][0]['list'] if x['code'] not in prev]
                f['dropped'] = [x['name'] for x in h['quarters'][1]['list'] if x['code'] not in cur]
                f['prev_label'] = h['quarters'][1]['label']
        else:
            f['hold_mom'] = f['main_mom']
            f['expo'] = []
            f['top10'] = []
            f['q_label'] = ''
            f['top10_sum'] = 0
            f['new_in'], f['dropped'] = [], []

        strength = clamp((f['main_corr'] - 0.55) / 0.40)
        base = 0.60 * f['hold_mom'] + 0.40 * strength
        gate = 1.0 if f['main_healthy'] else 0.5
        f['s_herd'] = round(30 * base * gate, 2)
        f['herd_gate'] = gate
        f['score'] = round(f['s_ret'] + f['s_trend'] + f['s_herd'] + f['s_risk'], 1)
        # 持仓为主战场（有持仓就用持仓最大行业，更可信）
        f['arena'] = f['expo'][0]['sector'] if f['expo'] else f['main_sector']
        f['arena_mom'] = mom_of.get(f['arena'], f['main_mom'])

    pool.sort(key=lambda x: -x['score'])

    # ---- 相关系数矩阵（90 日）----
    def rmap(f):
        s = f['series']
        return {s[i][0]: s[i][1] / s[i - 1][1] - 1 for i in range(1, len(s)) if s[i - 1][1]}
    RM = {f['code']: rmap(f) for f in pool}

    def pcorr(a, b):
        ka, kb = RM[a], RM[b]
        ks = [k for k in ka if k in kb][-90:]
        if len(ks) < 30:
            return 0.0
        x = [ka[k] for k in ks]; y = [kb[k] for k in ks]; n = len(x)
        mx, my = sum(x) / n, sum(y) / n
        sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
        sxx = sum((x[i] - mx) ** 2 for i in range(n))
        syy = sum((y[i] - my) ** 2 for i in range(n))
        return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0

    # ---- 三只推荐 ----
    # 规则（可复现）：
    #   ① 先取分数最高的 2 只，且二者 90 日相关性 < 0.75（否则视为同一个赌注，跳过）
    #   ② 第 3 只：与已选方向（arena）不同、近3月 > 0、且与已选相关性 < 0.60 的最高分者
    #   ③ 若②找不到，退回按①继续取（宁缺毋滥，少于 3 只就如实说明）
    picks = []
    for f in pool:
        if len(picks) >= 2:
            break
        if any(pcorr(f['code'], p['code']) >= 0.75 for p in picks):
            continue
        picks.append(f)
    third = None
    for f in pool:
        if f in picks:
            continue
        if f['arena'] in [p['arena'] for p in picks]:
            continue
        if (f['m']['r3m'] or 0) <= 0:
            continue
        if any(pcorr(f['code'], p['code']) >= 0.60 for p in picks):
            continue
        third = f
        break
    if third:
        picks.append(third)
    else:
        for f in pool:
            if len(picks) >= 3:
                break
            if f in picks or any(pcorr(f['code'], p['code']) >= 0.75 for p in picks):
                continue
            picks.append(f)
    for f in picks:
        f['corr_with'] = [dict(code=p['code'], name=p['name'], corr=round(pcorr(f['code'], p['code']), 2))
                          for p in picks if p is not f]

    print('=== 三只推荐 ===')
    for f in picks:
        print('  %-8s %-20s %5.1f分 [%s] 1m=%5.1f%% 3m=%5.1f%% 回撤%5.1f%% 规模%6.1f亿' % (
            f['code'], f['name'][:18], f['score'], f['arena'],
            f['m']['r1m'] * 100, f['m']['r3m'] * 100, f['m']['mdd'] * 100, f['scale'] or 0))
    print('\n=== 备选（4-12）===')
    for f in pool[3:12]:
        print('  %-8s %-20s %5.1f分 [%s]' % (f['code'], f['name'][:18], f['score'], f['arena']))

    def brief(f, n=750):
        u = uni.get(f['code'], {})
        return dict(
            code=f['code'], name=f['name'], score=f['score'],
            s_ret=f['s_ret'], s_trend=f['s_trend'], s_herd=f['s_herd'], s_risk=f['s_risk'],
            m=f['m'], scale=f['scale'], scale_prev=f['scale_prev'],
            scale_trend=f['scale_trend'], scale_date=f['scale_date'],
            mgr=f['mgr'], fee=f['fee'], fee2=f['fee2'],
            arena=f['arena'], arena_mom=f['arena_mom'],
            sector_corr=f['sector_corr'], expo=f['expo'], top10=f['top10'],
            q_label=f['q_label'], top10_sum=f['top10_sum'],
            new_in=f['new_in'], dropped=f['dropped'], prev_label=f.get('prev_label', ''),
            corr_with=f.get('corr_with', []),
            corr_p1=(round(pcorr(f['code'], picks[0]['code']), 2) if picks and f['code'] != picks[0]['code'] else None),
            has_hold=f['has_hold'], herd_gate=f['herd_gate'],
            hold_mom=f['hold_mom'],
            r_y2=f['m'].get('r2y'), r_y3=f['m'].get('r3y'), r_ytd=f['m'].get('ytd'),
            r_since=f['m'].get('since'), inception=u.get('est'),
            series=pack_series(f['series'], n),
            marks=marks(f['series'], f['arena'], SECS),
            adv=_adv_with_sigma(f, SECS),
        )

    # 备选池只放「最近三个月确实在赚钱」的：22 只过了趋势门槛，其中一部分近3月还是负的，
    # 那只满足"技术形态"，不满足页面上写的"最近收益优秀"，所以单独剔出去并记数。
    gate_ok = len(pool)
    cand_short = [f for f in pool if (f['m']['r3m'] or 0) > 0]
    dropped = gate_ok - len(cand_short)
    picks_codes = {f['code'] for f in picks}
    short_final = [f for f in cand_short if f['code'] not in picks_codes][:22]
    if len(short_final) < 3:
        short_final = [f for f in cand_short][:22]

    def brief_all(f):
        return brief(f, 750)

    # 主线集中度：过了门槛的基金里，最大那条线占几只（页面要用这个数字，不能写死）
    from collections import Counter
    cnt = Counter(f['arena'] for f in pool)
    top_arena, top_n = cnt.most_common(1)[0] if cnt else ('', 0)

    out = dict(
        meta=dict(gen=__import__('time').strftime('%Y-%m-%d %H:%M'),
                  data_date=pool[0]['m']['last_date'],
                  n_pool=len(sc['all']), n_scored=gate_ok,
                  n_gate=gate_ok, n_short=len(short_final), n_dropped_neg3m=dropped,
                  top_arena=top_arena, top_arena_n=top_n,
                  src='天天基金净值/季报持仓 · 腾讯ETF行情',
                  rf='板块代理=代表ETF；持仓分类按股票名称关键词，可人工核对'),
        sectors=sorted([_sector_row(s, SECS) for s in sc['sectors']],
                       key=lambda x: -x['mom']),
        picks=[brief(f, None) for f in picks],
        shortlist=[brief_all(f) for f in short_final],
    )
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('\n-> %s  %.0f KB' % (OUT, os.path.getsize(OUT) / 1024))


if __name__ == '__main__':
    main()

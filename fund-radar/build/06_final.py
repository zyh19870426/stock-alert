# -*- coding: utf-8 -*-
"""
06 · 定榜：持仓为主的抱团分 + 三只推荐 + 买卖点
------------------------------------------------------------
行业能力（25 分，属「质量分」四因子之一，在 04_score.py 计算；本文件只做时机判定与定榜）：
   行业能力 = 25 × clamp((净值与「押的那个板块(arena)」60日相关性 - 0.40) / 0.40, 0, 1)
     · 相关强度 = clamp((净值与「押的那个板块」60日相关性 - 0.55) / 0.40, 0, 1)
   若该基金拿不到持仓，退回用相关性最高板块代替（页面上标注「按净值推断」）
   ★ 注意：原「板块健康门（跌破 MA20 → ×0.5）」已删除——短期板块状态不再进质量分，
     改由「慢尺（年线/主线）未断」在时机层单独判定（见下「推荐规则」）。

★★ 2026-11-01 口径修正（9D_audit.py 量出来的）：
   原来「押的板块」有两个定义并存 —— 页面文案用 arena（持仓第一大行业），
   而上面的相关强度与健康门用的是 main_sector（60 日相关性最高的板块）。
   实测 16 只里 5 只（31%）两者不同，其中 010328 押银行、却拿「芯片设计」判健康门，
   且它与银行的相关性是 **-0.309**（负相关！）。
   现统一为：动量/相关强度/健康门 **全部用 arena**，并新增三个披露字段：
     arena_corr    净值与 arena 板块的 60 日相关性
     arena_healthy 该板块是否健康（跌破 MA20 -> 0.5）
     align         'aligned' 一致 / 'split' 持仓与走势不是同一个板块 / 'nav' 无持仓按净值推断
   当 arena_corr < 0.50 时 align 记为 'split'，页面必须标注「它持仓押这个板块，
   但净值走势跟这个板块关系很弱」——这是两个事实，不能合并成一句话。

★ 已知偏差（必须告诉用户）：ETF 是板块代理，不是基金的真实持仓。
  实测：中航趋势领航等基金重仓的是「汽零转型机器人」小票（福赛/斯菱/新泉），
  与中证机器人指数（汇川/埃斯顿）成分差别很大 → 这类基金的抱团分被系统性低估。

推荐规则（2026-11-01 重写 / 同日晚修正质量-时机拆分）：质量优先 + 好价位 + 慢尺未断，宁缺毋滥。
   ① 买点三条件：质量分>=池中位（好基金） + pos60<=合理区上沿（好价位） + slow_intact（慢尺/年线/主线未断）。
      ★ 修正点：不再用「板块跌破 MA20」卡候选——好基金短期回踩(板块破MA20但年线仍在)仍是好基金，
        跌到便宜区就是买点，彻底消除“好基金一跌到好价格就不是好基金”的矛盾。
   ② 同注去重按「板块」判：同一 arena 只取分数最高的一只（用户原话：两只都押银行就选最优）。
   ③ 再加相关性闸：不同板块但 90 日走势雷同(>=0.75)仍算同一个赌注。
   ④ 满足不了 3 只就如实给更少，并在 picks_note 里写明挡住了多少只、因为什么。
   ★ 每只都带 role：'best'（质量第一）/ 'spread'（分散位），并给出
     score_gap_best（比第 1 名低多少分），让「为了分散付出的代价」可见。
输出：数据/data.json
"""
import json, os, math

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'data', 'data.json')


def load(n):
    return json.load(open(os.path.join(HERE, n), encoding='utf-8'))


def clamp(v, a=0.0, b=1.0):
    return max(a, min(b, v))


def _rets(pairs):
    """[(date, v)] -> {date: pct}"""
    return {pairs[i][0]: pairs[i][1] / pairs[i - 1][1] - 1
            for i in range(1, len(pairs)) if pairs[i - 1][1]}


def _corr60(fr, sr):
    """60 日收益率相关性（与 04_score.py 同口径，便于两边数字对得上）"""
    ks = sorted(set(fr) & set(sr))[-60:]
    if len(ks) < 30:
        return None
    x = [fr[k] for k in ks]; y = [sr[k] for k in ks]
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    sxx = sum((x[i] - mx) ** 2 for i in range(n))
    syy = sum((y[i] - my) ** 2 for i in range(n))
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


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
        m120 = ma(vs, 120)   # ★ 慢尺：板块年线（主线是否还在长期上行）
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
                above120=(m120[i] is not None and vs[i] > m120[i]),
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
    # ★ 2026-11-01 价位三档（9D_audit.py 实测，22 只基金 / 3127 采样点，每 5 日取样）
    #   pos60<10%  训练 n=256 60日中位 +5.57% 胜率 73.4% | 验证 +2.85% 64.8%，5%分位比基线更小
    #   pos60 10-20% 训练 +3.15% 68.6% | 验证 +1.87% 58.8%
    #   pos60 20-40% 训练 +2.03% 61.1% | 验证 +2.56% 66.5%
    #   pos60 >40%  训练 +0.89%（低于基线 +1.63%）→ 所以 40% 以上不给买点
    pos_tier_deep=0.10,   # 深度便宜区上界
    pos_tier_cheap=0.25,  # 便宜区上界
    pos_tier_fair=0.40,   # 合理区上界（= b_pos60_max，两者必须一致，自检盯着）
)


def marks(series, arena='', SECS=None, cap=40):
    """买卖点 v3（2026-10-31 定稿，规则由 97_scan_rules.py 的扫描结果决定）

       买 B —— 低位 + 趋势在 + 板块没坏（三条件全满足，冷却 20 日）
         ① pos60 <= 0.40      净值在过去 60 日区间的下 40% 分位（这是「不追高」的硬约束）
         ② MA20 > MA60 且 MA60 高于 20 日前（趋势向上）
         ③ 慢尺（年线/主线）未断：基金自身年线(MA250)或退一步板块 MA120 还在（板块短期破 MA20/MA60 不算破）
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
        sec_ok = slow_scale_series(vs[:i + 1], arena, ds[i], SECS)[0] if SECS else True

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


def slow_scale_series(vs, arena, date, SECS):
    """慢尺（年线 / 主线）是否未断 —— 时机判定的长期护栏（接收序列，供 marks/advice 复用）。

    设计目标：解决「好基金一跌到好价格就从好基金名单消失」的矛盾。
    旧护栏用板块 MA20（太灵敏，一个短期回踩就破 → 质量分被对折、推荐被踢）。
    新护栏用「慢尺」：
      · 优先看基金自己的年线（MA250）：长期上行趋势还在，短期回踩 60 日低位也仍算“主线未断”；
      · 历史不足 250 日 → 回退到基金 MA120；
      · 再不足（<120 日）→ 看它押的板块 ETF 是否在 MA120（板块主线）上方；
      · 都无数据 → 不硬拦（fail-open）。
    返回值 (intact: bool, label: str, which: str)。
    """
    n = len(vs)
    fund_ok = None
    which = ''
    if n >= 250:
        m = ma(vs, 250)
        if m[-1] is not None:
            fund_ok = vs[-1] > m[-1]
            which = '年线(MA250)'
    elif n >= 120:
        m = ma(vs, 120)
        if m[-1] is not None:
            fund_ok = vs[-1] > m[-1]
            which = 'MA120'
    if fund_ok is not None:
        return bool(fund_ok), ('%s未断' % which if fund_ok else '%s已破' % which), which
    # 基金历史太短，用板块主线兜底
    if SECS and arena in SECS:
        st = sec_at(SECS, arena, date)
        if st and st.get('above120') is not None:
            return bool(st['above120']), ('板块主线(MA120)%s' % ('未断' if st['above120'] else '已破')), '板块MA120'
    return True, '慢尺数据不足(不拦)', 'none'


def slow_scale(f, SECS):
    """基金 dict 版封装（供 main 调用）"""
    vs = [x[1] for x in f['series']]
    return slow_scale_series(vs, f.get('arena', ''), f['m']['last_date'], SECS)


def sector_turned(arena, series, SECS):
    """板块动能拐头：板块动能分位曾 >0.85，且当前 <0.55 —— 长期主线转弱，是「该走」的一类触发。"""
    if not SECS or not arena:
        return False
    s3 = sec_at(SECS, arena, series[-1][0])
    rk = s3.get('mom_rk') if s3 else None
    past = [q['mom_rk'] for q in
            (sec_at(SECS, arena, d) for d in [x[0] for x in series][-RULE['s_look']:])
            if q and q.get('mom_rk') is not None]
    rank_max40 = max(past) if past else None
    return bool(rk is not None and rk < RULE['s_rank_lo']
                and rank_max40 is not None and rank_max40 > RULE['s_rank_hi'])


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
    # ---- 价位三档（阈值与收益见 RULE 注释，由 9D_audit.py 实测）----
    def _price_at(p):
        return round(lo60 + p * (hi60 - lo60), 4)
    tier_lines = dict(deep=_price_at(RULE['pos_tier_deep']),
                      cheap=_price_at(RULE['pos_tier_cheap']),
                      fair=_price_at(RULE['pos_tier_fair']))
    if pos60 <= RULE['pos_tier_deep']:
        tier, tier_name, tier_tone = 'deep', '深度便宜区', 'good'
        tier_txt = ('净值在近 60 日区间的下 10%。实测这一档之后 60 个交易日：中位 +5.57%、胜率 73.4%'
                    '（训练段 n=256），验证段 +2.85%；而且 5% 最差情形比「随便哪天买」还浅。'
                    '便宜到这个程度，下行风险反而更小 —— 这是全系统统计上最划算的一档。')
    elif pos60 <= RULE['pos_tier_cheap']:
        tier, tier_name, tier_tone = 'cheap', '便宜区', 'ok'
        tier_txt = ('净值在近 60 日区间的 10%~25%。实测这一档之后 60 个交易日中位 +3.15%、胜率 68.6%'
                    '（训练段 n=140），验证段 +1.87% —— 比深度便宜区略差，但仍是正超额。')
    elif pos60 <= RULE['pos_tier_fair']:
        tier, tier_name, tier_tone = 'fair', '合理区', 'ok'
        tier_txt = ('净值在近 60 日区间的 25%~40%。实测这一档之后 60 个交易日中位 +2.03%、胜率 61.1%'
                    '（训练段），验证段 +2.56% —— 温和正超额，是「可以买」的下沿。')
    else:
        tier, tier_name, tier_tone = 'high', '偏高区', 'warn'
        tier_txt = ('净值在近 60 日区间的 40% 以上。实测这一档之后 60 个交易日中位 +0.89%，'
                    '低于「随便哪天买」的 +1.63% —— 涨高了再买，统计上是负超额，这就是规则不给买点的原因。')
    stop = round(a60, 4)          # 60 日均线。它只是买点的趋势条件，**不是卖出信号**
    trend_up = a20 > a60 and not a60_dn
    # ★ 慢尺（年线/主线）判定：板块短期跌破 MA20/MA60 不算破，长期主线(年线/MA120)还在就是没断
    sec_ok = slow_scale(f, SECS)[0] if SECS else True
    slow_label = slow_scale(f, SECS)[1] if SECS else ''

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
    s2_ok = sector_turned(arena, f['series'], SECS)
    b_ok = bool(sec_ok and pos60 <= RULE['b_pos60_max'])

    sig = [
        dict(k='B', label='买入信号', hit=b_ok, rows=[
            dict(t='近 60 日位置 ≤ 40%',
                 now='%.0f%%' % (pos60 * 100), ok=pos60 <= RULE['b_pos60_max'],
                 need='要跌到 %.4f（%+.1f%%）' % (buy_line, (buy_line / c - 1) * 100)),
            dict(t='慢尺(年线/主线)未断',
                 now=(arena + ('：' + slow_label) if s3 else '无板块数据'),
                 ok=sec_ok, need=''),
            dict(t='短期走势（参考，非硬约束）',
                 now='20日%.4f %s 60日%.4f，%s' % (a20, '>' if a20 > a60 else '<', a60,
                                                 '上行' if not a60_dn else '下行'), ok=trend_ok, need=''),
        ], n_bad=sum(1 for x in (pos60 <= RULE['b_pos60_max'], sec_ok) if not x)),
        dict(k='S1', label='卖出信号 · 长期趋势破位', hit=(not sec_ok), rows=[
            dict(t='长期趋势(年线/主线)未破',
                 now=(arena + ('：' + slow_label) if s3 else '无板块数据'),
                 ok=sec_ok, need=''),
        ], n_bad=0 if sec_ok else 1),
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
        elif x['k'] == 'S1':
            x['verdict'] = '未触发（长期趋势未破）'
        else:
            x['verdict'] = '未触发（还差 %.1f 个百分点）' % abs(dd120 * 100 - RULE['s_dd120'] * 100)
    # 下一个会触发的信号 + 触发价，让「等什么」变成具体数字
    nxt = None
    ma250 = ma(vs, 250)
    if ma250 and ma250[-1] is not None and sec_ok:
        tl = round(ma250[-1], 4)
        nxt = dict(k='S1', what='长期趋势破位(跌破年线MA250)', price=tl, gap=(tl / c - 1) * 100)
    if not b_ok and pos60 > RULE['b_pos60_max']:
        cand = dict(k='B', what='买入', price=buy_line, gap=(buy_line / c - 1) * 100)
        if nxt is None or abs(cand['gap']) < abs(nxt['gap']):
            nxt = cand


    # 文案铁律：系统不知道你有没有持仓，所以每条都说清「已经买了的」「还没买的」各自怎么办，
    # 绝不出现「只能持有」「别加仓」这种替用户决定仓位的说法。
    # 卖出信号的真实口径（页面文案不得超出这两条，绝不能编「跌破均线就卖」）
    s_line = ('按规则，卖出信号只有两类：长期趋势（年线/主线）破位，或它押的板块动能拐头（板块主线转弱）。'
              '单看「从高点回撤了多少百分比」不算卖出信号——跌多了可能只是上涨途中的回调，会随机反弹。')

    if not sec_ok:
        state, tone = '该走 · 长期趋势破位', 'bad'
        act = ('它押的「%s」板块，长期主线（年线/MA120）已经破了（%s）。这才是真正的「该走」信号——'
               '长期上涨趋势破了，后面大概率是下跌或震荡，不是短暂回调。'
               '已经买了的：按纪律减仓/离场，别在破位下跌里扛。'
               '还没买的：长期趋势坏了，短期跌再多也不算买点。' % (arena, slow_label))
    elif s2_ok:
        state, tone = '该走 · 押的板块动能拐头', 'bad'
        act = ('它主要押的是「%s」板块。这个板块的动能分位从近 40 日内的最高 %.2f 掉到 %.2f，'
               '触发「板块动能拐头」—— 长期主线转弱，后面大概率是下跌或震荡。'
               '已经买了的：按纪律减仓/离场。还没买的：现在不是进场的时候。'
               % (arena, rank_max40 or 0, rk))
    elif sec_ok and pos60 <= RULE['b_pos60_max']:
        state, tone = '可以买 · 位置偏低', 'ok'
        act = ('三个条件同时满足：长期趋势未破（%s）、价格在近 60 个交易日区间的 %.0f%% 位置（规则要求 ≤%.0f%%）、'
               '质量分够格。这是买点——注意是「长期趋势 intact 下的低位」，短期还在不在探底不影响这个判断。'
               '买进去之后，%s 注意 60 日均线（%s）不是卖出线，它只是买点的趋势条件。'
               % (slow_label, pos60 * 100, RULE['b_pos60_max'] * 100, s_line, stop))
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
               '买入信号要求「长期趋势未破 + 位置低」，长期趋势这一条不成立。'
               '已经买了的：%s 都没触发，规则上不卖出，但也不会在这里加。'
               '还没买的：等它自己站回长期主线。' % (stop, s_line))
    else:
        state, tone = '先别买 · 方向没定', 'warn'
        act = ('基金自己的走势还没转强（20 日均线 %s 还没上穿 60 日均线 %s），长期趋势也未确认向上。'
               '买入信号和卖出信号都不成立。已经买了的：%s 都没触发，规则上没有动作。'
               '还没买的：先不动，等方向出来。' % (round(a20, 4), stop, s_line))
    # 18% 回撤不再是「该走」触发条件：跌多了可能只是上涨途中的回调，会随机反弹。
    # 只有当长期趋势(年线/主线)也破了，才算真该走。这里如实告知回撤幅度，但不作为卖出信号。
    if dd120 <= RULE['s_dd120'] and sec_ok:
        act += (' 另注：它从 120 日高点已回撤 %.1f%%，但长期趋势(年线/主线)未破，属上涨途中的回调，'
                '不是破位，不触发卖出；真要离场得等长期趋势破。' % (abs(dd120) * 100))

    # ★ 价位结论必须跟着一起说：state 讲的是「趋势+板块」是否确认，tier 讲的是「贵不贵」，
    #   两者可以不一致（深度便宜区里趋势往往还没转好）。不补这句，用户会觉得自相矛盾。
    if tier == 'deep':
        act += (' 另外看价位：它现在在「深度便宜区」（近 60 日区间下 10%），'
                '这一档历史上是收益最好的一档；但历史中位好不等于买了就涨，'
                '要不要在便宜区左侧分批，取决于你能不能承受它继续跌。')

    return dict(state=state, tone=tone, act=act,
                buy_line=buy_line, buy_lo=round(lo60, 4), buy_hi=round(hi60, 4),
                stop=stop, trigger_up=round(a20, 4), pos60=pos60, dd120=dd120,
                sec_ok=sec_ok, mom_rk=(round(rk, 2) if rk is not None else None),
                slow_label=slow_label,
                a60_dn=a60_dn, trend_up=trend_up, rule=RULE,
                sig=sig, next=nxt, stop_price=stop_price, hi120=round(hi120, 4),
                pos_tier=tier, pos_tier_name=tier_name, pos_tier_tone=tier_tone,
                pos_tier_txt=tier_txt, tier_lines=tier_lines,
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
    # 原始板块日线：算「净值 vs 押的板块」的真实相关性（04 只存了相关性 top3，押的板块未必在里面）
    SRAW = json.load(open(os.path.join(HERE, 'sectors.json'), encoding='utf-8'))
    SEC_RET = {s['label']: _rets(s['k']) for s in SRAW}

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
            # 持仓 → ETF 映射覆盖率：对不上 ETF 的行业按 0.5 中位并降权，
            # 覆盖率低时抱团分基本是常数，页面必须披露（实测最低 28.6%）
            hit = sum(x['pct'] for x in lst if x['sector'] in mom_of)
            f['hold_cover'] = round(hit / tot, 3) if tot else 0.0
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
            f['hold_cover'] = 0.0
            f['expo'] = []
            f['top10'] = []
            f['q_label'] = ''
            f['top10_sum'] = 0
            f['new_in'], f['dropped'] = [], []

        # ---------- 主战场统一用 arena（持仓第一大行业），拿不到持仓才退回相关性最高板块 ----------
        arena = f['expo'][0]['sector'] if f['expo'] else f['main_sector']
        f['arena'] = arena
        f['arena_mom'] = mom_of.get(arena, f['main_mom'])
        a_corr = None
        if arena in SEC_RET:
            a_corr = _corr60(_rets(f['series']), SEC_RET[arena])
        inferred_from_nav = a_corr is None
        if inferred_from_nav:
            a_corr = f['main_corr']
        a_healthy = bool(secs[arena]['healthy']) if arena in secs else bool(f['main_healthy'])
        f['arena_corr'] = round(a_corr, 3)
        f['arena_healthy'] = a_healthy
        # align 只描述「两个口径是不是同一个板块」，不评价好坏；weak 表示净值根本没跟着这个板块走
        if not f['expo']:
            f['align'] = 'nav'
        elif arena == f['main_sector']:
            f['align'] = 'aligned'
        else:
            f['align'] = 'split'
        f['arena_weak'] = bool(a_corr is not None and a_corr < 0.50)

        # s_herd 字段在 04 已算好（= 行业能力分），这里不再重算、也不乘板块健康门。
        # ★ 注意：质量分 f['score'] 由 04_score 统一定义（长期收益+经理+行业能力+风险），
        #   不在 06 重算——否则又会把质量分与短期板块状态绑死（旧矛盾根源）。
        #   f['herd_gate'] 在 main 末尾按「慢尺(年线/主线)状态」统一重设，这里不写。
        f['_inferred'] = inferred_from_nav

    pool.sort(key=lambda x: -x['score'])

    # ---- 每个基金的「慢尺状态」与「当前价位分位」（时机判定，不进质量分）----
    # ★ 关键修正：质量分已不含任何瞬时板块状态；这里单独算时机护栏。
    #   slow_intact=True 表示「年线/主线未断」——好基金短期回踩(板块跌破MA20但年线仍在)仍是好基金。
    #   herd_gate 改为慢尺状态镜像(1.0/0.5)，仅用于前端 chip 与自检兼容。
    for f in pool:
        p60, _ = cur_pos(f['series'])
        f['pos60'] = p60
        intact, slow_label, slow_which = slow_scale(f, SECS)
        f['slow_intact'] = intact
        f['slow_label'] = slow_label
        f['slow_which'] = slow_which
        f['herd_gate'] = 1.0 if intact else 0.5
        # ★ 该走互斥：长期趋势(年线/主线)已破，或板块动能拐头 → 绝不进推荐位（避免“让被套”）
        f['is_exit'] = (not intact) or sector_turned(f.get('arena', ''), f['series'], SECS)

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

    # ---- 推荐：质量优先 + 好价位 + 慢尺未断，宁缺毋滥 ----
    # ★ 2026-11-01 修正（解决“好基金跌到好价格就不是好基金”的矛盾）：
    #   买点 = 质量基金(质量分>=池中位) + 好价位(pos60<=合理区上沿) + 慢尺未断(年线/主线还在)。
    #   质量分本身已不随短期涨跌掉档；这里的门槛只卡“时机”，卡的是“慢尺”而非“板块MA20”。
    #   所以：好基金短期回踩、板块跌破MA20但年线仍在 → 质量分不掉、慢尺未断、跌到便宜区照样进推荐。
    # ① 慢尺未断（slow_intact）：年线/主线还在，长期趋势没坏。
    # ② 好价位：pos60 <= 合理区上沿(40%)，即“不追高”的硬约束。
    # ③ 质量门槛：质量分 >= 池中位，不拿低分基金凑数。
    # ④ 同注去重按「板块」判（2026-11-01 修正）：同一 arena 只取分数最高的一只。
    # ⑤ 再加一道相关性闸：不同板块但走势雷同（>=0.75）仍算同一个赌注。
    # ⑥ 凑不满 3 只就如实给更少，并在 picks_note 里写明挡住了什么。
    scores_sorted = sorted(f['score'] for f in pool)
    quality_med = scores_sorted[len(pool) // 2] if scores_sorted else 0
    qual = [f for f in pool
            if f['slow_intact']
            and f['pos60'] <= RULE['pos_tier_fair']
            and f['score'] >= quality_med
            and not f.get('is_exit')]
    picks = []
    used_arena = set()
    for f in qual:
        if len(picks) >= 3:
            break
        if f['arena'] in used_arena:
            continue
        if any(pcorr(f['code'], p['code']) >= 0.75 for p in picks):
            continue
        picks.append(f)
        used_arena.add(f['arena'])

    best_score = pool[0]['score'] if pool else 0
    rank_of = {f['code']: i + 1 for i, f in enumerate(pool)}
    picked_codes = {f['code'] for f in picks}
    for i, f in enumerate(picks):
        f['role'] = 'best' if i == 0 else 'spread'
        f['score_gap_best'] = round(best_score - f['score'], 1)
        f['rank_in_pool'] = rank_of.get(f['code'])
    n_slow_bad = sum(1 for f in pool if not f['slow_intact'])
    n_price_bad = sum(1 for f in pool if f['pos60'] > RULE['pos_tier_fair'])
    n_lowq = sum(1 for f in pool if f['score'] < quality_med)
    n_exit = sum(1 for f in pool if f.get('is_exit'))
    n_same_arena = sum(1 for f in qual
                       if f['code'] not in picked_codes and f['arena'] in used_arena)
    if len(picks) >= 3:
        picks_note = ''
    else:
        bits = []
        if n_exit:
            bits.append('%d 只长期趋势已破(该走)，不推' % n_exit)
        if n_slow_bad:
            bits.append('%d 只慢尺(年线/主线)已破，时机未到' % n_slow_bad)
        if n_price_bad:
            bits.append('%d 只价位偏高(>合理区)，等回调' % n_price_bad)
        if n_lowq:
            bits.append('%d 只质量分低于池中位' % n_lowq)
        if n_same_arena:
            bits.append('%d 只同样押「%s」已按同注去重只留最优' % (
                n_same_arena, '、'.join(sorted(used_arena))))
        picks_note = ('今天只给 %d 只：%d 只候选里 %s。'
                      '按「质量优先 + 好价位 + 慢尺未断 + 同注去重、宁缺毋滥」，'
                      '不为了凑满 3 只去推一只时机未到、或押同一个板块的基金。'
                      % (len(picks), len(pool), '；'.join(bits)))
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
            s_ret=f['s_ret'], s_trend=f['s_trend'], s_herd=f['s_herd'],
            s_skill=f.get('s_skill'), s_mgr=f.get('mgr_score'), s_risk=f['s_risk'],
            mgr_q=f.get('mgr_q'), mgr_sum=f.get('mgr_sum'),
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
            hold_mom=f['hold_mom'], hold_cover=f.get('hold_cover'),
            role=f.get('role'), score_gap_best=f.get('score_gap_best'),
            rank_in_pool=f.get('rank_in_pool'),
            arena_corr=f.get('arena_corr'), arena_healthy=f.get('arena_healthy'),
            align=f.get('align'), arena_weak=f.get('arena_weak'),
            slow_intact=f.get('slow_intact'), slow_label=f.get('slow_label'),
            pos60=f.get('pos60'),
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

    # ---- 历史信号的表现（前向验证的入口）：上一次推荐到现在，到底赚没赚 ----
    # 只用「当前净值回溯」算，不做任何假设；基金已不在池内就跳过（不虚构）
    slog_path = os.path.join(HERE, '..', 'data', 'signal-log.json')
    try:
        prev_log = json.load(open(slog_path, encoding='utf-8')) if os.path.exists(slog_path) else []
    except Exception:
        prev_log = []
    series_of = {x['code']: dict(x['series']) for x in sc['all']}
    today_d = pool[0]['m']['last_date']
    sig_perf = []
    for rec in prev_log[-6:]:
        if rec.get('date') == today_d:
            continue
        items = []
        for pk in rec.get('picks', []):
            s = series_of.get(pk['code'])
            if not s or rec['date'] not in s or today_d not in s or not s[rec['date']]:
                continue
            items.append(dict(code=pk['code'], name=pk.get('name', ''), score=pk.get('score'),
                              ret=round(s[today_d] / s[rec['date']] - 1, 4)))
        if items:
            sig_perf.append(dict(date=rec['date'], items=items))

    out = dict(
        meta=dict(gen=__import__('time').strftime('%Y-%m-%d %H:%M'),
                  data_date=pool[0]['m']['last_date'],
                  n_pool=len(sc['all']), n_scored=gate_ok,
                  n_gate=gate_ok, n_short=len(short_final), n_dropped_neg3m=dropped,
                  top_arena=top_arena, top_arena_n=top_n,
                  picks_note=picks_note, pool_median=round(quality_med, 1),
                  n_slow_bad=n_slow_bad, n_picks=len(picks),
                  signal_perf=sig_perf,
                  src='天天基金净值/季报持仓 · 腾讯ETF行情',
                  rf='板块代理=代表ETF；持仓分类按股票名称关键词，可人工核对'),
        sectors=sorted([_sector_row(s, SECS) for s in sc['sectors']],
                       key=lambda x: -x['mom']),
        picks=[brief(f, None) for f in picks],
        shortlist=[brief_all(f) for f in short_final],
    )
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    # ---- 全局价位状态（2026-11-01）：回答「今天到底有没有能买的价位」 ----
    # 用户要的是「买到好基金 + 进到好价位」，这是两个条件。分开数，才能诚实地说
    # 「今天没有一只同时满足」——而不是把一堆高位基金推出去让人自己去比。
    all_out = out['picks'] + out['shortlist']
    tstat = {'deep': 0, 'cheap': 0, 'fair': 0, 'high': 0}
    for p in all_out:
        tt = (p.get('adv') or {}).get('pos_tier') or 'high'
        tstat[tt] = tstat.get(tt, 0) + 1
    buyzone = [p for p in all_out
               if (p.get('adv') or {}).get('pos60', 1) <= RULE['b_pos60_max']
               and p.get('slow_intact', True)]
    # 最接近买点的清单：从全质量池(质量>=中位 且 慢尺未断)按当前价位分位升序取前 3，
    # 让「等什么价位」变成具体数字（即便今天还没到买点也能看到谁最接近）。
    near_pool = [f for f in pool if f['slow_intact'] and f['score'] >= quality_med
                  and not f.get('is_exit')]
    near_pool.sort(key=lambda f: f['pos60'])

    def _fair_line(series):
        vs = [x[1] for x in series]
        seg = vs[-60:]
        lo, hi = min(seg), max(seg)
        return round(lo + RULE['pos_tier_fair'] * (hi - lo), 4)

    near_buy = []
    for f in near_pool[:3]:
        line = _fair_line(f['series'])
        last = f['m']['last']
        near_buy.append(dict(
            code=f['code'], name=f['name'], score=f['score'],
            pos60=f['pos60'], line=line,
            to_line=(round((line / last - 1) * 100, 1) if last else None)))
    out['meta']['tier_stat'] = tstat
    out['meta']['n_in_buyzone'] = len(buyzone)
    out['meta']['near_buy'] = near_buy
    print('\n价位分布: 深度便宜 %d / 便宜 %d / 合理 %d / 偏高 %d  → 可买区(≤40%% 且慢尺未断) %d 只' % (
        tstat['deep'], tstat['cheap'], tstat['fair'], tstat['high'], len(buyzone)))
    for x in near_buy:
        print('  最接近买点: %-22s 分数%5.1f 位置%3.0f%% 需跌 %s%% 到 %s' % (
            x['name'][:18], x['score'], (x['pos60'] or 0) * 100,
            x['to_line'] if x['to_line'] is not None else '—', x['line']))

    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('\n-> %s  %.0f KB' % (OUT, os.path.getsize(OUT) / 1024))

    # ---- 信号台账（2026-11-01 新增）：每天追加一条，滚动 260 期 ----
    # 为什么必须留档：data.json 是覆盖式写入、只有「今天」。没有台账就无法回答
    # 「上次推荐之后到底怎样了」，也做不了任何前向验证 —— 系统的判断力就永远无法被考核。
    slog = os.path.join(HERE, '..', 'data', 'signal-log.json')
    try:
        hist = json.load(open(slog, encoding='utf-8')) if os.path.exists(slog) else []
    except Exception:
        hist = []
    rec = dict(
        date=out['meta']['data_date'], gen=out['meta']['gen'],
        picks=[dict(code=p['code'], name=p['name'], score=p['score'], role=p.get('role'),
                    arena=p['arena'], tier=(p.get('adv') or {}).get('pos_tier'),
                    pos60=(p.get('adv') or {}).get('pos60'),
                    state=(p.get('adv') or {}).get('state')) for p in out['picks']],
        arena_top=[s['label'] for s in out['sectors'][:3]],
        note=out['meta'].get('picks_note', ''),
    )
    hist = [x for x in hist if x.get('date') != rec['date']] + [rec]
    hist = sorted(hist, key=lambda x: x.get('date') or '')[-260:]
    json.dump(hist, open(slog, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('-> signal-log.json  %d 期（%s ~ %s）' % (
        len(hist), hist[0].get('date', '-') if hist else '-', hist[-1].get('date', '-') if hist else '-'))


if __name__ == '__main__':
    main()

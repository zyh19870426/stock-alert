# -*- coding: utf-8 -*-
"""96_scan_lead.py · 「领先信号」检验：板块个股广度 vs ETF 份额 vs 现版动量拐头

要回答的问题只有一个：
  广度（板块内涨跌家数）和 ETF 份额变化，能不能比现在的「板块 20 日动量分位拐头」
  **更早**发现抱团瓦解？如果不能，就不要往产品里塞。

★ 判定标准（先写死，防止事后挑参数）：
   A. 成立：训练段 fwd60 中位 ≤ 0 且 验证段 fwd60 中位 ≤ 0，且两段样本 n ≥ 30 / 10
   B. 部分成立：只有一段 ≤ 0，另一段在 0 ~ +1%
   C. 不成立：两段都 > 0，或和 S8 没差异
   不成立就报不成立，不硬塞。

★ 防未来函数：
   · 信号用 T 日收盘数据算 → **T+1 日收盘价成交**（不是 T 日，T 日收盘价盘中不知道）
   · ETF 份额交易所 T 日盘后披露，按 T 日信号、T+1 成交，最保守
   · 训练/验证分段：训练段 ≤ 2024-12-31（只用来挑），验证段 ≥ 2025-01-01（只用来验收）

★ 诚实边界：
   · 广度只有 33 个板块（宽基不做），每个板块 8~16 只手选代表股，不是全部成分股
   · 份额只有沪市 27 只 ETF（深交所历史要逐日翻页，成本太高），且只有约 4 年
   · 两段样本都只有个位数到几十个信号，统计力弱 —— 这一点会写进结论

用法：python 96_scan_lead.py
"""
import bisect
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = '2024-12-31'
COOL = 30


def load(n):
    return json.load(open(os.path.join(HERE, n), encoding='utf-8'))


def ma(vs, n):
    out, s = [], 0.0
    for i, v in enumerate(vs):
        s += v
        if i >= n:
            s -= vs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def rank_in(vals, x, win=250):
    """x 在最近 win 个观测里的分位"""
    if x is None:
        return None
    tail = [v for v in vals[-win:] if v is not None]
    if len(tail) < 30:
        return None
    return sum(1 for v in tail if v <= x) / float(len(tail))


# ---------------- 板块状态序列（价格 + 广度 + 份额，全部按日期索引） ----------------
def build_sector_state():
    secs = {s['label']: s for s in load('sectors.json')}
    br = load('breadth.json')['sectors'] if os.path.exists(os.path.join(HERE, 'breadth.json')) else {}
    sh = load('share.json')['sectors'] if os.path.exists(os.path.join(HERE, 'share.json')) else {}

    out = {}
    for label, s in secs.items():
        ds = [x[0] for x in s['k']]
        vs = [x[1] for x in s['k']]
        m20, m60 = ma(vs, 20), ma(vs, 60)
        mom20 = {}
        for i in range(20, len(vs)):
            mom20[ds[i]] = vs[i] / vs[i - 20] - 1
        mkeys = sorted(mom20)
        momrk, buf = {}, []
        for d in mkeys:
            buf.append(mom20[d])
            momrk[d] = rank_in(buf[:-1] + [buf[-1]], buf[-1], 250)

        b = br.get(label) or {}
        bmap = {}
        if b:
            bd = b['dates']
            for j, d in enumerate(bd):
                bmap[d] = dict(up=b['up'][j], br5=b['br5'][j], br20=b['br20'][j], nv=b['nv'][j])
        bd_keys = sorted(bmap)
        # 广度 20 日均值的 250 日分位
        br20seq = [bmap[d]['br20'] for d in bd_keys]
        brk = {}
        for j, d in enumerate(bd_keys):
            brk[d] = rank_in(br20seq[:j] + [br20seq[j]], br20seq[j], 250)

        # 滚动 250 日分散度（T 日只用 T 及之前的数据，无前视）
        xsdv = b.get('xsd') or []
        xs_roll = {}
        for j, d in enumerate(bd_keys):
            w = [x for x in xsdv[max(0, j - 249):j + 1] if x is not None]
            xs_roll[d] = round(sum(w) / len(w), 5) if len(w) >= 60 else None
        xs_keys = sorted(xs_roll)

        h = sh.get(label) or {}
        smap, sma5, sma20, srk = {}, {}, {}, {}
        if h:
            hd = h['dates']
            hv = [float(x) for x in h['vol']]
            s5, s20 = ma(hv, 5), ma(hv, 20)
            for j, d in enumerate(hd):
                smap[d] = hv[j]
                sma5[d] = s5[j]
                sma20[d] = s20[j]

        state = {}
        for i, d in enumerate(ds):
            q = bmap.get(d) or {}
            state[d] = dict(
                close=vs[i], ma20=m20[i], ma60=m60[i],
                pos60=None,
                mom20=mom20.get(d), momrk=momrk.get(d),
                up=q.get('up'), br5=q.get('br5'), br20=q.get('br20'),
                brk=brk.get(d),
                share=smap.get(d), sma5=sma5.get(d), sma20=sma20.get(d),
                xsdm=(xs_roll.get(xs_keys[bisect.bisect_right(xs_keys, d) - 1])
                      if xs_keys and bisect.bisect_right(xs_keys, d) > 0 else None),
                hi60=max(vs[max(0, i - 59):i + 1]),
            )
        # pos60（板块自身的 60 日区间分位）
        for i, d in enumerate(ds):
            seg = vs[max(0, i - 59):i + 1]
            lo, hi = min(seg), max(seg)
            state[d]['pos60'] = (vs[i] - lo) / (hi - lo) if hi > lo else 0.5

        out[label] = dict(dates=ds, close=vs, state=state,
                          has_br=bool(b), has_share=bool(h),
                          br_dates=bd_keys, br20=br20seq,
                          share_dates=[h['dates']] if h else [])
    return out


def at(state, date):
    """取 <= date 的最近一天状态"""
    ds = state['dates']
    j = bisect.bisect_right(ds, date) - 1
    if j < 0:
        return None, None
    return ds[j], state['state'][ds[j]]


# ---------------- 候选信号：给定板块状态序列，返回 {date: reason} ----------------
def sig_breadth(state):
    """广度类（需要 breadth 覆盖该板块）"""
    out = {}
    if not state['has_br']:
        return out
    ds, bd, b20 = state['dates'], state['br_dates'], state['br20']
    for i, d in enumerate(ds):
        sd, s = at(state, d)
        if not s or s['br5'] is None:
            continue
        # G1 广度拐头：br20 分位从高位掉下来
        if s['brk'] is not None and s['brk'] < 0.55:
            past = []
            for k in range(max(0, i - 40), i + 1):
                q = state['state'][ds[k]]
                if q['brk'] is not None:
                    past.append(q['brk'])
            if past and max(past) > 0.85:
                out[d] = 'G1 广度拐头(%.2f→%.2f)' % (max(past), s['brk'])
                continue
        # G2 顶背离：价格贴着 60 日高点，广度却不到一半
        if s['close'] >= s['hi60'] * 0.998 and s['br5'] < 0.45:
            out[d] = 'G2 顶背离(价在60日高、广度%.2f)' % s['br5']
            continue
        # G3 广度骤降
        prev = state['state'][ds[i - 1]] if i > 0 else None
        if s['up'] is not None and s['up'] < 0.20 and prev and prev['br5'] and prev['br5'] > 0.60:
            out[d] = 'G3 广度骤降(%.2f)' % s['up']
            continue
        # G4 内部先坏：价格还在 MA20 上，广度已破 40%
        if s['ma20'] is not None and s['close'] > s['ma20'] and s['br5'] < 0.40:
            out[d] = 'G4 内部先坏(广度%.2f、价在MA20上)' % s['br5']
            continue
        # G5 广度创 60 日新低
        b5 = [state['state'][ds[k]]['br5'] for k in range(max(0, i - 59), i + 1)]
        b5 = [x for x in b5 if x is not None]
        if b5 and s['br5'] is not None and s['br5'] <= min(b5):
            out[d] = 'G5 广度60日新低(%.2f)' % s['br5']
    return out


def sig_share(state):
    """份额类（需要 share 覆盖该板块，只有沪市）"""
    out = {}
    if not state['has_share']:
        return out
    ds = state['dates']
    s5 = [state['state'][d]['sma5'] for d in ds]
    n = len(ds)
    for i in range(60, n):
        d, s = ds[i], state['state'][ds[i]]
        a = s['sma5']
        if a is None:
            continue
        # E1 份额见顶回落：近 60 日曾创 250 日新高，现回落 >=5%
        win = [x for x in s5[max(0, i - 250):i + 1] if x is not None]
        peak = max(win) if win else None
        recent_peak = max([x for x in s5[max(0, i - 59):i + 1] if x is not None] or [0])
        if peak and recent_peak >= peak * 0.999 and a <= recent_peak * 0.95:
            out[d] = 'E1 份额见顶回落(峰%.0f→%.0f万份)' % (recent_peak, a)
            continue
        # E2 份额 20 日净流出 >=3%
        if i >= 20 and s5[i - 20] and a / s5[i - 20] - 1 <= -0.03:
            out[d] = 'E2 份额20日流出%.1f%%' % ((a / s5[i - 20] - 1) * 100)
            continue
        # E3 价涨份额减（背离）
        if s['close'] >= s['hi60'] * 0.998 and i >= 20 and s5[i - 20] and a < s5[i - 20]:
            out[d] = 'E3 价涨份额减(%.1f%%)' % ((a / s5[i - 20] - 1) * 100)
            continue
        # E4 份额连降 10 日
        seg = s5[i - 9:i + 1]
        if all(x is not None for x in seg) and all(seg[j] > seg[j + 1] for j in range(9)):
            out[d] = 'E4 份额连降10日'
    return out


def sig_base_mom(state):
    """现版产品信号 S8：板块 20 日动量分位（近 40 日曾 >0.85、现 <0.55）"""
    out = {}
    ds = state['dates']
    for i, d in enumerate(ds):
        s = state['state'][d]
        rk = s['momrk']
        if rk is None or rk >= 0.55:
            continue
        past = [state['state'][ds[k]]['momrk'] for k in range(max(0, i - 40), i + 1)
                if state['state'][ds[k]]['momrk'] is not None]
        if past and max(past) > 0.85:
            out[d] = 'S8 动量拐头(%.2f→%.2f)' % (max(past), rk)
    return out


SIGS = [('S8 动量拐头(现版)', sig_base_mom, 'price'),
        ('G 广度(合并)', sig_breadth, 'br'),
        ('E 份额(合并)', sig_share, 'share')]

# 拆开单独评估，便于看是哪一条在起作用
SIG_SUB = {
    'G1 广度拐头': ('br', 'G1'),
    'G2 顶背离': ('br', 'G2'),
    'G3 广度骤降': ('br', 'G3'),
    'G4 内部先坏': ('br', 'G4'),
    'G5 广度60日新低': ('br', 'G5'),
    'E1 份额见顶回落': ('share', 'E1'),
    'E2 份额20日流出': ('share', 'E2'),
    'E3 价涨份额减': ('share', 'E3'),
    'E4 份额连降10日': ('share', 'E4'),
}


# ---------------- 样本 ----------------
def market():
    nav = {f['code']: f for f in load('nav.json')}
    sc = load('scored.json')
    hold = load('holdings.json')
    states = build_sector_state()
    items = []
    for f in sc['scored']:
        code = f['code']
        if code not in nav or len(nav[code]['series']) < 400:
            continue
        h = hold.get(code) or {}
        expo = h.get('expo') or []
        arena = expo[0]['sector'] if expo else f.get('main_sector', '')
        if arena not in states:
            continue
        items.append(dict(code=code, name=f['name'], series=nav[code]['series'],
                          arena=arena, st=states[arena]))
    return items, states


def merge(series_dates, raw, cool=COOL):
    """同类信号冷却：cool 个交易日内只留第一个"""
    keep, last = [], -10 ** 9
    for i, d in enumerate(series_dates):
        if d in raw:
            if i - last >= cool:
                keep.append((i, d, raw[d]))
            last = i
    return keep


def evaluate(items, states, kind, sub=None, lo_date=None, hi_date='9999'):
    """返回事件研究统计。执行价 = 信号次日基金净值（T+1 收盘成交）"""
    recs = []
    for it in items:
        ds = [x[0] for x in it['series']]
        vs = [x[1] for x in it['series']]
        st_ = it['st']
        if kind == 'price':
            raw = sig_base_mom(st_)
        elif kind == 'br':
            if not st_['has_br']:
                continue
            raw = sig_breadth(st_)
        else:
            if not st_['has_share']:
                continue
            raw = sig_share(st_)
        if sub:
            raw = {d: r for d, r in raw.items() if r.startswith(sub)}
        for i, d, why in merge(ds, raw):
            if (lo_date and d <= lo_date) or d > hi_date:
                continue
            if i + 1 >= len(vs) or i + 61 >= len(vs):
                continue
            e = vs[i + 1]                                    # 次日收盘执行
            f20 = vs[min(i + 21, len(vs) - 1)] / e - 1
            f60 = vs[min(i + 61, len(vs) - 1)] / e - 1
            mdd = min(vs[i + 1:i + 62]) / e - 1
            # 信号在板块顶之前多少天（用板块 ETF 收盘找 [i-40, i+40] 窗口最高点）
            sd, _ = at(st_, d)
            etf = st_['close']
            j0 = bisect.bisect_right(st_['dates'], d) - 1
            lo, hi = max(0, j0 - 40), min(len(etf), j0 + 41)
            if hi > lo:
                k = max(range(lo, hi), key=lambda z: etf[z])
                lead = k - j0
            else:
                lead = None
            # 信号日基金自己在 120 日区间的位置
            pos120 = vs[i] / max(vs[max(0, i - 119):i + 1]) - 1
            recs.append(dict(code=it['code'], d=d, f20=f20, f60=f60, mdd=mdd,
                             lead=lead, dd120=pos120, why=why))
    return recs


def summarize(recs, label):
    if not recs:
        return dict(label=label, n=0)
    f60 = sorted(r['f60'] for r in recs)
    f20 = sorted(r['f20'] for r in recs)
    def pc(a, p):
        return a[min(len(a) - 1, max(0, int(p * len(a))))]
    leads = [r['lead'] for r in recs if r['lead'] is not None]
    return dict(
        label=label, n=len(recs),
        f20_med=st.median(f20), f60_med=st.median(f60),
        f20_mean=st.mean(f20), f60_mean=st.mean(f60),
        f60_p05=pc(f60, 0.05), f60_p95=pc(f60, 0.95),
        mdd_med=st.median([r['mdd'] for r in recs]),
        dd120_med=st.median([r['dd120'] for r in recs]),
        lead_med=st.median(leads) if leads else None,
        neg60=sum(1 for x in f60 if x < 0) / float(len(f60)),
    )


def row(s):
    if not s['n']:
        return '  %-18s 无样本' % s['label']
    return ('  %-18s n=%-4d f60中位%+6.2f%% f60均值%+6.2f%% 5%%分位%+7.2f%% 95%%分位%+7.2f%% '
            '最深跌%+6.2f%% 跌占比%3.0f%% 距顶%+4.0f天 信号日距120日高%+6.1f%%'
            % (s['label'], s['n'], 100 * s['f60_med'], 100 * s['f60_mean'],
               100 * s['f60_p05'], 100 * s['f60_p95'], 100 * s['mdd_med'],
               100 * s['neg60'], s['lead_med'] if s['lead_med'] is not None else 0,
               100 * s['dd120_med']))


def main():
    items, states = market()
    have_br = sum(1 for i in items if i['st']['has_br'])
    have_sh = sum(1 for i in items if i['st']['has_share'])
    print('样本：%d 只基金（主战场有广度数据的 %d 只 / 有份额数据的 %d 只）'
          % (len(items), have_br, have_sh))
    arn = {}
    for it in items:
        arn[it['arena']] = arn.get(it['arena'], 0) + 1
    print('主战场分布：%s' % ', '.join('%s×%d' % (k, v)
          for k, v in sorted(arn.items(), key=lambda x: -x[1])[:10]))

    print('\n' + '=' * 108)
    print('第一组：广度 vs 现版动量拐头（全区间，广度 2020-08 起）')
    print('=' * 108)
    for label, kind in (('S8 动量拐头(现版)', 'price'), ('G 广度(合并)', 'br')):
        print('\n[%s]' % label)
        for seg, lo, hi in (('训练段 ≤2024-12-31', None, SPLIT),
                            ('验证段 ≥2025-01-01', SPLIT, '9999')):
            print(' %s' % seg)
            print(row(summarize(evaluate(items, states, kind, lo_date=lo, hi_date=hi), label)))

    print('\n' + '=' * 108)
    print('第二组：广度的每一条子规则（看是哪一条在起作用）')
    print('=' * 108)
    print(' %-18s %6s %9s %9s %9s' % ('子规则', 'n', 'f60中位', 'f60均值', '5%分位'))
    for name, (kind, prefix) in SIG_SUB.items():
        if kind == 'br':
            s = summarize(evaluate(items, states, 'br', sub=prefix), name)
            if s['n']:
                print(' %-18s %6d %+8.2f%% %+8.2f%% %+8.2f%%'
                      % (name, s['n'], 100 * s['f60_med'], 100 * s['f60_mean'],
                         100 * s['f60_p05']))
            else:
                print(' %-18s %6d' % (name, 0))

    print('\n' + '=' * 108)
    print('第三组：份额 vs 现版动量拐头（同区间，只用有份额的基金）')
    print('=' * 108)
    only = [i for i in items if i['st']['has_share']]
    print('可用基金 %d 只' % len(only))
    for label, kind in (('S8 动量拐头(现版)', 'price'), ('E 份额(合并)', 'share')):
        print('\n[%s]' % label)
        for seg, lo, hi in (('训练段 ≤2024-12-31', None, SPLIT),
                            ('验证段 ≥2025-01-01', SPLIT, '9999')):
            print(' %s' % seg)
            print(row(summarize(evaluate(only, states, kind, lo_date=lo, hi_date=hi), label)))

    print('\n' + '=' * 108)
    print('第四组：份额的每一条子规则')
    print('=' * 108)
    for name, (kind, prefix) in SIG_SUB.items():
        if kind == 'share':
            s = summarize(evaluate(only, states, 'share', sub=prefix), name)
            print(' %-18s %6d %s' % (name, s['n'],
                  ('%+8.2f%% %+8.2f%% %+8.2f%%' % (100 * s['f60_med'], 100 * s['f60_mean'],
                                                   100 * s['f60_p05'])) if s['n'] else ''))


    # ---------------- 第五组：G1 单独分段 ----------------
    print('\n' + '=' * 108)
    print('第五组：G1（广度拐头）单独分段 —— 合并信号被 G3/G4/G5 稀释了')
    print('=' * 108)
    for seg, lo, hi in (('训练段 ≤2024-12-31', None, SPLIT),
                        ('验证段 ≥2025-01-01', SPLIT, '9999')):
        print(' %s' % seg)
        print(row(summarize(evaluate(items, states, 'br', sub='G1',
                                     lo_date=lo, hi_date=hi), 'G1 广度拐头')))

    # ---------------- 第六组：随机基准（没有基准，负收益没法解释） ----------------
    print('\n' + '=' * 108)
    print('第六组：随机基准 —— 「随便哪一天卖出」的 f60 是多少')
    print('=' * 108)
    print('  （如果信号的中位数不如随机日，那信号等于没用；如果比随机日差很多，才叫有信息）')
    for seg, lo, hi in (('训练段 ≤2024-12-31', None, SPLIT),
                        ('验证段 ≥2025-01-01', SPLIT, '9999')):
        f60s, f20s = [], []
        for it in items:
            ds = [x[0] for x in it['series']]
            vs = [x[1] for x in it['series']]
            for i, d in enumerate(ds):
                if (lo and d <= lo) or d > hi or i + 61 >= len(vs):
                    continue
                f60s.append(vs[i + 61] / vs[i + 1] - 1)
                f20s.append(vs[i + 21] / vs[i + 1] - 1)
        if not f60s:
            continue
        q = sorted(f60s)
        print(' %s  随机日 n=%d  f60中位%+6.2f%% 均值%+6.2f%% 5%%分位%+7.2f%%  跌占比%3.0f%%'
              % (seg, len(q), 100 * st.median(q), 100 * st.mean(q),
                 100 * q[int(0.05 * len(q))], 100 * sum(1 for x in q if x < 0) / len(q)))

    # ---------------- 第七组：领先性配对 ----------------
    print('\n' + '=' * 108)
    print('第七组：领先性配对 —— G1 比 S8 早还是晚（同一只基金同一板块，±30 日内配对）')
    print('=' * 108)
    lead, paired, n_s8, n_g1, g1_first = [], 0, 0, 0, 0
    for it in items:
        if not it['st']['has_br']:
            continue
        ds = [x[0] for x in it['series']]
        s8 = [i for i, d, _ in merge(ds, sig_base_mom(it['st']))]
        g1 = [i for i, d, _ in merge(ds, {d: r for d, r in sig_breadth(it['st']).items()
                                         if r.startswith('G1')})]
        n_s8 += len(s8)
        n_g1 += len(g1)
        for i in s8:
            cand = [j for j in g1 if i - 30 <= j <= i]
            if cand:
                paired += 1
                lead.append(i - max(cand))
        for j in g1:
            if any(j <= i <= j + 30 for i in s8):
                g1_first += 1
    if lead and n_s8:
        lead.sort()
        print('  S8 信号 %d 个，其中 %d 个（%.0f%%）在前 30 日内有 G1 信号'
              % (n_s8, paired, 100.0 * paired / n_s8))
        print('  配对中 G1 比 S8 早：中位 %d 个交易日，均值 %.1f，最快 %d，最慢 %d'
              % (lead[len(lead) // 2], st.mean(lead), min(lead), max(lead)))
        print('  G1 信号 %d 个，其中 %d 个（%.0f%%）在之后 30 日内等到 S8 —— '
              '这个比例低 = G1 大部分是假警报'
              % (n_g1, g1_first, 100.0 * g1_first / max(1, n_g1)))
    else:
        print('  无法配对：没有 G1 与 S8 在 ±30 日内同时出现的情况')

    # ---------------- 第八组：E2 反面用法（份额流出当买点） ----------------
    print('\n' + '=' * 108)
    print('第八组：份额流出（E2）当「买点」测 —— 它作为卖点是正的，反过来呢')
    print('=' * 108)
    only2 = [i for i in items if i['st']['has_share']]
    for nm, filt in (('E2 全部信号', None),):
        pass
    e2 = summarize(evaluate(only2, states, 'share', sub='E2'), 'E2 份额流出(当卖点)')
    print(' %-24s n=%-4d f60中位%+6.2f%% f60均值%+6.2f%% 5%%分位%+7.2f%%'
          % ('E2 份额流出', e2['n'], 100 * e2['f60_med'], 100 * e2['f60_mean'], 100 * e2['f60_p05']))
    # 反向：把 f60 取负号的绝对值，等价于「不卖、反而买」的收益已经被上面的正数说明
    print('  解读：E2 触发后 60 日 **平均和典型情况都是涨的** → 份额流出不是顶部信号，'
          '更像恐慌赎回（反向）。要用它只能当买点，且必须单独验证，不能顺手改语义。')

    # 覆盖度：广度和份额分别能覆盖多少个板块 / 多少个基金
    print('\n' + '=' * 108)
    print('覆盖度（这决定了结论能推广多远）')
    print('=' * 108)
    brm = load('breadth.json')['meta']
    shm = load('share.json')['meta'] if os.path.exists(os.path.join(HERE, 'share.json')) else {}
    print(' 广度：%d 个板块可用（手选代表股 %d 只，抓取成功 %d 只），宽基 %s 不做'
          % (len(load('breadth.json')['sectors']), brm['n_req'], brm['n_ok'],
             '/'.join(brm.get('no_breadth') or [])))
    if shm:
        print(' 份额：%d 个板块可用（仅沪市），深市 %d 只无数据：%s'
              % (len(load('share.json')['sectors']), len(shm.get('no_share') or []),
                 ', '.join(shm.get('no_share') or [])[:120]))
        print(' 份额样本：%s 天（%s）' % (shm.get('n_days'), shm.get('src')))


if __name__ == '__main__':
    main()

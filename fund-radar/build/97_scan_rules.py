# -*- coding: utf-8 -*-
"""
97_scan_rules.py · 买卖点候选规则扫描（先挑规则，再改产品）
--------------------------------------------------------------------------
上一版（v2）在 98_diag 里没通过检验：频率降下来了，但
  · B 位置中位仍 0.73（追高没解决）
  · S 后 20 日 +1.34%（卖了还在涨，比 v1 更差）
说明「用 MA60 判抱团瓦解」还是太滞后。所以这里不再拍脑袋改参数，
而是把一个**候选规则池**全部测一遍，用数据挑。

★ 防过拟合（硬性）：按时间切成两段
   训练段 = 2024-12-31 之前   → 只在这里挑规则
   验证段 = 2025-01-01 之后   → 只用来验证，不参与挑选
   两段都成立才允许进产品；只有训练段成立的，报出来但不采用。

每条规则报四个数：触发次数/年、信号日 pos60 中位、信号后 20 日收益均值、信号后 60 日收益均值。
用法：python 97_scan_rules.py
"""
import json, os, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
SPLIT = '2024-12-31'
COOL = 30          # 与产品 06_final.RULE 的冷却一致（交易日），扫描口径不能比产品宽


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


def mkt():
    nav = {f['code']: f for f in load('nav.json')}
    sc = load('scored.json')
    hold = load('holdings.json')
    secs, vols = {}, {}
    for s in load('sectors.json'):
        secs[s['label']] = {x[0]: x[1] for x in s['k']}
        vols[s['label']] = {x[0]: (x[2] if len(x) > 2 else 0) for x in s['k']}

    items = []
    for f in sc['scored']:
        code = f['code']
        if code not in nav or len(nav[code]['series']) < 400:
            continue
        h = hold.get(code) or {}
        expo = h.get('expo') or []
        arena = expo[0]['sector'] if expo else f.get('main_sector', '')
        items.append(dict(code=code, name=f['name'], series=nav[code]['series'],
                          arena=arena, sec=secs.get(arena, {}), vol=vols.get(arena, {})))
    return items


# ---------------- 候选规则：返回 [(index, 额外信息)] ----------------
def ctx(it):
    series = it['series']
    ds = [x[0] for x in series]
    vs = [x[1] for x in series]
    n = len(vs)
    m20, m60, m120 = ma(vs, 20), ma(vs, 60), ma(vs, 120)
    pos60, dd120 = [None] * n, [None] * n
    for i in range(n):
        seg = vs[max(0, i - 59):i + 1]
        lo, hi = min(seg), max(seg)
        pos60[i] = (vs[i] - lo) / (hi - lo) if hi > lo else 0.5
        dd120[i] = vs[i] / max(vs[max(0, i - 119):i + 1]) - 1
    # 板块 ETF 的 5/20 日动量，按日期对齐
    sk = it['sec']
    sd = sorted(sk)
    smom5, smom20 = {}, {}
    for j in range(20, len(sd)):
        d = sd[j]
        v, v5, v20 = sk[d], sk[sd[j - 5]], sk[sd[j - 20]]
        smom5[d] = v / v5 - 1 if v5 else 0.0
        smom20[d] = v / v20 - 1 if v20 else 0.0
    # 板块放量下跌：当日跌幅 + 量比（量 / 20日均量）
    vk = it.get('vol') or {}
    sv = sorted(vk)
    sdrop, svr = {}, {}
    for j in range(20, len(sv)):
        d = sv[j]
        prev = sk.get(sv[j - 1]) or 0
        sdrop[d] = (sk[d] / prev - 1) if prev else 0.0
        avg = sum(vk[sv[j - k]] for k in range(1, 21)) / 20.0
        svr[d] = vk[d] / avg if avg else 1.0
    # 板块 20 日动量在近 250 个观测里的分位（用于「动量拐头」）
    srank = {}
    buf = []
    for d in sd[20:]:
        buf.append(smom20.get(d, 0))
        tail = buf[-250:]
        srank[d] = sum(1 for x in tail if x <= buf[-1]) / len(tail)
    return ds, vs, m20, m60, m120, pos60, dd120, smom5, smom20, sdrop, svr, srank


def rules_b(it, c):
    """候选买点：返回 {name: [index...]}"""
    ds, vs, m20, m60, m120, pos60, dd120, sm5, sm20, sdrop, svr, srank = c
    n = len(vs)
    out = {k: [] for k in ('B1 回踩MA20', 'B2 回踩MA60', 'B3 pos60低位',
                           'B4 创60日新高', 'B5 急跌后首阳', 'B6 板块动量转正')}
    for i in range(121, n):
        if m20[i] is None or m60[i] is None or m120[i] is None:
            continue
        if not (m20[i] > m60[i] and m60[i] > m60[i - 20]):
            continue                       # 所有买点都要求大趋势向上，先统一
        d = ds[i]
        if vs[i] <= m20[i] * 1.03:
            out['B1 回踩MA20'].append(i)
        if vs[i] <= m60[i] * 1.02:
            out['B2 回踩MA60'].append(i)
        if pos60[i] <= 0.40:
            out['B3 pos60低位'].append(i)
        if vs[i] >= max(vs[max(0, i - 59):i + 1]) * 0.999:
            out['B4 创60日新高'].append(i)
        if (min(vs[i - 5:i]) / vs[i - 6] - 1 <= -0.05) and vs[i] > vs[i - 1]:
            out['B5 急跌后首阳'].append(i)
        if sm5.get(d, 0) > 0 and sm20.get(d, 0) > 0 and vs[i] > m60[i]:
            out['B6 板块动量转正'].append(i)
    return out


def rules_s(it, c):
    ds, vs, m20, m60, m120, pos60, dd120, sm5, sm20, sdrop, svr, srank = c
    n = len(vs)
    out = {k: [] for k in ('S1 破MA20', 'S2 破MA60+MA60下行', 'S3 板块破MA60',
                           'S4 板块动量转负', 'S5 相对强度转负', 'S6 回撤18%',
                           'S7 S4或S6', 'S8 板块动量拐头', 'S9 双弱破MA20',
                           'S10 板块放量下跌', 'S11 早回撤+板块弱')}
    for i in range(121, n):
        if m20[i] is None or m60[i] is None:
            continue
        d = ds[i]
        if vs[i] < m20[i]:
            out['S1 破MA20'].append(i)
        if vs[i] < m60[i] and m60[i] < m60[i - 20]:
            out['S2 破MA60+MA60下行'].append(i)
        if vs[i] < m60[i] * 0.995 and sm20.get(d, 0) < 0:
            out['S3 板块破MA60'].append(i)
        if sm5.get(d, 0) < 0 and sm20.get(d, 0) < 0.02:
            out['S4 板块动量转负'].append(i)
        if (vs[i] / vs[i - 20] - 1) - sm20.get(d, 0) < -0.03:
            out['S5 相对强度转负'].append(i)
        if dd120[i] <= -0.18:
            out['S6 回撤18%'].append(i)
        if (sm5.get(d, 0) < 0 and sm20.get(d, 0) < 0.02) or dd120[i] <= -0.18:
            out['S7 S4或S6'].append(i)
        # 动量拐头：20日动量分位从高位（近40个观测内曾 >0.85）掉到 0.55 以下
        rk = srank.get(d)
        if rk is not None:
            past = [srank.get(sd2, 0) for sd2 in ds[max(0, i - 40):i + 1] if sd2 in srank]
            if rk < 0.55 and past and max(past) > 0.85:
                out['S8 板块动量拐头'].append(i)
        if vs[i] < m20[i] and sm5.get(d, 0) < 0:
            out['S9 双弱破MA20'].append(i)
        if sdrop.get(d, 0) <= -0.02 and svr.get(d, 1) >= 1.5:
            out['S10 板块放量下跌'].append(i)
        hi60 = max(vs[max(0, i - 59):i + 1])
        if vs[i] / hi60 - 1 <= -0.08 and sm20.get(d, 0) < 0:
            out['S11 早回撤+板块弱'].append(i)
    return out


def cool(idx, gap):
    """信号去重：同一条规则 gap 日内只留第一个（模拟真实只做一次动作）"""
    out, last = [], -10 ** 9
    for i in idx:
        if i - last >= gap:
            out.append(i)
            last = i
    return out


def score(items, key, gap=COOL):
    seg = {'train': ([], [], []), 'test': ([], [], [])}   # pos, f20, f60
    cnt = {'train': 0, 'test': 0}
    yrs = {'train': 0.0, 'test': 0.0}
    for it in items:
        c = ctx(it)
        ds, vs = c[0], c[1]
        rs = rules_b(it, c) if key.startswith('B') else rules_s(it, c)
        idx = cool(rs[key], gap)
        # 记录每段的天数
        for i, d in enumerate(ds):
            seg_name = 'train' if d <= SPLIT else 'test'
            yrs[seg_name] += 1 / 250.0
        for i in idx:
            s = 'train' if ds[i] <= SPLIT else 'test'
            cnt[s] += 1
            if i + 20 < len(vs):
                seg[s][0].append(c[5][i])
                seg[s][1].append(vs[i + 20] / vs[i] - 1)
            if i + 60 < len(vs):
                seg[s][2].append(vs[i + 60] / vs[i] - 1)
    res = {}
    for s in ('train', 'test'):
        pos, f20, f60 = seg[s]
        res[s] = dict(
            n=cnt[s], per_year=(cnt[s] / (yrs[s] / len(items)) if yrs[s] else 0),
            pos=st.median(pos) if pos else None,
            f20=st.mean(f20) if f20 else None,
            f60=st.mean(f60) if f60 else None,
            win20=(100.0 * sum(1 for x in f20 if x > 0) / len(f20)) if f20 else None,
        )
    return res


def main():
    items = mkt()
    print('样本：%d 只，训练段 <= %s，验证段 > %s\n' % (len(items), SPLIT, SPLIT))

    print('=== 候选买点（要求大趋势向上才考虑） ===')
    print('  %-16s %-28s %-28s' % ('规则', '训练段 n/年 位置 B后20 B后60', '验证段 n/年 位置 B后20 B后60'))
    brank = []
    for k in ('B1 回踩MA20', 'B2 回踩MA60', 'B3 pos60低位', 'B4 创60日新高',
              'B5 急跌后首阳', 'B6 板块动量转正'):
        r = score(items, k)
        tr, te = r['train'], r['test']
        print('  %-16s %5.1f次 %.2f %+6.2f%% %+6.2f%%   %5.1f次 %.2f %+6.2f%% %+6.2f%%'
              % (k, tr['per_year'], tr['pos'] or 0, 100 * (tr['f20'] or 0), 100 * (tr['f60'] or 0),
                 te['per_year'], te['pos'] or 0, 100 * (te['f20'] or 0), 100 * (te['f60'] or 0)))
        brank.append((k, tr, te))

    print('\n=== 候选卖点 ===')
    print('  %-16s %-28s %-28s' % ('规则', '训练段 n/年 位置 S后20 S后60', '验证段 n/年 位置 S后20 S后60'))
    srank = []
    for k in ('S1 破MA20', 'S2 破MA60+MA60下行', 'S3 板块破MA60', 'S4 板块动量转负',
              'S5 相对强度转负', 'S6 回撤18%', 'S7 S4或S6', 'S8 板块动量拐头',
              'S9 双弱破MA20', 'S10 板块放量下跌', 'S11 早回撤+板块弱'):
        r = score(items, k)
        tr, te = r['train'], r['test']
        print('  %-16s %5.1f次 %.2f %+6.2f%% %+6.2f%%   %5.1f次 %.2f %+6.2f%% %+6.2f%%'
              % (k, tr['per_year'], tr['pos'] or 0, 100 * (tr['f20'] or 0), 100 * (tr['f60'] or 0),
                 te['per_year'], te['pos'] or 0, 100 * (te['f20'] or 0), 100 * (te['f60'] or 0)))
        srank.append((k, tr, te))

    print('\n=== 挑选（只看训练段，验证段只做验收） ===')
    b_ok = [x for x in brank if (x[1]['pos'] or 1) <= 0.60 and (x[1]['f20'] or -1) > 0]
    if b_ok:
        b_ok.sort(key=lambda x: (-(x[1]['f20'] or 0), (x[1]['pos'] or 1)))
        k, tr, te = b_ok[0]
        print('  买点选 %s：训练段 位置%.2f / 后20日%+.2f%%，验证段 位置%.2f / 后20日%+.2f%% → %s'
              % (k, tr['pos'], 100 * tr['f20'], te['pos'], 100 * (te['f20'] or 0),
                 '两段一致，可用' if ((te['pos'] or 1) <= 0.70 and (te['f20'] or -1) > 0) else '验证段不成立，不能只说训练段好看'))
    else:
        print('  买点：没有任何规则能在训练段同时做到「位置<=0.60 且 后20日收益>0」')

    s_ok = [x for x in srank if (x[1]['f60'] or 1) < 0]
    if s_ok:
        s_ok.sort(key=lambda x: (x[1]['f60'] or 0))
        k, tr, te = s_ok[0]
        print('  卖点选 %s：训练段 后60日%+.2f%%，验证段 后60日%+.2f%% → %s'
              % (k, 100 * tr['f60'], 100 * (te['f60'] or 0),
                 '两段一致，可用' if (te['f60'] or 1) < 0 else '验证段不成立'))
    else:
        print('  卖点：没有任何规则在训练段做到「信号后 60 日收益为负」——'
              '也就是说，这些技术信号都没法提前看出顶。')

    tail_and_strategy(items)


# ============ 尾部风险 + 策略级对比（回答「到底该不该卖」） ============
def tail_and_strategy(items):
    print('\n=== 卖点的尾部风险（均值是正的，但尾部决定要不要卖） ===')
    for key in ('S1 破MA20', 'S2 破MA60+MA60下行', 'S8 板块动量拐头', 'S6 回撤18%'):
        f60, mdd60 = [], []
        for it in items:
            c = ctx(it)
            ds, vs = c[0], c[1]
            for i in cool(rules_s(it, c)[key], COOL):
                j = min(i + 60, len(vs) - 1)
                if j <= i + 20:
                    continue
                f60.append(vs[j] / vs[i] - 1)
                mdd60.append(min(vs[i:j + 1]) / vs[i] - 1)
        if not f60:
            continue
        q = sorted(f60)
        def pc(p):
            return q[min(len(q) - 1, int(p * len(q)))]
        print('  %-16s n=%-4d 后60日收益 5%%分位%+7.2f%% 中位%+7.2f%% 95%%分位%+7.2f%% | '
              '期间最深跌幅 中位%+7.2f%% 最差%+7.2f%%'
              % (key, len(f60), 100 * pc(0.05), 100 * pc(0.5), 100 * pc(0.95),
                 100 * st.median(mdd60), 100 * min(mdd60)))

    print('\n=== 策略级对比：买入持有 vs 「低位买 + 动量拐头卖」 ===')
    print('  %-22s %8s %8s %8s %8s' % ('基金', '持有收益', '持有回撤', '策略收益', '策略回撤'))
    agg = {'hold_r': [], 'hold_dd': [], 'stg_r': [], 'stg_dd': [], 'n': []}
    for it in items[:12]:
        c = ctx(it)
        ds, vs = c[0], c[1]
        # 买入持有
        hr = vs[-1] / vs[0] - 1
        hdd = min(vs[i] / max(vs[:i + 1]) - 1 for i in range(len(vs)))
        # 策略：B3 买、S8 卖（冷却 20 日）
        bs = cool(rules_b(it, c)['B3 pos60低位'], COOL)
        ss = cool(rules_s(it, c)['S8 板块动量拐头'], COOL)
        BS, SS = set(bs), set(ss)
        holding, eq, entry, peak, mdd, ntr = 0, 1.0, 0, 1.0, 0.0, 0
        for i in range(len(vs)):
            if not holding and i in BS:
                holding, entry = 1, i
                ntr += 1
            elif holding and i in SS and i > entry:
                eq *= vs[i] / vs[entry]
                holding = 0
            cur = eq * (vs[i] / vs[entry]) if holding else eq
            peak = max(peak, cur)
            mdd = min(mdd, cur / peak - 1)
        sr = (eq * (vs[-1] / vs[entry]) if holding else eq) - 1
        print('  %-22s %+7.1f%% %+7.1f%% %+7.1f%% %+7.1f%%  (%d 笔)'
              % (it['name'][:20], 100 * hr, 100 * hdd, 100 * sr, 100 * mdd, ntr))
        for k, v in (('hold_r', hr), ('hold_dd', hdd), ('stg_r', sr), ('stg_dd', mdd)):
            agg[k].append(v)
        agg['n'].append(ntr)
    print('  %-22s %+7.1f%% %+7.1f%% %+7.1f%% %+7.1f%%  (平均 %d 笔 / %d 年)'
          % ('平均', 100 * st.mean(agg['hold_r']), 100 * st.mean(agg['hold_dd']),
             100 * st.mean(agg['stg_r']), 100 * st.mean(agg['stg_dd']),
             round(st.mean(agg['n'])), round(8.5)))


if __name__ == '__main__':
    main()

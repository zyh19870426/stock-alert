# -*- coding: utf-8 -*-
"""93_robust_g1.py · G1（广度拐头）的稳健性检验

96_scan_lead.py 给出一个诱人结果：G1 训练段 f60 中位 -2.16%、验证段 -2.41%，
而且都比同期随机日好 3~4 个百分点。但**一个参数点 + 一个样本，不能算结论**。
这个脚本专门拆它：

  1. 逐年       —— 是不是只靠 2022 或 2024 一年撑起来的
  2. 分板块     —— 是不是只靠银行 9 只（样本占比最大）撑起来的
  3. 参数网格   —— 阈值从 (0.85,0.55) 挪开会不会崩；只有「一整片区域都好」才算稳
  4. 组合       —— G1 与 S8 同时（±5 日）触发，是否比单用更强
  5. 策略回测   —— 同区间、同一买点，三种卖法的收益/回撤/笔数

判定按钮先写死：
  稳健 = 逐年至少 4/6 年为负 且 非银行板块也为负 且 参数网格 80% 以上格子为负
  任何一条不满足 → 只报「有条件成立」，不进产品
"""
import importlib.util
import json
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('scan96', os.path.join(HERE, '96_scan_lead.py'))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

SPLIT = S.SPLIT


def sig_g1(state, hi=0.85, lo=0.55, look=40):
    """广度拐头，参数可调（默认值就是 96 里用的那组）"""
    out = {}
    if not state['has_br']:
        return out
    ds = state['dates']
    for i, d in enumerate(ds):
        rk = state['state'][d]['brk']
        if rk is None or rk >= lo:
            continue
        past = [state['state'][ds[k]]['brk'] for k in range(max(0, i - look), i + 1)
                if state['state'][ds[k]]['brk'] is not None]
        if past and max(past) > hi:
            out[d] = 'G1(%.2f→%.2f)' % (max(past), rk)
    return out


def eval_sig(items, sig_fn, lo=None, hi='9999'):
    recs = []
    for it in items:
        ds = [x[0] for x in it['series']]
        vs = [x[1] for x in it['series']]
        raw = sig_fn(it['st'])
        for i, d, why in S.merge(ds, raw):
            if (lo and d <= lo) or d > hi or i + 61 >= len(vs):
                continue
            e = vs[i + 1]
            recs.append(dict(d=d, code=it['code'], arena=it['arena'], why=why,
                             f20=vs[i + 21] / e - 1, f60=vs[i + 61] / e - 1,
                             mdd=min(vs[i + 1:i + 62]) / e - 1))
    return recs


def med(recs, key='f60'):
    return st.median([r[key] for r in recs]) if recs else None


def main():
    items, states = S.market()
    print('样本 %d 只基金，其中主战场有广度数据 %d 只\n'
          % (len(items), sum(1 for i in items if i['st']['has_br'])))

    # ---------- 0. 基准：随机日 ----------
    base = {}
    for seg, lo, hi in (('train', None, SPLIT), ('test', SPLIT, '9999')):
        xs = []
        for it in items:
            ds = [x[0] for x in it['series']]
            vs = [x[1] for x in it['series']]
            for i, d in enumerate(ds):
                if (lo and d <= lo) or d > hi or i + 61 >= len(vs):
                    continue
                xs.append(vs[i + 61] / vs[i + 1] - 1)
        base[seg] = st.median(xs) if xs else 0
    print('随机基准 f60 中位：训练段 %+.2f%% / 验证段 %+.2f%%\n'
          % (100 * base['train'], 100 * base['test']))

    g1 = eval_sig(items, sig_g1)
    s8 = eval_sig(items, S.sig_base_mom)
    print('G1 信号 %d 个 / S8 信号 %d 个（全区间）\n' % (len(g1), len(s8)))

    # ---------- 1. 逐年 ----------
    print('=' * 92)
    print('1. 逐年（看是不是只有某一年有效）')
    print('=' * 92)
    print(' %-6s %6s %10s %10s %10s' % ('年份', 'n', 'G1 f60中位', 'S8 f60中位', '随机日中位'))
    yrs = sorted({r['d'][:4] for r in g1})
    neg_years = 0
    for y in yrs:
        g = [r for r in g1 if r['d'][:4] == y]
        s8y = [r for r in s8 if r['d'][:4] == y]
        rnd = []
        for it in items:
            ds = [x[0] for x in it['series']]
            vs = [x[1] for x in it['series']]
            for i, d in enumerate(ds):
                if d[:4] != y or i + 61 >= len(vs):
                    continue
                rnd.append(vs[i + 61] / vs[i + 1] - 1)
        m = med(g)
        if m is not None and m < 0:
            neg_years += 1
        print(' %-6s %6d %+9.2f%% %+9.2f%% %+9.2f%%%s'
              % (y, len(g), 100 * m if m is not None else 0,
                 100 * med(s8y) if s8y else 0, 100 * st.median(rnd) if rnd else 0,
                 '  ← G1 为正' if (m is not None and m > 0) else ''))
    print(' G1 为负的年份：%d / %d' % (neg_years, len(yrs)))

    # ---------- 2. 分板块 ----------
    print('\n' + '=' * 92)
    print('2. 分板块（银行占样本一半以上，必须看离开银行还成不成立）')
    print('=' * 92)
    arn = sorted({r['arena'] for r in g1})
    print(' %-14s %6s %10s %10s' % ('主战场', 'n', 'G1 f60中位', 'S8 f60中位'))
    for a in arn:
        g = [r for r in g1 if r['arena'] == a]
        s8a = [r for r in s8 if r['arena'] == a]
        print(' %-14s %6d %+9.2f%% %+9.2f%%'
              % (a, len(g), 100 * med(g), 100 * med(s8a) if s8a else 0))
    nb = [r for r in g1 if r['arena'] != '银行']
    print(' 非银行合计 n=%d  G1 f60中位 %+.2f%%' % (len(nb), 100 * med(nb)))
    nbank = [r for r in g1 if r['arena'] == '银行']
    print(' 银行       n=%d  G1 f60中位 %+.2f%%' % (len(nbank), 100 * med(nbank)))

    # ---------- 3. 参数网格 ----------
    print('\n' + '=' * 92)
    print('3. 参数网格（阈值挪开会不会崩；一整片都负才算稳）')
    print('=' * 92)
    grid_neg = grid_all = 0
    for hi in (0.80, 0.85, 0.90):
        for lo in (0.45, 0.55, 0.65):
            for look in (20, 40, 60):
                f = lambda st_, hi=hi, lo=lo, look=look: sig_g1(st_, hi, lo, look)
                tr = [r for r in eval_sig(items, f, hi=SPLIT)]
                te = [r for r in eval_sig(items, f, lo=SPLIT)]
                mtr, mte = med(tr), med(te)
                ok = (mtr is not None and mtr < 0 and mte is not None and mte < 0)
                grid_all += 1
                if ok:
                    grid_neg += 1
                print('  hi=%.2f lo=%.2f look=%2d  训练 n=%-3d %+7.2f%%  验证 n=%-3d %+7.2f%%  %s'
                      % (hi, lo, look, len(tr), 100 * (mtr or 0), len(te), 100 * (mte or 0),
                         'OK' if ok else '—'))
    print(' 两段同时为负的格子：%d / %d（%.0f%%）'
          % (grid_neg, grid_all, 100.0 * grid_neg / grid_all))

    # ---------- 4. 组合：G1 与 S8 同向确认 ----------
    print('\n' + '=' * 92)
    print('4. 组合（G1 与 S8 在 ±5 日内同时触发 → 是否比单用更强）')
    print('=' * 92)

    def sig_both(state, win=5):
        """两边都报警才算：G1 与 S8 的日期互相在 win 日内配对，取较晚的那个日期"""
        g = sig_g1(state)
        s = S.sig_base_mom(state)
        ds = state['dates']
        gd = sorted(g)
        out = {}
        for d in sorted(s):
            j = __import__('bisect').bisect_right(gd, d) - 1
            if j >= 0 and ds.index(d) - ds.index(gd[j]) <= win:
                out[d] = 'G1+S8 双确认'
        return out

    for nm, fn in (('G1 单独', sig_g1), ('S8 单独', S.sig_base_mom), ('G1+S8 双确认', sig_both)):
        tr = eval_sig(items, fn, hi=SPLIT)
        te = eval_sig(items, fn, lo=SPLIT)
        if not tr or not te:
            print(' %-12s 样本不足' % nm)
            continue
        print(' %-12s 训练 n=%-3d 中位%+7.2f%% (优于随机%+5.2f%%)   '
              '验证 n=%-3d 中位%+7.2f%% (优于随机%+5.2f%%)'
              % (nm, len(tr), 100 * med(tr), 100 * (med(tr) - base['train']),
                 len(te), 100 * med(te), 100 * (med(te) - base['test'])))


    # ---------- 6. 按板块个股分散度分层（假设：σ 越大，广度越有信息） ----------
    print('\n' + '=' * 92)
    print('6. 分散度分层 —— G1 是不是只在「个股各走各的」板块才有效')
    print('=' * 92)
    brm = json.load(open(os.path.join(HERE, 'breadth.json'), encoding='utf-8'))['sectors']
    XSD = {k: v.get('xsd_med') for k, v in brm.items()}
    print(' %-14s %8s %5s %11s %11s' % ('主战场', 'σ(个股)', 'n', 'G1 f60中位', 'S8 f60中位'))
    rows = []
    for a in sorted({r['arena'] for r in g1}, key=lambda z: XSD.get(z) or 0):
        g = [r for r in g1 if r['arena'] == a]
        s8a = [r for r in s8 if r['arena'] == a]
        rows.append((XSD.get(a) or 0, a, len(g), med(g), med(s8a) if s8a else None))
        print(' %-14s %8.4f %5d %+10.2f%% %+11.2f%%'
              % (a, XSD.get(a) or 0, len(g), 100 * med(g), 100 * (med(s8a) if s8a else 0)))
    # 用训练段的数据定阈值（不能拿全样本最优阈值，那是偷看答案）
    def bucket(recs, lo, hi):
        return [r for r in recs if lo <= (XSD.get(r['arena']) or 0) < hi]
    print()
    print(' %-26s %6s %11s %11s' % ('按 σ 分档（0.012 为界）', 'n', '训练中位', '验证中位'))
    for nm, lo, hi in (('低σ < 0.012（同质）', 0, 0.012),
                       ('中σ 0.012~0.015', 0.012, 0.015),
                       ('高σ ≥ 0.015（异质）', 0.015, 9)):
        g = bucket(g1, lo, hi)
        tr = [r for r in g if r['d'] <= SPLIT]
        te = [r for r in g if r['d'] > SPLIT]
        if not tr or not te:
            print(' %-26s 样本不足' % nm)
            continue
        print(' %-26s %6d %+10.2f%% %+10.2f%%'
              % (nm, len(g), 100 * med(tr), 100 * med(te)))
    print(' 解读：如果低σ档 ≈ 0、高σ档明显为负 → 广度信号必须**按板块分散度开关**，不能全局用。')


    # ---------- 7. 滚动 σ（无前视）+ 只用训练段定阈值 ----------
    print('\n' + '=' * 92)
    print('7. 无前视版：滚动 250 日 σ 在信号日判断，阈值只用训练段定')
    print('=' * 92)
    tr_xs = [it['st']['state'][d]['xsdm'] for it in items for d in it['st']['dates']
             if d <= SPLIT and it['st']['state'][d].get('xsdm')]
    tr_xs.sort()
    thr = tr_xs[len(tr_xs) // 2] if tr_xs else 0.012
    print(' 训练段滚动 σ 中位 = %.4f → 用它当开关阈值（不看验证段）' % thr)
    print(' （σ 低于阈值 = 板块内个股同步涨跌，广度就是噪声，不启用）')

    def sig_g1_gated(state, hi=0.85, lo=0.55, look=40, gate=None):
        out = sig_g1(state, hi, lo, look)
        if gate is None:
            return out
        return {d: v for d, v in out.items()
                if (state['state'][d].get('xsdm') or 0) >= gate}

    for nm, gate in (('G1 全局（不开关）', None), ('G1 只在高σ板块', thr)):
        tr = eval_sig(items, lambda st_, g=gate: sig_g1_gated(st_, gate=g), hi=SPLIT)
        te = eval_sig(items, lambda st_, g=gate: sig_g1_gated(st_, gate=g), lo=SPLIT)
        if not tr or not te:
            print(' %-22s 样本不足（训练%d / 验证%d）' % (nm, len(tr), len(te)))
            continue
        print(' %-22s 训练 n=%-3d 中位%+7.2f%%（优于随机%+5.2f%%）   '
              '验证 n=%-3d 中位%+7.2f%%（优于随机%+5.2f%%）'
              % (nm, len(tr), 100 * med(tr), 100 * (med(tr) - base['train']),
                 len(te), 100 * med(te), 100 * (med(te) - base['test'])))

    # 同样测一遍 S8 是否也受 σ 影响（如果 S8 也只在异质板块有效，说明这不是广度的功劳）
    print()
    print(' 对照：S8 在同样的 σ 开关下表现如何（用来区分「广度的功劳」还是「板块本身的功劳」）')
    def sig_s8_gated(state, gate):
        out = S.sig_base_mom(state)
        if gate is None:
            return out
        return {d: v for d, v in out.items()
                if (state['state'][d].get('xsdm') or 0) >= gate}
    for nm, gate in (('S8 全局', None), ('S8 只在高σ板块', thr)):
        tr = eval_sig(items, lambda st_, g=gate: sig_s8_gated(st_, g), hi=SPLIT)
        te = eval_sig(items, lambda st_, g=gate: sig_s8_gated(st_, g), lo=SPLIT)
        if not tr or not te:
            print(' %-22s 样本不足' % nm)
            continue
        print(' %-22s 训练 n=%-3d 中位%+7.2f%%  验证 n=%-3d 中位%+7.2f%%'
              % (nm, len(tr), 100 * med(tr), len(te), 100 * med(te)))


    # ---------- 8. 显著性：中位数的 bootstrap 95% 区间 ----------
    print('\n' + '=' * 92)
    print('8. 显著性（中位数 bootstrap 95% 区间，2000 次）—— 不给区间就等于没结论')
    print('=' * 92)
    import random
    random.seed(42)

    def boot(xs, B=2000):
        m = []
        for _ in range(B):
            m.append(st.median([random.choice(xs) for _ in xs]))
        m.sort()
        return m[int(0.025 * B)], m[int(0.975 * B)]

    def show(nm, recs):
        if len(recs) < 8:
            print(' %-24s n=%d 样本太少，不给区间' % (nm, len(recs)))
            return
        xs = [r['f60'] for r in recs]
        lo, hi = boot(xs)
        print(' %-24s n=%-3d 中位%+7.2f%%  95%%区间 [%+7.2f%%, %+7.2f%%]  %s'
              % (nm, len(xs), 100 * st.median(xs), 100 * lo, 100 * hi,
                 '显著为负' if hi < 0 else ('不显著' if lo < 0 < hi else '显著为正')))

    for seg, l0, h0 in (('训练', None, SPLIT), ('验证', SPLIT, '9999')):
        print(' %s段：' % seg)
        show('  随机日基准', [dict(f60=(it['series'][i + 61][1] / it['series'][i + 1][1] - 1))
                         for it in items
                         for i, d in enumerate([x[0] for x in it['series']])
                         if ((not l0 or d > l0) and d <= h0 and i + 61 < len(it['series']))][::5])
        show('  G1 全局', [r for r in g1 if (not l0 or r['d'] > l0) and r['d'] <= h0])
        show('  G1 高σ', [r for r in g1 if (not l0 or r['d'] > l0) and r['d'] <= h0
                         and (XSD.get(r['arena']) or 0) >= 0.015])
        show('  S8 全局', [r for r in s8 if (not l0 or r['d'] > l0) and r['d'] <= h0])
        show('  S8 高σ', [r for r in s8 if (not l0 or r['d'] > l0) and r['d'] <= h0
                         and (XSD.get(r['arena']) or 0) >= 0.015])
        show('  S8 低σ(<0.012)', [r for r in s8 if (not l0 or r['d'] > l0) and r['d'] <= h0
                                and (XSD.get(r['arena']) or 0) < 0.012])

    # ---------- 5. 策略回测 ----------
    print('\n' + '=' * 92)
    print('5. 策略回测（同一买点，只换卖法；区间取广度可用期，2020-08 起）')
    print('=' * 92)
    run_strategy(items, g1, s8)


def buy_signal(it):
    """现版买点：pos60<=0.40 + MA20>MA60 且 MA60 上行 + 板块 ETF 在 MA60 上"""
    ds = [x[0] for x in it['series']]
    vs = [x[1] for x in it['series']]
    m20, m60 = S.ma(vs, 20), S.ma(vs, 60)
    out = {}
    for i in range(61, len(vs)):
        if m20[i] is None or m60[i] is None or m60[i - 20] is None:
            continue
        seg = vs[max(0, i - 59):i + 1]
        lo, hi = min(seg), max(seg)
        pos60 = (vs[i] - lo) / (hi - lo) if hi > lo else 0.5
        if not (m20[i] > m60[i] and m60[i] > m60[i - 20]):
            continue
        st_ = it['st']
        j = S.bisect.bisect_right(st_['dates'], ds[i]) - 1
        if j < 0:
            continue
        s = st_['state'][st_['dates'][j]]
        if s['ma60'] is not None and s['close'] < s['ma60']:
            continue
        if pos60 <= 0.40:
            out[ds[i]] = 'B'
    return out


def run_strategy(items, g1recs, s8recs):
    g1set = {}
    s8set = {}
    for r in g1recs:
        g1set.setdefault(r['code'], set()).add(r['d'])
    for r in s8recs:
        s8set.setdefault(r['code'], set()).add(r['d'])

    COST = 0.0065   # 场外基金一次来回：申购 0.15% + 赎回 0.5%
    print(' （已扣申赎成本 0.65%/来回；场外基金实际就是这么贵，不扣等于自欺）')
    print(' %-16s %8s %9s %8s %7s' % ('卖法', '累计收益', '最大回撤', '交易笔数', '胜率'))
    for label in ('不止盈（买入持有）', 'S8 动量拐头', 'G1 广度拐头', 'G1 硬止损18%'):
        rets, mdds, ntrs, wins = [], [], [], []
        for it in items:
            ds = [x[0] for x in it['series']]
            vs = [x[1] for x in it['series']]
            B = buy_signal(it)
            Ss = s8set.get(it['code'], set()) if label.startswith('S8') else (
                g1set.get(it['code'], set()) if label.startswith('G1') else set())
            holding, entry, eq, ntr, win = 0, 0, 1.0, 0, 0
            peak, mdd = 1.0, 0.0
            for i, d in enumerate(ds):
                if not holding and d in B:
                    holding, entry = 1, i
                    ntr += 1
                elif holding and label != '不止盈（买入持有）':
                    dd120 = vs[i] / max(vs[max(0, i - 119):i + 1]) - 1
                    hit = (d in Ss) if not label.startswith('G1 硬止损') else (dd120 <= -0.18)
                    if hit and i > entry:
                        eq *= vs[i] / vs[entry] * (1 - COST)
                        if vs[i] > vs[entry]:
                            win += 1
                        holding = 0
                cur = eq * (vs[i] / vs[entry]) if holding else eq
                peak = max(peak, cur)
                mdd = min(mdd, cur / peak - 1)
            if holding:
                eq *= vs[-1] / vs[entry]
            rets.append(eq - 1)
            mdds.append(mdd)
            ntrs.append(ntr)
            wins.append(win / ntr if ntr else 0)
        print(' %-16s %+7.1f%% %+8.1f%% %8.1f %6.0f%%'
              % (label, 100 * st.mean(rets), 100 * st.mean(mdds),
                 st.mean(ntrs), 100 * st.mean(wins)))


if __name__ == '__main__':
    main()

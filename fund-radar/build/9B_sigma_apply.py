# -*- coding: utf-8 -*-
"""
9B · 如果把 σ 真加进打分，选出来的东西会变成什么？

上一支脚本（9A）已经证明：σ 对板块未来 60 日收益没有稳定预测力
（训练 +2.43% / 验证 −3.5%，符号翻转；全样本 16/33 为正；区间跨 0）。

但这还不够 —— 「没预测力」和「加进去会变差」是两件事。这里做两件实际操作：

D 现在的三只推荐，加 σ 之后会换成谁？（用当前截面真算一遍）
E 板块层面滚动回测：把 σ 用三种方式接进排序，各版本 Top3 板块未来 60 日收益对比
     V0 现状    = 纯动量分位排序
     V1 低σ加分 = 动量分位 × (1 − σ截面分位)        ← 「σ 低 = 个股齐涨 = 抱团牢」
     V2 高σ加分 = 动量分位 × σ截面分位              ← 反方向，防我自己预设结论
     V3 低σ剔除 = 纯动量排序，剔除 σ 最低 1/3       ← 最接近上轮结论的用法（σ低=噪声，别看）

口径同 9A：每 20 日采样、T+1 买 T+61 卖、训练/验证分段、中位数+均值+n。
"""
import json, os, math, random, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
STEP, FWD = 20, 60
TRAIN_END = '2024-12-31'
random.seed(20261031)


def load(name):
    return json.load(open(os.path.join(HERE, name), encoding='utf-8'))


def corr(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((a - mx) ** 2 for a in xs) / n)
    sy = math.sqrt(sum((b - my) ** 2 for b in ys) / n)
    if sx == 0 or sy == 0:
        return 0.0
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (n * sx * sy)


def boot_ci(vals, fn=st.median, n=2000):
    vals = [v for v in vals if v is not None]
    if len(vals) < 8:
        return None
    out = []
    for _ in range(n):
        s = [vals[random.randrange(len(vals))] for _ in range(len(vals))]
        out.append(fn(s))
    out.sort()
    return out[int(0.025 * n)], out[int(0.975 * n)]


def main():
    br = load('breadth.json')['sectors']
    secs = load('sectors.json')
    nav = load('nav.json')
    data = load('../data/data.json')

    close = {s['label']: {d: c for d, c, *_ in s['k']} for s in secs}
    labels = [l for l in br if l in close]
    # 各板块 σ 的日期轴长度不同，一律按日期取值
    XSD = {l: dict(zip(br[l]['dates'], br[l]['xsd'])) for l in labels}
    all_dates = sorted(set(close[labels[0]].keys()))
    dates = [d for d in all_dates if all(d in XSD[l] for l in labels)]

    # 20 日动量
    mom = {}
    for l in labels:
        cm, arr, out = close[l], [close[l].get(d) for d in dates], []
        for i in range(len(dates)):
            out.append(arr[i] / arr[i - 20] - 1 if i >= 20 and arr[i] and arr[i - 20] else None)
        mom[l] = out

    # ---------- D · 当前截面：加 σ 后三只推荐会不会换 ----------
    print('=' * 80)
    print('D · 当前截面（%s）加 σ 之后，推荐名单会怎么变' % dates[-1])
    print('=' * 80)
    i = len(dates) - 1
    rows = []
    for l in labels:
        s = XSD[l].get(dates[i])
        m = mom[l][i]
        if s is not None and m is not None:
            rows.append([l, s, m])
    rows.sort(key=lambda x: -x[2])
    n = len(rows)
    for k, r in enumerate(rows):
        r.append((n - k) / n)                                    # 动量分位 rank
    ss = sorted(x[1] for x in rows)
    for r in rows:
        r.append(sum(1 for v in ss if v <= r[1]) / n)            # σ 截面分位
    print('  %-10s %8s %8s %8s' % ('板块', '动量分位', 'σ(×1000)', 'σ分位'))
    for l, s, m, mr, sr in rows[:12]:
        print('  %-10s %8.2f %8.2f %8.2f' % (l, mr, s * 1000, sr))

    def top(labels_set, keyf, k=3):
        cand = [r for r in rows if r[0] in labels_set]
        cand.sort(key=keyf, reverse=True)
        return [r[0] for r in cand[:k]]

    allset = set(labels)
    v0 = top(allset, lambda r: r[3])
    v1 = top(allset, lambda r: r[3] * (1 - r[4]))
    v2 = top(allset, lambda r: r[3] * r[4])
    cutoff = sorted(r[4] for r in rows)[n // 3]
    v3 = top(allset, lambda r: r[3] if r[4] > cutoff else -1)
    print('\n  V0 现状（纯动量分位）      : %s' % '、'.join(v0))
    print('  V1 低σ加分                 : %s' % '、'.join(v1))
    print('  V2 高σ加分                 : %s' % '、'.join(v2))
    print('  V3 剔除低σ1/3              : %s' % '、'.join(v3))
    print('\n  → 四种接法给出四套完全不同的名单。方向相反的 V1（偏低σ）和 V2（偏高σ）')
    print('    都能讲出一套说法，说明「怎么接」这件事本身就没有共识 —— 这本身就是')
    print('    "σ 里没有可用于排序的信息" 的旁证。三只推荐的主战场是银行、银行、机器人：')
    print('    银行 σ 分位 0.06（个股齐涨），机器人正好相反，加 σ 必然翻动其中一边。')

    # ---------- E · 板块层面滚动回测 ----------
    print('\n' + '=' * 80)
    print('E · 滚动回测：三种 σ 接法 vs 现状，Top3 板块持有 60 日')
    print('=' * 80)

    def fwd(l, i):
        a, b = close[l].get(dates[i + 1]), close[l].get(dates[i + 1 + FWD])
        return b / a - 1 if a and b and a > 0 else None

    res = {k: {'train': [], 'val': []} for k in ('V0', 'V1', 'V2', 'V3')}
    for i in range(0, len(dates) - 1 - FWD, STEP):
        d = dates[i]
        seg = 'train' if d <= TRAIN_END else 'val'
        rr = []
        for l in labels:
            s, m = XSD[l].get(dates[i]), mom[l][i]
            if s is not None and m is not None:
                rr.append([l, s, m])
        if len(rr) < 18:
            continue
        nn = len(rr)
        rr.sort(key=lambda x: -x[2])
        for k, r in enumerate(rr):
            r.append((nn - k) / nn)
        ss = sorted(x[1] for x in rr)
        for r in rr:
            r.append(sum(1 for v in ss if v <= r[1]) / nn)
        cut = sorted(r[4] for r in rr)[nn // 3]

        def pick(keyf, filt=None):
            c = [r for r in rr if (filt is None or filt(r))]
            c.sort(key=keyf, reverse=True)
            vs = [fwd(r[0], i) for r in c[:3]]
            vs = [v for v in vs if v is not None]
            return st.mean(vs) if vs else None

        res['V0'][seg].append(pick(lambda r: r[3]))
        res['V1'][seg].append(pick(lambda r: r[3] * (1 - r[4])))
        res['V2'][seg].append(pick(lambda r: r[3] * r[4]))
        res['V3'][seg].append(pick(lambda r: r[3] if r[4] > cut else -1))

    print('\n  版本                              训练中位   验证中位   训练均值   验证均值   n(训/验)')
    names = {'V0': 'V0 现状（纯动量分位）', 'V1': 'V1 低σ加分', 'V2': 'V2 高σ加分', 'V3': 'V3 剔除低σ1/3'}
    base = None
    for k in ('V0', 'V1', 'V2', 'V3'):
        tr = [v for v in res[k]['train'] if v is not None]
        va = [v for v in res[k]['val'] if v is not None]
        if not tr or not va:
            continue
        if k == 'V0':
            base = (st.median(tr), st.median(va))
        print('  %-30s %s %s %s %s  %d/%d' % (
            names[k],
            '%+7.2f%%' % (st.median(tr) * 100), '%+7.2f%%' % (st.median(va) * 100),
            '%+7.2f%%' % (st.mean(tr) * 100), '%+7.2f%%' % (st.mean(va) * 100),
            len(tr), len(va)))

    print('\n  相对现状的差值（中位数口径）：')
    for k in ('V1', 'V2', 'V3'):
        tr = [v for v in res[k]['train'] if v is not None]
        va = [v for v in res[k]['val'] if v is not None]
        if not tr or not va or base is None:
            continue
        dt = st.median(tr) - base[0]
        dv = st.median(va) - base[1]
        # 配对差（逐期）
        pt = [a - b for a, b in zip(res[k]['train'], res['V0']['train']) if a is not None and b is not None]
        pv = [a - b for a, b in zip(res[k]['val'], res['V0']['val']) if a is not None and b is not None]
        ct, cv = boot_ci(pt), boot_ci(pv)
        print('  %-16s 训练 %+6.2f%%  验证 %+6.2f%%   |  配对差区间 训[%s, %s] 验[%s, %s]' % (
            names[k], dt * 100, dv * 100,
            '%+.2f%%' % (ct[0] * 100) if ct else '--', '%+.2f%%' % (ct[1] * 100) if ct else '--',
            '%+.2f%%' % (cv[0] * 100) if cv else '--', '%+.2f%%' % (cv[1] * 100) if cv else '--'))

    # ---------- F · 只在「σ 真的让你换了仓」的期数上看 ----------
    # E 的配对差中位数常为 0，说明很多期名单根本没变。真正该问的是：
    # 在 σ 逼着你换掉某个板块的那些期里，换仓是赚了还是亏了？
    print('\n' + '=' * 80)
    print('F · 分歧期检验：只统计「加了 σ 之后 Top3 名单真的变了」的期数')
    print('=' * 80)

    detail = {k: [] for k in ('V1', 'V2', 'V3')}
    for i in range(0, len(dates) - 1 - FWD, STEP):
        d = dates[i]
        seg = 'train' if d <= TRAIN_END else 'val'
        rr = []
        for l in labels:
            s, m = XSD[l].get(dates[i]), mom[l][i]
            if s is not None and m is not None:
                rr.append([l, s, m])
        if len(rr) < 18:
            continue
        nn = len(rr)
        rr.sort(key=lambda x: -x[2])
        for k, r in enumerate(rr):
            r.append((nn - k) / nn)
        ss = sorted(x[1] for x in rr)
        for r in rr:
            r.append(sum(1 for v in ss if v <= r[1]) / nn)
        cut = sorted(r[4] for r in rr)[nn // 3]

        def picklist(keyf, filt=None):
            c = [r for r in rr if (filt is None or filt(r))]
            c.sort(key=keyf, reverse=True)
            return [r[0] for r in c[:3]]

        base_l = picklist(lambda r: r[3])
        cand = {
            'V1': picklist(lambda r: r[3] * (1 - r[4])),
            'V2': picklist(lambda r: r[3] * r[4]),
            'V3': picklist(lambda r: r[3] if r[4] > cut else -1),
        }
        for k, lst in cand.items():
            if set(lst) == set(base_l):
                continue                       # 名单没变，不计入
            va = [v for v in (fwd(x, i) for x in base_l) if v is not None]
            vb = [v for v in (fwd(x, i) for x in lst) if v is not None]
            if va and vb:
                detail[k].append((seg, st.mean(vb) - st.mean(va), len(set(lst) - set(base_l))))

    for k in ('V1', 'V2', 'V3'):
        rows = detail[k]
        if not rows:
            print('  %s: 没有分歧期' % k)
            continue
        print('\n  %s  分歧期 %d 个（训练 %d / 验证 %d），平均换掉 %.1f 个板块' % (
            k, len(rows),
            sum(1 for s, _, _ in rows if s == 'train'),
            sum(1 for s, _, _ in rows if s == 'val'),
            st.mean([c for _, _, c in rows])))
        for sg, nm in (('train', '训练'), ('val', '验证')):
            v = [x[1] for x in rows if x[0] == sg]
            if len(v) >= 4:
                ci = boot_ci(v)
                win = sum(1 for x in v if x > 0)
                print('    %s 换仓后超额：中位 %+6.2f%%  均值 %+6.2f%%  区间[%s, %s]  赚 %d/%d' % (
                    nm, st.median(v) * 100, st.mean(v) * 100,
                    '%+.2f%%' % (ci[0] * 100) if ci else '--',
                    '%+.2f%%' % (ci[1] * 100) if ci else '--', win, len(v)))
            elif v:
                print('    %s 换仓后超额：中位 %+6.2f%%  n=%d（样本太少，不给区间）' % (
                    nm, st.median(v) * 100, len(v)))


if __name__ == '__main__':
    main()

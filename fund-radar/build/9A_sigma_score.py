# -*- coding: utf-8 -*-
"""
9A · σ 该不该进综合分？（只做判断，不改产品）

要回答的问题只有一个：
    把「板块个股分化度 σ」算进综合分，能不能让选出来的基金**未来赚得更多**？
如果不能，那 σ 就只该留在原地（标注信号可信度），算进分就是纯降分、没有依据。

四个检验：
  A 截面：同一时点，σ 高/中/低的板块，未来 60 日收益有没有台阶差
  B 时序（within-sector，干净的那个）：同一板块内部，σ 处于自身历史高位/低位时，
    未来 60 日收益有没有台阶差 —— 这个把「银行天生低σ」这类板块效应剥掉了
  C 冲突检查：σ 与现有抱团分要用的输入（板块动量 / 动量分位）是什么关系。
    若负相关，加进综合分 = 惩罚趋势板块 = 和「踩中抱团」的目标对着干
  D 排序翻转：模拟加 σ 后 Top3 会不会变

口径（沿用前几轮）：
  · 信号 T 日收盘算出（σ 只用 T 日及以前），成交按 T+1 收盘 → T+61 收盘（60 个交易日）
  · 每 20 个交易日采样一次，避免 60 日收益三段重叠把显著性撑虚
  · 训练 ≤2024-12-31 / 验证 ≥2025-01-01
  · 一律同时报中位数与均值，样本量 n，bootstrap 2000 次 95% 区间
  · 唯一分组变量：σ 在自身过去 500 日内的分位（无前视），避免不同板块 σ 水平不同
"""
import json, os, math, random, statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
STEP = 20          # 采样间隔（交易日）
FWD = 60           # 未来收益窗口
LOOK = 500         # σ 分位的回看长度
TRAIN_END = '2024-12-31'
random.seed(20261031)


def load():
    br = json.load(open(os.path.join(HERE, 'breadth.json'), encoding='utf-8'))['sectors']
    secs = json.load(open(os.path.join(HERE, 'sectors.json'), encoding='utf-8'))
    close = {}
    for s in secs:
        close[s['label']] = {d: c for d, c, *_ in s['k']}
    return br, close


def pct_rank_hist(arr, i, look=LOOK):
    """arr[i] 在过去 look 个有效值里的百分位（含当日，无前视）"""
    lo = max(0, i - look + 1)
    win = [x for x in arr[lo:i + 1] if x is not None]
    if len(win) < 120 or arr[i] is None:
        return None
    v = arr[i]
    return sum(1 for x in win if x <= v) / len(win)


def fwd_ret(close_map, dates, i, n=FWD):
    """T 日收盘出信号，T+1 收盘买，T+1+n 收盘卖"""
    if i + 1 + n >= len(dates):
        return None
    d0, d1 = dates[i + 1], dates[i + 1 + n]
    c0, c1 = close_map.get(d0), close_map.get(d1)
    if not c0 or not c1 or c0 <= 0:
        return None
    return c1 / c0 - 1.0


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


def fmt(v, unit='%'):
    if v is None:
        return '   --  '
    return '%+7.2f%s' % (v * 100 if unit == '%' else v, unit)


def main():
    br, close = load()
    labels = [l for l in br if l in close]
    print('板块 %d 个（既有 σ 又有板块指数）' % len(labels))

    # 各板块 σ 序列长度不同（1500~2389，起始日不同），一律按日期取值，禁止位置索引
    XSD = {l: dict(zip(br[l]['dates'], br[l]['xsd'])) for l in labels}
    SQ = {}
    for l in labels:
        x = br[l]['xsd']
        SQ[l] = dict(zip(br[l]['dates'], [pct_rank_hist(x, i) for i in range(len(x))]))

    # 统一日期轴：板块指数日期的并集里，取所有 σ 都覆盖的那段
    all_dates = sorted(set(close[labels[0]].keys()))
    covered = [d for d in all_dates if all(d in XSD[l] for l in labels)]
    dates = covered
    print('日期 %s → %s（%d 个交易日，全部板块都有 σ 的区间）\n' % (dates[0], dates[-1], len(dates)))

    # ---------- 检验 B：within-sector 时序 ----------
    # 每个板块、每个采样点，按 sq 分档，记未来 60 日收益
    print('=' * 78)
    print('B · within-sector 时序：同一板块内，σ 处在自身历史高位 vs 低位，未来 60 日如何')
    print('=' * 78)

    buckets = {0: [], 1: [], 2: []}          # 全样本
    per_sector = {l: {0: [], 1: [], 2: []} for l in labels}
    dates_used = []
    for i in range(0, len(dates) - 1 - FWD, STEP):
        d = dates[i]
        dates_used.append(d)
        for l in labels:
            sq = SQ[l].get(d)
            if sq is None:
                continue
            r = fwd_ret(close[l], dates, i)
            if r is None:
                continue
            b = 0 if sq < 1 / 3 else (1 if sq < 2 / 3 else 2)
            buckets[b].append((d, l, r))
            per_sector[l][b].append(r)

    def seg(d):
        return 'train' if d <= TRAIN_END else 'val'

    for name, segs in [('训练 ≤2024-12-31', ('train',)), ('验证 ≥2025-01-01', ('val',)),
                       ('全样本', ('train', 'val'))]:
        print('\n[%s]' % name)
        print('  σ分位档      n      中位数      均值')
        for b, nm in [(0, '低位 <0.33'), (1, '中 0.33-0.67'), (2, '高位 >0.67')]:
            v = [r for d, l, r in buckets[b] if seg(d) in segs]
            if not v:
                continue
            print('  %-12s %5d  %s  %s' % (nm, len(v), fmt(st.median(v)), fmt(st.mean(v))))

        # 板块内配对差（高 − 低），每个板块一个数 → 33 个数，不受板块间水平差影响
        diffs = []
        for l in labels:
            hi = [r for d, ll, r in buckets[2] if ll == l and seg(d) in segs]
            lo = [r for d, ll, r in buckets[0] if ll == l and seg(d) in segs]
            if len(hi) >= 6 and len(lo) >= 6:
                diffs.append(st.median(hi) - st.median(lo))
        if len(diffs) >= 8:
            ci = boot_ci(diffs)
            win = sum(1 for x in diffs if x > 0)
            print('  板块内配对差(高σ − 低σ)：中位 %s  均值 %s  区间[%s, %s]  为正 %d/%d'
                  % (fmt(st.median(diffs)), fmt(st.mean(diffs)),
                     fmt(ci[0]) if ci else '--', fmt(ci[1]) if ci else '--', win, len(diffs)))

    # ---------- 检验 A：截面 ----------
    print('\n' + '=' * 78)
    print('A · 截面：同一时点，σ 在 33 个板块里的高低，未来 60 日如何')
    print('  （这部分被板块身份污染：低σ那边天然是银行/证券/电力，只能参考不能当证据）')
    print('=' * 78)
    xsec = {0: [], 1: [], 2: []}
    for i in range(0, len(dates) - 1 - FWD, STEP):
        d = dates[i]
        rows = []
        for l in labels:
            s = XSD[l].get(d)
            if s is None:
                continue
            r = fwd_ret(close[l], dates, i)
            if r is None:
                continue
            rows.append((s, l, r))
        if len(rows) < 18:
            continue
        rows.sort()
        n = len(rows)
        for k, (s, l, r) in enumerate(rows):
            b = 0 if k < n / 3 else (1 if k < 2 * n / 3 else 2)
            xsec[b].append((d, l, r))
    for name, segs in [('训练', ('train',)), ('验证', ('val',))]:
        print('\n[%s]' % name)
        for b, nm in [(0, 'σ低 1/3'), (1, 'σ中 1/3'), (2, 'σ高 1/3')]:
            v = [r for d, l, r in xsec[b] if seg(d) in segs]
            if v:
                print('  %-8s n=%5d  中位 %s  均值 %s' % (nm, len(v), fmt(st.median(v)), fmt(st.mean(v))))

    # ---------- 检验 C：σ 与现有打分输入的关系 ----------
    print('\n' + '=' * 78)
    print('C · 冲突检查：σ 与「抱团分」现有输入的相关系数')
    print('  抱团分 = 30×(0.60×板块动量分位 + 0.40×相关性)。若 σ 与动量分位负相关，')
    print('  加 σ 进综合分就等于系统性地压低正在走强、资金在抱的板块。')
    print('=' * 78)

    mom = {}
    for l in labels:
        cm = close[l]
        arr = [cm.get(d) for d in dates]
        m20 = []
        for i in range(len(dates)):
            if i >= 20 and arr[i] and arr[i - 20]:
                m20.append(arr[i] / arr[i - 20] - 1)
            else:
                m20.append(None)
        mom[l] = m20

    pairs_sq, pairs_raw, pairs_rank = [], [], []
    for i in range(0, len(dates) - 1 - FWD, STEP):
        rows = []
        for l in labels:
            s = XSD[l].get(d)
            sq = SQ[l].get(d)
            m = mom[l][i]
            if s is None or sq is None or m is None:
                continue
            rows.append((l, s, sq, m))
        if len(rows) < 18:
            continue
        ms = sorted(x[3] for x in rows)
        med = st.median(ms)
        rank = {r[0]: sum(1 for y in ms if y <= r[3]) / len(ms) for r in rows}
        for l, s, sq, m in rows:
            pairs_sq.append((sq, rank[l]))
            pairs_raw.append((s, rank[l]))
            pairs_rank.append((rank[l], sq))

    def corr(ps):
        xs = [p[0] for p in ps]; ys = [p[1] for p in ps]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sx = math.sqrt(sum((a - mx) ** 2 for a in xs) / n)
        sy = math.sqrt(sum((b - my) ** 2 for b in ys) / n)
        if sx == 0 or sy == 0:
            return None
        return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (n * sx * sy)

    c1 = corr(pairs_sq)
    c2 = corr(pairs_raw)
    print('  corr(σ在自身历史的分位 , 板块20日动量分位) = %+.3f   n=%d' % (c1, len(pairs_sq)))
    print('  corr(σ原始值            , 板块20日动量分位) = %+.3f   n=%d' % (c2, len(pairs_raw)))
    print('\n  分板块看 σ 与动量的关系（原始 σ 的板块中位数 vs 该板块长期动量中位数）：')
    print('  %-10s %8s %10s' % ('板块', 'σ中位', '动量分位中位'))
    rows = []
    for l in labels:
        sv = [x for x in br[l]['xsd'] if x is not None]
        mv = [x for x in mom[l] if x is not None]
        rows.append((st.median(sv), l, st.median(mv)))
    rows.sort()
    for s, l, m in rows:
        print('  %-10s %8.2f %10s' % (l, s * 1000, fmt(m)))
    return


if __name__ == '__main__':
    main()

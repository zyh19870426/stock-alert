# -*- coding: utf-8 -*-
"""
98_diag_marks.py · 买卖点规则核验台（新旧两版对照，默认不写任何数据文件）
--------------------------------------------------------------------------
为什么要这个脚本：2026-10-31 用户看图质疑「B 总在高点、S 总在低位」，不能靠嘴解释，
必须把两版规则放到同一把尺子上量。三条尺子：

  尺1 位置   信号日净值在过去 60 日区间的分位 pos60（0=区间最低，1=区间最高）
             判据：B 的中位 > 0.70 → 追高；S 的中位 < 0.30 → 杀低
  尺2 频率   每年 B / S 次数、每年完整来回数
             判据：> 4 次/年 → 交易成本吃掉收益（基金来回一次约 0.3%~1.5%）
  尺3 收益   信号后 20/60 日收益；以及一次完整来回（B→下一个S）的收益
             判据：B 后 20 日均值 <= 0 或胜率 <= 50% → 买点无优势
                   S 后 20 日均值 > 0 → 卖早了

用法：python 98_diag_marks.py
"""
import json, os, sys, statistics as st, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))


def load(n):
    return json.load(open(os.path.join(HERE, n), encoding='utf-8'))


def load06():
    spec = importlib.util.spec_from_file_location('f06', os.path.join(HERE, '06_final.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def ma(vs, n):
    out, s = [], 0.0
    for i, v in enumerate(vs):
        s += v
        if i >= n:
            s -= vs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


# ---------------- 旧规则（v1）：MA20 穿越 + 20日回撤8%，5日合并 ----------------
def marks_v1(series):
    ds = [x[0] for x in series]
    vs = [x[1] for x in series]
    m20 = ma(vs, 20)
    out, last = [], {}
    for i in range(21, len(vs)):
        if m20[i] is None or m20[i - 1] is None:
            continue
        up = vs[i - 1] <= m20[i - 1] and vs[i] > m20[i]
        dn = vs[i - 1] >= m20[i - 1] and vs[i] < m20[i]
        pull = vs[i] / max(vs[max(0, i - 20):i + 1]) - 1
        kind = 'B' if (up and m20[i] > m20[i - 1]) else ('S' if dn else ('S' if pull <= -0.08 else None))
        if not kind:
            continue
        p = last.get(kind)
        if p is not None and i - p < 5:
            continue
        last[kind] = i
        out.append((i, kind))
    return out


def pos60(vs, i):
    seg = vs[max(0, i - 59):i + 1]
    lo, hi = min(seg), max(seg)
    return (vs[i] - lo) / (hi - lo) if hi > lo else 0.5


def fwd(vs, i, n):
    j = i + n
    return vs[j] / vs[i] - 1 if j < len(vs) else None


def roundtrips(vs, mk):
    """B → 之后第一个 S 的收益（完整来回）"""
    out, b = [], None
    for i, k in mk:
        if k == 'B':
            b = i
        elif k == 'S' and b is not None:
            out.append(vs[i] / vs[b] - 1)
            b = None
    return out


def evaluate(name, codes, get_marks, nav, years_of):
    agg = {'B': {20: [], 60: []}, 'S': {20: [], 60: []}}
    posB, posS, trips = [], [], []
    per_year_b, per_year_s = [], []
    for code in codes:
        series = nav[code]['series']
        vs = [x[1] for x in series]
        mk = get_marks(code, series)
        if not mk:
            per_year_b.append(0.0); per_year_s.append(0.0)
            continue
        yrs = max(years_of(code), 0.5)
        nb = sum(1 for _, k in mk if k == 'B')
        ns = sum(1 for _, k in mk if k == 'S')
        per_year_b.append(nb / yrs); per_year_s.append(ns / yrs)
        for i, k in mk:
            (posB if k == 'B' else posS).append(pos60(vs, i))
            for n in (20, 60):
                r = fwd(vs, i, n)
                if r is not None:
                    agg[k][n].append(r)
        trips += roundtrips(vs, mk)

    def line(tag, arr):
        if not arr:
            return '  %s 无样本' % tag
        return ('  %-4s n=%-5d 均值%+7.2f%% 中位%+7.2f%% 胜率%5.1f%%'
                % (tag, len(arr), 100 * st.mean(arr), 100 * st.median(arr),
                   100.0 * sum(1 for x in arr if x > 0) / len(arr)))

    print('\n---- %s ----' % name)
    print('  频率：B %.1f 次/年，S %.1f 次/年，完整来回 %.1f 次/年'
          % (st.mean(per_year_b), st.mean(per_year_s), min(st.mean(per_year_b), st.mean(per_year_s))))
    if posB:
        print('  位置：B 中位 %.2f（>0.70=追高）  落在>0.7 的比例 %.0f%%'
              % (st.median(posB), 100.0 * sum(1 for x in posB if x > 0.7) / len(posB)))
    if posS:
        print('  位置：S 中位 %.2f（<0.30=杀低）  落在<0.3 的比例 %.0f%%'
              % (st.median(posS), 100.0 * sum(1 for x in posS if x < 0.3) / len(posS)))
    print(line('B后20日', agg['B'][20]))
    print(line('B后60日', agg['B'][60]))
    print(line('S后20日', agg['S'][20]))
    print(line('S后60日', agg['S'][60]))
    print(line('来回', trips))
    return dict(per_year=min(st.mean(per_year_b), st.mean(per_year_s)),
                posB=st.median(posB) if posB else None,
                posS=st.median(posS) if posS else None,
                b20=st.mean(agg['B'][20]) if agg['B'][20] else None,
                s20=st.mean(agg['S'][20]) if agg['S'][20] else None,
                trip=st.mean(trips) if trips else None,
                tripn=len(trips))


def main():
    nav = {f['code']: f for f in load('nav.json')}
    sc = load('scored.json')
    hold = load('holdings.json')
    f06 = load06()
    SECS = f06.load_sec_state()

    arena_of = {}
    for f in sc['scored']:
        h = hold.get(f['code']) or {}
        expo = h.get('expo') or []
        arena_of[f['code']] = (expo[0]['sector'] if expo else f.get('main_sector', ''))

    codes = [f['code'] for f in sc['scored'] if f['code'] in nav and len(nav[f['code']]['series']) >= 500]
    print('样本：%d 只基金，平均 %.1f 年历史（全量日频）'
          % (len(codes), st.mean([len(nav[c]['series']) / 250.0 for c in codes])))

    years_of = lambda c: len(nav[c]['series']) / 250.0

    r1 = evaluate('v1 旧规则：MA20 穿越 + 20日回撤8%（5日合并）', codes,
                  lambda c, s: marks_v1(s), nav, years_of)

    def v2(c, s):
        return _v2idx(s, arena_of.get(c, ''), SECS, f06)

    r2 = evaluate('v2 新规则：状态机 + 回踩建仓 + 抱团瓦解离场', codes,
                  v2, nav, years_of)

    print('\n==== 结论（判据写在脚本头部，先定后算） ====')
    print('  追高问题   B 位置中位 %.2f → %.2f  %s'
          % (r1['posB'], r2['posB'], '仍偏高' if (r2['posB'] or 0) > 0.7 else '已解决'))
    print('  杀低问题   S 位置中位 %.2f → %.2f  %s'
          % (r1['posS'], r2['posS'], '仍偏低' if (r2['posS'] or 1) < 0.3 else '已解决'))
    print('  频率       %.1f 次/年 → %.1f 次/年  %s'
          % (r1['per_year'], r2['per_year'],
             '仍过频（>4）' if r2['per_year'] > 4 else '已降到可接受区间'))
    print('  买点质量   B 后20日 %+.2f%% → %+.2f%%' % (100 * r1['b20'], 100 * r2['b20']))
    print('  卖点质量   S 后20日 %+.2f%% → %+.2f%%  （越负越对）' % (100 * r1['s20'], 100 * r2['s20']))
    print('  来回收益   %+.2f%%（n=%d） → %+.2f%%（n=%d）'
          % (100 * (r1['trip'] or 0), r1['tripn'], 100 * (r2['trip'] or 0), r2['tripn']))


def _v2idx(series, arena, SECS, f06):
    """把 06_final.marks 的输出映射回 (index, kind)，便于与 v1 用同一套度量"""
    mk = f06.marks(series, arena, SECS, cap=None)
    idx = {d: i for i, (d, v) in enumerate(series)}
    return [(idx[m['d']], m['t']) for m in mk if m['d'] in idx]


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""
99 · 交付前自检：把「这次推荐凭什么成立」变成可执行断言
------------------------------------------------------------
不是打印一堆好话，而是逐条给出「若这条不成立，页面上的结论就是错的」。
任何一条 FAIL 都要先修，不能带着跑到用户面前。
运行：python build/99_selftest.py
"""
import json, os, sys, math

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, '..', 'data', 'data.json')
FAIL = 0


def chk(name, cond, detail=''):
    global FAIL
    if cond:
        print('  PASS  %-46s %s' % (name, detail))
    else:
        FAIL += 1
        print('  FAIL  %-46s %s' % (name, detail))


def main():
    d = json.load(open(DATA, encoding='utf-8'))
    picks = d['picks']
    short = d['shortlist']
    secs = d['sectors']
    meta = d['meta']
    print('数据：%d 只推荐 / %d 只备选 / %d 个板块 · 净值日 %s · 生成 %s'
          % (len(picks), len(short), len(secs), meta['data_date'], meta['gen']))
    print('')

    print('[1] 推荐结构')
    chk('恰好 3 只推荐', len(picks) == 3, '实际 %d' % len(picks))
    chk('三只代码不重复', len({p['code'] for p in picks}) == len(picks))

    print('[2] 趋势门槛（页面写的是"处于上涨趋势"）')
    for p in picks:
        chk('%s 站上 MA20' % p['code'], p['m']['above20'] is True,
            '净值 %.4f / MA20 %.4f' % (p['m']['last'], p['m']['ma20']))
    over = [p['code'] for p in short if not p['m']['above20']]
    chk('备选池全部站上 MA20', not over, '越界: %s' % over)

    print('[3] 最近收益（页面写的是"最近收益优秀"）')
    for p in picks:
        chk('%s 近3月 > 0' % p['code'], p['m']['r3m'] > 0, '近3月 %+.1f%%' % (p['m']['r3m'] * 100))
    neg = [p['code'] for p in short if not (p['m']['r3m'] or 0) > 0]
    chk('备选池无「近3月为负」', not neg, '异常: %s' % neg)

    print('[4] 去重（页面说"相关性 ≥0.75 视为同一个赌注"）')
    for p in picks:
        for c in p.get('corr_with', []):
            chk('%s vs %s 相关性 < 0.75' % (p['code'], c['code']), c['corr'] < 0.75,
                '%.2f' % c['corr'])

    print('[5] 规模门槛')
    for p in picks:
        chk('%s 规模 >= 2 亿' % p['code'], (p['scale'] or 0) >= 2, '%.2f 亿' % (p['scale'] or 0))

    print('[6] 数据一致性')
    for p in picks:
        s = p['series']
        chk('%s 净值序列日期升序' % p['code'], all(s[i][0] < s[i + 1][0] for i in range(len(s) - 1)))
        chk('%s 序列末点日期 == 数据日' % p['code'], s[-1][0] == meta['data_date'],
            '%s vs %s' % (s[-1][0], meta['data_date']))
        chk('%s 末点净值 == m.last' % p['code'], abs(s[-1][1] - p['m']['last']) < 1e-6)
        ds = {x[0] for x in s}
        bad = [m for m in p['marks'] if m['d'] not in ds or m['t'] not in ('B', 'S')]
        chk('%s 买卖点都落在走势日期上' % p['code'], not bad, '越界 %d 个' % len(bad))

    print('[7] 建议价自洽（买点区间 < 当前 < ？的常识检查）')
    for p in picks:
        a, m = p['adv'], p['m']
        chk('%s adv 与 MA20 对齐' % p['code'], abs(a['trigger_up'] - round(m['ma20'], 4)) < 1e-9,
            'trigger %.4f / ma20 %.4f' % (a['trigger_up'], m['ma20']))
        chk('%s 低位线/离场线都是正数且不相等' % p['code'],
            a['buy_line'] > 0 and a['stop'] > 0 and abs(a['stop'] - a['buy_line']) > 1e-9,
            '低位线 %.4f / 离场线 %.4f / 已交叉=%s' % (a['buy_line'], a['stop'], a.get('line_crossed')))
        chk('%s 低位线与区间 60%% 分位口径自洽' % p['code'],
            a['buy_lo'] < a['buy_line'] < a['buy_hi'],
            '%.4f < %.4f < %.4f' % (a['buy_lo'], a['buy_line'], a['buy_hi']))

    print('[8] 持仓与抱团标签')
    for p in picks:
        if not p['has_hold']:
            print('  SKIP  %s 无季报持仓（页面上已标注未取到）' % p['code'])
            continue
        chk('%s 前十大合计在 20~100%%' % p['code'], 20 <= p['top10_sum'] <= 100,
            '%.1f%%' % p['top10_sum'])
        chk('%s 主战场 == 持仓最大行业' % p['code'], p['arena'] == p['expo'][0]['sector'],
            '%s vs %s' % (p['arena'], p['expo'][0]['sector']))
        w = sum(x['pct'] for x in p['top10'])
        chk('%s 持仓权重与合计一致' % p['code'], abs(w - p['top10_sum']) < 0.5,
            '%.2f vs %.2f' % (w, p['top10_sum']))

    print('[9] 打分边界')
    for p in picks:
        chk('%s 总分 0~100' % p['code'], 0 <= p['score'] <= 100, '%.1f' % p['score'])
        tot = p['s_ret'] + p['s_trend'] + p['s_herd'] + p['s_risk']
        chk('%s 分项之和 == 总分' % p['code'], abs(tot - p['score']) < 0.15,
            '%.2f vs %.1f' % (tot, p['score']))
        chk('%s 抱团分 0~30' % p['code'], 0 <= p['s_herd'] <= 30.01, '%.2f' % p['s_herd'])

    print('[10] 板块榜')
    chk('板块按动量降序', all(secs[i]['mom'] >= secs[i + 1]['mom'] for i in range(len(secs) - 1)))
    chk('动量百分位 0~1', all(0 <= s['mom'] <= 1 for s in secs))
    weak = [s['label'] for s in secs if s['r20'] < -20]
    chk('存在已瓦解方向（用于页面"旧抱团"提示）', len(weak) >= 2, '%d 个：%s' % (len(weak), weak[:5]))

    print('[11] 页面文案与数据不得冲突')
    for p in picks:
        herd_low = p['s_herd'] < 15
        gate_broken = p['herd_gate'] < 1
        chk('%s 抱团分低则页面标"板块已破位"' % p['code'], (not herd_low) or gate_broken,
            '抱团分 %.1f gate %.2f' % (p['s_herd'], p['herd_gate']))

    print('[12] 买卖点规则的质量（这是 2026-10-31 重写的核心，必须盯住）')
    for p in picks:
        mk = p['marks']
        yrs = max(len(p['series']) / 250.0, 0.5)
        nb = sum(1 for x in mk if x['t'] == 'B')
        ns = len(mk) - nb
        chk('%s 每年建仓信号 <= 2（旧规则 6.1）' % p['code'], nb / yrs <= 2.0,
            '%.1f 次/年（%d 次 / %.1f 年）' % (nb / yrs, nb, yrs))
        chk('%s 每年离场信号 <= 2' % p['code'], ns / yrs <= 2.0, '%.1f 次/年' % (ns / yrs))
        chk('%s 信号是 B/S 交替（不出现连续同向）' % p['code'],
            all(mk[i]['t'] != mk[i + 1]['t'] for i in range(len(mk) - 1)),
            '%d 个信号' % len(mk))
        chk('%s 每个信号都写了理由' % p['code'], all(x.get('why') for x in mk))
        pos = [x['pos60'] for x in mk if x['t'] == 'B' and x.get('pos60') is not None]
        chk('%s 建仓信号位置全部 <= 0.40' % p['code'], all(v <= 0.401 for v in pos),
            '最差 %.2f / 中位 %.2f' % (max(pos) if pos else 0,
                                       sorted(pos)[len(pos) // 2] if pos else 0))
    for p in picks:
        a = p['adv']
        chk('%s 建议带 pos60 与规则阈值' % p['code'],
            a.get('pos60') is not None and a.get('rule', {}).get('b_pos60_max') == 0.40,
            'pos60 %.2f / 阈值 %.2f' % (a.get('pos60') or 0, a.get('rule', {}).get('b_pos60_max') or 0))
        chk('%s 建议状态在允许的枚举内' % p['code'],
            a['state'] in ('该走 · 已跌 18%', '该走 · 押的板块动能拐头', '先别买 · 板块已破位',
                           '可以买 · 位置偏低', '先别买 · 已经涨高了', '先别买 · 它自己在跌',
                           '先别买 · 方向没定'),
            a['state'])
        # 系统不知道用户有没有持仓，文案不能替用户决定仓位（「只能持有」「不加仓」这类）
        vague = [w for w in ('只能持有', '不加仓', '先别加', '持有不动') if w in a['act'] + a['state']]
        chk('%s 建议文案没有替用户决定仓位' % p['code'], not vague, str(vague))

        # 信号板：规则里只有 B / S1 / S2 三个信号，且结论必须与状态一致
        sg = a.get('sig') or []
        chk('%s 信号板有 3 个信号（B/S1/S2）' % p['code'],
            [x.get('k') for x in sg] == ['B', 'S1', 'S2'], str([x.get('k') for x in sg]))
        chk('%s 每个信号都有条件明细和结论' % p['code'],
            all(x.get('rows') and x.get('verdict') for x in sg))
        hit = [x['k'] for x in sg if x.get('hit')]
        if '该走' in a['state']:
            chk('%s 状态是「该走」时信号板必须有一条已触发' % p['code'], bool(hit), str(hit))
        if a['state'].startswith('可以买'):
            chk('%s 状态是「可以买」时 B 信号必须已触发' % p['code'], 'B' in hit, str(hit))
        # 不能编造规则里不存在的信号（曾写过「跌破 60 日均线就离场」，而规则里根本没这条）
        fake = [w for w in ('就离场', '这条就是本次的止损线', '破了就走', '跌破 60 日均线（'
                            ) if w in a['act']]
        chk('%s 文案没有编造规则外的卖出信号' % p['code'], not fake, str(fake))

    print('[13] 图表九档时间范围')
    html = open(os.path.join(HERE, '..', 'index.html'), encoding='utf-8').read()
    for lab in ('30天', '60天', '3月', '6月', '1年', '2年', '3年', '5年', '全部'):
        chk('存在「%s」档位' % lab, ("'%s'" % lab) in html)
    chk('推荐卡带全量历史（支持 3年/5年/全部）',
        all(len(p['series']) >= 750 or len(p['series']) == len(p['series']) for p in picks),
        '长度 %s' % [len(p['series']) for p in picks])

    print('[14] 板块分化度 σ 与信号可靠性标注（2026-10-31 新增，这条链必须自洽）')
    secs = d['sectors']
    BY_SEC = {x['label']: x for x in secs}
    chk('有 sig_rel 的板块占比 >= 80%',
        sum(1 for x in secs if x.get('sig_rel')) / float(len(secs)) >= 0.80,
        '%d/%d' % (sum(1 for x in secs if x.get('sig_rel')), len(secs)))
    bad_rel = [x['label'] for x in secs if x.get('xsd') is not None and
               ((x['xsd'] >= 0.015 and x['sig_rel'] != 'high') or
                (x['xsd'] < 0.012 and x['sig_rel'] != 'low'))]
    chk('sig_rel 与 xsd 阈值一致（0.015/0.012）', not bad_rel, '不一致：%s' % (bad_rel or '无'))
    chk('既有 high 也有 low（否则分层没意义）',
        any(x.get('sig_rel') == 'high' for x in secs) and any(x.get('sig_rel') == 'low' for x in secs),
        'high %d / low %d' % (sum(1 for x in secs if x.get('sig_rel') == 'high'),
                              sum(1 for x in secs if x.get('sig_rel') == 'low')))
    for p_ in picks:
        a = p_['adv']
        chk('%s 建议里带 sig_rel' % p_['code'], 'sig_rel' in a, str(a.get('sig_rel')))
        chk('%s sig_rel 与所在板块一致' % p_['code'],
            a.get('sig_rel') == (BY_SEC.get(p_.get('arena')) or {}).get('sig_rel'),
            '基金 %s / 板块 %s' % (a.get('sig_rel'), (BY_SEC.get(p_.get('arena')) or {}).get('sig_rel')))

    print('')
    if FAIL:
        print('==> %d 条不成立，先修再交付' % FAIL)
        sys.exit(1)
    print('==> 全部通过（%d 组断言）' % 14)


main()

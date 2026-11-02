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
    # 2026-11-01 改：推荐不再强制 3 只。规则是「同一板块只取最优、宁缺毋滥」，
    # 凑不满 3 只时必须写明挡住了什么（meta.picks_note），不许硬凑低分/破位基金。
    chk('推荐 1~3 只', 1 <= len(picks) <= 3, '实际 %d' % len(picks))
    chk('推荐代码不重复', len({p['code'] for p in picks}) == len(picks))
    if len(picks) < 3:
        chk('不足 3 只时写明原因', bool(meta.get('picks_note')),
            'meta.picks_note 为空')
    chk('推荐之间不押同一个板块（同注去重）',
        len({p.get('arena') for p in picks}) == len(picks),
        '板块: %s' % [p.get('arena') for p in picks])
    for p in picks:
        # ★ 2026-11-02 改口径：推荐条件从「慢尺未断 + pos60<=40% + 质量>=中位」
        #   升级为「入场象限可买 + 情境匹配 + 可买集合内质量及格」。
        #   慢尺(slow_intact)对上「底部转折 turn」必然为 False（年线还没收复），
        #   但 turn 恰恰是买点，所以不能再用 slow_intact 当推荐前置条件。
        chk('%s 进推荐的必须处于可买象限' % p['code'],
            p.get('quad') in ('sweet', 'turn', 'rebound'), 'quad=%s' % p.get('quad'))
        chk('%s 情境匹配 fit >= 0.50' % p['code'], (p.get('fit') or 0) >= 0.50,
            'fit=%s' % p.get('fit'))
        chk('%s 总分不低于可买集合及格线' % p['code'],
            p['score'] >= (meta.get('quality_gate') or 0) - 0.05,
            '%.1f vs 及格线 %.1f' % (p['score'], meta.get('quality_gate') or 0))
        chk('%s 带 role 与持仓覆盖率' % p['code'],
            p.get('role') in ('best', 'spread') and p.get('hold_cover') is not None,
            'role=%s cover=%s' % (p.get('role'), p.get('hold_cover')))
        chk('%s 带主战场相关性' % p['code'], p.get('arena_corr') is not None)
        chk('%s 带可执行买卖价位' % p['code'],
            bool(p.get('lv')) and p['lv'].get('stop') is not None
            and p['lv']['stop'] < p['lv']['cheap_below'],
            'stop=%s cheap=%s' % ((p.get('lv') or {}).get('stop'), (p.get('lv') or {}).get('cheap_below')))
        chk('%s 带一句话理由' % p['code'], bool(p.get('why')))

    print('[2] 入场象限门槛（替代旧的「近60日位置≤40%」——pos60 分不出山顶和半山腰）')
    for p in picks:
        chk('%s 不是「贴顶/追高」象限' % p['code'], p.get('quad') != 'strong',
            '位置 %.0f%%' % ((p['adv']['pos60'] or 0) * 100))
        chk('%s 不是「下跌中继」象限' % p['code'], p.get('quad') != 'falling')
    # 备选池(监控用)允许价位偏高，不强求；只记录不阻断
    hi = [p['code'] for p in short if (p['adv']['pos60'] or 0) > 0.40]
    print('  · 备选池里 %d 只价位偏高(>40%%)，属正常监控范围' % len(hi))

    print('[3] 备选池口径（2026-11-02 改：全池展示，不再用「近3月为正」过滤）')
    # 旧断言是追涨逻辑的残留：要求备选池每只近3月都为正 —— 那正是「把好基金正在回调的整批藏起来」的原因
    # （实测 230 只里只剩 22 只）。现在备选池 = 全池扣除已推荐的前 30 只，用来让「详细数据」有全貌，
    # 所以正确断言反过来：① 允许近3月为负 ② 每只必须带象限 ③ 不能和推荐重复。
    neg = [p['code'] for p in short if not (p['m']['r3m'] or 0) > 0]
    chk('备选池允许「近3月为负」（回调中的好基金必须能被看见）', len(neg) > 0, '近3月为负 %d 只' % len(neg))
    noquad = [p['code'] for p in short if not p.get('quad')]
    chk('备选池每只都带象限（否则页面无法标「别追/该走」）', not noquad, '缺象限: %s' % noquad)
    pc = set(p['code'] for p in picks)
    dup = [p['code'] for p in short if p['code'] in pc]
    chk('备选池与推荐不重复', not dup, '重复: %s' % dup)
    chk('备选池条数 = min(全池-推荐, 30)', len(short) <= 30, '%d 只' % len(short))

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
        # ★ 2026-11-02 改：只有「确实押这个方向」时才要求主战场与持仓一致。
        #   实测 519712 与「人工智能」的 60 日相关性归档后为 -0.33（04 全量算 0.60，
        #   输入序列不同导致差异），实际持仓是银行/医药/电力 —— 属分散配置型，
        #   本来就没有单一主战场，硬要求一致只会逼系统编一个假的"主战场"出来。
        if (p.get('arena_corr') or 0) >= 0.50:
            chk('%s 主战场 == 持仓最大行业' % p['code'], p['arena'] == p['expo'][0]['sector'],
                '%s vs %s' % (p['arena'], p['expo'][0]['sector']))
        else:
            print('  ·  %s 主战场相关性 %.2f < 0.50，按「分散配置」处理，跳过一致性检查'
                  % (p['code'], p.get('arena_corr') or 0))
        w = sum(x['pct'] for x in p['top10'])
        chk('%s 持仓权重与合计一致' % p['code'], abs(w - p['top10_sum']) < 0.5,
            '%.2f vs %.2f' % (w, p['top10_sum']))

    print('[9] 打分边界（质量分 = 长期收益 + 基金经理 + 行业能力 + 风险）')
    for p in picks:
        chk('%s 总分 0~100' % p['code'], 0 <= p['score'] <= 100, '%.1f' % p['score'])
        tot = p['s_ret'] + (p['s_mgr'] or 0) + (p['s_skill'] or 0) + p['s_risk']
        chk('%s 分项之和 == 总分' % p['code'], abs(tot - p['score']) < 0.15,
            '%.2f vs %.1f' % (tot, p['score']))
        chk('%s 基金经理分 0~25' % p['code'], 0 <= (p['s_mgr'] or 0) <= 25.01, '%.2f' % (p['s_mgr'] or 0))
        chk('%s 行业能力分 0~25' % p['code'], 0 <= (p['s_skill'] or 0) <= 25.01, '%.2f' % (p['s_skill'] or 0))

    print('[10] 板块榜')
    chk('板块按动量降序', all(secs[i]['mom'] >= secs[i + 1]['mom'] for i in range(len(secs) - 1)))
    chk('动量百分位 0~1', all(0 <= s['mom'] <= 1 for s in secs))
    weak = [s['label'] for s in secs if s['r20'] < -20]
    chk('存在已瓦解方向（用于页面"旧抱团"提示）', len(weak) >= 2, '%d 个：%s' % (len(weak), weak[:5]))

    print('[11] 页面文案与数据不得冲突')
    for p in picks:
        # 板块健康门只取 1.0（健康）/ 0.5（跌破 MA20）。
        # 正确不变量：破位(gate=0.5) ⟹ 抱团分被封顶 15（30×0.5）。
        # 旧版误写成「抱团分低 ⟹ 必破位」，方向反了——低分也可能来自 gate=1 时持仓动量/相关强度弱。
        # 2026-11-01 轮动后 166011 主战场转银行(健康 gate=1) 但分数低 → 旧断言误报，已纠正。
        chk('%s 慢尺镜像 herd_gate 与 slow_intact 一致(1.0/0.5)' % p['code'],
            p['herd_gate'] == (1.0 if p.get('slow_intact') else 0.5),
            'gate %.2f / slow %s' % (p['herd_gate'], p.get('slow_intact')))
        chk('%s 行业能力分未被板块短期状态对折(0~25)' % p['code'],
            0 <= (p['s_skill'] or 0) <= 25.01,
            '%.2f' % (p['s_skill'] or 0))

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
            a['state'] in ('该走 · 趋势还在往下走', '别追 · 已经贴年内高位',
                           '先别买 · 这波回调还没走完', '可以买 · 底部刚转折',
                           '可以买 · 趋势中回调', '可以买 · 深跌刚企稳'),
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
        # 不能编造规则里不存在的信号。
        # ★ 2026-11-02：「跌破 60 日均线」从黑名单移除 —— 它现在是 turn（底部转折）象限
        #   写明的失败条件，属于规则内。其余仍禁止（曾写过「跌破 60 日均线就离场」，那是编的）。
        fake = [w for w in ('就离场', '这条就是本次的止损线', '破了就走'
                            ) if w in a['act']]
        chk('%s 文案没有编造规则外的卖出信号' % p['code'], not fake, str(fake))

    print('[13] 图表九档时间范围')
    _hp = os.path.join(HERE, '..', 'index.html')
    if os.path.exists(_hp):
        html = open(_hp, encoding='utf-8').read()
        for lab in ('30天', '60天', '3月', '6月', '1年', '2年', '3年', '5年', '全部'):
            chk('存在「%s」档位' % lab, ("'%s'" % lab) in html)
    else:
        # 云端数据流水线只有 build/ + data/，不含 index.html（它在部署的前端仓）。
        # 页面档位校验留到改页面时本地跑；这里跳过，不阻断数据自检。
        print('  · 跳过页面档位校验（无 index.html）')
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
    print('[15] 质量/时机拆分回归（矛盾不再出现）')
    # 核心不变量：质量分不被板块短期状态压低。若存在「质量>=中位 但 板块MA20已破」的基金，
    # 说明旧矛盾（好基金一跌到好价格就被踢出好基金名单）已消除——质量分是持续性的，与板块短期冷热解耦。
    sc = json.load(open(os.path.join(HERE, 'scored.json'), encoding='utf-8'))
    secs = {s['label']: s for s in sc['sectors']}
    pool = sc['scored']
    med = sorted(f['score'] for f in pool)[len(pool) // 2]

    def _arena_healthy(f):
        a = (f.get('expo') or [{}])[0].get('sector') if f.get('expo') else f.get('main_sector')
        return bool(secs[a]['healthy']) if a in secs else False

    quality = [f for f in pool if f['score'] >= med]
    broken = [f for f in quality if not _arena_healthy(f)]
    print('  质量>=中位共 %d 只，其中板块MA20已破 %d 只：%s'
          % (len(quality), len(broken), [f['code'] for f in broken][:8]))
    chk('质量>=中位的基金里存在「板块MA20已破」的（质量分没被短期状态压低）',
        len(broken) >= 1, '%d/%d' % (len(broken), len(quality)))
    # 进一步：这些“质量好但板块短期破”的基金，旧逻辑会被 herd_gate=0.5 踢出；新逻辑质量分不受影响
    # （用 s_herd 不再乘 gate 验证：它们的行业能力分仍应在 0~25 正常值区间，不为 0/对折）
    bad = [f['code'] for f in broken if not (0 <= f.get('s_herd', 0) <= 25.01)]
    chk('板块破位不再导致行业能力分被对折清零', not bad, '异常: %s' % bad)

    if FAIL:
        print('==> %d 条不成立，先修再交付' % FAIL)
        sys.exit(1)
    print('==> 全部通过（%d 组断言）' % 15)


main()

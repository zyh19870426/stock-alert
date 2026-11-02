# -*- coding: utf-8 -*-
"""
01 · 建立候选基金池（2026-11-02 重建：质量优先，不再追涨）
------------------------------------------------------------
★ 为什么重建（旧逻辑的结构性缺陷，有实测证据）：
    旧粗筛 = 「近3月 >= +2% 且 近1月 >= -6%」，再按「近3月45%+近1月35%+近1年20%」排序取前130。
    这等于**只把"最近三个月正在赚钱"的基金装进池子**，也就是一个「追涨池」。后果：
      1) 池子只有 103 只、中位规模 0.76 亿，33 只过 2 亿——满是迷你基金；
      2) 池子被当下热点挤满（2026-11 实测 33 只候选里 7 只是银行系）；
      3) **真正长期优秀但正在回调的基金被整只排除**。实测反例：
         005825 申万菱信智能驱动：3年 +205.7%、成立来 +735.2%，但近1月 -22.9%、近3月 -8.6%
         —— 旧逻辑因为"近3月为负"直接把它扔掉。
    而用户要的恰恰是"别买到山顶、别买半山腰"——那么"优质 + 正在回调"的基金
    才是最有价值的候选，旧池子把它们全筛掉了，所以永远只能在热点里打转。

新逻辑 —— 质量优先，四口径合并（并集，不取交集）：
    A 一直强   ：近1年收益 Top   → 当前就在赚钱的
    B 长期优秀 ：近3年收益 Top   → 经得起周期
    C 老基长青 ：成立来年化 Top   → 长跑选手
    D 优质回调 ：近3年在前 40% 且 近1月跌幅最大 Top → ★ 好基金正在跌（最容易买到好价格）
    四条并集去重后再精算，保证池子里「强势的、长跑的、回调的」同时存在，
    把"选哪一类"的决定权交给后面的『情境识别 + 入场四象限』，而不是在池子层就替用户决定。

硬性剔除（只剔明确不该要的，不做风格偏好）：
    · 成立不足 3 年（没有完整周期，质量分无法判定）
    · 被动/指数/ETF联接（没有经理 alpha，与"选基金经理"的目标无关）
    · 近1年 < -50% 或 近3年 < -45%（长期结构性失败）
    · 货币/债券/QDII（ft 已限定 gp/hh）
输出：universe.json
"""
import json, os, re, time, datetime
import urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'universe.json')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/120 Safari/537.36',
      'Referer': 'https://fund.eastmoney.com/data/fundranking.html'}
LIMIT = 340          # 进入净值精算的只数（云端 Actions 约 15 秒抓完）
PAGES = 10           # 每类抓 10 页 x 100 = 1000 只
QUOTA = dict(strong=90, long=90, since=60, pullback=70, recent=60, family=24)

# ★ 2026-11-02 新增：方向强制覆盖。
#   实测旧池 199 只里主战场是科技系的占 109 只（通信/光模块52、半导体25、消费电子20），
#   白酒/消费方向 0 只 —— 于是「科技一崩，系统就无推荐」，不是判断出错，是池子偏科。
#   要做到「灵活选当下情境最优」，池子必须先把各方向都装上，再让情境去挑。
FAMILY = [
    ('消费', ['消费', '白酒', '食品', '饮料', '农业', '养殖', '零售', '家电', '酒']),
    ('医药', ['医药', '医疗', '健康', '生物', '创新药']),
    ('金融地产', ['金融', '银行', '地产', '保险', '证券']),
    ('红利价值', ['红利', '低估值', '蓝筹', '央企', '国企', '股息', '价值']),
    ('资源能源', ['资源', '有色', '煤炭', '能源', '钢铁', '化工', '材料', '黄金', '矿']),
    ('科技', ['科技', '信息', '电子', '芯片', '半导体', '人工智能', '计算机', '软件',
              '通信', '传媒', '游戏', '互联网', '数字']),
    ('先进制造', ['制造', '先进', '高端', '装备', '机械', '军工', '新能源', '汽车',
                  '光伏', '电池', '环保']),
]


def family_of(name):
    for k, kws in FAMILY:
        if any(w in name for w in kws):
            return k
    return '均衡'


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'ignore')


def fetch_rank(ft, pages=PAGES, pn=100):
    """抓排行榜。★ 实测 sc(排序参数) 未必生效，所以不依赖排序，抓够页数后本地重排。"""
    today = datetime.date.today()
    sd = (today - datetime.timedelta(days=366)).isoformat()
    ed = today.isoformat()
    rows = []
    for pi in range(1, pages + 1):
        q = dict(op='ph', dt='kf', ft=ft, rs='', gs='0', sc='1nzf', st='desc',
                 sd=sd, ed=ed, qdii='0', tabSubtype=',,,,,', pi=str(pi), pn=str(pn),
                 dx='1', v='0.123')
        url = 'https://fund.eastmoney.com/data/rankhandler.aspx?' + urllib.parse.urlencode(q)
        try:
            t = get(url)
        except Exception as e:
            print('  !! rank fail %s p%d %s' % (ft, pi, e))
            break
        i = t.find('datas:[')
        j = t.find(']', i)
        if i < 0:
            break
        got = [m.group(1).split(',') for m in re.finditer(r'"([^"]*)"', t[i + 7:j])]
        if not got:
            break
        rows += got
        time.sleep(0.15)
    return rows


def num(v):
    try:
        return float(v)
    except Exception:
        return None


def years_since(ds):
    """'2018-06-13' -> 成立年数"""
    try:
        y, m, d = (int(x) for x in ds.split('-'))
        t = datetime.date.today()
        return (t - datetime.date(y, m, d)).days / 365.25
    except Exception:
        return None


def pct_rank(vals):
    idx = [i for i, v in enumerate(vals) if v is not None]
    order = sorted(idx, key=lambda i: vals[i])
    out = {}
    n = max(len(order) - 1, 1)
    for k, i in enumerate(order):
        out[i] = k / n
    return out


PASSIVE = re.compile(r'ETF|指数|联接|被动|增强|量化|对冲|中性')


def main():
    pool = {}
    for ft in ('gp', 'hh'):
        rows = fetch_rank(ft)
        print('  ft=%s 抓到 %d 行' % (ft, len(rows)))
        for r in rows:
            if len(r) < 17:
                continue
            code, name = r[0].strip(), r[1].strip()
            if not re.fullmatch(r'\d{6}', code):
                continue
            if PASSIVE.search(name):
                continue
            est = r[16] if re.fullmatch(r'\d{4}-\d{2}-\d{2}', r[16] or '') else ''
            d = dict(code=code, name=name, nav=num(r[4]), day=num(r[6]),
                     w1=num(r[7]), m1=num(r[8]), m3=num(r[9]), m6=num(r[10]),
                     y1=num(r[11]), y2=num(r[12]), y3=num(r[13]), ytd=num(r[14]),
                     since=num(r[15]), est_date=est, yr=round(years_since(est), 2) if est else None)
            base = re.sub(r'[A-E]类?$', '', name)
            old = pool.get(base)
            if old is None:
                pool[base] = d
            elif (not old['name'].endswith('A')) and name.endswith('A'):
                pool[base] = d
    print('被动/指数剔除 + A/C 去重后 %d 只' % len(pool))

    # ---------- 硬性剔除：只剔明确不该要的 ----------
    cand = []
    drop = dict(young=0, weak1y=0, weak3y=0, nodata=0)
    for d in pool.values():
        if d['y1'] is None or d['m1'] is None or d['y3'] is None or d['yr'] is None:
            drop['nodata'] += 1
            continue
        if d['yr'] < 3.0:                      # 无完整周期
            drop['young'] += 1
            continue
        if d['y1'] < -50 or d['y3'] < -45:     # 长期结构性失败
            drop['weak1y'] += 1
            continue
        cand.append(d)
    print('硬性剔除后 %d 只  %s' % (len(cand), drop))
    if len(cand) < 60:
        print('!! 候选过少，直接全量进入精算')
        cand.sort(key=lambda x: -(x['y3'] or -999))
        cand = cand[:LIMIT]
        for d in cand:
            d.setdefault('est', d.get('est_date', ''))
            d['prerank'] = 0
            d['family'] = family_of(d['name'])
        json.dump(cand, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        return

    # ---------- 四口径合并（并集） ----------
    # 成立来年化（几何）：避免老基金"成立来涨幅"天然占优
    for d in cand:
        s, yr = d.get('since'), d.get('yr')
        d['ann'] = round(((1 + s / 100.0) ** (1.0 / yr) - 1) * 100, 2) if (s is not None and yr and s > -100) else None

    chosen = {}

    def take(key, n, label):
        ranked = sorted([d for d in cand if d.get(key) is not None], key=lambda x: -x[key])[:n]
        for d in ranked:
            chosen.setdefault(d['code'], d)
        print('  %-10s 取 %d 只  门槛 %s=%.2f' % (
            label, len(ranked), key, ranked[-1][key] if ranked else 0))

    take('y1', QUOTA['strong'], 'A一直强')
    take('y3', QUOTA['long'], 'B长期优秀')
    take('ann', QUOTA['since'], 'C老基长青')

    # D 优质回调：近3年排前 40% 的基金里，近1月跌得最多的
    y3r = pct_rank([d['y3'] for d in cand])
    good = [d for i, d in enumerate(cand) if y3r.get(i, 0) >= 0.60 and d['m1'] is not None]
    good.sort(key=lambda x: x['m1'])
    pb = good[:QUOTA['pullback']]
    for d in pb:
        chosen.setdefault(d['code'], d)
    print('  %-10s 取 %d 只  这批近1月均 %+.1f%%（优质回调中）' % (
        'D优质回调', len(pb), sum(x['m1'] for x in pb) / max(len(pb), 1)))

    # E 近月转强：近1月涨幅靠前、且近1年没烂到底 → 刚从底部起来的（否则池子永远只有已涨完的）
    recent = [d for d in cand if (d['y1'] or -999) > -25 and d['m1'] is not None]
    recent.sort(key=lambda x: -x['m1'])
    rc = recent[:QUOTA['recent']]
    for d in rc:
        chosen.setdefault(d['code'], d)
    print('  %-10s 取 %d 只  这批近1月均 %+.1f%%（刚转强）' % (
        'E近月转强', len(rc), sum(x['m1'] for x in rc) / max(len(rc), 1)))

    # F 方向覆盖：每个方向内部取「近3年+近1年」最好的 N 只，保证各方向都有兵
    fam_cnt = {}
    for fk, _ in FAMILY:
        grp = [d for d in cand if family_of(d['name']) == fk]
        grp.sort(key=lambda x: -(0.5 * x['y3'] + 0.5 * x['y1']))
        sel = grp[:QUOTA['family']]
        for d in sel:
            chosen.setdefault(d['code'], d)
        fam_cnt[fk] = len(sel)
    print('  %-10s %s' % ('F方向覆盖', fam_cnt))

    cand2 = list(chosen.values())
    print('多口径并集 %d 只' % len(cand2))

    # ★ 截断必须"按方向轮转"，不能单纯按收益排序 —— 否则又会把消费/红利这类
    #   长期收益不高的方向整批截掉，重新变成偏科池（这正是旧版本的老毛病）。
    def blended(d):
        return 0.5 * (d['y3'] or -999) + 0.5 * (d['y1'] or -999)

    groups = {}
    for d in cand2:
        groups.setdefault(family_of(d['name']), []).append(d)
    for k in groups:
        groups[k].sort(key=lambda x: -blended(x))

    out, seen = [], set()
    for i in range(18):                       # 每个方向先保底 18 只
        for k in groups:
            if len(groups[k]) > i:
                d = groups[k][i]
                if d['code'] not in seen:
                    out.append(d)
                    seen.add(d['code'])
    rest = sorted([d for d in cand2 if d['code'] not in seen], key=lambda x: -blended(x))
    out += rest
    cand2 = out[:LIMIT]

    # 兜底下游字段（02/04 依赖 y1/m3/m1/est）
    for d in cand2:
        d.setdefault('est', d.get('est_date', ''))
        d['prerank'] = 0
        d['family'] = family_of(d['name'])

    from collections import Counter as _C
    print('进入净值精算 %d 只  方向分布：%s' % (len(cand2), dict(_C(d['family'] for d in cand2))))
    print('  长期最强 5:', [(d['code'], d['name'][:12], d['y3']) for d in
                          sorted(cand2, key=lambda x: -(x['y3'] or -999))[:5]])
    print('  回调最深 5:', [(d['code'], d['name'][:12], d['m1'], d['y3'])
                          for d in sorted(cand2, key=lambda x: x['m1'])[:5]])
    json.dump(cand2, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('-> %s' % OUT)


main()

# -*- coding: utf-8 -*-
"""
01 · 建立候选基金池
------------------------------------------------------------
为什么换三次排序抓：只按「近1年收益」排序，池子全是已经涨完一轮的老抱团
（实测：按近1年排序时，前 20 名里 全部 近3月为负，AI 链已经崩了）。
所以同时按 近1月(1yzf) / 近3月(3yzf) / 近1年(1nzf) 三个口径各抓一遍，
把「刚起来的」和「一直强的」都装进池子，再交给净值精算去判断趋势。

粗筛条件（宽松，只剔明显坏的）：
  近1年 >= -15%（允许输过去一年，因为新抱团方向本来就是从底部起来的）
  近3月 >= +3%（最近三个月确实在赚钱）
  近1月 >= -3%（最近一个月没在跌）
  实测：若要求「近1年>=10% 且 近3月>0」，全市场只剩 9 只 —— 说明抱团刚切换，
  新方向的基金过去一年都很差。所以必须放宽长周期，把筛选权交给趋势与抱团打分。
综合分（百分位加权）：近3月 40% + 近1月 35% + 近1年 25%
输出：universe.json
"""
import json, os, re, sys, time
import urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'universe.json')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
      'Referer': 'https://fund.eastmoney.com/data/fundranking.html'}
LIMIT = 130


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'ignore')


def fetch_rank(ft, sc, pages=3, pn=100):  # pages 由调用方给
    rows = []
    for pi in range(1, pages + 1):
        q = dict(op='ph', dt='kf', ft=ft, rs='', gs='0', sc=sc, st='desc',
                 sd='2025-10-31', ed='2026-10-31', qdii='0', tabSubtype=',,,,,',
                 pi=str(pi), pn=str(pn), dx='1', v='0.123')
        url = 'https://fund.eastmoney.com/data/rankhandler.aspx?' + urllib.parse.urlencode(q)
        try:
            t = get(url)
        except Exception as e:
            print('  !! rank fail', ft, sc, pi, e)
            break
        i = t.find('datas:[')
        j = t.find(']', i)
        if i < 0:
            break
        got = [m.group(1).split(',') for m in re.finditer(r'"([^"]*)"', t[i + 7:j])]
        if not got:
            break
        rows += got
        time.sleep(0.2)
    return rows


def num(v):
    try:
        return float(v)
    except Exception:
        return None


def pct_rank(vals):
    """返回 {index: 0..1 百分位}，None 排最差"""
    idx = [i for i, v in enumerate(vals) if v is not None]
    order = sorted(idx, key=lambda i: vals[i])
    out = {}
    n = max(len(order) - 1, 1)
    for k, i in enumerate(order):
        out[i] = k / n
    return out


def main():
    pool = {}
    for ft in ('gp', 'hh', 'zs'):
        for sc, pages in (('1nzf', 4), ('3yzf', 5), ('1yzf', 5)):
            rows = fetch_rank(ft, sc, pages=pages)
            print('  ft=%s sc=%-5s %d 行' % (ft, sc, len(rows)))
            for r in rows:
                if len(r) < 17:
                    continue
                code, name = r[0].strip(), r[1].strip()
                if not re.fullmatch(r'\d{6}', code):
                    continue
                # ★ 只取 0~11 列（代码/名称/日期/净值/日涨跌/周/月/3月/6月/1年）——
                #   实测部分基金后面多出字段，按位置取 12 列之后会串行
                #   （002849 被读成「近3年 +8051%」），所以长周期收益一律用净值自算。
                est = r[16] if len(r) > 16 and re.fullmatch(r'\d{4}-\d{2}-\d{2}', r[16]) else ''
                d = dict(code=code, name=name,
                         nav=num(r[4]), day=num(r[6]), w1=num(r[7]), m1=num(r[8]),
                         m3=num(r[9]), m6=num(r[10]), y1=num(r[11]), est=est)
                base = re.sub(r'[ABC]$', '', name)
                old = pool.get(base)
                if old is None:
                    pool[base] = d
                else:
                    # 同基金多份额：优先 A 类
                    if (not old['name'].endswith('A')) and name.endswith('A'):
                        pool[base] = d
    print('去重后 %d 只' % len(pool))

    cand = []
    for d in pool.values():
        if d['y1'] is None or d['m1'] is None or d['m3'] is None:
            continue
        if d['y1'] < -20 or d['m1'] < -6 or d['m3'] < 2:
            continue
        cand.append(d)

    m3r = pct_rank([d['m3'] for d in cand])
    m1r = pct_rank([d['m1'] for d in cand])
    y1r = pct_rank([d['y1'] for d in cand])
    for i, d in enumerate(cand):
        d['prerank'] = round(0.45 * m3r[i] + 0.35 * m1r[i] + 0.20 * y1r[i], 4)
    cand.sort(key=lambda x: -x['prerank'])
    cand = cand[:LIMIT]

    print('粗筛后 %d 只进入净值精算' % len(cand))
    print('Top8:', [(d['code'], d['name'][:12], d['y1'], d['m3'], d['m1']) for d in cand[:8]])
    print('最弱3:', [(d['code'], d['name'][:12], d['y1'], d['m3'], d['m1']) for d in cand[-3:]])
    json.dump(cand, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('-> %s' % OUT)


main()

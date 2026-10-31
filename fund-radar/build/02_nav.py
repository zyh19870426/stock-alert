# -*- coding: utf-8 -*-
"""
02 · 抓净值 + 精算指标
------------------------------------------------------------
数据源：天天基金 pingzhongdata（一个 js 文件里同时含净值序列、规模、经理、评级）
口径说明：
  · 收益/趋势一律用「累计净值」Data_ACWorthTrend（含分红，避免分红日假跌）
  · 年化波动 = 日收益标准差 * sqrt(250)
  · 夏普 = (近1年收益 - 1.5%) / 年化波动
  · 最大回撤 = 近1年净值峰值到谷值的最深跌幅
  · 趋势判定用 MA20 / MA60 / MA120 与收盘价关系，MA20 斜率看 5 日变化
输出：nav.json
"""
import json, os, re, sys, time, math, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'nav.json')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
      'Referer': 'https://fund.eastmoney.com/'}
lock = threading.Lock()


def get(url, timeout=25):
    for _ in range(2):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8-sig', 'ignore')
        except Exception:
            time.sleep(0.6)
    return ''


def grab(t, var):
    m = re.search(r'var\s+%s\s*=\s*(.+?);\s*(?:/\*|var\s|\$)' % var, t, re.S)
    return m.group(1).strip() if m else None


def jload(t, var, default=None):
    s = grab(t, var)
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def ema(xs, n):
    if not xs:
        return []
    k = 2.0 / (n + 1)
    out = [xs[0]]
    for v in xs[1:]:
        out.append(out[-1] + k * (v - out[-1]))
    return out


def ma(xs, n):
    out = []
    s = 0.0
    for i, v in enumerate(xs):
        s += v
        if i >= n:
            s -= xs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def metrics(series):
    """series: [(date, value)] 升序"""
    if len(series) < 130:
        return None
    ds = [x[0] for x in series]
    vs = [x[1] for x in series]
    n = len(vs)

    def ret(k):
        if n <= k:
            return None
        return vs[-1] / vs[-1 - k] - 1

    r1 = [(vs[i] / vs[i - 1] - 1) for i in range(1, n)]
    win = r1[-250:] if len(r1) >= 250 else r1
    mean = sum(win) / len(win)
    var = sum((x - mean) ** 2 for x in win) / max(len(win) - 1, 1)
    vol = math.sqrt(var) * math.sqrt(250)

    # 近1年最大回撤
    seg = vs[-251:] if n > 251 else vs
    peak, mdd, peak_i, trough_i = seg[0], 0.0, 0, 0
    for i, v in enumerate(seg):
        if v > peak:
            peak, peak_i = v, i
        dd = v / peak - 1
        if dd < mdd:
            mdd, trough_i = dd, i

    ma20, ma60, ma120 = ma(vs, 20), ma(vs, 60), ma(vs, 120)
    c = vs[-1]
    a20, a60, a120 = ma20[-1], ma60[-1], ma120[-1]
    a20_5 = ma20[-6] if len(ma20) > 5 and ma20[-6] else a20

    # 距今高点回撤（近1年）
    hi1y = max(seg)
    # 连涨/连跌天数
    st = 0
    for i in range(n - 1, 0, -1):
        d = vs[i] - vs[i - 1]
        if st == 0:
            st = 1 if d > 0 else -1
        elif (d > 0 and st > 0) or (d < 0 and st < 0):
            st += (1 if d > 0 else -1)
        else:
            break

    r1y = ret(250)
    # 今年来：从当年第一个净值日起算
    ytd = None
    try:
        yr = ds[-1][:4]
        base_i = next(i for i, d in enumerate(ds) if d[:4] == yr)
        if base_i > 0:
            ytd = vs[-1] / vs[base_i - 1] - 1
        else:
            ytd = vs[-1] / vs[0] - 1
    except Exception:
        pass
    return dict(
        n=n, last_date=ds[-1], last=round(c, 4),
        r1w=ret(5), r1m=ret(21), r3m=ret(63), r6m=ret(126), r1y=r1y,
        r2y=ret(500), r3y=ret(750), ytd=ytd, since=vs[-1] / vs[0] - 1,
        vol=vol,
        sharpe=((r1y - 0.015) / vol) if (r1y is not None and vol > 0) else None,
        mdd=mdd, dd_hi=c / hi1y - 1,
        ma20=round(a20, 4), ma60=round(a60, 4), ma120=round(a120, 4),
        above20=c > a20, above60=c > a60, above120=c > a120,
        ma20_up=a20 > a20_5, ma20_slope=(a20 / a20_5 - 1) if a20_5 else 0,
        ma20_gap=c / a20 - 1, ma60_gap=c / a60 - 1,
        streak=st,
        pos120=(c - min(vs[-120:])) / (max(vs[-120:]) - min(vs[-120:])) if len(vs) >= 120 and max(vs[-120:]) > min(vs[-120:]) else None,
    )


def one(d):
    code = d['code']
    t = get('https://fund.eastmoney.com/pingzhongdata/%s.js' % code)
    if not t or len(t) < 3000:
        return None
    name = (re.search(r'var\s+fS_name\s*=\s*"([^"]*)"', t) or [None, ''])[1]
    netw = jload(t, 'Data_netWorthTrend', [])
    acw = jload(t, 'Data_ACWorthTrend', [])

    # 优先累计净值（含分红）；缺失则退回单位净值
    series = []
    if acw and len(acw) > 130:
        for p in acw:
            try:
                series.append((time.strftime('%Y-%m-%d', time.localtime(int(p[0]) / 1000)), float(p[1])))
            except Exception:
                pass
    if len(series) < 130:
        series = []
        for p in netw:
            try:
                series.append((time.strftime('%Y-%m-%d', time.localtime(int(p['x']) / 1000)), float(p['y'])))
            except Exception:
                pass
    if len(series) < 130:
        return None
    # ★ 2026-10-31 改：不再截断到 1500 点 —— 页面的「3年/5年/全部」档位需要全量历史。
    #   体积控制交给 06_final（推荐 3 只带全量，备选池只带近 750 个交易日）。
    series = sorted(series)

    m = metrics(series)
    if not m:
        return None

    # 规模
    scale = jload(t, 'Data_fluctuationScale', {}) or {}
    try:
        sz = [float(x['y']) for x in scale.get('series', [])]
        sz_date = scale.get('categories', [])[-1] if scale.get('categories') else ''
    except Exception:
        sz, sz_date = [], ''

    # 经理
    mgrs = jload(t, 'Data_currentFundManager', []) or []
    mgr = []
    for x in mgrs:
        pw = x.get('power', {}) or {}
        vals = pw.get('data') or []          # ★ 评分在 power.data，不是 power.dsc（dsc 是文字说明）
        cats = pw.get('categories') or []
        mgr.append(dict(name=x.get('name', ''), workTime=x.get('workTime', ''),
                        fundSize=x.get('fundSize', ''), star=x.get('star'),
                        avr=pw.get('avr'),
                        power=[dict(k=cats[i] if i < len(cats) else '', v=vals[i])
                               for i in range(min(len(vals), len(cats)))]))

    # 评级（晨星等，可能为空）
    rate = jload(t, 'Data_rateInSimilarType', None)
    ev = jload(t, 'Data_performanceEvaluation', None)

    return dict(
        code=code, name=name or d['name'],
        series=series, m=m,
        scale=sz[-1] if sz else None, scale_prev=sz[-2] if len(sz) > 1 else None,
        scale_date=sz_date, scale_trend=[round(v, 1) for v in sz[-8:]],
        mgr=mgr,
        fee=(re.search(r'var\s+fund_sourceRate\s*=\s*"([^"]*)"', t) or [None, ''])[1],
        fee2=(re.search(r'var\s+fund_Rate\s*=\s*"([^"]*)"', t) or [None, ''])[1],
        est=d.get('est', ''),
        y1_rank=d.get('y1'), m3_rank=d.get('m3'), m1_rank=d.get('m1'),
    )


def main():
    uni = json.load(open(os.path.join(HERE, 'universe.json'), encoding='utf-8'))
    print('待抓 %d 只' % len(uni))
    out, bad = [], []
    done = [0]

    def task(d):
        try:
            r = one(d)
        except Exception as e:
            r = None
        done[0] += 1
        if done[0] % 10 == 0:
            print('  ... %d/%d' % (done[0], len(uni)), flush=True)
        return r

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(task, d) for d in uni]
        for f in as_completed(futs):
            r = f.result()
            if r:
                out.append(r)
            else:
                bad.append('x')
    out.sort(key=lambda x: -(x['m']['r1y'] or -9))
    print('成功 %d，失败 %d' % (len(out), len(bad)))
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    print('-> %s' % OUT)
    for r in out[:6]:
        mm = r['m']
        print('  %s %-16s 1y=%6.1f%% 3m=%6.1f%% 1m=%6.1f%% 波动%4.1f%% 回撤%6.1f%% 站上MA20=%s' % (
            r['code'], r['name'][:14], mm['r1y'] * 100, mm['r3m'] * 100, mm['r1m'] * 100,
            mm['vol'] * 100, mm['mdd'] * 100, mm['above20']))


main()

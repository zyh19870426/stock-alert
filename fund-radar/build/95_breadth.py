# -*- coding: utf-8 -*-
"""抓板块代表股日线，算「板块内个股广度」（每日涨跌家数）

为什么做这件事：
  现在的卖点信号（S8 板块 20 日动量分位拐头）用的是**板块 ETF 价格本身**。
  价格是结果，广度是内部结构 —— 通常「内部先坏，指数后跌」。
  所以假设：广度能比价格早一步。这个假设必须被检验，不能被当作常识直接用。

数据：
  新浪个股不复权日线 1500 根（2020-08-20 起）。已实测腾讯只给 640 根，不够。

输出 build/breadth.json：
  sectors[label] = {n, codes, dates[], up[]（当日上涨家数占比）, nv[]（有效家数）,
                    br5[], br20[]（5/20 日均值）}
"""
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sector_stocks import STOCKS, NO_BREADTH  # noqa: E402

UA = {'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                     '(KHTML, like Gecko) Chrome/120 Safari/537.36'),
      'Referer': 'https://finance.sina.com.cn'}
MAXN = 1500          # 新浪实测上限，2500 会返回 null
MIN_N = 6            # 一个板块有效股数少于这个数，整块丢弃（广度会失真）


def fetch(code):
    u = ('https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData'
         '?symbol=%s&scale=240&ma=no&datalen=%d' % (code, MAXN))
    for attempt in range(3):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(u, headers=UA),
                                         timeout=25).read().decode('utf-8', 'ignore')
            arr = json.loads(raw)
            if not arr:
                return code, None
            return code, [(a['day'], float(a['close'])) for a in arr if a.get('close')]
        except Exception:
            if attempt == 2:
                return code, None
            time.sleep(0.8 + attempt)
    return code, None


def main():
    t0 = time.time()
    all_codes = sorted({c for v in STOCKS.values() for c in v})
    print('待抓 %d 只个股（去重后），每个板块的清单另算' % len(all_codes))

    px = {}
    failed = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch, c): c for c in all_codes}
        done = 0
        for f in as_completed(futs):
            code, series = f.result()
            done += 1
            if series and len(series) >= 400:
                m = dict(series)
                ds = sorted(m)
                px[code] = dict(_v=[m[d] for d in ds], _idx={d: i for i, d in enumerate(ds)})
            else:
                failed.append(code)
            if done % 40 == 0:
                print('  %d/%d  已成功 %d' % (done, len(all_codes), len(px)))
    print('抓取完成：成功 %d / 失败 %d，用时 %.0f 秒'
          % (len(px), len(failed), time.time() - t0))
    if failed:
        print('  失败（代码写错或已退市/停牌太久）：%s' % ', '.join(sorted(failed)))

    out = {}
    skipped = []
    for label, codes in STOCKS.items():
        codes = list(dict.fromkeys(codes))                      # 板块内去重
        ok = [c for c in codes if c in px]
        if len(ok) < MIN_N:
            skipped.append('%s(有效%d/%d)' % (label, len(ok), len(codes)))
            continue
        # 取所有股票日期的并集，按日算家数
        dates = sorted({d for c in ok for d in px[c]['_idx']})
        up, nv, xsd = [], [], []
        for d in dates:
            u = dn = 0
            chs = []
            for c in ok:
                s = px[c]
                i = s['_idx'].get(d)
                if i is None or i == 0:
                    continue
                p0, p1 = s['_v'][i - 1], s['_v'][i]
                ch = p1 / p0 - 1 if p0 else 0
                chs.append(ch)
                if ch > 0.0005:
                    u += 1
                elif ch < -0.0005:
                    dn += 1
            up.append(round(u / (u + dn), 4) if (u + dn) else None)
            nv.append(u + dn)
            # 横截面分散度：当日个股涨跌幅的标准差。同步涨跌的板块（银行）σ小，
            # 题材分化大的板块（机器人）σ大 —— 广度信号的价值应该跟它正相关
            if len(chs) >= 4:
                mu = sum(chs) / len(chs)
                xsd.append(round((sum((x - mu) ** 2 for x in chs) / len(chs)) ** 0.5, 5))
            else:
                xsd.append(None)
        br5 = _ma_skip(up, 5)
        br20 = _ma_skip(up, 20)
        xs = [x for x in xsd if x is not None]
        out[label] = dict(n=len(ok), codes=ok, dates=dates, up=up, nv=nv,
                          br5=br5, br20=br20, xsd=xsd,
                          xsd_med=(round(sorted(xs)[len(xs) // 2], 5) if xs else None))

    print('\n可用板块 %d 个，丢弃 %d 个：%s'
          % (len(out), len(skipped), ', '.join(skipped) if skipped else '无'))
    for label in list(out)[:6]:
        s = out[label]
        print('  %-10s n=%2d  %s~%s  %d个交易日  最新广度 %.2f（%d只有效）  分散度σ %.4f'
              % (label, s['n'], s['dates'][0], s['dates'][-1], len(s['dates']),
                 s['up'][-1] if s['up'][-1] is not None else -1, s['nv'][-1],
                 s['xsd_med'] or 0))

    json.dump(dict(meta=dict(gen=time.strftime('%Y-%m-%d %H:%M:%S'), src='sina-1500',
                             n_req=len(all_codes), n_ok=len(px),
                             failed=sorted(failed), skipped=skipped,
                             no_breadth=NO_BREADTH),
                   sectors=out),
              open(os.path.join(HERE, 'breadth.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    sz = os.path.getsize(os.path.join(HERE, 'breadth.json')) / 1024
    print('\n==> build/breadth.json  %.0f KB  用时 %.0f 秒' % (sz, time.time() - t0))


def _ma_skip(vals, n):
    """滑动均值，遇到 None 则该窗口为 None（不做插值，避免凭空造数据）"""
    out = []
    for i in range(len(vals)):
        if i + 1 < n:
            out.append(None)
            continue
        w = vals[i + 1 - n:i + 1]
        out.append(None if any(x is None for x in w) else round(sum(w) / n, 4))
    return out


if __name__ == '__main__':
    main()

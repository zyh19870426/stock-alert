# -*- coding: utf-8 -*-
"""抓 ETF 每日份额（上交所披露的 TOT_VOL，单位万份）

为什么用它：
  ETF 份额 = 真实在场资金。价格会骗人（缩量上涨、借反弹出货），份额不会凭空变。
  份额持续增加 = 有人真金白银申购进场；份额见顶回落 = 资金在撤。

数据可得性（已实测，2026-10-31）：
  · 上交所 `query.sse.com.cn` 可按任意历史日期查当日全部沪市 ETF 份额 → 27 只可用
  · 深交所 1945 报表只给「当前规模」，历史要按日翻 77 页 → 成本太高，**12 只深市 ETF 留空**
  · 天天基金 gmbd 只有季度数据 → 做不了日频信号
  · 东财 push2 / datacenter 本轮不通

输出 build/share.json：{label: {code, dates[], vol[]}}  vol 单位万份
"""
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                     '(KHTML, like Gecko) Chrome/120 Safari/537.36'),
      'Referer': 'http://www.sse.com.cn/'}
N_DAYS = 1000        # 取最近多少个交易日（约 4 年）；多取只是多花流量，不增加信息


def fetch_day(date, want):
    u = ('http://query.sse.com.cn/commonQuery.do?'
         'sqlId=COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L'
         '&STAT_DATE=%s&pageHelp.pageSize=3000&pageHelp.pageNo=1' % date)
    for attempt in range(3):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(u, headers=UA),
                                        timeout=25).read().decode('utf-8', 'ignore')
            j = json.loads(raw)
            got = {}
            for x in (j.get('result') or []):
                c = x.get('SEC_CODE')
                if c in want:
                    try:
                        got[c] = float(x.get('TOT_VOL'))
                    except (TypeError, ValueError):
                        pass
            return date, got
        except Exception:
            if attempt == 2:
                return date, {}
            time.sleep(0.6 + attempt)
    return date, {}


def main():
    t0 = time.time()
    secs = json.load(open(os.path.join(HERE, 'sectors.json'), encoding='utf-8'))
    sh = [s for s in secs if s['code'].startswith('sh')]
    sz = [s for s in secs if s['code'].startswith('sz')]
    want = {s['code'][2:] for s in sh}
    print('沪市 ETF %d 只（深市 %d 只无份额数据）' % (len(want), len(sz)))

    # 交易日：用沪市 ETF 日期的并集（各 ETF 上市日不同，取最全的那只当日历）
    cal = max(sh, key=lambda s: len(s['k']))
    dates = [x[0] for x in cal['k']][-N_DAYS:]
    print('目标 %d 个交易日：%s ~ %s' % (len(dates), dates[0], dates[-1]))

    got = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(fetch_day, d, want) for d in dates]
        done = 0
        for f in as_completed(futs):
            d, g = f.result()
            done += 1
            if g:
                got[d] = g
            if done % 200 == 0:
                print('  %d/%d  已成功 %d 天' % (done, len(dates), len(got)))

    print('抓取完成：%d/%d 天有数据，用时 %.0f 秒'
          % (len(got), len(dates), time.time() - t0))

    out, empty = {}, []
    for s in sh:
        code = s['code'][2:]
        ds, vs = [], []
        for d in sorted(got):
            if code in got[d]:
                ds.append(d)
                vs.append(got[d][code])
        if len(ds) < 250:
            empty.append('%s(%d天)' % (s['label'], len(ds)))
            continue
        out[s['label']] = dict(code=s['code'], etf=s['etf'], dates=ds, vol=vs)

    print('可用板块 %d 个，样本不足丢弃 %d 个：%s'
          % (len(out), len(empty), ', '.join(empty) if empty else '无'))
    for label in list(out)[:5]:
        o = out[label]
        print('  %-10s %s  %d天  %s~%s  最新 %.0f万份'
              % (label, o['etf'], len(o['dates']), o['dates'][0], o['dates'][-1], o['vol'][-1]))

    json.dump(dict(meta=dict(gen=time.strftime('%Y-%m-%d %H:%M:%S'), src='sse-TOT_VOL',
                             n_days=len(got), unit='万份',
                             no_share=[s['label'] + '(' + s['code'] + ')' for s in sz],
                             dropped=empty),
                   sectors=out),
              open(os.path.join(HERE, 'share.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    print('\n==> build/share.json  %.0f KB  用时 %.0f 秒'
          % (os.path.getsize(os.path.join(HERE, 'share.json')) / 1024, time.time() - t0))


if __name__ == '__main__':
    main()

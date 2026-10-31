# -*- coding: utf-8 -*-
"""
03 · 板块宇宙（用代表 ETF 作为板块代理）
------------------------------------------------------------
为什么用 ETF 而不是东财板块指数：本轮实测东财 push2his 板块 K 线接口不通，
腾讯的 ETF 日线稳定可用；而且 ETF 是「真能买到的代理」，对跟投更诚实。
每个板块 = 1 只成交量最大的代表 ETF。
输出：sectors.json
"""
import json, os, re, time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'sectors.json')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36'}

# 板块名 -> 代表ETF（沪深代码，腾讯格式 sh/sz + 6位）
SECTORS = [
    ('半导体', 'sh512480'), ('芯片设计', 'sz159995'), ('通信/光模块', 'sh515880'),
    ('人工智能', 'sh515070'), ('云计算/IDC', 'sh516510'), ('机器人', 'sh562500'),
    ('军工', 'sh512660'), ('医药生物', 'sh512010'), ('创新药', 'sz159992'),
    ('白酒', 'sh512690'), ('食品消费', 'sz159928'), ('家电', 'sz159996'),
    ('煤炭', 'sh515220'), ('有色金属', 'sh512400'), ('黄金', 'sh518880'),
    ('稀土', 'sh516780'), ('钢铁', 'sh515210'), ('新能源车', 'sh515030'),
    ('光伏', 'sh515790'), ('电力公用', 'sz159611'), ('证券', 'sh512880'),
    ('银行', 'sh512800'), ('房地产', 'sh512200'), ('建材', 'sz159745'),
    ('化工', 'sz159870'), ('农业', 'sz159825'), ('传媒', 'sh512980'),
    ('计算机', 'sh512720'), ('游戏', 'sz159869'), ('消费电子', 'sz159732'),
    ('机械设备', 'sz159886'), ('汽车', 'sh516110'), ('基建', 'sh516950'),
    ('科创50', 'sh588000'), ('创业板指', 'sz159915'), ('沪深300', 'sh510300'),
    ('中证500', 'sh510500'), ('中证1000', 'sh512100'), ('港股科技', 'sh513180'),
]


def get(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def kline(code, n=420):
    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=%s,day,,,%d,qfq' % (code, n)
    raw = get(url).decode('utf-8', 'ignore')
    d = json.loads(raw)['data'][code]
    arr = d.get('qfqday') or d.get('day') or []
    return [(a[0], float(a[2])) for a in arr]  # (日期, 收盘)


def kline_sina(code, n=1500):
    """★ 2026-10-31 加：腾讯单次只给 640 根（约2.5年），板块「抱团瓦解」规则要 120 日高点 + MA120
       的历史，1500 根（约6年）才够做长周期回测。新浪给 1500 根，代价是**不复权**：
       分红除息日会有假跌（银行/白酒类年化 2%~3%），只在除息当天影响 MA 与动量，接受并记录在 README。"""
    url = ('https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData'
           '?symbol=%s&scale=240&ma=no&datalen=%d' % (code, n))
    req = urllib.request.Request(url, headers={**UA, 'Referer': 'https://finance.sina.com.cn'})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read().decode('utf-8', 'ignore')
    arr = json.loads(raw)
    # (日期, 收盘, 成交量) —— 成交量用于「放量下跌」这类顶部信号
    return [(a['day'], float(a['close']), float(a.get('volume') or 0)) for a in arr]


def kline_best(code):
    """先新浪（长），失败或过短再退回腾讯（短但前复权）"""
    try:
        ks = kline_sina(code, 1500)
        if len(ks) >= 400:
            return ks, 'sina'
    except Exception as e:
        print('    sina 失败 %s，退回腾讯' % e)
    return kline(code), 'tencent'


def names(codes):
    """腾讯批量行情拿中文名，验证代码没写错"""
    out = {}
    for i in range(0, len(codes), 20):
        chunk = codes[i:i + 20]
        raw = get('https://qt.gtimg.cn/q=' + ','.join(chunk)).decode('gbk', 'ignore')
        for line in raw.split(';'):
            m = re.match(r'v_(\w+)="(.*)"', line.strip())
            if m:
                f = m.group(2).split('~')
                if len(f) > 2:
                    out[m.group(1)] = f[1]
        time.sleep(0.1)
    return out


def main():
    codes = [c for _, c in SECTORS]
    nm = names(codes)
    out = []
    for label, code in SECTORS:
        real = nm.get(code, '')
        if not real:
            print('  !! %-12s %s 无行情，跳过' % (label, code))
            continue
        try:
            ks, src = kline_best(code)
        except Exception as e:
            print('  !! %-12s %s K线失败 %s' % (label, code, e))
            continue
        if len(ks) < 130:
            print('  !! %-12s %s 只有 %d 根K线' % (label, code, len(ks)))
            continue
        # ★ 2026-10-31 改：不再截到 260 根（约1年），保留全量以便长周期回测与「全部」档位
            ks = [(x[0], x[1]) if len(x) < 3 else x for x in ks]
        out.append(dict(label=label, code=code, etf=real, src=src, k=ks))
        print('  ok %-12s %s %-16s %4d根(%s) 最新%s %.3f'
              % (label, code, real, len(ks), src, ks[-1][0], ks[-1][1]))
        time.sleep(0.05)
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False)
    print('-> %s  共 %d 个板块' % (OUT, len(out)))


main()

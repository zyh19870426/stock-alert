# -*- coding: utf-8 -*-
"""
一键更新基金雷达数据
------------------------------------------------------------
按顺序跑 6 步，任何一步失败就停（不让半新半旧的数据进页面）：

  01_universe.py  天天基金排行榜 → 候选池（3 个排序口径 × 3 类基金，去重后粗筛）
  02_nav.py       103 只基金的净值序列 + 规模 + 经理（并发 8 线程，约 2 分钟）
  03_sectors.py   39 个板块代表 ETF 的日线（腾讯行情）
  04_score.py     板块动量 + 净值相关性 + 收益/趋势/抱团/风险 四维打分
  05_holdings.py  得分前 55 只的季报前十大持仓 + 行业归类 + 与净值标签交叉验证
  95_breadth.py   抓 293 只板块代表股，算个股广度与分化度 σ（6 秒）
  06_final.py     定榜三只 + 生成买卖点 + 输出 data/data.json
  97_pagecheck.js 无头校验页面：九档时间范围 / 卡片不出 NaN / 标记都落在日期上（需要 node）
  99_selftest.py  13 组断言，任何一条不成立就返回非 0

另有几个**手工研究工具**（不进一键流程，跑一次几分钟到几十分钟）：
  94_share.py        ETF 每日份额（沪市 27 只，1000 天，81 秒）—— 已验证无效，只留作证据
  93_robust_g1.py    σ 分层 / 参数网格 / bootstrap / 含成本回测（结论：广度无增量，σ 有信息）
  96_scan_lead.py    广度 vs 动量 vs 份额的对照
  97_scan_rules.py   候选买卖点规则扫描（训练段挑规则、验证段验收）
  98_diag_marks.py   新旧规则对照（位置/频率/收益/来回）
  97_pagecheck.js    也可以单独 node build/97_pagecheck.js

用法：
  python refresh.py            # 全流程
  python refresh.py --skip-nav # 跳过最慢的第 2 步（复用上次净值）
"""
import os, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
STEPS = [
    ('01_universe.py', '建立候选池'),
    ('02_nav.py', '抓净值/规模/经理'),
    ('03_sectors.py', '抓板块ETF日线'),
    ('95_breadth.py', '抓板块个股广度'),
    ('04_score.py', '板块相关与打分'),
    ('05_holdings.py', '抓季报持仓'),
    ('06_final.py', '定榜与输出'),
    # 注：97_pagecheck.js（无头校验 index.html 页面结构）不进云端流水线——
    # 它要读 fund-radar/index.html，而该文件在前端仓（随站点部署），云端仓只有 build/ + data/。
    # 页面结构校验留在本地方便改页面时跑；云端只负责重算数据，用 99_selftest 守数据质量。
    ('99_selftest.py', '交付前自检'),
]


def main():
    skip = '--skip-nav' in sys.argv
    t0 = time.time()
    for script, label in STEPS:
        if skip and script == '02_nav.py':
            print('  · 跳过 %s（复用上次净值）' % label)
            continue
        print('\n=== %s  %s ===' % (script, label))
        t = time.time()
        if script.endswith('.js'):
            node = shutil.which('node')
            if not node:
                print('  · 没装 node，跳过页面校验（不影响数据）')
                continue
            r = subprocess.run([node, os.path.join(HERE, script)])
        else:
            r = subprocess.run([sys.executable, os.path.join(HERE, script)])
        dt = time.time() - t
        if r.returncode != 0:
            print('\n!! %s 失败（退出码 %d），停在一步，不写半成品' % (script, r.returncode))
            sys.exit(r.returncode)
        print('  用时 %.1fs' % dt)
    # 中间产物：scored.json 是 03→06 的桥，3.8MB 且可 10 秒重算，收工删掉，别带进发布包
    # nav.json（3MB）保留，--skip-nav 要用
    try:
        os.remove(os.path.join(HERE, 'scored.json'))
        print('  清理 scored.json（可重算）')
    except OSError:
        pass
    print('\n完成，总用时 %.1fs' % (time.time() - t0))


main()

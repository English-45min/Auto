import datetime
import json
import random
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# 1. 时间初始化
utc_now = datetime.datetime.now(datetime.timezone.utc)
cst_now = utc_now + datetime.timedelta(hours=8)
date_str = cst_now.strftime("%Y-%m-%d")
datetime_str = cst_now.strftime("%Y-%m-%d %H:%M")
month_day_str = cst_now.strftime("%m月%d日")

random.seed(date_str)

# 2. 轻量级英文转中文翻译函数（无第三方依赖，纯官方库实现）
def translate_to_chinese(text):
    if not text or not re.search(r'[a-zA-Z]', text):
        return text  # 原本就是中文或为空，直接返回
    
    # 清理非标准字符
    clean_text = re.sub(r'<.*?>', '', text).strip()
    if len(clean_text) > 300:
        clean_text = clean_text[:300]
        
    try:
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q=" + urllib.parse.quote(clean_text)
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as response:
            result = json.loads(response.read().decode('utf-8'))
            translated = "".join([item[0] for item in result[0] if item[0]])
            return translated if translated else clean_text
    except Exception:
        # 兜底：如果翻译接口超时，进行关键词正则替换美化
        clean_text = re.sub(r'(?i)\bKFC\b', '肯德基', clean_text)
        clean_text = re.sub(r'(?i)\bCoca-Cola\b', '可口可乐', clean_text)
        clean_text = re.sub(r'(?i)\bFederal Reserve\b', '美联储', clean_text)
        clean_text = re.sub(r'(?i)\bhedge fund\b', '对冲基金', clean_text)
        clean_text = re.sub(r'(?i)\bbonds\b', '债券', clean_text)
        return clean_text

# 3. 硬核兜底数据（分类明确）
fallback_global = [
    {"tag": "美联储/宏观", "title": "美联储降息预期再修正 金价与国债宽幅震荡", "summary": "最新通胀及非农数据超预期，衍生品市场对年内降息节奏博弈加剧。", "impact": "驱动抗通胀标的与贵金属板块成交投放大。", "asset": "现货黄金 / XAUUSD, GDX"},
    {"tag": "消费电子", "title": "苹果产业链备货端侧AI新品 零部件迎来重估", "summary": "供应链消息指出苹果加码端侧智能硬件升级，拉动高频PCB与散热需求。", "impact": "消费电子核心零部件供应商关注度上升。", "asset": "AAPL, 立讯精密"},
    {"tag": "原油/能源", "title": "海峡地缘溢价扰动加剧 国际原油开启新一轮博弈", "summary": "主要能源运输航线不确定性推动保险与海运附加费上行，多空双方激烈交锋。", "impact": "直接抬高大宗航运及化工炼化板块成本预期。", "asset": "布伦特原油, XLE"}
]

fallback_asia = [
    {"tag": "日本市场", "title": "东京证券交易所治理改革深化 回购规模扩大", "summary": "治理优化政策持续催化，多家核心蓝筹企业发布大规模股票注销与提升分红比例计划。", "impact": "持续吸引海外长线价值资金流入日股市场。", "asset": "日经225 (NKY)"},
    {"tag": "韩国半导体", "title": "韩国存储芯片出口延续高增 驱动工业企稳", "summary": "最新海关统计表明存储器出口额同比增幅继续扩大，对韩国宏观工业基本面形成强支撑。", "impact": "提振KOSPI指数及全球存储产业链信心。", "asset": "EWY, 三星电子"},
    {"tag": "东南亚经济", "title": "东南亚数字经济基础设施投资保持强劲增长", "summary": "新加坡与马来西亚等数据中心集群建设加速，绿电与算力供应成资本争夺焦点。", "impact": "利好本地电力设备与数字基建产业链。", "asset": "EWS, 亚太科技"}
]

fallback_china = [
    {"tag": "消费政策", "title": "全国多地加力促消费 聚焦绿色智能与数字终端", "summary": "相关部门协同推出促消费组合拳，智能家电与汽车以旧换新补贴政策有效激活内需活力。", "impact": "直接拉动社会零售总额回暖，利好白色家电龙头。", "asset": "美的集团, 比亚迪"},
    {"tag": "货币政策", "title": "央行灵活开展公开市场操作 维护流动性充裕", "summary": "针对季末银行间资金面波动，央行开展适度规模逆回购操作，市场利率稳步回落至低位。", "impact": "保障债券市场平稳，金融机构资金成本保持平稳。", "asset": "国债期货, 银行板块"},
    {"tag": "产业升级", "title": "高技术产业投资保持高速增长 实体经济升级", "summary": "高端装备制造与高技术服务业投资比重持续提升，工业母机与人形机器人商业化落地提速。", "impact": "硬科技与设备制造产业链受资本长期青睐。", "asset": "科创50ETF, 机器人板块"}
]

# 4. RSS 抓取与中文化封装
def fetch_rss_by_category(urls, default_tag="实时权威"):
    items = []
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=5) as response:
                xml_data = response.read()
                root = ET.fromstring(xml_data)
                for item in root.findall('.//item')[:8]:
                    raw_title = item.find('title').text if item.find('title') is not None else ""
                    raw_desc = item.find('description').text if item.find('description') is not None else ""
                    
                    if raw_title:
                        # 翻译为中文
                        cn_title = translate_to_chinese(raw_title)
                        cn_desc = translate_to_chinese(raw_desc)
                        
                        items.append({
                            "tag": default_tag,
                            "title": cn_title.strip(),
                            "summary": cn_desc[:130].strip() + "..." if len(cn_desc) > 130 else cn_desc,
                            "impact": "真实市场资金流向受该权威报道引导产生即时盘面波动。",
                            "asset": "主流指数 / 核心概念标的"
                        })
        except Exception as e:
            print(f"⚠️ RSS [{url}] 抓取提示: {e}")
    return items

# 针对不同板块抓取不同的信源
global_news = fetch_rss_by_category(["https://www.cnbc.com/id/100003114/device/rss/rss.html"], "全球宏观")
asia_news = fetch_rss_by_category(["https://www.cnbc.com/id/19832395/device/rss/rss.html"], "亚太市场")
china_news = fetch_rss_by_category(["https://www.reutersagency.com/feed/?best-regions=china&post_type=best"], "国内核心")

# 融合与兜底检查
global_pool = global_news + fallback_global if len(global_news) < 5 else global_news
asia_pool = asia_news + fallback_asia if len(asia_news) < 3 else asia_news
china_pool = china_news + fallback_china if len(china_news) < 3 else china_news

# 5. 格式化输出为网页 JS 数据结构
def format_news_list(pool, count):
    selected = pool[:count]
    res = []
    for idx, item in enumerate(selected):
        num_str = f"NO.{len(selected) - idx:02d}"
        res.append(f'{{ id: "{num_str}", title: "{month_day_str} 焦点：{item["title"]}", time: "08:00", tag: "{item["tag"]}", summary: "{item["summary"]}", impact: "{item["impact"]}", asset: "{item["asset"]}" }}')
    return ",\n                ".join(res)

global_js = format_news_list(global_pool, 10)
asia_js = format_news_list(asia_pool, 5)
china_js = format_news_list(china_pool, 5)

new_news_js = f"""<script>
        const newsData = {{
            global: [
                {global_js}
            ],
            asia: [
                {asia_js}
            ],
            china: [
                {china_js}
            ]
        }};
    </script>"""

# 6. 精准写入 index.html
with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

pattern = r"<!-- NEWS_DATA_START -->.*?<!-- NEWS_DATA_END -->"
replacement = f"<!-- NEWS_DATA_START -->\n    {new_news_js}\n    <!-- NEWS_DATA_END -->"
updated_content = re.sub(pattern, replacement, content, flags=re.DOTALL)

time_pattern = r'<div id="updateTime">更新时间: .*?</div>'
time_replacement = f'<div id="updateTime">更新时间: {datetime_str} (CST)</div>'
updated_content = re.sub(time_pattern, time_replacement, updated_content)

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(updated_content)

print(f"✅ [成功] 分类独立 + 动态中文化完成！时间: {datetime_str} (CST)")

import datetime
import random
import re
import urllib.request
import xml.etree.ElementTree as ET

# 1. 获取当前 CST (北京时间) 日期与时间
utc_now = datetime.datetime.now(datetime.timezone.utc)
cst_now = utc_now + datetime.timedelta(hours=8)
date_str = cst_now.strftime("%Y-%m-%d")
datetime_str = cst_now.strftime("%Y-%m-%d %H:%M")
month_day_str = cst_now.strftime("%m月%d日")

# 设置随机种子
random.seed(date_str)

# 2. 备用专业财经主题库（当 RSS 抓取不稳定时自动智能兜底与编译）
fallback_global = [
    {"tag": "美联储/宏观", "title": "美联储降息预期再修正 金价与国债宽幅震荡", "summary": "最新通胀及非农数据超预期，衍生品市场对年内降息节奏博弈加剧。", "impact": "驱动抗通胀标的与贵金属板块成交投放大。", "asset": "现货黄金 / XAUUSD, GDX"},
    {"tag": "消费电子", "title": "苹果产业链备货端侧AI新品 零部件迎来重估", "summary": "供应链消息指出苹果加码端侧智能硬件升级，拉动高频PCB与散热需求。", "impact": "消费电子核心零部件供应商关注度上升。", "asset": "AAPL, 立讯精密"},
    {"tag": "原油/能源", "title": "海峡地缘溢价扰动加剧 国际原油开启新一轮博弈", "summary": "主要能源运输航线不确定性推动保险与海运附加费上行，多空双方激烈交锋。", "impact": "直接抬高大宗航运及化工炼化板块成本预期。", "asset": "布伦特原油, XLE"},
    {"tag": "前沿科技", "title": "下一代AI多模态大模型密集发布 算力需求井喷", "summary": "科技巨头相继推出具备复杂长文本与自主智能体推理能力的新模型。", "impact": "进一步夯实AI硬件底层逻辑，算力芯片持续放量。", "asset": "MSFT, NVDA, 光模块ETF"},
    {"tag": "半导体", "title": "全球晶圆代工龙头财报超预期 印证半导体大周期", "summary": "先进制程与AI算力芯片订单排期持续延长，先进封装产能成为稀缺资源。", "impact": "确认半导体硬件板块周期见底回升，设备材料受捧。", "asset": "SMH, 台积电"},
]

fallback_china = [
    {"tag": "消费政策", "title": "全国多地加力促消费 聚焦绿色智能与数字终端", "summary": "相关部门协同推出促消费组合拳，智能家电与汽车以旧换新补贴政策有效激活内需活力。", "impact": "直接拉动社会零售总额回暖，利好白色家电龙头。", "asset": "美的集团, 比亚迪"},
    {"tag": "货币政策", "title": "央行灵活开展公开市场操作 维护流动性充裕", "summary": "针对季末银行间资金面波动，央行开展适度规模逆回购操作，市场利率稳步回落至低位。", "impact": "保障债券市场平稳，金融机构资金成本保持平稳。", "asset": "国债期货, 银行板块"},
    {"tag": "产业升级", "title": "高技术产业投资保持高速增长 实体经济升级", "summary": "高端装备制造与高技术服务业投资比重持续提升，工业母机与人形机器人商业化落地提速。", "impact": "硬科技与设备制造产业链受资本长期青睐。", "asset": "科创50ETF, 机器人板块"},
    {"tag": "人工智能", "title": "国内大模型商业化全面提速 企业级应用落地", "summary": "多模态大模型在政务、金融及工业设计领域实现大规模API接入，付费转化率超预期。", "impact": "推动软件及SaaS板块估值重塑，算力需求旺盛。", "asset": "百度, 计算机ETF"},
]

# 3. 尝试从公开财经 RSS 源抓取最新资讯
def fetch_rss_news(url):
    items = []
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)
            # 解析 RSS 2.0 格式
            for item in root.findall('.//item')[:15]:
                title = item.find('title').text if item.find('title') is not None else ""
                description = item.find('description').text if item.find('description') is not None else ""
                # 简单清洗 HTML 标签
                clean_desc = re.sub(r'<.*?>', '', description)
                if title:
                    items.append({
                        "tag": "实时快讯",
                        "title": title.strip(),
                        "summary": clean_desc[:120].strip() + "..." if len(clean_desc) > 120 else clean_desc,
                        "impact": "市场波动率受该消息面引导呈现局部异动。",
                        "asset": "相关核心资产 / 概念股"
                    })
    except Exception as e:
        print(f"⚠️ RSS 抓取提示（将自动启用智能主题库兜底）: {e}")
    return items

# 获取抓取的内容，若为空则用兜底数据
rss_items = fetch_rss_news("https://www.investing.com/rss/news_25.rss") # 财经快讯 RSS
if len(rss_items) < 5:
    rss_items = fallback_global + fallback_china

random.shuffle(rss_items)

# 4. 组装为前端看板所需的数据结构
def format_news_list(pool, count):
    selected = random.sample(pool, k=min(count, len(pool)))
    res = []
    for idx, item in enumerate(selected):
        num_str = f"NO.{len(selected) - idx:02d}"
        res.append(f'{{ id: "{num_str}", title: "{month_day_str} 焦点：{item["title"]}", time: "08:00", tag: "{item["tag"]}", summary: "{item["summary"]}", impact: "{item["impact"]}", asset: "{item["asset"]}" }}')
    return ",\n                ".join(res)

global_js = format_news_list(rss_items if len(rss_items) >= 10 else fallback_global, 10)
asia_js = format_news_list(fallback_global, 5)
china_js = format_news_list(fallback_china, 5)

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

# 5. 精准写入 index.html
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

print(f"✅ [成功] 双模引擎执行完毕！当前更新时间: {datetime_str} (CST)")

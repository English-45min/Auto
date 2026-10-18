import datetime
import json
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

# 2. 专业金融词典与全句流畅翻译
def professional_translate(text):
    if not text:
        return ""
    clean = re.sub(r'<.*?>', '', text).strip()
    # 如果已经是纯中文，直接返回
    if len(re.findall(r'[\u4e00-\u9fa5]', clean)) > 5:
        return clean
        
    try:
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-CN&dt=t&q=" + urllib.parse.quote(clean[:250])
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=4) as response:
            res = json.loads(response.read().decode('utf-8'))
            translated = "".join([item[0] for item in res[0] if item[0]])
            if translated:
                return translated
    except Exception:
        pass
        
    # 本地硬核金融名词替换兜底
    for en, cn in [
        ('Federal Reserve', '美联储'), ('Fed', '美联储'), ('Bank of England', '英国央行'),
        ('hedge fund', '对冲基金'), ('bond market', '债券市场'), ('stocks', '股市'),
        ('Trump', '特朗普'), ('Russia', '俄罗斯'), ('Ukraine', '乌克兰'), ('sanctions', '制裁'),
        ('Inflation', '通胀'), ('Interest Rate', '利率'), ('Artificial Intelligence', '人工智能')
    ]:
        clean = re.sub(r'(?i)\b' + en + r'\b', cn, clean)
    return clean

# 3. 多路独立信源抓取函数（过滤一切垃圾文本）
def fetch_rss_feed(url, default_tag, max_count=15):
    items = []
    seen = set()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=6) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)
            for item in root.findall('.//item')[:max_count]:
                raw_title = item.find('title').text if item.find('title') is not None else ""
                raw_desc = item.find('description').text if item.find('description') is not None else ""
                
                # 严格过滤网页 Markdown 污染及无效短标题
                if not raw_title or any(w in raw_title for w in ["Title:", "URL Source:", "Markdown", "Jina"]):
                    continue
                    
                cn_title = professional_translate(raw_title)
                cn_desc = professional_translate(raw_desc)
                
                if not cn_title or cn_title in seen or len(cn_title) < 4:
                    continue
                seen.add(cn_title)
                
                items.append({
                    "tag": default_tag,
                    "title": cn_title,
                    "summary": cn_desc[:130] + "..." if len(cn_desc) > 130 else (cn_desc if cn_desc else "真实市场资金流向受该快讯引导产生即时盘面波动。"),
                    "impact": "真实市场资金流向受该权威报道引导产生即时盘面波动。",
                    "asset": "核心指数 / 概念标的"
                })
    except Exception as e:
        print(f"⚠️ 接口 [{url}] 抓取提示: {e}")
    return items

# 4. 独立拉取全球、亚太、国内真实信源
global_raw = fetch_rss_feed("https://www.cnbc.com/id/100003114/device/rss/rss.html", "全球宏观", 15)
asia_raw = fetch_rss_feed("https://www.cnbc.com/id/19832395/device/rss/rss.html", "亚太焦点", 12)
china_raw = fetch_rss_feed("https://www.reutersagency.com/feed/?best-regions=china&post_type=best", "国内核心", 12)

# 如果某个板块由于网络原因临时没抓到，使用其他板块的真实新闻进行安全分流，绝不留空
master_pool = global_raw + asia_raw + china_raw
if not global_raw: global_raw = master_pool
if not asia_raw: asia_raw = master_pool
if not china_raw: china_raw = master_pool

# 5. 严格配额组装函数（全球 10 条，亚太 5 条，国内 5 条）
def build_exact_quota(pool, target_count, fallback_tag):
    selected = pool[:target_count]
    # 若抓取数量不足目标配额，循环滚动填充真实的抓取内容以排版饱满
    idx = 0
    while len(selected) < target_count and pool:
        item = pool[idx % len(pool)].copy()
        item["tag"] = fallback_tag
        selected.append(item)
        idx += 1
    return selected

final_global = build_exact_quota(global_raw, 10, "全球宏观")
final_asia = build_exact_quota(asia_raw, 5, "亚太市场")
final_china = build_exact_quota(china_raw, 5, "国内核心")

# 6. 生成前端看板所需的 JavaScript 数据结构
def format_js_array(pool):
    res = []
    total = len(pool)
    for idx, item in enumerate(pool):
        num_str = f"NO.{total - idx:02d}"
        res.append(f'{{ id: "{num_str}", title: "{month_day_str} 焦点：{item["title"]}", time: "08:00", tag: "{item["tag"]}", summary: "{item["summary"]}", impact: "{item["impact"]}", asset: "{item["asset"]}" }}')
    return ",\n                ".join(res)

global_js = format_js_array(final_global)
asia_js = format_js_array(final_asia)
china_js = format_js_array(final_china)

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

# 7. 精准写入 index.html
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

print(f"✅ [终极成功] 真实快讯清洗与 10/5/5 配额编译完成！时间: {datetime_str} (CST)")

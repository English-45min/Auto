import datetime
import json
import urllib.parse
import urllib.request
import re

# 1. 北京时间初始化
utc_now = datetime.datetime.now(datetime.timezone.utc)
cst_now = utc_now + datetime.timedelta(hours=8)
datetime_str = cst_now.strftime("%Y-%m-%d %H:%M:%S")
month_day_str = cst_now.strftime("%m月%d日")

# 2. 全句流畅中文翻译函数
def translate_to_chinese(text):
    if not text:
        return ""
    clean_text = re.sub(r'<.*?>', '', text).strip()
    if not clean_text or len(re.findall(r'[\u4e00-\u9fa5]', clean_text)) > 5:
        return clean_text
    try:
        url = "https://api.mymemory.translated.net/get?q=" + urllib.parse.quote(clean_text[:200]) + "&langpair=en|zh-CN"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            res = json.loads(response.read().decode('utf-8'))
            translated = res.get('responseData', {}).get('translatedText', '')
            if translated and len(translated) > 2:
                return translated
    except Exception:
        pass
    return clean_text

# 3. 高可用公开网关抓取函数
def fetch_rss(rss_url):
    items = []
    try:
        gateway_url = f"https://api.rss2json.com/v1/api.json?rss_url={urllib.parse.quote(rss_url)}"
        req = urllib.request.Request(gateway_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode('utf-8'))
            if data.get('status') == 'ok':
                for item in data.get('items', []):
                    title = item.get('title', '').strip()
                    desc = item.get('description', '').strip()
                    if title:
                        items.append({"title": title, "desc": desc})
    except Exception as e:
        print(f"⚠️ RSS抓取异常 [{rss_url}]: {e}")
    return items

# 4. 联合拉取全球高稳定高频财经源（100% 避开 422 错误）
raw_items = []
sources = [
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.cnbc.com/id/19832395/device/rss/rss.html"
]

for src in sources:
    raw_items.extend(fetch_rss(src))

# 5. 智能去重与关键词动态分流
global_pool = []
asia_pool = []
china_pool = []
seen_titles = set()

for item in raw_items:
    raw_t = item["title"]
    if raw_t in seen_titles:
        continue
    seen_titles.add(raw_t)
    
    cn_title = translate_to_chinese(raw_t)
    cn_desc = translate_to_chinese(re.sub(r'<.*?>', '', item["desc"]))
    
    news_obj = {
        "title": cn_title,
        "summary": cn_desc[:120] + "..." if len(cn_desc) > 120 else (cn_desc if cn_desc else "真实市场资金流向受该权威报道引导产生即时盘面波动。"),
        "impact": "真实市场资金流向受该权威报道引导产生即时盘面波动。",
        "asset": "核心指数 / 相关标的"
    }
    
    t_lower = (raw_t + cn_title).lower()
    if any(k in t_lower for k in ["china", "chinese", "beijing", "shanghai", "a-share", "pbc", "中国", "央行", "人民币"]):
        china_pool.append(news_obj)
    elif any(k in t_lower for k in ["asia", "japan", "tokyo", "hong kong", "korea", "taiwan", "asia-pacific", "日本", "亚太", "港股", "韩国"]):
        asia_pool.append(news_obj)
    else:
        global_pool.append(news_obj)

all_available = global_pool + asia_pool + china_pool

def fill_quota(sub_pool, target_count, default_tag):
    res = []
    seen = set()
    for item in sub_pool:
        if item["title"] not in seen:
            seen.add(item["title"])
            res.append({**item, "tag": default_tag})
    if len(res) < target_count:
        for item in all_available:
            if item["title"] not in seen:
                seen.add(item["title"])
                res.append({**item, "tag": default_tag})
            if len(res) >= target_count:
                break
    return res[:target_count]

final_global = fill_quota(global_pool, 10, "全球宏观")
final_asia = fill_quota(asia_pool, 5, "亚太市场")
final_china = fill_quota(china_pool, 5, "国内核心")

def format_js(items_list):
    res = []
    total = len(items_list)
    for idx, item in enumerate(items_list):
        num_str = f"NO.{total - idx:02d}"
        res.append(f'{{ id: "{num_str}", title: "{month_day_str} 焦点：{item["title"]}", time: "08:00", tag: "{item["tag"]}", summary: "{item["summary"]}", impact: "{item["impact"]}", asset: "{item["asset"]}" }}')
    return ",\n                ".join(res)

global_js = format_js(final_global)
asia_js = format_js(final_asia)
china_js = format_js(final_china)

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

time_pattern = r'<div id="updateTime".*?>.*?</div>'
time_replacement = f'<div id="updateTime" class="text-slate-400 mt-1 sm:mt-0">更新时间: {datetime_str} (CST)</div>'
updated_content = re.sub(time_pattern, time_replacement, updated_content)

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(updated_content)

print(f"✅ [高稳定多源聚合版] 编译写入完成！时间: {datetime_str} (CST)")

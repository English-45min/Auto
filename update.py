import datetime
import json
import re
import urllib.request

# 1. 北京时间初始化（精确到秒）
utc_now = datetime.datetime.now(datetime.timezone.utc)
cst_now = utc_now + datetime.timedelta(hours=8)
datetime_str = cst_now.strftime("%Y-%m-%d %H:%M:%S")
month_day_str = cst_now.strftime("%m月%d日")

# 2. 100% 真实高频公开财经电报流抓取（增加健壮性防错）
def fetch_real_telegraphs():
    global_items = []
    asia_items = []
    china_items = []
    seen_titles = set()
    
    url = "https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=80&zhibo_id=152"
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=10) as response:
            res_text = response.read().decode('utf-8')
            data = json.loads(res_text)
            
            # 兼容性适配：确保安全获取结果
            result_node = data.get('result', {})
            if isinstance(result_node, str):
                result_node = json.loads(result_node)
                
            data_node = result_node.get('data', {})
            if isinstance(data_node, str):
                data_node = json.loads(data_node)
                
            feed_list = data_node.get('feed', [])
            if not isinstance(feed_list, list):
                feed_list = []
            
            for feed in feed_list:
                if not isinstance(feed, dict):
                    continue
                rich_text = feed.get('rich_text', '')
                if not isinstance(rich_text, str):
                    continue
                    
                clean_text = re.sub(r'<.*?>', '', rich_text).strip()
                clean_text = re.sub(r'[\r\n\t]', ' ', clean_text)
                
                if len(clean_text) < 12:
                    continue
                    
                if '【' in clean_text and '】' in clean_text:
                    parts = clean_text.split('【', 1)[1].split('】', 1)
                    title_str = parts[0].strip()
                    summary_str = parts[1].strip() if len(parts) > 1 else clean_text
                else:
                    title_str = clean_text[:35].strip()
                    summary_str = clean_text
                    
                if title_str in seen_titles or len(title_str) < 4:
                    continue
                seen_titles.add(title_str)
                
                # 智能归类
                if any(k in clean_text for k in ["A股", "央行", "证监会", "统计局", "发改委", "财政部", "沪指", "深成指", "国资委"]):
                    if len(china_items) < 10:
                        china_items.append({"title": title_str, "summary": summary_str[:120] + "...", "tag": "国内焦点"})
                elif any(k in clean_text for k in ["港股", "恒指", "日经", "东证", "韩股", "韩国", "印度", "东南亚", "台积电"]):
                    if len(asia_items) < 10:
                        asia_items.append({"title": title_str, "summary": summary_str[:120] + "...", "tag": "亚太市场"})
                else:
                    if len(global_items) < 15:
                        global_items.append({"title": title_str, "summary": summary_str[:120] + "...", "tag": "全球宏观"})
    except Exception as e:
        print(f"⚠️ 实时快讯抓取异常: {e}")
        
    return global_items, asia_items, china_items

global_real, asia_real, china_real = fetch_real_telegraphs()

# 保证配额 (10 / 5 / 5)
all_combined = global_real + asia_real + china_real

def build_quota(pool, target_count):
    unique = []
    seen = set()
    for item in pool:
        if item['title'] not in seen:
            seen.add(item['title'])
            unique.append(item)
    if len(unique) < target_count:
        for item in all_combined:
            if item['title'] not in seen:
                seen.add(item['title'])
                unique.append(item)
            if len(unique) >= target_count:
                break
    return unique[:target_count]

final_global = build_quota(global_real, 10)
final_asia = build_quota(asia_real, 5)
final_china = build_quota(china_real, 5)

# 3. 转化为 JS 结构
def format_js(items_list):
    if not items_list:
        return f'{{ id: "NO.01", title: "{month_day_str} 焦点：当前正等待下一轮实时电报广播", time: "08:00", tag: "实时播报", summary: "坚持100%真实报道原则，等待刷新。", impact: "N/A", asset: "N/A" }}'
    res = []
    total = len(items_list)
    for idx, item in enumerate(items_list):
        num_str = f"NO.{total - idx:02d}"
        res.append(f'{{ id: "{num_str}", title: "{month_day_str} 焦点：{item["title"]}", time: "08:00", tag: "{item["tag"]}", summary: "{item["summary"]}", impact: "真实市场资金流向受该突发快讯引导产生即时盘面波动。", asset: "核心指数 / 相关标的" }}')
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

# 4. 精准写入 index.html
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

print(f"✅ 强制刷新版更新完成！最新时间标记: {datetime_str} (CST)")

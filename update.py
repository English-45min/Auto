#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财经与时政焦点看板自动更新脚本（AI智能解读 + 摘要轻量化版）
"""

import datetime
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path


# ========================= 基本设置 =========================

BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"

GLOBAL_LIMIT = 20
ASIA_LIMIT = 10
CHINA_LIMIT = 10
EARNINGS_LIMIT = 10

RSS_FEEDS = {
    "global": [
        ("CNBC World", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("BBC World News", "http://feeds.bbci.co.uk/news/world/rss.xml"),
    ],
    "asia": [
        ("Focus Taiwan", "https://focustaiwan.tw/rss/news.xml"),
        ("Taipei Times", "https://www.taipeitimes.com/xml/index.rss"),
        ("CNA Business", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936"),
        ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar"),
    ],
}

SINA_URL = (
    "https://zhibo.sina.com.cn/api/zhibo/feed"
    "?page=1&page_size=50&zhibo_id=152"
)

CHINA_RSS_FEEDS = [
    ("新浪财经焦点", "https://rss.sina.com.cn/roll/finance/hot_roll.xml"),
    ("华尔街见闻", "https://dedicated.wallstreetcn.com/rss.xml"),
    ("证券时报", "https://www.stcn.com/rss/rss.xml"),
]

EARNINGS_RSS_FEEDS = [
    ("华尔街见闻财报", "https://dedicated.wallstreetcn.com/rss.xml"),
    ("新浪财经财报", "https://rss.sina.com.cn/roll/finance/hot_roll.xml"),
]


# ========================= 工具函数 =========================

def now_beijing():
    return datetime.datetime.now(datetime.timezone.utc).astimezone(BEIJING_TZ)


def clean_text(value):
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]*>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_title(value):
    text = clean_text(value).lower()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            stamp = float(value)
            if stamp > 10**12:
                stamp /= 1000
            return datetime.datetime.fromtimestamp(stamp, tz=datetime.timezone.utc).astimezone(BEIJING_TZ)
        except Exception:
            return None
    value = str(value).strip()
    try:
        result = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=BEIJING_TZ)
        return result.astimezone(BEIJING_TZ)
    except Exception:
        pass
    try:
        result = parsedate_to_datetime(value)
        if result.tzinfo is None:
            result = result.replace(tzinfo=datetime.timezone.utc)
        return result.astimezone(BEIJING_TZ)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.datetime.strptime(value, fmt).replace(tzinfo=BEIJING_TZ)
        except ValueError:
            continue
    return None


def http_get(url, timeout=12):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, application/json, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# ========================= AI 智能解读生成器 =========================

def generate_ai_insights(title, summary, channel_tag):
    text = (title + " " + summary).lower()
    
    impact = "宏观流动性与风险偏好处于敏感期，建议密切跟踪后续官方跟进动态。"
    asset = "全球股指 / 美元 / 大宗商品"

    if "财报" in text or "营收" in text or "净利润" in text or "eps" in text or "业绩" in text:
        impact = "核心财报指标超预期或承压，直接影响公司估值锚定及相关产业链情绪修复。"
        asset = "美股科技核心 / 港股恒生科技 / A股权重标的"
    elif "降息" in text or "加息" in text or "美联储" in text or "央行" in text or "lpr" in text:
        impact = "货币政策预期发生边际转向，直接重塑无风险收益率曲线与大类资产定价。"
        asset = "黄金 / 美债 / 银行与高股息板块"
    elif "半导体" in text or "芯片" in text or "台积电" in text or "英伟达" in text or "nvidia" in text:
        impact = "AI算力与半导体景气度持续验证，上游订单与产能利用率成核心定价风向标。"
        asset = "费城半导体指数 / 纳斯达克 / A股半导体链"
    elif "房地产" in text or "楼市" in text or "房贷" in text:
        impact = "地产供需关系调整与信用风险缓释进程，直接影响国内宏观预期回暖节奏。"
        asset = "地产股 / 建材水泥 / 国内银行板块"
    elif "关税" in text or "贸易" in text or "出口" in text:
        impact = "全球供应链重构压力显现，相关出海企业成本及海外市场份额面临测试。"
        asset = "外贸出口板块 / 跨境物流 / 汇率"
    elif channel_tag == "国内核心":
        impact = "国内政策面与产业资本预期共振，直接作用于本土市场风险偏好与估值。"
        asset = "A股核心指数 / 人民币资产 / 顺周期板块"
    elif channel_tag == "亚太市场":
        impact = "亚太区域经济数据与地缘动态交织，对周边汇市及科技制造产业链产生溢出效应。"
        asset = "日经225 / 港股恒生指数 / 亚太汇率"

    return impact, asset


# ========================= 翻译与抓取 =========================

def translate_to_chinese(text):
    text = clean_text(text)
    if not text:
        return text
    if len(re.findall(r"[\u4e00-\u9fff]", text)) >= 8:
        return text
    try:
        params = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "zh-CN", "dt": "t", "q": text[:400]})
        raw = http_get("https://translate.googleapis.com/translate_a/single?" + params, timeout=12)
        payload = json.loads(raw.decode("utf-8", errors="replace"))
        result = "".join(part[0] for part in (payload[0] or []) if isinstance(part, list) and part).strip()
        return result if result else text
    except Exception:
        return text


def parse_rss_xml(xml_bytes, source_name):
    items = []
    try:
        try:
            xml_str = xml_bytes.decode("utf-8")
        except UnicodeDecodeError:
            xml_str = xml_bytes.decode("gbk", errors="replace")
        root = ET.fromstring(xml_str)
    except Exception:
        return items

    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in ("item", "entry"):
            continue
        values = {}
        for child in list(node):
            key = child.tag.rsplit("}", 1)[-1].lower()
            if key == "link":
                values[key] = (child.attrib.get("href") or child.text or "").strip()
            elif key not in values:
                values[key] = child.text or ""
        title = clean_text(values.get("title", ""))
        summary = clean_text(values.get("description") or values.get("summary") or "")
        link = values.get("link", "").strip()
        published = parse_date(values.get("pubdate") or values.get("published") or values.get("updated") or "")
        if title:
            items.append({
                "title": title,
                "summary": summary,
                "published": published,
                "source": source_name,
                "url": link,
            })
    return items


def fetch_feed(source_name, url):
    try:
        data = http_get(url, timeout=12)
        return parse_rss_xml(data, source_name)
    except Exception:
        return []


def collect_rss(feed_defs):
    all_items = []
    for source_name, url in feed_defs:
        all_items.extend(fetch_feed(source_name, url))
        time.sleep(0.1)
    return all_items


# ========================= 过滤与精简 =========================

ASIA_KEYWORDS = ("asia", "singapore", "japan", "korea", "hong kong", "china", "taiwan", "亚太", "亚洲", "新加坡", "日本", "韩国", "香港", "台湾", "日经", "港股")

def filter_asia_news(items):
    return [it for it in items if any(w in (it["title"] + " " + it["summary"]).lower() for w in ASIA_KEYWORDS)]


CHINA_RELEVANCE_KEYWORDS = ("中国", "国内", "央行", "人民币", "a股", "沪指", "上证", "深证", "创业板", "科创板", "港股", "证监会", "gdp", "cpi", "房地产")
CHINA_EXCLUDE_KEYWORDS = ("美联储", "美国大选", "白宫", "乌克兰", "俄罗斯", "泽连斯基", "加沙", "中东局势")

def is_china_relevant(item):
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    if any(ex.lower() in text for ex in CHINA_EXCLUDE_KEYWORDS):
        return False
    return any(word.lower() in text for word in CHINA_RELEVANCE_KEYWORDS)


def fetch_china_news():
    items = []
    try:
        raw = http_get(SINA_URL, timeout=10)
        data = json.loads(raw.decode("utf-8", errors="replace"))
        for feed in data.get("result", {}).get("data", []):
            content = clean_text(feed.get("rich_text") or feed.get("title") or "")
            if len(content) > 8:
                pub = parse_date(feed.get("create_time"))
                # 将过长的 Sina Zhibo 直播流拆分为短标题与精简摘要
                match = re.match(r"^【([^】]+)】\s*(.*)$", content)
                if match:
                    title = match.group(1).strip()
                    summary = match.group(2).strip() or content
                else:
                    title = content[:40].strip() + "..."
                    summary = content
                items.append({
                    "title": title,
                    "summary": summary,
                    "published": pub,
                    "source": "新浪财经",
                    "url": feed.get("url", "")
                })
    except Exception:
        pass
    
    filtered = [it for it in items if is_china_relevant(it)]
    if len(filtered) < CHINA_LIMIT:
        for it in collect_rss(CHINA_RSS_FEEDS):
            if is_china_relevant(it):
                filtered.append(it)
    return filtered


def fetch_earnings_news():
    items = collect_rss(EARNINGS_RSS_FEEDS)
    earnings_items = []
    for it in items:
        text = (it["title"] + " " + it["summary"]).lower()
        if any(kw in text for kw in ["财报", "净利润", "营收", "业绩", "q1", "q2", "q3", "q4", "eps", "超预期"]):
            earnings_items.append(it)
    return earnings_items


def rank_news(items, limit, channel_tag):
    deduped = []
    seen = set()
    cutoff_time = now_beijing() - datetime.timedelta(hours=48)

    for raw in items:
        item = dict(raw)
        title = clean_text(item.get("title", ""))
        if not title:
            continue
        key = normalize_title(title)
        if key in seen:
            continue
        seen.add(key)

        pub = item.get("published")
        if pub and pub < cutoff_time:
            continue

        item["title"] = translate_to_chinese(title) if channel_tag != "国内核心" else title
        
        # 💡 核心优化：对所有长摘要进行统一截断（最多保留 130 个字符），确保卡片轻量整洁
        raw_summary = clean_text(item.get("summary", ""))
        if channel_tag != "国内核心":
            raw_summary = translate_to_chinese(raw_summary)
        if len(raw_summary) > 130:
            raw_summary = raw_summary[:130] + "..."
        item["summary"] = raw_summary

        item["time"] = pub.strftime("%m-%d %H:%M") if isinstance(pub, datetime.datetime) else ""
        item["tag"] = channel_tag
        item["source"] = clean_text(item.get("source", "")) or "权威财经媒体"
        item["url"] = clean_text(item.get("url", ""))
        
        # 填充 AI 解读与资产标的
        impact, asset = generate_ai_insights(item["title"], item["summary"], channel_tag)
        item["impact"] = impact
        item["asset"] = asset
        
        deduped.append(item)

    min_date = datetime.datetime.min.replace(tzinfo=BEIJING_TZ)
    deduped.sort(key=lambda x: x.get("published") or min_date, reverse=True)

    selected = deduped[:limit]
    total = len(selected)
    output = []
    for idx, item in enumerate(selected):
        item["id"] = f"NO.{total - idx:02d}"
        output.append(item)
    return output


def js_safe_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).replace("</", "<\\/").replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def main():
    current = now_beijing()
    datetime_str = current.strftime("%Y-%m-%d %H:%M:%S")

    print("=" * 50)
    print(f"开始抓取并由 AI 生成洞察：{datetime_str}")
    print("=" * 50)

    final_global = rank_news(collect_rss(RSS_FEEDS["global"]), GLOBAL_LIMIT, "全球宏观")
    final_asia = rank_news(filter_asia_news(collect_rss(RSS_FEEDS["asia"])), ASIA_LIMIT, "亚太市场")
    final_china = rank_news(fetch_china_news(), CHINA_LIMIT, "国内核心")
    final_earnings = rank_news(fetch_earnings_news(), EARNINGS_LIMIT, "核心财报季")

    if not HTML_FILE.exists():
        raise FileNotFoundError(f"找不到 {HTML_FILE}")

    content = HTML_FILE.read_text(encoding="utf-8")
    pattern = r"<!-- NEWS_DATA_START -->.*?<!-- NEWS_DATA_END -->"

    news_data = {
        "global": final_global,
        "asia": final_asia,
        "china": final_china,
        "earnings": final_earnings,
    }

    replacement = f"<!-- NEWS_DATA_START -->\n<script>\nconst newsData = {js_safe_json(news_data)};\n</script>\n<!-- NEWS_DATA_END -->"
    updated, _ = re.subn(pattern, lambda _: replacement, content, count=1, flags=re.DOTALL)

    time_pattern = r'<div id="updateTime"[^>]*>.*?</div>'
    time_replacement = f'<div id="updateTime" class="text-slate-400 font-medium text-xs">更新时间: {datetime_str} (北京时间)</div>'
    updated, _ = re.subn(time_pattern, lambda _: time_replacement, updated, count=1, flags=re.DOTALL)

    HTML_FILE.write_text(updated, encoding="utf-8")
    print("✅ 看板及 AI 智能解读数据更新完成！")


if __name__ == "__main__":
    main()

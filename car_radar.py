
# -*- coding: utf-8 -*-
"""免费二手车搜索雷达：Bing RSS + Telegram + SQLite 去重。"""

import os
import re
import time
import html
import json
import hashlib
import sqlite3
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "car_radar.sqlite3"
TZ = timezone(timedelta(hours=8))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TG_CHAT_ID")

MAX_PRICE = 150000
MIN_DATE = "2019-01-01"
MAX_PUSH = 8
BING_URL = "https://www.bing.com/search"

# 域名使用后缀匹配，允许平台的子域名。
PLATFORMS = {
    "瓜子二手车": ["guazi.com"],
    "懂车帝": ["dongchedi.com"],
    "58同城": ["58.com"],
    "闲鱼": ["goofish.com", "xianyu.com", "2.taobao.com"],
}

VEHICLES = [
    {
        "name": "沃尔沃 S60 T4/T5（2020年款）",
        "terms": ['"S60 T4"', '"S60 T5"', '"S60T4"', '"S60T5"'],
        "aliases": ["S60 T4", "S60T4", "S60 T5", "S60T5"],
        "required_year": 2020,
        "year_from": None,
    },
    {
        "name": "马自达 CX-50 智行版",
        "terms": ['"CX-50 智行"', '"CX50 智行"', '"CX-50"'],
        "aliases": ["CX-50", "CX50"],
        "required_year": None,
        "year_from": None,
    },
    {
        "name": "丰田 RAV4 荣放",
        "terms": ['"RAV4 荣放"', '"RAV4"'],
        "aliases": ["RAV4", "荣放"],
        "required_year": None,
        "year_from": None,
    },
    {
        "name": "特斯拉 Model 3（2023年及以后）",
        "terms": ['"特斯拉 Model 3"', '"Tesla Model 3"', '"Model 3"'],
        "aliases": ["Model 3", "Model3", "特斯拉"],
        "required_year": None,
        "year_from": 2023,
    },
    {
        "name": "福特蒙迪欧双擎运动版",
        "terms": ['"蒙迪欧双擎运动"', '"蒙迪欧 双擎 运动"'],
        "aliases": ["蒙迪欧", "双擎", "运动"],
        "required_year": None,
        "year_from": None,
    },
]


def log(message):
    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[北京时间 {now}] {message}")


def fetch_url(url, timeout=20):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                          "AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def clean(value):
    value = re.sub(r"<[^>]*>", " ", value or "")
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def parse_rss(raw, source):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        log(f"RSS 解析失败：{exc}")
        return []

    result = []
    for node in root.findall(".//item"):
        title = clean(node.findtext("title", default=""))
        link = (node.findtext("link", default="") or "").strip()
        desc = clean(node.findtext("description", default=""))
        if title and link:
            result.append({
                "title": title,
                "content": desc,
                "url": link,
                "source": source,
                "id": node.findtext("guid", default="") or link,
            })

    if not result:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for node in root.findall(".//a:entry", ns):
            title = clean(node.findtext("a:title", default="", namespaces=ns))
            desc = clean(node.findtext("a:summary", default="", namespaces=ns))
            link_node = node.find("a:link", ns)
            link = link_node.get("href", "") if link_node is not None else ""
            if title and link:
                result.append({
                    "title": title,
                    "content": desc,
                    "url": link,
                    "source": source,
                    "id": link,
                })
    return result


def build_queries():
    """每个车型分别搜索每个平台，避免复杂 OR 条件导致漏结果。"""
    for vehicle in VEHICLES:
        for platform, domains in PLATFORMS.items():
            domain = domains[0]
            # 不限定价格、年份等可能尚未被搜索摘要收录的字段。
            term = " OR ".join(vehicle["terms"])
            query = f"site:{domain} 二手车 {term}"
            yield vehicle, platform, query


def search_bing(query, platform):
    params = urllib.parse.urlencode({
        "q": query,
        "format": "rss",
        "count": 10,
    })
    url = f"{BING_URL}?{params}"
    try:
        entries = parse_rss(fetch_url(url), platform)
        log(f"  搜索返回 {len(entries)} 条")
        return entries
    except Exception as exc:
        log(f"  搜索失败：{type(exc).__name__}: {exc}")
        return []


def identify_platform(url):
    host = (urlparse(url).hostname or "").lower()
    for name, domains in PLATFORMS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
    return None


def contains_vehicle(text, vehicle):
    text = (text or "").casefold()
    return any(term.casefold() in text for term in vehicle["aliases"])


def parse_price(text):
    text = (text or "").replace(",", "").replace("，", "")
    patterns = [
        r"(?:售价|价格|报价|仅售|卖价)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*万元?",
        r"(\d+(?:\.\d+)?)\s*万元",
        r"(\d+(?:\.\d+)?)\s*万(?!公里)",
        r"(?:售价|价格|报价|仅售)\s*[:：]?\s*(\d{4,7})\s*元",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        try:
            value = float(m.group(1))
            if "元" in m.group(0) and "万" not in m.group(0):
                value = int(value)
            else:
                value *= 10000
            if 1000 <= value <= 100000000:
                return int(value)
        except ValueError:
            pass
    return None


def parse_production_date(text):
    patterns = [
        r"(?:生产日期|出厂日期)\s*[:：]?\s*(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})日?",
        r"(?:生产日期|出厂日期)\s*[:：]?\s*(20\d{2})年",
    ]
    m = re.search(patterns[0], text or "")
    if m:
        try:
            y, mo, d = map(int, m.groups())
            return datetime(y, mo, d).date().isoformat()
        except ValueError:
            return None
    m = re.search(patterns[1], text or "")
    if m:
        return f"{m.group(1)}-01-01"
    return None


def parse_year(text):
    for pattern in [r"(20\d{2})\s*年款", r"(20\d{2})款"]:
        m = re.search(pattern, text or "")
        if m:
            return int(m.group(1))
    return None


def evaluate(item, vehicle):
    title = item["title"]
    text = f"{title} {item['content']}"
    url = item["url"]
    platform = identify_platform(url)

    if not platform:
        return None, "链接不是四个平台之一"

    if not contains_vehicle(text, vehicle):
        # 搜索引擎可能将车型关键词放在查询中，但标题摘要未体现。
        return None, "标题和摘要没有识别到车型关键词"

    price = parse_price(text)
    prod_date = parse_production_date(text)
    year = parse_year(text)

    if price is not None and price > MAX_PRICE:
        return None, "明确超过15万元"

    if prod_date and prod_date < MIN_DATE:
        return None, "明确早于2019年生产"

    required = vehicle["required_year"]
    year_from = vehicle["year_from"]

    if required is not None and year is not None and year != required:
        return None, f"明确不是{required}年款"

    if year_from is not None and year is not None and year < year_from:
        return None, f"明确早于{year_from}年款"

    if year_from is not None and prod_date and int(prod_date[:4]) < year_from:
        return None, f"明确早于{year_from}年生产"

    pending = []
    if price is None:
        pending.append("售价待核实")
    if prod_date is None:
        pending.append("生产日期待核实")
    if required is not None and year is None:
        pending.append(f"{required}年款待核实")
    if year_from is not None and year is None:
        pending.append(f"{year_from}年及以后年款待核实")

    return {
        "title": title,
        "url": url,
        "platform": platform,
        "vehicle": vehicle["name"],
        "price": price,
        "production_date": prod_date,
        "year": year,
        "pending": pending,
    }, None


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        log("错误：未找到 Telegram Bot Token 或 Chat ID Secrets")
        return False

    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data=payload,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
        if data.get("ok"):
            log("Telegram 推送成功")
            return True
        log(f"Telegram 返回错误：{data}")
    except Exception as exc:
        log(f"Telegram 推送失败：{type(exc).__name__}: {exc}")
    return False


def make_message(item):
    price = f"约 ¥{item['price']:,}" if item["price"] is not None else "待核实"
    prod_date = item["production_date"] or "待核实"
    year = f"{item['year']}年款" if item["year"] else "待核实"
    pending = "、".join(item["pending"]) if item["pending"] else "仍需核实真实库存和车况"

    return (
        "🚗 <b>二手车雷达：发现候选车源</b>\n\n"
        f"车型：{html.escape(item['vehicle'])}\n"
        f"来源：{html.escape(item['platform'])}\n"
        f"标题：{html.escape(item['title'])}\n"
        f"价格：{price}\n"
        f"生产日期：{html.escape(prod_date)}\n"
        f"年款：{html.escape(year)}\n"
        f"待核实：{html.escape(pending)}\n\n"
        f'<a href="{html.escape(item["url"], quote=True)}">打开原始搜索结果</a>\n\n'
        "提示：搜索结果不代表车辆仍在售。请打开链接核实售价、生产日期、配置及车况。"
    )


def stable_id(url):
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()


def main():
    log("=" * 55)
    log("二手车雷达启动：逐车型、逐平台搜索")
    log("注意：搜索引擎结果不是平台完整库存。")

    all_items = []
    for vehicle, platform, query in build_queries():
        log(f"搜索：{vehicle['name']} / {platform}")
        entries = search_bing(query, platform)
        for item in entries:
            item["_query_vehicle"] = vehicle
        all_items.extend(entries)
        time.sleep(1)

    log(f"搜索结果合计：{len(all_items)} 条")

    # 展示样本标题，便于确认搜索引擎实际返回了什么。
    for item in all_items[:12]:
        log(f"样本：{item['title']} | {item['url']}")

    # 按链接去重，保留搜索到的第一次结果。
    unique = {}
    for item in all_items:
        unique.setdefault(item["url"].strip(), item)

    log(f"链接去重后：{len(unique)} 条")

    stats = {
        "no_platform": 0,
        "no_vehicle": 0,
        "over_price": 0,
        "too_old": 0,
        "wrong_year": 0,
        "candidate": 0,
        "already_seen": 0,
    }

    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_listings (
            item_id TEXT PRIMARY KEY,
            title TEXT,
            url TEXT,
            first_seen TEXT
        )
    """)
    conn.commit()

    pushed = 0
    try:
        for url, raw in unique.items():
            vehicle = raw["_query_vehicle"]
            item, reason = evaluate(raw, vehicle)

            if item is None:
                if reason == "链接不是四个平台之一":
                    stats["no_platform"] += 1
                elif reason == "标题和摘要没有识别到车型关键词":
                    stats["no_vehicle"] += 1
                elif reason == "明确超过15万元":
                    stats["over_price"] += 1
                elif reason == "明确早于2019年生产":
                    stats["too_old"] += 1
                else:
                    stats["wrong_year"] += 1
                continue

            stats["candidate"] += 1
            key = stable_id(item["url"])

            if conn.execute(
                "SELECT 1 FROM seen_listings WHERE item_id = ?", (key,)
            ).fetchone():
                stats["already_seen"] += 1
                continue

            if pushed >= MAX_PUSH:
                log(f"达到本次推送上限 {MAX_PUSH} 条")
                break

            if send_telegram(make_message(item)):
                conn.execute(
                    "INSERT OR IGNORE INTO seen_listings "
                    "(item_id, title, url, first_seen) VALUES (?, ?, ?, ?)",
                    (
                        key,
                        item["title"],
                        item["url"],
                        datetime.now(TZ).isoformat(),
                    ),
                )
                conn.commit()
                pushed += 1
            else:
                log("推送失败；本条不记入去重数据库，后续可重试")

        log("========== 筛选统计 ==========")
        log(f"非目标平台链接：{stats['no_platform']}")
        log(f"车型关键词未匹配：{stats['no_vehicle']}")
        log(f"明确超过预算：{stats['over_price']}")
        log(f"明确早于生产日期下限：{stats['too_old']}")
        log(f"明确年款不符或其他排除：{stats['wrong_year']}")
        log(f"候选车源：{stats['candidate']}")
        log(f"之前已通知：{stats['already_seen']}")
        log(f"本次推送成功：{pushed}")
        log("运行结束")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

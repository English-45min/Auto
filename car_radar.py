# -*- coding: utf-8 -*-
"""
二手车雷达：免费搜索 RSS + 可选外部 RSS + Telegram 推送

注意：
1. 搜索引擎结果不是平台完整库存，也不保证实时。
2. 缺少价格、生产日期或年款时，推送会标注“待核实”。
3. 不绕过登录、验证码或访问限制。
4. 兼容现有 Telegram Secrets 和 SQLite 去重数据库。
"""

import os
import re
import json
import time
import html
import hashlib
import sqlite3
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse


# ==================== 基础配置 ====================

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "car_radar.sqlite3"
CONFIG_FILE = BASE_DIR / "car_radar_config.json"
BEIJING_TZ = timezone(timedelta(hours=8))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TG_CHAT_ID")

MAX_PRICE = 150000
MIN_PRODUCTION_DATE = "2019-01-01"

# 每次运行最多推送数量，防止搜索结果突然增多时刷屏。
MAX_PUSH_PER_RUN = 8

# Bing 搜索 RSS：这是搜索结果入口，不是官方车源 API。
BING_RSS_URL = "https://www.bing.com/search"

# 允许搜索的目标平台域名。
PLATFORM_DOMAINS = {
    "瓜子二手车": ("guazi.com",),
    "懂车帝": ("dongchedi.com",),
    "58同城": ("58.com",),
    "闲鱼": ("goofish.com", "xianyu.com", "2.taobao.com"),
}

PLATFORM_TERMS = [
    "瓜子二手车",
    "懂车帝",
    "58同城",
    "闲鱼",
    "二手车",
    "出售",
    "在售",
    "车源",
    "个人转让",
]

VEHICLE_RULES = [
    {
        "name": "沃尔沃 S60 T4/T5（2020年款）",
        "aliases": [
            "沃尔沃 S60 T4", "沃尔沃S60 T4",
            "沃尔沃 S60 T5", "沃尔沃S60 T5",
            "S60 T4", "S60T4", "S60 T5", "S60T5",
        ],
        "required_year": 2020,
        "year_from": None,
    },
    {
        "name": "马自达 CX-50 智行版",
        "aliases": [
            "马自达 CX-50 智行版",
            "马自达CX-50智行版",
            "CX-50 智行",
            "CX50 智行",
        ],
        "required_year": None,
        "year_from": None,
    },
    {
        "name": "丰田 RAV4 荣放",
        "aliases": [
            "丰田 RAV4", "丰田RAV4",
            "RAV4 荣放", "RAV4荣放",
            "RAV4", "荣放",
        ],
        "required_year": None,
        "year_from": None,
    },
    {
        "name": "特斯拉 Model 3（2023年及以后）",
        "aliases": [
            "特斯拉 Model 3",
            "特斯拉Model 3",
            "Tesla Model 3",
            "Model 3",
            "Model3",
        ],
        "required_year": None,
        "year_from": 2023,
    },
    {
        "name": "福特蒙迪欧双擎运动版",
        "aliases": [
            "福特蒙迪欧双擎运动版",
            "蒙迪欧双擎运动版",
            "蒙迪欧双擎运动",
        ],
        "required_year": None,
        "year_from": None,
    },
]


# ==================== 日志与数据库 ====================

def log(message):
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[北京时间 {now}] {message}")


def get_db():
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
    return conn


def stable_id(url, title):
    value = (url or "").strip() or (title or "").strip()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ==================== 文本处理 ====================

def strip_html(text):
    text = re.sub(r"<[^>]*>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_url(url, timeout=20):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            ),
            "Accept": (
                "application/rss+xml, application/xml, text/xml, "
                "text/html;q=0.9, */*;q=0.8"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
        },
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# ==================== 搜索请求 ====================

def build_search_queries():
    """
    每款车型生成一条搜索请求，并限定在目标平台域名范围内。
    Bing 不保证支持或执行所有复杂搜索条件，因此结果仍需核实。
    """
    site_clause = (
        "(site:guazi.com OR site:dongchedi.com OR "
        "site:58.com OR site:goofish.com OR site:xianyu.com "
        "OR site:2.taobao.com)"
    )

    query_specs = [
        (
            "沃尔沃 S60 T4/T5（2020年款）",
            '"沃尔沃 S60 T4" OR "沃尔沃 S60 T5" OR "S60 T4" OR "S60 T5"',
        ),
        (
            "马自达 CX-50 智行版",
            '"马自达 CX-50 智行版" OR "CX-50 智行"',
        ),
        (
            "丰田 RAV4 荣放",
            '"丰田 RAV4" OR "RAV4 荣放"',
        ),
        (
            "特斯拉 Model 3（2023年及以后）",
            '"特斯拉 Model 3" OR "Tesla Model 3"',
        ),
        (
            "福特蒙迪欧双擎运动版",
            '"蒙迪欧双擎运动版" OR "蒙迪欧双擎运动"',
        ),
    ]

    for vehicle_name, terms in query_specs:
        query = (
            f"二手车 {terms} {site_clause} "
            f"(在售 OR 出售 OR 车源 OR 价格)"
        )
        yield vehicle_name, query


def fetch_bing_rss(query):
    params = urllib.parse.urlencode({
        "q": query,
        "format": "rss",
        "count": 20,
    })
    url = f"{BING_RSS_URL}?{params}"

    try:
        raw = fetch_url(url)
        entries = parse_rss(raw, source_name="Bing 搜索 RSS")
        log(f"搜索 RSS 返回 {len(entries)} 条结果。")
        return entries
    except Exception as exc:
        log(f"搜索 RSS 读取失败：{type(exc).__name__}: {exc}")
        return []


# ==================== RSS 解析 ====================

def parse_rss(xml_bytes, source_name="RSS"):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        log(f"{source_name} XML 解析失败：{exc}")
        return []

    entries = []

    # RSS 2.0
    for node in root.findall(".//item"):
        title = strip_html(node.findtext("title", default=""))
        link = (node.findtext("link", default="") or "").strip()
        description = strip_html(
            node.findtext("description", default="")
        )
        pub_date = node.findtext("pubDate", default="") or ""
        guid = node.findtext("guid", default="") or link or title

        if title and link:
            entries.append({
                "id": guid,
                "title": title,
                "content": description,
                "url": link,
                "published": pub_date,
                "source": source_name,
            })

    # Atom
    if not entries:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for node in root.findall(".//atom:entry", ns):
            title = strip_html(
                node.findtext("atom:title", default="", namespaces=ns)
            )
            summary = strip_html(
                node.findtext("atom:summary", default="", namespaces=ns)
            )
            link_node = node.find("atom:link", ns)
            link = (
                link_node.get("href", "")
                if link_node is not None else ""
            )
            item_id = node.findtext(
                "atom:id", default="", namespaces=ns
            ) or link or title

            if title and link:
                entries.append({
                    "id": item_id,
                    "title": title,
                    "content": summary,
                    "url": link,
                    "published": "",
                    "source": source_name,
                })

    return entries


# ==================== 车型与平台识别 ====================

def match_vehicle(text):
    text = (text or "").casefold()

    # 先匹配较长关键词，避免“RAV4”等短词过早命中。
    rules = sorted(
        VEHICLE_RULES,
        key=lambda rule: max(len(x) for x in rule["aliases"]),
        reverse=True,
    )

    for rule in rules:
        for alias in rule["aliases"]:
            if alias.casefold() in text:
                return rule

    return None


def identify_platform(url, text=""):
    host = (urlparse(url).hostname or "").lower()

    for platform, domains in PLATFORM_DOMAINS.items():
        if any(host == domain or host.endswith("." + domain)
               for domain in domains):
            return platform

    # 如果链接没有指向平台域名，仅在文本有明确平台名称时标注。
    for platform in PLATFORM_DOMAINS:
        if platform in (text or ""):
            return platform

    return "来源待确认"


def looks_like_listing(item):
    text = f"{item.get('title', '')} {item.get('content', '')}".casefold()

    # 搜索结果必须看起来与汽车交易有关，尽量过滤新闻和文章。
    listing_terms = [
        "二手车", "在售", "出售", "转让", "车源",
        "上牌", "万公里", "售价", "报价", "车况",
        "个人卖车", "车辆详情", "二手",
    ]

    return any(term.casefold() in text for term in listing_terms)


# ==================== 价格、日期和年款识别 ====================

def parse_price(text):
    """返回人民币元；未识别时返回 None。"""
    text = (text or "").replace(",", "").replace("，", "")

    patterns_wan = [
        r"(?:售价|价格|报价|仅售|卖价|价格为)\s*[:：]?\s*"
        r"(\d{1,3}(?:\.\d+)?)\s*万元?",
        r"(\d{1,3}(?:\.\d+)?)\s*万元",
        r"(\d{1,3}(?:\.\d+)?)\s*万(?!公里)",
    ]

    for pattern in patterns_wan:
        match = re.search(pattern, text)
        if match:
            try:
                value = float(match.group(1)) * 10000
                if 1000 <= value <= 100000000:
                    return int(value)
            except ValueError:
                pass

    patterns_yuan = [
        r"(?:售价|价格|报价|仅售)\s*[:：]?\s*(\d{4,7})\s*元",
    ]

    for pattern in patterns_yuan:
        match = re.search(pattern, text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass

    return None


def parse_production_date(text):
    """
    只接受明确的生产/出厂日期。
    上牌日期和车型年份不自动当作生产日期。
    """
    text = text or ""

    patterns = [
        r"(?:生产日期|出厂日期)\s*[:：]?\s*"
        r"(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})日?",
        r"(?:生产日期|出厂日期)\s*[:：]?\s*(20\d{2})年",
    ]

    match = re.search(patterns[0], text)
    if match:
        year, month, day = map(int, match.groups())
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None

    match = re.search(patterns[1], text)
    if match:
        return f"{match.group(1)}-01-01"

    return None


def parse_model_year(text):
    text = text or ""

    patterns = [
        r"(20\d{2})\s*年款",
        r"(20\d{2})款",
        r"(?:车型|年款)\s*[:：]?\s*(20\d{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))

    return None


# ==================== 候选车源筛选 ====================

def evaluate_listing(item):
    title = item.get("title", "")
    content = item.get("content", "")
    url = item.get("url", "")
    text = f"{title} {content}"

    rule = match_vehicle(text)
    if not rule:
        return None

    if not looks_like_listing(item):
        return None

    platform = identify_platform(url, text)

    # 不允许把明显不属于目标平台的普通网页当作目标平台车源。
    host = (urlparse(url).hostname or "").lower()
    is_target_domain = any(
        host == domain or host.endswith("." + domain)
        for domains in PLATFORM_DOMAINS.values()
        for domain in domains
    )
    if not is_target_domain:
        log(f"跳过非目标平台链接：{title}")
        return None

    price = parse_price(text)
    production_date = parse_production_date(text)
    model_year = parse_model_year(text)

    # 已明确超预算，直接排除。
    if price is not None and price > MAX_PRICE:
        return None

    # 已明确生产日期早于下限，直接排除。
    if production_date is not None and production_date < MIN_PRODUCTION_DATE:
        return None

    # 已明确年款不符时，直接排除。
    required_year = rule.get("required_year")
    year_from = rule.get("year_from")

    if required_year is not None and model_year is not None:
        if model_year != required_year:
            return None

    if year_from is not None and model_year is not None:
        if model_year < year_from:
            return None

    # Model 3 没有年款时，若生产年份已知且早于 2023，也排除。
    if rule["name"].startswith("特斯拉 Model 3"):
        if production_date and production_date[:4].isdigit():
            if int(production_date[:4]) < 2023:
                return None

    pending = []

    if price is None:
        pending.append("售价未确认")
    if production_date is None:
        pending.append("生产日期未确认")

    if required_year is not None and model_year is None:
        pending.append(f"{required_year}年款未确认")

    if year_from is not None and model_year is None:
        pending.append("2023年及以后年款未确认")

    return {
        **item,
        "matched_vehicle": rule["name"],
        "platform": platform,
        "price": price,
        "production_date": production_date,
        "model_year": model_year,
        "pending": pending,
    }


# ==================== Telegram ====================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        log("没有检测到 Telegram 密钥，请检查现有 GitHub Secrets。")
        return False

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    request = urllib.request.Request(
        api_url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))

        if data.get("ok"):
            log("Telegram 推送成功。")
            return True

        log("Telegram 返回失败状态。")
        return False

    except Exception as exc:
        log(f"Telegram 推送失败：{type(exc).__name__}: {exc}")
        return False


def make_message(item):
    title = html.escape(item.get("title", "无标题"))
    vehicle = html.escape(item["matched_vehicle"])
    platform = html.escape(item["platform"])
    url = html.escape(item["url"], quote=True)

    if item["price"] is None:
        price_text = "待核实"
    else:
        price_text = f"约 ¥{item['price']:,}"

    production_date = item["production_date"] or "待核实"
    model_year = (
        f"{item['model_year']} 年款"
        if item["model_year"] is not None
        else "待核实"
    )

    if item["pending"]:
        status = "⚠️ 候选车源（部分条件待核实）"
        pending_text = "、".join(item["pending"])
    else:
        status = "🔎 候选车源（已知条件初步符合）"
        pending_text = "仍需打开原始页面核对车况、配置和真实售价"

    return (
        f"🚗 <b>二手车雷达：发现新候选车源</b>\n\n"
        f"状态：{html.escape(status)}\n"
        f"车型：{vehicle}\n"
        f"来源：{platform}\n"
        f"标题：{title}\n"
        f"价格：{price_text}\n"
        f"生产日期：{html.escape(production_date)}\n"
        f"年款：{html.escape(model_year)}\n"
        f"核实提示：{html.escape(pending_text)}\n\n"
        f'<a href="{url}">打开原始车源</a>\n\n'
        "说明：这是搜索结果候选项，不代表车辆仍在售。请核对实际价格、生产日期、配置和车况。"
    )


# ==================== 可选外部 RSS ====================

def load_extra_feed_urls():
    """
    兼容现有工作流变量 CAR_RADAR_FEED_URLS。
    可用英文逗号分隔多个 RSS/Atom 地址。
    """
    return [
        item.strip()
        for item in os.getenv("CAR_RADAR_FEED_URLS", "").split(",")
        if item.strip()
    ]


# ==================== 主流程 ====================

def main():
    log("=" * 55)
    log("二手车雷达启动")
    log(f"预算上限：¥{MAX_PRICE:,}")
    log(f"生产日期下限：{MIN_PRODUCTION_DATE}")
    log("数据入口：Bing 搜索 RSS + 可选外部 RSS")
    log("注意：搜索 RSS 不等于平台实时库存。")

    all_entries = []
    successful_queries = 0

    for vehicle_name, query in build_search_queries():
        log(f"搜索车型：{vehicle_name}")
        entries = fetch_bing_rss(query)

        if entries:
            successful_queries += 1
            all_entries.extend(entries)

        # 控制请求频率，减少触发限流的可能。
        time.sleep(2)

    extra_urls = load_extra_feed_urls()

    for feed_url in extra_urls:
        try:
            log(f"读取额外 RSS：{feed_url}")
            entries = parse_rss(fetch_url(feed_url), feed_url)
            all_entries.extend(entries)
            log(f"额外 RSS 返回 {len(entries)} 条结果。")
        except Exception as exc:
            log(f"额外 RSS 读取失败：{type(exc).__name__}: {exc}")

    if successful_queries == 0 and not extra_urls:
        log(
            "本次所有搜索请求均未成功，且没有配置额外 RSS。"
            "不会虚构车源，也不会发送虚假成功通知。"
        )
        return

    # 先按 URL 去重，避免同一结果被多个搜索词重复处理。
    unique_entries = {}
    for item in all_entries:
        url = item.get("url", "").strip()
        title = item.get("title", "").strip()

        if not url or not title:
            continue

        key = stable_id(url, title)
        if key not in unique_entries:
            unique_entries[key] = item

    log(f"搜索结果总数：{len(all_entries)}")
    log(f"去重后结果数：{len(unique_entries)}")

    conn = get_db()
    pushed = 0
    candidates = 0

    try:
        for key, item in unique_entries.items():
            if pushed >= MAX_PUSH_PER_RUN:
                log(f"已达到本次推送上限 {MAX_PUSH_PER_RUN} 条。")
                break

            candidate = evaluate_listing(item)
            if not candidate:
                continue

            candidates += 1

            existing = conn.execute(
                "SELECT 1 FROM seen_listings WHERE item_id = ?",
                (key,),
            ).fetchone()

            if existing:
                continue

            message = make_message(candidate)

            if send_telegram(message):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO seen_listings
                    (item_id, title, url, first_seen)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        key,
                        candidate["title"],
                        candidate["url"],
                        datetime.now(BEIJING_TZ).isoformat(),
                    ),
                )
                conn.commit()
                pushed += 1
            else:
                log("推送失败，本条不会写入去重数据库，下次可以重试。")
                time.sleep(2)

        log(f"本次识别到的候选车源：{candidates}")
        log(f"本次成功推送：{pushed}")
        log("本次运行结束。")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

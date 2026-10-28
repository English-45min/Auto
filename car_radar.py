# -*- coding: utf-8 -*-
"""二手车雷达：多来源搜索、筛选、Telegram 推送、SQLite 去重"""

import html
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "car_radar_config.json"
DB_FILE = BASE / "car_radar.sqlite3"

DOMAINS = {
    "瓜子二手车": ["guazi.com"],
    "懂车帝": ["dongchedi.com"],
    "58同城": ["58.com"],
    "闲鱼": ["goofish.com", "xianyu.com", "2.taobao.com"],
    "汽车之家": ["che168.com", "autohome.com.cn"],
    "爱卡汽车": ["xcar.com.cn"],
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
    )
}


def log(message):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def request(url, timeout=15):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def init_db():
    with sqlite3.connect(DB_FILE) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS sent_listings (
                url TEXT PRIMARY KEY,
                title TEXT,
                sent_at TEXT
            )
        """)


def already_sent(url):
    with sqlite3.connect(DB_FILE) as db:
        return db.execute(
            "SELECT 1 FROM sent_listings WHERE url=?", (url,)
        ).fetchone() is not None


def mark_sent(url, title):
    with sqlite3.connect(DB_FILE) as db:
        db.execute(
            "INSERT OR IGNORE INTO sent_listings VALUES (?, ?, ?)",
            (url, title, datetime.now().isoformat(timespec="seconds")),
        )


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def get_host(url):
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def platform_for_url(url):
    host = get_host(url)
    for platform, domains in DOMAINS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return platform
    return None


def unwrap_url(url):
    """解开搜索引擎的跳转链接。"""
    for _ in range(3):
        p = urllib.parse.urlparse(url)
        host = (p.hostname or "").lower()
        query = urllib.parse.parse_qs(p.query)
        new_url = None

        if "bing.com" in host:
            for key in ("url", "u"):
                if query.get(key):
                    new_url = query[key][0]
                    if new_url.startswith("a1"):
                        try:
                            import base64
                            new_url = base64.urlsafe_b64decode(
                                new_url[2:] + "=="
                            ).decode("utf-8", "ignore")
                        except Exception:
                            pass
                    break

        elif "duckduckgo.com" in host:
            if query.get("uddg"):
                new_url = query["uddg"][0]

        if not new_url:
            break

        url = urllib.parse.unquote(new_url)

    return url.split("#")[0]


class SearchParser(HTMLParser):
    """从搜索结果 HTML 中提取标题与链接。"""

    def __init__(self):
        super().__init__()
        self.results = []
        self.current = None
        self.capture = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)

        if tag == "a":
            href = attrs.get("href", "")
            cls = attrs.get("class", "")

            if href and (
                "result__a" in cls
                or "result-link" in cls
                or "uddg=" in href
            ):
                self.current = {"url": unwrap_url(href), "title": ""}
                self.capture = True

    def handle_data(self, data):
        if self.capture and self.current is not None:
            self.current["title"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.capture and self.current:
            item = {
                "url": self.current["url"],
                "title": clean_text(self.current["title"]),
            }
            if item["url"].startswith("http") and item["title"]:
                self.results.append(item)
            self.current = None
            self.capture = False


def search_duckduckgo(query):
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({
        "q": query,
    })
    data = request(url)
    parser = SearchParser()
    parser.feed(data.decode("utf-8", "ignore"))
    return parser.results


def search_bing(query):
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({
        "q": query,
        "count": 20,
    })
    data = request(url).decode("utf-8", "ignore")
    results = []

    for block in re.findall(
        r"<li class=\"b_algo\".*?</li>", data, flags=re.S | re.I
    ):
        match = re.search(
            r"<h2[^>]*>.*?<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>",
            block, flags=re.S | re.I,
        )
        if match:
            results.append({
                "url": unwrap_url(html.unescape(match.group(1))),
                "title": clean_text(match.group(2)),
            })

    return results


def search_web(query):
    """一个搜索源失败时尝试另一个；两者都失败才报错。"""
    errors = []

    for name, func in (
        ("DuckDuckGo", search_duckduckgo),
        ("Bing", search_bing),
    ):
        try:
            results = func(query)
            if results:
                return results
            errors.append(f"{name}:无结果")
        except Exception as exc:
            errors.append(f"{name}:{exc}")

    raise RuntimeError("；".join(errors))


def vehicle_matches(text, vehicle):
    text = text.lower()
    brand = vehicle.get("brand", "")
    model = vehicle.get("model", "")
    year_from = vehicle.get("year_from")
    year_to = vehicle.get("year_to")

    aliases = {
        "S60 T4/T5": ["s60", "沃尔沃"],
        "V60": ["v60", "沃尔沃"],
        "CX-50": ["cx-50", "cx50", "马自达"],
        "RAV4 荣放": ["rav4", "荣放", "丰田"],
        "Model 3": ["model 3", "model3", "特斯拉"],
        "蒙迪欧双擎运动版": ["蒙迪欧", "福特"],
    }

    terms = aliases.get(model, [model, brand])
    if not all(term.lower() in text for term in terms):
        return False

    years = [int(y) for y in re.findall(r"20\d{2}", text)]
    if years:
        if year_from and max(years) < year_from:
            return False
        if year_to and min(years) > year_to:
            return False

    if model == "S60 T4/T5":
        if not re.search(r"\bT[45]\b|T4|T5", text, re.I):
            return False

    if model == "蒙迪欧双擎运动版":
        if not any(x in text for x in ("双擎", "混动", "运动版")):
            return False

    return True


def parse_price(text):
    """提取人民币价格，统一转换为元。"""
    patterns = [
        r"(?:售价|价格|报价|￥|¥)\s*([0-9]+(?:\.[0-9]+)?)\s*(万|万元|元)?",
        r"([0-9]+(?:\.[0-9]+)?)\s*(万元|万|元)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue

        try:
            number = float(match.group(1))
            unit = match.group(2) or ""
            if unit in ("万", "万元"):
                number *= 10000
            elif number < 1000:
                continue
            return int(number)
        except (ValueError, IndexError):
            continue

    return None


def parse_production_year(text):
    patterns = [
        r"(20\d{2})年(?:\d{1,2}月)?上牌",
        r"(20\d{2})年(?:\d{1,2}月)?出厂",
        r"(20\d{2})年款",
        r"(20\d{2})款",
        r"生产日期[：:\s]*(20\d{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))

    return None


def fetch_page_text(url):
    try:
        data = request(url, timeout=10)
        text = data.decode("utf-8", "ignore")
        text = re.sub(
            r"<script\b.*?</script>|<style\b.*?</style>",
            " ", text, flags=re.S | re.I
        )
        return clean_text(text)[:12000]
    except Exception:
        return ""


def evaluate(item, vehicle, config):
    title = clean_text(item["title"])
    url = item["url"]
    page_text = fetch_page_text(url)
    text = f"{title} {page_text}"

    if not vehicle_matches(text, vehicle):
        return None

    if not platform_for_url(url):
        return None

    price = parse_price(text)
    max_price = config.get("max_price")

    if price is not None and max_price is not None and price > max_price:
        return None

    year = parse_production_year(text)
    minimum_date = config.get("production_date_from", "2019-01-01")
    minimum_year = int(minimum_date[:4])

    if year is not None and year < minimum_year:
        return None

    # 资料缺失时保留为待核实，不把搜索日期当作上架日期。
    pending = []
    if price is None:
        pending.append("价格待核实")
    if year is None:
        pending.append("生产年份待核实")

    return {
        "title": title,
        "url": url,
        "platform": platform_for_url(url),
        "price": price,
        "year": year,
        "pending": pending,
        "vehicle": vehicle.get("model", ""),
    }


def telegram_send(message):
    token = os.getenv("TG_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TG_CHAT_ID", "").strip()

    if not token or not chat_id:
        raise RuntimeError("缺少 TG_BOT_TOKEN 或 TG_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode()

    req = urllib.request.Request(url, data=payload, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(str(result))


def format_listing(item):
    price = (
        f"{item['price']:,} 元"
        if item["price"] is not None else "价格待核实"
    )
    year = (
        str(item["year"]) if item["year"] else "年份待核实"
    )
    pending = (
        "；".join(item["pending"]) if item["pending"] else "无"
    )

    return (
        f"🚘 {item['vehicle']}\n"
        f"标题：{item['title']}\n"
        f"平台：{item['platform']}\n"
        f"价格：{price}\n"
        f"年份：{year}\n"
        f"待核实：{pending}\n"
        f"链接：{item['url']}"
    )


def build_queries(config):
    queries = []

    for platform, domains in DOMAINS.items():
        if platform not in config.get("platforms", []):
            continue

        for vehicle in config.get("vehicles", []):
            model = vehicle.get("model", "")
            brand = vehicle.get("brand", "")
            year_from = vehicle.get("year_from") or 2019
            year_to = vehicle.get("year_to")

            domain = domains[0]
            year_text = (
                str(year_from) if year_from == year_to
                else f"{year_from} 二手"
            )

            queries.append({
                "platform": platform,
                "vehicle": vehicle,
                "query": f"site:{domain} {brand} {model} {year_text} 二手车",
            })

    return queries


def main():
    config = load_config()
    init_db()

    queries = build_queries(config)
    found = {}
    errors = []
    searched = 0

    for task in queries:
        platform = task["platform"]
        vehicle = task["vehicle"]

        try:
            results = search_web(task["query"])
            searched += len(results)

            for result in results:
                url = result.get("url", "")
                if platform_for_url(url) != platform:
                    continue

                if already_sent(url) or url in found:
                    continue

                item = evaluate(result, vehicle, config)
                if item:
                    found[url] = item

            log(f"{platform} / {vehicle.get('model')}: 搜索 {len(results)} 条")

        except Exception as exc:
            errors.append(f"{platform} / {vehicle.get('model')}: {exc}")
            log(f"搜索失败：{errors[-1]}")

        time.sleep(1)

    listings = list(found.values())
    sent_count = 0
    push_errors = []

    # Telegram 每条消息独立发送，避免超出长度限制。
    for item in listings:
        try:
            telegram_send(format_listing(item))
            mark_sent(item["url"], item["title"])
            sent_count += 1
            time.sleep(1)
        except Exception as exc:
            push_errors.append(f"{item['title']}: {exc}")
            log(f"推送失败：{push_errors[-1]}")

    summary = (
        "🚗 二手车雷达运行报告\n\n"
        f"搜索结果：{searched} 条\n"
        f"符合条件的候选：{len(listings)} 条\n"
        f"本次成功推送：{sent_count} 条\n"
        f"搜索错误：{len(errors)} 次\n"
        f"推送失败：{len(push_errors)} 次\n\n"
        "注意：未显示价格或年份的车源仍需人工核实。"
    )

    if errors:
        summary += "\n\n搜索错误（最多5条）：\n" + "\n".join(errors[:5])

    if push_errors:
        summary += "\n\n推送错误（最多3条）：\n" + "\n".join(push_errors[:3])

    try:
        telegram_send(summary)
    except Exception as exc:
        log(f"运行报告发送失败：{exc}")

    log(summary.replace("\n", " | "))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""二手车雷达：公开网页搜索、历史记录、Telegram 通知和网页数据导出。"""

import datetime as dt
import html
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "car_radar.sqlite3"
JSON_FILE = BASE_DIR / "car_radar_data.json"
CONFIG_FILE = BASE_DIR / "car_radar_config.json"
TZ = dt.timezone(dt.timedelta(hours=8))
BUDGET = 150000

DEFAULT_CONFIG = {
    "budget_rmb": BUDGET,
    "min_year": 2019,
    "listing_age_days": None,
    "match_mode": "relaxed",
    "results_per_search": 10,
    "request_timeout": 15,
    "search_delay_seconds": 1,
    "models": [
        {"name": "Volvo S60 T4/T5", "queries": ["沃尔沃 S60 T4 二手车", "沃尔沃 S60 T5 二手车"], "min_year": 2020},
        {"name": "Volvo V60", "queries": ["沃尔沃 V60 二手车"], "min_year": 2019},
        {"name": "Mazda CX-50", "queries": ["马自达 CX-50 二手车"], "min_year": 2019},
        {"name": "Toyota RAV4 荣放", "queries": ["丰田 RAV4 荣放 二手车"], "min_year": 2019},
        {"name": "Tesla Model 3", "queries": ["特斯拉 Model 3 二手车"], "min_year": 2023},
        {"name": "Ford Mondeo 双擎运动版", "queries": ["福特 蒙迪欧 双擎运动版 二手车"], "min_year": 2019}
    ],
    "platforms": [
        {"name": "瓜子二手车", "domains": ["guazi.com"]},
        {"name": "懂车帝", "domains": ["dongchedi.com"]},
        {"name": "58同城", "domains": ["58.com"]},
        {"name": "闲鱼", "domains": ["goofish.com", "2.taobao.com"]},
        {"name": "汽车之家", "domains": ["autohome.com.cn"]},
        {"name": "爱卡汽车", "domains": ["xcar.com.cn"]}
    ]
}


def now():
    return dt.datetime.now(TZ).isoformat(timespec="seconds")


def load_config():
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        return DEFAULT_CONFIG

    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        # 以现有配置为主，不覆盖用户已经设置的车型和平台。
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)
        return merged
    except Exception as exc:
        print(f"配置读取失败，使用默认配置：{exc}")
        return DEFAULT_CONFIG


def connect_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with connect_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_listings (
                url TEXT PRIMARY KEY,
                title TEXT,
                sent_at TEXT
            )
        """)

        # 兼容旧版只有两列的 sent_listings 表。
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(sent_listings)"
            ).fetchall()
        }
        if "title" not in columns:
            conn.execute("ALTER TABLE sent_listings ADD COLUMN title TEXT")
        if "sent_at" not in columns:
            conn.execute("ALTER TABLE sent_listings ADD COLUMN sent_at TEXT")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                url TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                model TEXT,
                platform TEXT,
                price_rmb INTEGER,
                year INTEGER,
                snippet TEXT,
                first_seen TEXT,
                last_seen TEXT,
                needs_check INTEGER DEFAULT 0
            )
        """)


def clean_text(value):
    value = re.sub(r"<[^>]*>", " ", value or "")
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        query = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        query = [
            (k, v) for k, v in query
            if not k.lower().startswith("utm_")
            and k.lower() not in ("from", "spm", "share_token")
        ]
        return urllib.parse.urlunsplit(
            (p.scheme or "https", p.netloc, p.path,
             urllib.parse.urlencode(query), "")
        )
    except Exception:
        return url


def fetch_text(url, timeout=15):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7"
        }
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def search_web(query, limit=10, timeout=15):
    """从 DuckDuckGo 网页搜索结果提取标题、链接和摘要。"""
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode(
        {"q": query}
    )
    try:
        page = fetch_text(url, timeout)
    except Exception as exc:
        print(f"搜索失败：{query}；原因：{exc}")
        return []

    results = []
    # 解析常见的 DuckDuckGo HTML 结果结构。
    matches = re.finditer(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>'
        r'(.*?)</a>',
        page, re.I | re.S
    )

    for match in matches:
        link = html.unescape(match.group(1))
        title = clean_text(match.group(2))

        if "uddg=" in link:
            try:
                link = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(link).query
                ).get("uddg", [link])[0]
            except Exception:
                pass

        if not link.startswith("http") or not title:
            continue

        # 尽量取得同一结果块的摘要。
        start = max(0, match.start() - 1200)
        end = min(len(page), match.end() + 1800)
        block = page[start:end]
        sm = re.search(
            r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</',
            block, re.I | re.S
        )
        snippet = clean_text(sm.group(1)) if sm else ""

        results.append({
            "url": normalize_url(link),
            "title": title,
            "snippet": snippet
        })
        if len(results) >= limit:
            break

    # 同一链接去重。
    unique = {}
    for item in results:
        unique.setdefault(item["url"], item)
    return list(unique.values())[:limit]


def platform_for(url, platforms):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    for platform in platforms:
        for domain in platform.get("domains", []):
            if domain.lower() in host:
                return platform["name"]
    return None


def model_matches(text, model):
    t = re.sub(r"\s+", "", text.lower())

    rules = {
        "Volvo S60 T4/T5": ["s60", "沃尔沃s60"],
        "Volvo V60": ["v60", "沃尔沃v60"],
        "Mazda CX-50": ["cx-50", "cx50", "马自达cx50", "马自达cx-50"],
        "Toyota RAV4 荣放": ["rav4", "荣放"],
        "Tesla Model 3": ["model3", "特斯拉model3"],
        "Ford Mondeo 双擎运动版": ["蒙迪欧", "mondeo"]
    }
    aliases = rules.get(model["name"], [model["name"].lower()])
    if not any(alias.replace(" ", "") in t for alias in aliases):
        return False

    if model["name"] == "Ford Mondeo 双擎运动版":
        if not any(word in t for word in ["双擎", "混动", "hybrid"]):
            return False

    return True


def extract_price(text):
    text = (text or "").replace(",", "").replace("，", "")

    patterns = [
        (r"(\d+(?:\.\d+)?)\s*万元", 10000),
        (r"[￥¥]\s*(\d{4,7})(?:元)?", 1),
        (r"(?:售价|价格|报价|仅售)[^\d]{0,10}(\d{4,7})(?:元)?", 1)
    ]
    for pattern, multiplier in patterns:
        match = re.search(pattern, text)
        if match:
            value = int(float(match.group(1)) * multiplier)
            if 1000 <= value <= 10000000:
                return value
    return None


def extract_year(text):
    current_year = dt.datetime.now(TZ).year
    years = [
        int(y) for y in re.findall(r"(20\d{2})\s*年?", text or "")
        if 2010 <= int(y) <= current_year + 1
    ]
    return min(years) if years else None


def is_sent(url):
    with connect_db() as conn:
        return conn.execute(
            "SELECT 1 FROM sent_listings WHERE url=?", (url,)
        ).fetchone() is not None


def mark_sent(url, title):
    with connect_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO sent_listings(url, title, sent_at)
            VALUES (?, ?, ?)
        """, (url, title, now()))


def save_listing(item):
    with connect_db() as conn:
        old = conn.execute(
            "SELECT first_seen FROM listings WHERE url=?",
            (item["url"],)
        ).fetchone()
        first_seen = old["first_seen"] if old else now()

        conn.execute("""
            INSERT INTO listings
            (url,title,model,platform,price_rmb,year,snippet,
             first_seen,last_seen,needs_check)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
                title=excluded.title,
                model=excluded.model,
                platform=excluded.platform,
                price_rmb=COALESCE(excluded.price_rmb,listings.price_rmb),
                year=COALESCE(excluded.year,listings.year),
                snippet=excluded.snippet,
                last_seen=excluded.last_seen,
                needs_check=excluded.needs_check
        """, (
            item["url"], item["title"], item["model"], item["platform"],
            item["price_rmb"], item["year"], item["snippet"],
            first_seen, now(), int(item["needs_check"])
        ))


def send_telegram(message):
    token = os.getenv("TG_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TG_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("未设置 Telegram Secrets，跳过推送。")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode("utf-8")

    try:
        request = urllib.request.Request(url, data=payload)
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        return bool(result.get("ok"))
    except Exception as exc:
        print(f"Telegram 推送失败：{exc}")
        return False


def export_json():
    with connect_db() as conn:
        rows = conn.execute("""
            SELECT url,title,model,platform,price_rmb,year,snippet,
                   first_seen,last_seen,needs_check
            FROM listings
            ORDER BY
                CASE WHEN price_rmb IS NULL THEN 1 ELSE 0 END,
                price_rmb ASC, last_seen DESC
        """).fetchall()

    data = {
        "updatedAt": now(),
        "budgetRmb": BUDGET,
        "count": len(rows),
        "listings": [dict(row) for row in rows]
    }
    JSON_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"已导出 {len(rows)} 条历史车源到 {JSON_FILE.name}")


def main():
    config = load_config()
    init_db()

    budget = int(config.get("budget_rmb", BUDGET))
    models = config.get("models", DEFAULT_CONFIG["models"])
    platforms = config.get("platforms", DEFAULT_CONFIG["platforms"])
    limit = int(config.get("results_per_search", 10))
    timeout = int(config.get("request_timeout", 15))
    delay = float(config.get("search_delay_seconds", 1))

    new_count = 0
    search_count = 0

    for model in models:
        min_year = int(model.get("min_year", config.get("min_year", 2019)))

        for query in model.get("queries", [model["name"] + " 二手车"]):
            for platform in platforms:
                for domain in platform.get("domains", []):
                    # 闲鱼等平台只使用可用于域名检索的域名。
                    if "." not in domain:
                        continue

                    search_query = f"site:{domain} {query}"
                    results = search_web(search_query, limit, timeout)
                    search_count += 1

                    for result in results:
                        actual_platform = platform_for(
                            result["url"], platforms
                        )
                        if actual_platform != platform["name"]:
                            continue

                        text = result["title"] + " " + result["snippet"]
                        if not model_matches(text, model):
                            continue

                        price = extract_price(text)
                        year = extract_year(text)

                        if price is not None and price > budget:
                            continue
                        if year is not None and year < min_year:
                            continue

                        item = {
                            "url": result["url"],
                            "title": result["title"],
                            "model": model["name"],
                            "platform": platform["name"],
                            "price_rmb": price,
                            "year": year,
                            "snippet": result["snippet"],
                            "needs_check": price is None or year is None
                        }

                        new = not is_sent(item["url"])
                        save_listing(item)

                        if new:
                            message = (
                                "🚗 二手车雷达：发现新车源\n"
                                f"车型：{item['model']}\n"
                                f"平台：{item['platform']}\n"
                                f"标题：{item['title']}\n"
                                f"价格：{price:,}元\n" if price else
                                "🚗 二手车雷达：发现新车源\n"
                                f"车型：{item['model']}\n"
                                f"平台：{item['platform']}\n"
                                f"标题：{item['title']}\n价格：待核实\n"
                            )
                            message += (
                                f"年份：{year}年\n" if year else "年份：待核实\n"
                            )
                            message += f"链接：{item['url']}"

                            if send_telegram(message):
                                mark_sent(item["url"], item["title"])
                                new_count += 1

                    time.sleep(delay)

    export_json()
    print(
        f"运行结束。搜索组合：{search_count}；"
        f"成功推送的新车源：{new_count}。"
    )


if __name__ == "__main__":
    main()

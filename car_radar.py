# -*- coding: utf-8 -*-
"""
二手车雷达
功能：多平台公开搜索、车型筛选、预算筛选、SQLite 去重、
Telegram 通知、JSON 网页数据导出。
"""

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
CONFIG_FILE = BASE_DIR / "car_radar_config.json"
DB_FILE = BASE_DIR / "car_radar.db"
JSON_FILE = BASE_DIR / "car_radar_data.json"

BUDGET = 150000
MIN_YEAR = 2019

DEFAULT_CONFIG = {
    "budget_rmb": 150000,
    "min_year": 2019,
    "listing_age_days": None,
    "match_mode": "relaxed",
    "results_per_search": 10,
    "request_timeout": 15,
    "search_delay_seconds": 1,
    "models": [
        {
            "name": "Volvo S60 T4/T5",
            "queries": [
                "沃尔沃 S60 T4 二手车",
                "沃尔沃 S60 T5 二手车"
            ],
            "min_year": 2020
        },
        {
            "name": "Volvo V60",
            "queries": ["沃尔沃 V60 二手车"],
            "min_year": 2019
        },
        {
            "name": "Mazda CX-50",
            "queries": ["马自达 CX-50 二手车"],
            "min_year": 2019
        },
        {
            "name": "Toyota RAV4 荣放",
            "queries": ["丰田 RAV4 荣放 二手车"],
            "min_year": 2019
        },
        {
            "name": "Tesla Model 3",
            "queries": ["特斯拉 Model 3 二手车"],
            "min_year": 2023
        },
        {
            "name": "Ford Mondeo 双擎运动版",
            "queries": ["福特 蒙迪欧 双擎运动版 二手车"],
            "min_year": 2019
        }
    ],
    "platforms": [
        {"name": "瓜子二手车", "domains": ["guazi.com"]},
        {"name": "懂车帝", "domains": ["dongchedi.com"]},
        {"name": "58同城", "domains": ["58.com"]},
        {
            "name": "闲鱼",
            "domains": ["goofish.com", "2.taobao.com"]
        },
        {"name": "汽车之家", "domains": ["autohome.com.cn"]},
        {"name": "爱卡汽车", "domains": ["xcar.com.cn"]}
    ]
}


# --------------------------------------------------
# 配置读取与兼容
# --------------------------------------------------

def normalize_platforms(platforms):
    """同时兼容平台字典列表和平台名称字符串列表。"""

    defaults = {
        p["name"]: p
        for p in DEFAULT_CONFIG["platforms"]
    }

    if not isinstance(platforms, list):
        return DEFAULT_CONFIG["platforms"]

    normalized = []

    for item in platforms:
        if isinstance(item, str):
            name = item.strip()

            if name in defaults:
                normalized.append({
                    "name": defaults[name]["name"],
                    "domains": list(defaults[name]["domains"])
                })
            elif "." in name:
                normalized.append({
                    "name": name,
                    "domains": [name]
                })

        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            domains = item.get("domains", [])

            if isinstance(domains, str):
                domains = [domains]

            domains = [
                str(domain).strip()
                for domain in domains
                if str(domain).strip()
            ]

            if name and domains:
                normalized.append({
                    "name": name,
                    "domains": domains
                })

    # 去除重复平台
    result = []
    seen = set()

    for item in normalized:
        if item["name"] not in seen:
            seen.add(item["name"])
            result.append(item)

    return result or DEFAULT_CONFIG["platforms"]


def load_config():
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(
            json.dumps(
                DEFAULT_CONFIG,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )
        return dict(DEFAULT_CONFIG)

    try:
        cfg = json.loads(
            CONFIG_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(cfg, dict):
            raise ValueError("配置文件必须是 JSON 对象")

        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)

        # 修复 platforms 是字符串列表导致的 AttributeError
        merged["platforms"] = normalize_platforms(
            merged.get("platforms")
        )

        # 兼容旧配置的 vehicles 字段
        if cfg.get("vehicles") and not cfg.get("models"):
            models = []

            for vehicle in cfg["vehicles"]:
                if not isinstance(vehicle, dict):
                    continue

                brand = str(vehicle.get("brand", "")).strip()
                model_name = str(
                    vehicle.get("model", "")
                ).strip()

                if not model_name:
                    continue

                queries = [
                    f"{brand} {model_name} 二手车".strip()
                ]

                models.append({
                    "name": f"{brand} {model_name}".strip(),
                    "queries": queries,
                    "min_year": int(
                        vehicle.get(
                            "year_from",
                            merged.get("min_year", MIN_YEAR)
                        )
                    ),
                    "max_year": vehicle.get("year_to")
                })

            if models:
                merged["models"] = models

        # 兼容旧配置字段
        if cfg.get("max_price") is not None:
            merged["budget_rmb"] = cfg["max_price"]

        if cfg.get("production_date_from"):
            merged["min_year"] = int(
                str(cfg["production_date_from"])[:4]
            )

        return merged

    except Exception as exc:
        print(f"配置读取失败，使用默认配置：{exc}")
        return dict(DEFAULT_CONFIG)


# --------------------------------------------------
# 数据库
# --------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                url TEXT PRIMARY KEY,
                title TEXT,
                model TEXT,
                platform TEXT,
                price_rmb INTEGER,
                year INTEGER,
                snippet TEXT,
                needs_check INTEGER DEFAULT 0,
                first_seen TEXT,
                last_seen TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_listings (
                url TEXT PRIMARY KEY,
                title TEXT,
                sent_at TEXT
            )
        """)

        # 兼容旧数据库的 listings 表结构
        columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(listings)"
            ).fetchall()
        }

        required_columns = {
            "title": "TEXT",
            "model": "TEXT",
            "platform": "TEXT",
            "price_rmb": "INTEGER",
            "year": "INTEGER",
            "snippet": "TEXT",
            "needs_check": "INTEGER DEFAULT 0",
            "first_seen": "TEXT",
            "last_seen": "TEXT"
        }

        for column, column_type in required_columns.items():
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE listings ADD COLUMN "
                    f"{column} {column_type}"
                )

        # 兼容旧数据库的 sent_listings 表结构
        sent_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(sent_listings)"
            ).fetchall()
        }

        for column, column_type in {
            "title": "TEXT",
            "sent_at": "TEXT"
        }.items():
            if column not in sent_columns:
                conn.execute(
                    f"ALTER TABLE sent_listings ADD COLUMN "
                    f"{column} {column_type}"
                )


def is_sent(url):
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM sent_listings WHERE url = ?",
            (url,)
        ).fetchone()
        return row is not None


def mark_sent(url, title):
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO sent_listings
            (url, title, sent_at)
            VALUES (?, ?, ?)
        """, (
            url,
            title,
            dt.datetime.now(dt.timezone.utc).isoformat()
        ))


def save_listing(item):
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    with get_db() as conn:
        conn.execute("""
            INSERT INTO listings (
                url, title, model, platform, price_rmb,
                year, snippet, needs_check, first_seen, last_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                model = excluded.model,
                platform = excluded.platform,
                price_rmb = excluded.price_rmb,
                year = excluded.year,
                snippet = excluded.snippet,
                needs_check = excluded.needs_check,
                last_seen = excluded.last_seen
        """, (
            item["url"],
            item["title"],
            item["model"],
            item["platform"],
            item.get("price_rmb"),
            item.get("year"),
            item.get("snippet", ""),
            int(item.get("needs_check", False)),
            now,
            now
        ))


# --------------------------------------------------
# 文本清理与链接处理
# --------------------------------------------------

def clean_text(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<script.*?</script>", " ", value,
                   flags=re.I | re.S)
    value = re.sub(r"<style.*?</style>", " ", value,
                   flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_url(url):
    try:
        parsed = urllib.parse.urlsplit(url)

        query_items = urllib.parse.parse_qsl(
            parsed.query,
            keep_blank_values=True
        )

        ignored = (
            "utm_", "spm", "from", "source",
            "track", "clickid"
        )

        query_items = [
            (key, value)
            for key, value in query_items
            if not key.lower().startswith(ignored)
        ]

        query = urllib.parse.urlencode(query_items)

        return urllib.parse.urlunsplit((
            parsed.scheme or "https",
            parsed.netloc,
            parsed.path.rstrip("/") or "/",
            query,
            ""
        ))

    except Exception:
        return url


# --------------------------------------------------
# 网页搜索
# --------------------------------------------------

def fetch_text(url, timeout=15):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
        }
    )

    with urllib.request.urlopen(
        request, timeout=timeout
    ) as response:
        raw = response.read()

        charset = response.headers.get_content_charset() or "utf-8"

        try:
            return raw.decode(charset, errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")


def search_web(query, limit=10, timeout=15):
    """通过 DuckDuckGo HTML 搜索公开网页。"""

    search_url = (
        "https://html.duckduckgo.com/html/?q="
        + urllib.parse.quote(query)
    )

    try:
        page = fetch_text(search_url, timeout)
    except Exception as exc:
        print(f"搜索失败：{query}；{exc}")
        return []

    results = []

    # 解析搜索结果链接
    pattern = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*'
        r'href="([^"]+)"[^>]*>(.*?)</a>',
        re.I | re.S
    )

    matches = list(pattern.finditer(page))

    for index, match in enumerate(matches):
        if len(results) >= limit:
            break

        raw_url = html.unescape(match.group(1))
        title = clean_text(match.group(2))

        parsed = urllib.parse.urlsplit(raw_url)

        if "duckduckgo.com" in (parsed.hostname or "").lower():
            params = urllib.parse.parse_qs(parsed.query)
            if params.get("uddg"):
                raw_url = params["uddg"][0]

        if not raw_url.startswith(("http://", "https://")):
            continue

        # 尝试读取当前结果的摘要
        next_start = match.end()
        next_end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(page)
        )

        block = page[next_start:next_end]

        snippet_match = re.search(
            r'class="[^"]*result__snippet[^"]*"[^>]*>'
            r'(.*?)</(?:a|div|span)>',
            block,
            re.I | re.S
        )

        snippet = (
            clean_text(snippet_match.group(1))
            if snippet_match else ""
        )

        results.append({
            "url": normalize_url(raw_url),
            "title": title,
            "snippet": snippet
        })

    return results


# --------------------------------------------------
# 平台识别
# --------------------------------------------------

def platform_for(url, platforms):
    host = (
        urllib.parse.urlsplit(url).hostname or ""
    ).lower()

    for platform in platforms:
        # 再次兼容字符串格式，避免类型错误
        if isinstance(platform, str):
            name = platform.strip()

            platform = next(
                (
                    p for p in DEFAULT_CONFIG["platforms"]
                    if p["name"] == name
                ),
                {
                    "name": name,
                    "domains": [name] if "." in name else []
                }
            )

        if not isinstance(platform, dict):
            continue

        domains = platform.get("domains", [])

        if isinstance(domains, str):
            domains = [domains]

        for domain in domains:
            if not isinstance(domain, str):
                continue

            domain = domain.lower().strip()

            if domain and (
                host == domain or host.endswith("." + domain)
            ):
                return platform.get("name")

    return None


# --------------------------------------------------
# 车型、价格、年份识别
# --------------------------------------------------

def model_matches(text, model):
    text = text.lower()

    name = str(model.get("name", "")).lower()
    aliases = {
        "volvo s60 t4/t5": [
            "沃尔沃", "s60", "t4", "t5"
        ],
        "volvo v60": ["沃尔沃", "v60"],
        "mazda cx-50": ["马自达", "cx-50"],
        "toyota rav4 荣放": ["rav4", "荣放"],
        "tesla model 3": ["特斯拉", "model 3"],
        "ford mondeo 双擎运动版": [
            "福特", "蒙迪欧"
        ]
    }

    required = aliases.get(name)

    if required:
        if name == "volvo s60 t4/t5":
            return (
                ("沃尔沃" in text or "volvo" in text)
                and "s60" in text
                and ("t4" in text or "t5" in text)
            )

        if name == "volvo v60":
            return (
                ("沃尔沃" in text or "volvo" in text)
                and "v60" in text
            )

        if name == "mazda cx-50":
            return (
                ("马自达" in text or "mazda" in text)
                and ("cx-50" in text or "cx50" in text)
            )

        if name == "toyota rav4 荣放":
            return (
                ("rav4" in text)
                and ("荣放" in text or "丰田" in text
                     or "toyota" in text)
            )

        if name == "tesla model 3":
            return (
                ("特斯拉" in text or "tesla" in text)
                and ("model 3" in text or "model3" in text)
            )

        if name == "ford mondeo 双擎运动版":
            return (
                ("福特" in text or "ford" in text)
                and ("蒙迪欧" in text or "mondeo" in text)
            )

    return bool(name and name in text)


def extract_price(text):
    text = str(text or "").replace(",", "")

    patterns = [
        r"(?:售价|价格|报价|仅售|卖)\s*[:：]?\s*"
        r"(\d+(?:\.\d+)?)\s*万元",
        r"(\d+(?:\.\d+)?)\s*万元",
        r"(?:售价|价格|报价|仅售|卖)\s*[:：]?\s*"
        r"(\d{4,7})\s*元",
        r"￥\s*(\d{4,7})",
        r"¥\s*(\d{4,7})"
    ]

    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text)

        if not match:
            continue

        try:
            value = float(match.group(1))

            if index in (0, 1):
                value *= 10000

            value = int(value)

            if 1000 <= value <= 100000000:
                return value

        except (ValueError, OverflowError):
            continue

    return None


def extract_year(text):
    text = str(text or "")

    patterns = [
        r"(20\d{2})\s*年",
        r"(20\d{2})款",
        r"(20\d{2})\s*款"
    ]

    for pattern in patterns:
        match = re.search(pattern, text)

        if match:
            year = int(match.group(1))

            if 2000 <= year <= dt.datetime.now().year + 1:
                return year

    return None


# --------------------------------------------------
# Telegram 推送
# --------------------------------------------------

def send_telegram(message):
    token = os.getenv("TG_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TG_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("未配置 TG_BOT_TOKEN 或 TG_CHAT_ID，跳过推送。")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(
                response.read().decode("utf-8")
            )

        if data.get("ok"):
            return True

        print(f"Telegram 推送失败：{data}")
        return False

    except Exception as exc:
        print(f"Telegram 请求异常：{exc}")
        return False


# --------------------------------------------------
# 导出网页 JSON
# --------------------------------------------------

def export_json():
    with get_db() as conn:
        rows = conn.execute("""
            SELECT url, title, model, platform, price_rmb,
                   year, snippet, needs_check, first_seen, last_seen
            FROM listings
            ORDER BY last_seen DESC
        """).fetchall()

    data = []

    for row in rows:
        item = dict(row)
        item["needs_check"] = bool(item["needs_check"])
        data.append(item)

    output = {
        "updatedAt": dt.datetime.now(
            dt.timezone.utc
        ).isoformat(),
        "count": len(data),
        "listings": data
    }

    JSON_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"已导出 {len(data)} 条车源：{JSON_FILE.name}")


# --------------------------------------------------
# 主程序
# --------------------------------------------------

def main():
    config = load_config()
    init_db()

    budget = int(
        config.get("budget_rmb", BUDGET) or BUDGET
    )

    models = config.get("models") or DEFAULT_CONFIG["models"]
    platforms = normalize_platforms(
        config.get("platforms")
    )

    limit = int(config.get("results_per_search", 10))
    timeout = int(config.get("request_timeout", 15))
    delay = float(config.get("search_delay_seconds", 1))

    new_count = 0
    search_count = 0

    print("=" * 50)
    print("二手车雷达启动")
    print(f"预算：{budget:,} 元")
    print(f"车型数：{len(models)}")
    print(f"平台数：{len(platforms)}")
    print("=" * 50)

    for model in models:
        if not isinstance(model, dict):
            continue

        model_name = str(model.get("name", "")).strip()

        if not model_name:
            continue

        min_year = int(
            model.get(
                "min_year",
                config.get("min_year", MIN_YEAR)
            ) or MIN_YEAR
        )

        max_year = model.get("max_year")

        queries = model.get("queries") or [
            model_name + " 二手车"
        ]

        if isinstance(queries, str):
            queries = [queries]

        for query in queries:
            for platform in platforms:
                if not isinstance(platform, dict):
                    continue

                platform_name = platform["name"]
                domains = platform.get("domains", [])

                if isinstance(domains, str):
                    domains = [domains]

                for domain in domains:
                    domain = str(domain).strip()

                    if "." not in domain:
                        continue

                    search_query = f"site:{domain} {query}"

                    print(f"搜索：{search_query}")

                    results = search_web(
                        search_query,
                        limit,
                        timeout
                    )

                    search_count += 1

                    for result in results:
                        actual_platform = platform_for(
                            result["url"], platforms
                        )

                        if actual_platform != platform_name:
                            continue

                        text = (
                            result["title"] + " "
                            + result["snippet"]
                        )

                        if not model_matches(text, model):
                            continue

                        price = extract_price(text)
                        year = extract_year(text)

                        if price is not None and price > budget:
                            continue

                        if year is not None and year < min_year:
                            continue

                        if max_year is not None and year is not None:
                            if year > int(max_year):
                                continue

                        item = {
                            "url": normalize_url(result["url"]),
                            "title": result["title"],
                            "model": model_name,
                            "platform": platform_name,
                            "price_rmb": price,
                            "year": year,
                            "snippet": result["snippet"],
                            "needs_check": (
                                price is None or year is None
                            )
                        }

                        save_listing(item)

                        if is_sent(item["url"]):
                            continue

                        price_text = (
                            f"{price:,} 元"
                            if price is not None
                            else "待核实"
                        )

                        year_text = (
                            f"{year} 年"
                            if year is not None
                            else "待核实"
                        )

                        message = (
                            "🚗 二手车雷达：发现新车源\n"
                            f"车型：{item['model']}\n"
                            f"平台：{item['platform']}\n"
                            f"标题：{item['title']}\n"
                            f"价格：{price_text}\n"
                            f"年份：{year_text}\n"
                            f"链接：{item['url']}"
                        )

                        if send_telegram(message):
                            mark_sent(
                                item["url"],
                                item["title"]
                            )
                            new_count += 1

                    time.sleep(delay)

    export_json()

    print("=" * 50)
    print("运行结束")
    print(f"搜索组合：{search_count}")
    print(f"成功推送的新车源：{new_count}")
    print("=" * 50)


if __name__ == "__main__":
    main()

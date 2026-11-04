# -*- coding: utf-8 -*-
import datetime as dt
import html
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "car_radar_config.json"
DB_FILE = BASE_DIR / "car_radar.db"
JSON_FILE = BASE_DIR / "car_radar_data.json"

DEFAULT_CONFIG = {
    "budget_rmb": 150000,
    "min_year": 2019,
    "results_per_search": 10,
    "request_timeout": 20,
    "search_delay_seconds": 1,
    "models": [
        {"name": "Volvo S60 T4/T5", "queries": ["沃尔沃 S60 T4 二手车", "沃尔沃 S60 T5 二手车"], "min_year": 2020, "max_year": 2020},
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


def load_config():
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        return dict(DEFAULT_CONFIG)

    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)

        if cfg.get("max_price") is not None:
            merged["budget_rmb"] = cfg["max_price"]

        if cfg.get("production_date_from"):
            merged["min_year"] = int(str(cfg["production_date_from"])[:4])

        if cfg.get("vehicles") and not cfg.get("models"):
            merged["models"] = []
            for v in cfg["vehicles"]:
                brand = str(v.get("brand", "")).strip()
                model = str(v.get("model", "")).strip()
                if model:
                    merged["models"].append({
                        "name": f"{brand} {model}".strip(),
                        "queries": [f"{brand} {model} 二手车".strip()],
                        "min_year": int(v.get("year_from", 2019)),
                        "max_year": v.get("year_to")
                    })

        defaults = {p["name"]: p for p in DEFAULT_CONFIG["platforms"]}
        platforms = []

        for p in merged.get("platforms", []):
            if isinstance(p, str):
                if p in defaults:
                    platforms.append(defaults[p])
                elif "." in p:
                    platforms.append({"name": p, "domains": [p]})
            elif isinstance(p, dict):
                domains = p.get("domains", [])
                if isinstance(domains, str):
                    domains = [domains]
                if p.get("name") and domains:
                    platforms.append({
                        "name": p["name"],
                        "domains": domains
                    })

        merged["platforms"] = platforms or DEFAULT_CONFIG["platforms"]
        return merged

    except Exception as exc:
        print(f"配置读取失败：{exc}")
        return dict(DEFAULT_CONFIG)


def db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
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

        columns = {
            r["name"] for r in conn.execute(
                "PRAGMA table_info(listings)"
            ).fetchall()
        }

        for name, kind in {
            "title": "TEXT",
            "model": "TEXT",
            "platform": "TEXT",
            "price_rmb": "INTEGER",
            "year": "INTEGER",
            "snippet": "TEXT",
            "needs_check": "INTEGER DEFAULT 0",
            "first_seen": "TEXT",
            "last_seen": "TEXT"
        }.items():
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE listings ADD COLUMN {name} {kind}"
                )

        sent_columns = {
            r["name"] for r in conn.execute(
                "PRAGMA table_info(sent_listings)"
            ).fetchall()
        }

        for name, kind in {
            "title": "TEXT",
            "sent_at": "TEXT"
        }.items():
            if name not in sent_columns:
                conn.execute(
                    f"ALTER TABLE sent_listings ADD COLUMN {name} {kind}"
                )


def clean_text(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<script\b.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(url):
    try:
        p = urllib.parse.urlsplit(url)
        query = [
            (k, v) for k, v in urllib.parse.parse_qsl(p.query)
            if not k.lower().startswith(("utm_", "spm", "from", "source"))
        ]
        return urllib.parse.urlunsplit((
            p.scheme or "https", p.netloc,
            p.path.rstrip("/") or "/",
            urllib.parse.urlencode(query), ""
        ))
    except Exception:
        return url


def fetch_text(url, timeout=20):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Chrome/131.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def search_web(query, limit=10, timeout=20):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)

    try:
        page = fetch_text(url, timeout)
    except Exception as exc:
        print(f"搜索失败：{query}；{exc}")
        return []

    results = []
    pattern = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"'
        r'[^>]*>(.*?)</a>',
        re.I | re.S
    )

    for match in pattern.finditer(page):
        link = html.unescape(match.group(1))
        title = clean_text(match.group(2))

        parsed = urllib.parse.urlsplit(link)
        if "duckduckgo.com" in (parsed.hostname or ""):
            params = urllib.parse.parse_qs(parsed.query)
            link = params.get("uddg", [link])[0]

        if not link.startswith(("http://", "https://")):
            continue

        snippet = ""
        results.append({
            "url": normalize_url(link),
            "title": title,
            "snippet": snippet
        })

        if len(results) >= limit:
            break

    print(f"搜索解析结果：{query} → {len(results)} 条")
    return results


def platform_for(url, platforms):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()

    for p in platforms:
        if isinstance(p, str):
            continue

        domains = p.get("domains", [])
        if isinstance(domains, str):
            domains = [domains]

        for domain in domains:
            domain = str(domain).lower().strip()
            if host == domain or host.endswith("." + domain):
                return p.get("name")

    return None


def model_matches(text, model):
    text = text.lower()
    name = model.get("name", "").lower()

    if "s60" in name:
        return (
            ("沃尔沃" in text or "volvo" in text)
            and "s60" in text
            and ("t4" in text or "t5" in text)
        )

    if "v60" in name:
        return ("沃尔沃" in text or "volvo" in text) and "v60" in text

    if "cx-50" in name or "cx50" in name:
        return (
            ("马自达" in text or "mazda" in text)
            and ("cx-50" in text or "cx50" in text)
        )

    if "rav4" in name or "荣放" in name:
        return "rav4" in text and (
            "丰田" in text or "toyota" in text or "荣放" in text
        )

    if "model 3" in name:
        return (
            ("特斯拉" in text or "tesla" in text)
            and ("model 3" in text or "model3" in text)
        )

    if "mondeo" in name or "蒙迪欧" in name:
        return (
            ("福特" in text or "ford" in text)
            and ("蒙迪欧" in text or "mondeo" in text)
        )

    return name in text


def extract_price(text):
    text = str(text or "").replace(",", "")
    patterns = [
        (r"(\d+(?:\.\d+)?)\s*万元", 10000),
        (r"[￥¥]\s*(\d{4,7})", 1),
        (r"(\d{4,7})\s*元", 1)
    ]

    for pattern, multiplier in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                price = int(float(match.group(1)) * multiplier)
                if 1000 <= price <= 100000000:
                    return price
            except ValueError:
                pass

    return None


def extract_year(text):
    for pattern in (r"(20\d{2})\s*年", r"(20\d{2})\s*款"):
        match = re.search(pattern, str(text or ""))
        if match:
            year = int(match.group(1))
            if 2000 <= year <= dt.datetime.now().year + 1:
                return year
    return None


def save_listing(item):
    now = dt.datetime.now(dt.timezone.utc).isoformat()

    with db() as conn:
        conn.execute("""
            INSERT INTO listings (
                url,title,model,platform,price_rmb,year,
                snippet,needs_check,first_seen,last_seen
            )
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(url) DO UPDATE SET
                title=excluded.title,
                model=excluded.model,
                platform=excluded.platform,
                price_rmb=excluded.price_rmb,
                year=excluded.year,
                snippet=excluded.snippet,
                needs_check=excluded.needs_check,
                last_seen=excluded.last_seen
        """, (
            item["url"], item["title"], item["model"],
            item["platform"], item["price_rmb"], item["year"],
            item["snippet"], int(item["needs_check"]), now, now
        ))


def export_json():
    with db() as conn:
        rows = conn.execute("""
            SELECT url,title,model,platform,price_rmb,year,
                   snippet,needs_check,first_seen,last_seen
            FROM listings
            ORDER BY last_seen DESC
        """).fetchall()

    listings = []
    for row in rows:
        item = dict(row)
        item["needs_check"] = bool(item["needs_check"])
        listings.append(item)

    output = {
        "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "count": len(listings),
        "listings": listings
    }

    JSON_FILE.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    print(f"已导出 {len(listings)} 条车源：{JSON_FILE.name}")
    return len(listings)


def send_telegram(message):
    token = os.getenv("TG_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TG_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("未配置 TG_BOT_TOKEN 或 TG_CHAT_ID")
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
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))

        if result.get("ok"):
            return True

        print(f"Telegram 推送失败：{result}")
    except Exception as exc:
        print(f"Telegram 请求失败：{exc}")

    return False


def main():
    config = load_config()
    init_db()

    budget = int(config.get("budget_rmb", 150000))
    models = config.get("models") or DEFAULT_CONFIG["models"]
    platforms = config.get("platforms") or DEFAULT_CONFIG["platforms"]
    limit = int(config.get("results_per_search", 10))
    timeout = int(config.get("request_timeout", 20))
    delay = float(config.get("search_delay_seconds", 1))

    search_count = 0
    parsed_count = 0
    matched_count = 0

    print("二手车雷达启动")
    print(f"预算：{budget:,} 元")

    for model in models:
        model_name = model.get("name", "")
        min_year = int(model.get("min_year", config.get("min_year", 2019)))
        max_year = model.get("max_year")
        queries = model.get("queries") or [model_name + " 二手车"]

        if isinstance(queries, str):
            queries = [queries]

        for query in queries:
            for platform in platforms:
                for domain in platform.get("domains", []):
                    search_query = f"site:{domain} {query}"
                    results = search_web(search_query, limit, timeout)
                    search_count += 1
                    parsed_count += len(results)

                    for result in results:
                        actual_platform = platform_for(result["url"], platforms)
                        if actual_platform != platform["name"]:
                            continue

                        text = result["title"] + " " + result["snippet"]

                        if not model_matches(text, model):
                            continue

                        matched_count += 1
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
                            "url": result["url"],
                            "title": result["title"],
                            "model": model_name,
                            "platform": platform["name"],
                            "price_rmb": price,
                            "year": year,
                            "snippet": result["snippet"],
                            "needs_check": price is None or year is None
                        }

                        save_listing(item)

                    time.sleep(delay)

    total = export_json()

    # 每次运行只发送一条汇总通知
    page_url = (
        "https://english-45min.github.io/Auto/car_radar.html"
    )

    message = (
        "🚗 二手车雷达本次搜索完成\n"
        f"搜索组合：{search_count}\n"
        f"解析结果：{parsed_count}\n"
        f"车型匹配：{matched_count}\n"
        f"累计车源：{total}\n\n"
        f"点击查看全部车源：\n{page_url}"
    )

    send_telegram(message)

    print("=" * 45)
    print(f"搜索组合：{search_count}")
    print(f"解析结果：{parsed_count}")
    print(f"车型匹配：{matched_count}")
    print(f"累计车源：{total}")
    print("运行结束")


if __name__ == "__main__":
    main()


# -*- coding: utf-8 -*-
"""
二手车雷达
功能：Bing RSS 搜索、Bing 跳转链接解析、车源筛选、详情页核验、
Telegram 推送、SQLite 去重。

注意：
1. Bing 搜索不是二手车平台官方库存 API，无法保证覆盖全部车源。
2. 搜索结果发布时间不等于车辆真实上架时间。
3. 未识别的价格、生产日期或年款会标记为待核实。
4. 不会自动删除旧的 SQLite 推送记录。
"""

import base64
import html
import json
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "car_radar_config.json"
DB_FILE = BASE_DIR / "car_radar.sqlite3"
BEIJING_TZ = timezone(timedelta(hours=8))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

PLATFORM_DOMAINS = {
    "瓜子二手车": ["guazi.com"],
    "懂车帝": ["dongchedi.com"],
    "58同城": ["58.com"],
    "闲鱼": ["goofish.com", "xianyu.com", "2.taobao.com"],
    "汽车之家": ["che168.com", "autohome.com.cn"],
    "爱卡汽车": ["used.xcar.com.cn", "xcar.com.cn"],
}

# 已统一使用 CX-50，不再要求“智行版”。
VEHICLE_SEARCH_TERMS = {
    "S60 T4/T5": [
        "沃尔沃 S60 二手车",
        "Volvo S60 T4 T5",
    ],
    "V60": [
        "沃尔沃 V60 二手车",
        "Volvo V60 used",
    ],
    "CX-50": [
        "马自达 CX-50 二手车",
        "Mazda CX-50 used",
    ],
    "RAV4 荣放": [
        "丰田 RAV4 荣放 二手车",
        "Toyota RAV4 used",
    ],
    "Model 3": [
        "特斯拉 Model 3 二手车",
        "Tesla Model 3 used",
    ],
    "蒙迪欧双擎运动版": [
        "福特 蒙迪欧 双擎 二手车",
        "Ford Mondeo Hybrid used",
    ],
}


def log(message):
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def load_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"找不到配置文件：{CONFIG_FILE.name}")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    defaults = {
        "min_price": None,
        "max_price": 150000,
        "production_date_from": "2019-01-01",
        "production_date_to": None,
        "listing_age_days": None,
        "listing_age_custom_days": None,
        "match_mode": "relaxed",
        "platforms": list(PLATFORM_DOMAINS),
        "vehicles": [],
    }

    for key, value in defaults.items():
        config.setdefault(key, value)

    if config["match_mode"] not in ("relaxed", "strict"):
        raise ValueError("match_mode 只能是 relaxed 或 strict")

    return config


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_listings (
                url TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL
            )
        """)


def already_sent(url):
    with sqlite3.connect(DB_FILE) as conn:
        return conn.execute(
            "SELECT 1 FROM sent_listings WHERE url = ?",
            (url,),
        ).fetchone() is not None


def mark_sent(url):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_listings(url, sent_at) VALUES (?, ?)",
            (url, datetime.now(BEIJING_TZ).isoformat()),
        )


def decode_bing_url(url):
    """
    解析 Bing 搜索结果中的跳转链接。

    Bing 部分链接会把真实地址放在 u= 参数中，
    形式可能是 a1 + URL-safe Base64 编码。
    也兼容直接使用 url= 参数的情况。
    """
    url = html.unescape((url or "").strip())

    # 最多解析两层，防止嵌套跳转。
    for _ in range(2):
        try:
            parsed = urllib.parse.urlsplit(url)
            host = (parsed.hostname or "").lower()

            if not (
                host == "bing.com"
                or host.endswith(".bing.com")
            ):
                break

            params = urllib.parse.parse_qs(parsed.query)
            changed = False

            for key in ("url", "u"):
                values = params.get(key, [])
                if not values:
                    continue

                value = html.unescape(values[0]).strip()

                if value.startswith(("http://", "https://")):
                    url = value
                    changed = True
                    break

                if key == "u":
                    token = value

                    # Bing 常见格式为 a1 + Base64 字符串。
                    if token.startswith("a1"):
                        token = token[2:]

                    try:
                        token += "=" * (-len(token) % 4)
                        decoded = base64.urlsafe_b64decode(
                            token.encode("ascii")
                        ).decode("utf-8", errors="ignore")

                        if decoded.startswith(("http://", "https://")):
                            url = decoded
                            changed = True
                            break
                    except Exception:
                        pass

            if not changed:
                break

        except Exception:
            break

    return url


def normalize_url(url):
    """解开 Bing 跳转地址，并清除常见追踪参数。"""
    url = decode_bing_url(url)

    try:
        p = urllib.parse.urlsplit(url)

        if p.scheme not in ("http", "https") or not p.hostname:
            return url

        host = p.hostname.lower().rstrip(".")
        port = p.port

        netloc = host
        if port and not (
            (p.scheme == "http" and port == 80)
            or (p.scheme == "https" and port == 443)
        ):
            netloc = f"{host}:{port}"

        remove = {
            "utm_source", "utm_medium", "utm_campaign",
            "utm_content", "utm_term", "spm", "from", "ref",
        }

        query = [
            (k, v)
            for k, v in urllib.parse.parse_qsl(
                p.query, keep_blank_values=True
            )
            if k.lower() not in remove
        ]

        return urllib.parse.urlunsplit((
            p.scheme.lower(),
            netloc,
            p.path or "/",
            urllib.parse.urlencode(query),
            "",
        ))

    except Exception:
        return url


def get_host(url):
    try:
        url = decode_bing_url(url)
        return (
            urllib.parse.urlsplit(url).hostname or ""
        ).lower().rstrip(".")
    except Exception:
        return ""


def url_matches_platform(url, platform):
    url = normalize_url(url)
    host = get_host(url)

    return any(
        host == domain or host.endswith("." + domain)
        for domain in PLATFORM_DOMAINS.get(platform, [])
    )


def fetch_rss(query):
    params = urllib.parse.urlencode({
        "q": query,
        "format": "rss",
        "count": "10",
        "setlang": "zh-hans",
        "cc": "cn",
    })

    url = "https://www.bing.com/search?" + params
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml,application/xml,text/xml,*/*",
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()

    root = ET.fromstring(raw)
    results = []

    for node in root.findall(".//item"):
        title = html.unescape(node.findtext("title", "")).strip()
        link = html.unescape(node.findtext("link", "")).strip()
        description = html.unescape(
            node.findtext("description", "")
        ).strip()
        pub_date = node.findtext("pubDate", "").strip()

        link = normalize_url(link)

        # 若仍是 Bing 跳转地址，尝试跟随 HTTP 跳转。
        if "bing.com" in get_host(link):
            try:
                request = urllib.request.Request(
                    link,
                    headers={"User-Agent": USER_AGENT},
                )
                with urllib.request.urlopen(
                    request, timeout=8
                ) as response:
                    link = normalize_url(response.geturl())
            except Exception:
                pass

        results.append({
            "title": title,
            "url": link,
            "description": description,
            "pub_date": pub_date,
        })

    return results


def strip_html(value):
    value = html.unescape(value or "")
    value = re.sub(r"(?is)<script.*?>.*?</script>", " ", value)
    value = re.sub(r"(?is)<style.*?>.*?</style>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def fetch_page_text(url):
    """
    尝试读取公开车源页面。
    若网站要求登录、使用 JavaScript 或阻止自动访问，则返回空文本。
    """
    if not url or "bing.com" in get_host(url):
        return ""

    try:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*",
            },
        )

        with urllib.request.urlopen(request, timeout=8) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return ""
            raw = response.read(900_000)

        page = raw.decode("utf-8", errors="ignore")
        if len(page.strip()) < 100:
            page = raw.decode("gb18030", errors="ignore")

        return strip_html(page)[:30000]

    except Exception:
        return ""


def combined_text(item):
    return (
        f"{item.get('title', '')} "
        f"{item.get('description', '')} "
        f"{item.get('page_text', '')}"
    )


def vehicle_matches(text, vehicle):
    t = (text or "").lower()
    brand = str(vehicle.get("brand", "")).strip()
    model = str(vehicle.get("model", "")).strip()

    brand_aliases = {
        "沃尔沃": ["沃尔沃", "volvo"],
        "马自达": ["马自达", "mazda"],
        "丰田": ["丰田", "toyota"],
        "特斯拉": ["特斯拉", "tesla"],
        "福特": ["福特", "ford"],
    }

    aliases = brand_aliases.get(brand, [brand.lower()])
    brand_found = any(a.lower() in t for a in aliases if a)

    # 某些英文车源标题会省略中文品牌，允许用车型关键词继续核验。
    if brand and not brand_found:
        if model not in ("V60", "S60 T4/T5", "CX-50", "Model 3"):
            return False

    if model == "S60 T4/T5":
        return "s60" in t or "s 60" in t

    if model == "V60":
        return "v60" in t or "v 60" in t

    if model == "CX-50":
        return any(x in t for x in ("cx-50", "cx50", "cx 50"))

    if model == "RAV4 荣放":
        return any(x in t for x in ("rav4", "荣放", "荣枫"))

    if model == "Model 3":
        return "model 3" in t or "model3" in t

    if model == "蒙迪欧双擎运动版":
        return "蒙迪欧" in t or "mondeo" in t

    return bool(model) and model.lower() in t


def looks_like_listing(item):
    text = combined_text(item).lower()
    url = item.get("url", "").lower()

    negative = (
        "百科", "车型大全", "配置表", "参数配置",
        "排行榜", "新车上市", "试驾评测",
    )
    positive = (
        "二手车", "出售", "转让", "个人车", "车源",
        "万元", "万公里", "上牌", "过户", "急售",
        "可议价", "在售", "二手", "卖车",
    )

    if any(x in text for x in negative) and not any(
        x in text for x in positive
    ):
        return False

    if any(x in text for x in positive):
        return True

    listing_paths = (
        "/used", "/ershou", "/car/", "/vehicle/",
        "/detail", "/dealer", "/listing", "/item", "/sale",
    )

    return any(x in url for x in listing_paths)


def parse_price(text):
    patterns = [
        (r"([0-9]+(?:\.[0-9]+)?)\s*万元", True),
        (
            r"(?:售价|价格|报价|仅售|卖价|车价)\s*[:：]?\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*万(?:元)?",
            True,
        ),
        (r"[￥¥]\s*([0-9,]+)\s*元?", False),
        (
            r"(?:售价|价格|报价|卖价|车价)\s*[:：]?\s*"
            r"([0-9,]+)\s*元",
            False,
        ),
    ]

    for pattern, wan in patterns:
        match = re.search(pattern, text or "", re.I)
        if not match:
            continue

        try:
            value = float(match.group(1).replace(",", ""))
            if wan:
                value *= 10000
            if 1000 <= value <= 100000000:
                return int(value)
        except ValueError:
            continue

    return None


def parse_production_date(text):
    patterns = [
        r"(?:生产日期|出厂日期|生产时间)\s*[:：]?\s*"
        r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?",
        r"(?:生产日期|出厂日期|生产时间)\s*[:：]?\s*"
        r"(20\d{2})年(\d{1,2})月",
    ]

    for index, pattern in enumerate(patterns):
        match = re.search(pattern, text or "")
        if not match:
            continue

        try:
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3)) if index == 0 else 1
            return date(year, month, day)
        except (ValueError, TypeError):
            continue

    return None


def parse_model_year(text):
    patterns = (
        r"(20\d{2})\s*年款",
        r"(20\d{2})\s*款",
    )

    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            year = int(match.group(1))
            if 2010 <= year <= date.today().year + 1:
                return year

    return None


def parse_listing_date(item):
    pub_date = item.get("pub_date", "")

    if pub_date:
        try:
            dt = parsedate_to_datetime(pub_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(BEIJING_TZ).date()
        except Exception:
            pass

    text = combined_text(item)
    today = datetime.now(BEIJING_TZ).date()

    if "今天" in text or "刚刚" in text:
        return today
    if "昨天" in text:
        return today - timedelta(days=1)

    match = re.search(r"(\d{1,3})\s*天前", text)
    if match:
        return today - timedelta(days=int(match.group(1)))

    return None


def evaluate(item, vehicle, config):
    text = combined_text(item)

    if not vehicle_matches(text, vehicle):
        return False, [], {}, "车型不匹配"

    if not looks_like_listing(item):
        return False, [], {}, "疑似资讯或非车源页面"

    pending = []
    price = parse_price(text)
    production_date = parse_production_date(text)
    model_year = parse_model_year(text)
    listing_date = parse_listing_date(item)

    min_price = config.get("min_price")
    max_price = config.get("max_price")

    if price is None:
        pending.append("价格待核实")
    else:
        if min_price is not None and price < int(min_price):
            return False, [], {}, "低于价格下限"
        if max_price is not None and price > int(max_price):
            return False, [], {}, "高于价格上限"

    start = config.get("production_date_from")
    end = config.get("production_date_to")

    if production_date is None:
        pending.append("生产日期待核实")
    else:
        if start and production_date < date.fromisoformat(start):
            return False, [], {}, "生产日期早于下限"
        if end and production_date > date.fromisoformat(end):
            return False, [], {}, "生产日期晚于上限"

    year_from = vehicle.get("year_from")
    year_to = vehicle.get("year_to")

    if model_year is None:
        pending.append("车型年款待核实")
    else:
        if year_from is not None and model_year < int(year_from):
            return False, [], {}, "车型年款早于下限"
        if year_to is not None and model_year > int(year_to):
            return False, [], {}, "车型年款晚于上限"

    age_limit = config.get("listing_age_days")
    if age_limit == "custom":
        age_limit = config.get("listing_age_custom_days")

    if age_limit not in (None, "", "unlimited"):
        try:
            age_limit = int(age_limit)
            if listing_date is None:
                pending.append("搜索结果日期待核实")
            elif (
                datetime.now(BEIJING_TZ).date() - listing_date
            ).days > age_limit:
                return False, [], {}, "超过搜索结果日期限制"
        except (ValueError, TypeError):
            pass

    if config.get("match_mode", "relaxed") == "strict":
        if price is None or production_date is None or model_year is None:
            return False, [], {}, "严格模式下关键字段缺失"

    details = {
        "price": price,
        "production_date": production_date,
        "model_year": model_year,
        "listing_date": listing_date,
    }

    return True, pending, details, ""


def telegram_send(message):
    token = (
        os.environ.get("TG_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
    )
    chat_id = (
        os.environ.get("TG_CHAT_ID")
        or os.environ.get("TELEGRAM_CHAT_ID")
    )

    if not token or not chat_id:
        raise RuntimeError(
            "缺少 Telegram 密钥，请检查 Secrets：TG_BOT_TOKEN、TG_CHAT_ID"
        )

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={"User-Agent": USER_AGENT},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram 返回失败：{result}")

    return True


def safe_html(value):
    return html.escape(str(value or ""), quote=False)


def format_listing(item, platform, vehicle, pending, details):
    title = safe_html(item.get("title", "未命名车源"))
    url = html.escape(item.get("url", ""), quote=True)
    model = safe_html(vehicle.get("model", "目标车型"))

    lines = [
        f"🚗 <b>{title}</b>",
        f"平台：{safe_html(platform)}",
        f"匹配车型：{model}",
    ]

    if details["price"] is not None:
        lines.append(f"识别价格：约 ¥{details['price']:,}")
    else:
        lines.append("价格：未识别，请打开原网页核实")

    if details["production_date"]:
        lines.append(
            f"生产日期：{details['production_date'].isoformat()}"
        )
    else:
        lines.append("生产日期：未识别，请打开原网页核实")

    if details["model_year"]:
        lines.append(f"车型年款：{details['model_year']} 年款")
    else:
        lines.append("车型年款：未识别，请打开原网页核实")

    if details["listing_date"]:
        lines.append(
            f"搜索结果日期：{details['listing_date'].isoformat()} "
            "（不一定等于真实上架日期）"
        )

    if pending:
        lines.append(
            "⚠️ 待核实：" + "、".join(
                safe_html(x) for x in pending
            )
        )

    summary = strip_html(item.get("description", ""))
    if summary:
        lines.append("搜索摘要：" + safe_html(summary[:300]))

    if item.get("page_checked"):
        lines.append(
            "详情页：已尝试读取公开页面，仍需人工确认在售状态"
        )
    else:
        lines.append("详情页：未能读取或尚未读取，请手动打开核实")

    lines.append(f'🔗 <a href="{url}">打开原始车源页面</a>')
    return "\n".join(lines)


def build_queries(vehicle, platform):
    """
    按平台域名搜索。
    Bing 返回的 URL 仍会在后续步骤中再次检查真实域名。
    """
    model = str(vehicle.get("model", "")).strip()
    brand = str(vehicle.get("brand", "")).strip()
    terms = VEHICLE_SEARCH_TERMS.get(
        model, [f"{brand} {model} 二手车"]
    )

    queries = []

    for domain in PLATFORM_DOMAINS.get(platform, []):
        for term in terms:
            queries.append(f"site:{domain} {term}")

    return queries


def main():
    config = load_config()
    init_db()

    platforms = [
        p for p in config.get("platforms", [])
        if p in PLATFORM_DOMAINS
    ]
    vehicles = config.get("vehicles", [])

    if not platforms:
        raise RuntimeError("配置文件 platforms 没有有效平台。")
    if not vehicles:
        raise RuntimeError("配置文件 vehicles 为空。")

    log(f"开始搜索：车型 {len(vehicles)} 个，平台 {len(platforms)} 个")
    log("平台：" + "、".join(platforms))
    log(f"预算：{config.get('min_price')} - {config.get('max_price')} 元")
    log(f"生产日期下限：{config.get('production_date_from')}")
    log(f"匹配模式：{config.get('match_mode')}")
    log("说明：候选车源不等于已核实在售车辆。")

    stats = {
        p: {
            "search": 0,
            "urls": set(),
            "candidates": 0,
            "reasons": Counter(),
            "samples": [],
        }
        for p in platforms
    }

    found = {}
    errors = 0
    search_seen = set()

    for vehicle in vehicles:
        for platform in platforms:
            for query in build_queries(vehicle, platform):
                try:
                    log(
                        f"搜索：{platform} / "
                        f"{vehicle.get('model')} / {query}"
                    )

                    items = fetch_rss(query)
                    stats[platform]["search"] += len(items)
                    log(f"返回 {len(items)} 条")

                    for item in items:
                        url = normalize_url(item.get("url", ""))

                        if not url:
                            stats[platform]["reasons"]["链接为空"] += 1
                            continue

                        # 先解码 Bing 跳转，再核验平台域名。
                        if not url_matches_platform(url, platform):
                            stats[platform]["reasons"]["域名不匹配"] += 1

                            if len(stats[platform]["samples"]) < 3:
                                stats[platform]["samples"].append(url)

                            continue

                        stats[platform]["urls"].add(url)

                        key = (platform, url)
                        if key in search_seen:
                            continue
                        search_seen.add(key)

                        if not vehicle_matches(combined_text(item), vehicle):
                            stats[platform]["reasons"]["车型不匹配"] += 1
                            continue

                        if not looks_like_listing(item):
                            stats[platform]["reasons"][
                                "疑似资讯或非车源页面"
                            ] += 1
                            continue

                        item["url"] = url
                        item["page_text"] = fetch_page_text(url)
                        item["page_checked"] = bool(item["page_text"])

                        ok, pending, details, reason = evaluate(
                            item, vehicle, config
                        )

                        if not ok:
                            stats[platform]["reasons"][reason] += 1
                            continue

                        stats[platform]["candidates"] += 1

                        if url not in found:
                            found[url] = {
                                "item": item,
                                "platform": platform,
                                "vehicle": vehicle,
                                "pending": pending,
                                "details": details,
                            }

                except Exception as exc:
                    errors += 1
                    log(
                        f"搜索失败：{platform} / "
                        f"{vehicle.get('model')}：{exc}"
                    )

                time.sleep(0.8)

    sent_count = 0
    skipped_count = 0
    failed_sends = 0

    for url, result in found.items():
        if already_sent(url):
            skipped_count += 1
            continue

        message = format_listing(
            result["item"],
            result["platform"],
            result["vehicle"],
            result["pending"],
            result["details"],
        )

        try:
            telegram_send(message)
            mark_sent(url)
            sent_count += 1
            log("已推送：" + result["item"].get("title", "未命名车源"))
            time.sleep(1)

        except Exception as exc:
            failed_sends += 1
            log(f"推送失败：{exc}")

    log("各平台统计：搜索结果 / 域名通过 / 候选车源")

    for platform, item in stats.items():
        log(
            f"{platform}：{item['search']} / "
            f"{len(item['urls'])} / {item['candidates']}"
        )

        for reason, count in item["reasons"].most_common(5):
            log(f"  排除原因 - {reason}：{count}")

        for sample in item["samples"]:
            log(f"  域名排查样例：{sample}")

    summary = [
        "🚘 <b>二手车雷达运行汇总</b>",
        f"候选车源：{len(found)} 条",
        f"本次新推送：{sent_count} 条",
        f"已推送过而跳过：{skipped_count} 条",
        f"搜索错误：{errors} 次",
        f"推送失败：{failed_sends} 次",
        "",
        "<b>各平台统计</b>",
    ]

    for platform, item in stats.items():
        summary.append(
            f"{safe_html(platform)}：搜索 {item['search']} 条，"
            f"域名通过 {len(item['urls'])} 条，"
            f"候选 {item['candidates']} 条"
        )

        reasons = item["reasons"].most_common(3)
        if reasons:
            summary.append(
                "排除原因：" + "；".join(
                    f"{safe_html(reason)} {count} 条"
                    for reason, count in reasons
                )
            )

    summary.extend([
        "",
        "注意：公开搜索不能保证覆盖平台全部库存。",
        "价格、生产日期、年款、车况及是否仍在售，均应以原始页面和卖家确认结果为准。",
        "生产日期与车型年款是不同概念；搜索结果日期不等于车辆真实上架日期。",
    ])

    try:
        telegram_send("\n".join(summary))
    except Exception as exc:
        log(f"汇总消息发送失败：{exc}")

    log(
        f"运行结束：候选 {len(found)}，新推送 {sent_count}，"
        f"已推送跳过 {skipped_count}，搜索错误 {errors}，"
        f"推送失败 {failed_sends}"
    )


if __name__ == "__main__":
    main()

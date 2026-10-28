
# -*- coding: utf-8 -*-
"""
二手车雷达诊断增强版
公开网页搜索 + 车型筛选 + Telegram 推送 + SQLite 去重

说明：
1. 使用 Bing RSS，不是平台官方库存 API。
2. 搜索结果不等于平台全部库存。
3. 未能从摘要确认的价格、生产日期和年款会标记为待核实。
4. 统计区分搜索结果条数和去重后的结果。
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

PLATFORM_DOMAINS = {
    "瓜子二手车": ["guazi.com"],
    "懂车帝": ["dongchedi.com"],
    "58同城": ["58.com"],
    "闲鱼": ["goofish.com", "xianyu.com", "2.taobao.com"],
    "汽车之家": ["che168.com", "autohome.com.cn"],
    "爱卡汽车": ["used.xcar.com.cn", "xcar.com.cn"]
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
)


def log(message):
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def load_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(f"找不到配置文件：{CONFIG_FILE.name}")

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    config.setdefault("min_price", None)
    config.setdefault("max_price", 150000)
    config.setdefault("production_date_from", "2019-01-01")
    config.setdefault("production_date_to", None)
    config.setdefault("listing_age_days", None)
    config.setdefault("listing_age_custom_days", None)
    config.setdefault("platforms", list(PLATFORM_DOMAINS))
    config.setdefault("vehicles", [])
    config.setdefault("match_mode", "relaxed")

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


def extract_bing_destination(url):
    """从 Bing 跳转链接中尽可能提取原始目标 URL。"""
    current = html.unescape((url or "").strip())

    for _ in range(5):
        try:
            parsed = urllib.parse.urlsplit(current)
            host = (parsed.hostname or "").lower()

            if not (host == "bing.com" or host.endswith(".bing.com")):
                break

            params = urllib.parse.parse_qs(
                parsed.query, keep_blank_values=True
            )

            direct = params.get("url", [None])[0]
            if direct:
                direct = urllib.parse.unquote(direct.strip())
                if direct.startswith(("http://", "https://")):
                    current = direct
                    continue

            encoded = params.get("u", [None])[0]
            if not encoded:
                break

            # parse_qs 会将查询参数中的 + 转为空格；
            # Base64 解码前恢复，避免部分链接解码失败。
            encoded = encoded.strip().replace(" ", "+")
            encoded = urllib.parse.unquote(encoded)

            if encoded.startswith("a1"):
                encoded = encoded[2:]

            encoded += "=" * (-len(encoded) % 4)

            try:
                decoded = base64.urlsafe_b64decode(
                    encoded.encode("ascii")
                ).decode("utf-8", errors="ignore")
            except Exception:
                decoded = ""

            match = re.search(r"https?://[^\s\"<>]+", decoded)
            if match:
                current = html.unescape(
                    match.group(0).rstrip(").,;")
                )
                continue

            break

        except Exception:
            break

    return current


def normalize_url(url):
    """解码常见 Bing 跳转链接并清理追踪参数。"""
    current = extract_bing_destination(url)

    try:
        parsed = urllib.parse.urlsplit(current)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return current

        host = parsed.hostname.lower()
        port = parsed.port

        # 保留非默认端口；删除常见追踪参数。
        netloc = host
        if port and not (
            (parsed.scheme == "http" and port == 80)
            or (parsed.scheme == "https" and port == 443)
        ):
            netloc = f"{host}:{port}"

        tracking = {
            "from", "spm", "utm_source", "utm_medium",
            "utm_campaign", "utm_term", "utm_content",
            "ref", "source"
        }

        query_items = urllib.parse.parse_qsl(
            parsed.query, keep_blank_values=True
        )
        kept = [
            (k, v) for k, v in query_items
            if k.lower() not in tracking
        ]

        return urllib.parse.urlunsplit((
            parsed.scheme.lower(),
            netloc,
            parsed.path or "/",
            urllib.parse.urlencode(kept),
            ""
        ))
    except Exception:
        return current


def resolve_redirect(url):
    """只对仍指向 Bing 的链接尝试跟随 HTTP 跳转。"""
    url = normalize_url(url)

    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if host != "bing.com" and not host.endswith(".bing.com"):
            return url

        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            return normalize_url(response.geturl())
    except Exception:
        return url


def url_matches_platform(url, platform):
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    except Exception:
        return False

    return any(
        host == domain or host.endswith("." + domain)
        for domain in PLATFORM_DOMAINS.get(platform, [])
    )


def get_host(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def fetch_rss(query):
    params = urllib.parse.urlencode({
        "q": query,
        "format": "rss",
        "count": "10",
        "setlang": "zh-hans",
        "cc": "cn"
    })

    url = "https://www.bing.com/search?" + params
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml,application/xml,text/xml,*/*"
        }
    )

    with urllib.request.urlopen(req, timeout=20) as response:
        raw = response.read()

    root = ET.fromstring(raw)
    items = []

    for item in root.findall(".//item"):
        title = html.unescape(
            item.findtext("title", default="")
        ).strip()

        raw_link = html.unescape(
            item.findtext("link", default="")
        ).strip()

        description = html.unescape(
            item.findtext("description", default="")
        ).strip()

        pub_date = item.findtext("pubDate", default="").strip()

        normalized = normalize_url(raw_link)
        resolved = resolve_redirect(normalized)

        items.append({
            "title": title,
            "url": resolved,
            "description": description,
            "pub_date": pub_date,
            "raw_url": raw_link
        })

    return items


def combined_text(item):
    return (
        f"{item.get('title', '')} "
        f"{item.get('description', '')}"
    )


def vehicle_matches(text, vehicle):
    t = text.lower()
    brand = str(vehicle.get("brand", "")).strip()
    model = str(vehicle.get("model", "")).strip()

    brand_aliases = {
        "沃尔沃": ["沃尔沃", "volvo"],
        "马自达": ["马自达", "mazda"],
        "丰田": ["丰田", "toyota"],
        "特斯拉": ["特斯拉", "tesla"],
        "福特": ["福特", "ford"]
    }

    aliases = brand_aliases.get(brand, [brand.lower()])

    if brand and not any(alias.lower() in t for alias in aliases if alias):
        return False

    if model == "S60 T4/T5":
        # 不能只因出现“沃尔沃”就认为 T4/T5 匹配。
        return (
            ("s60" in t or "s 60" in t)
            and any(x in t for x in ("t4", "t5"))
        )

    if model == "V60":
        return "v60" in t or "v 60" in t

    if model == "CX-50 智行版":
        return (
            ("cx-50" in t or "cx50" in t)
            and ("马自达" in t or "mazda" in t)
        )

    if model == "RAV4 荣放":
        return any(x in t for x in ("rav4", "荣放", "荣枫"))

    if model == "Model 3":
        return (
            ("model 3" in t or "model3" in t)
            and ("特斯拉" in t or "tesla" in t)
        )

    if model == "蒙迪欧双擎运动版":
        return (
            ("蒙迪欧" in t or "mondeo" in t)
            and any(x in t for x in ("双擎", "混动", "hybrid"))
        )

    return bool(model) and model.lower() in t


def looks_like_listing(item):
    text = combined_text(item).lower()
    url = item.get("url", "").lower()

    negative = (
        "百科", "新闻", "资讯", "评测", "试驾",
        "配置表", "新车上市", "官方指导价",
        "品牌介绍", "车型大全", "参数配置", "排行榜"
    )

    positive = (
        "二手车", "出售", "转让", "个人车",
        "车源", "万元", "万公里", "上牌",
        "过户", "急售", "可议价"
    )

    if any(word in text for word in negative):
        if not any(word in text for word in (
            "个人出售", "二手车", "转让", "出售", "车源"
        )):
            return False

    if not any(word in text for word in positive):
        listing_paths = (
            "/used", "/ershou", "/car/", "/vehicle/", "/detail"
        )
        if not any(part in url for part in listing_paths):
            return False

    return True


def parse_price(text):
    patterns = [
        r"([0-9]+(?:\.[0-9]+)?)\s*万元",
        r"(?:售价|价格|报价|仅售|卖价|车价)\s*[:：]?\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*万(?:元)?",
        r"[￥¥]\s*([0-9,]+)(?:\s*元)?",
        r"(?:售价|价格|报价|卖价|车价)\s*[:：]?\s*"
        r"([0-9,]+)\s*元"
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue

        try:
            value = float(match.group(1).replace(",", ""))
            if "万" in match.group(0):
                value *= 10000

            if 1000 <= value <= 100000000:
                return int(value)
        except ValueError:
            pass

    return None


def parse_production_date(text):
    """只有日期精度足够时才返回日期，避免把年份擅自当作 1 月 1 日。"""
    patterns = [
        r"(?:生产日期|出厂日期|生产时间)\s*[:：]?\s*"
        r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?"
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue

        try:
            return date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3))
            )
        except (ValueError, TypeError):
            pass

    return None


def parse_model_year(text):
    for pattern in (r"(20\d{2})\s*年款", r"(20\d{2})\s*款"):
        match = re.search(pattern, text)
        if match:
            year = int(match.group(1))
            if 2010 <= year <= date.today().year + 1:
                return year
    return None


def parse_listing_date(item):
    """这是搜索结果日期，不保证是真实车辆上架日期。"""
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

    if production_date is None:
        pending.append("生产日期待核实")
    else:
        start = config.get("production_date_from")
        end = config.get("production_date_to")

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
            elif (datetime.now(BEIJING_TZ).date() - listing_date).days > age_limit:
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
        "listing_date": listing_date
    }

    return True, pending, details, ""


def already_sent(url):
    with sqlite3.connect(DB_FILE) as conn:
        return conn.execute(
            "SELECT 1 FROM sent_listings WHERE url = ?",
            (url,)
        ).fetchone() is not None


def mark_sent(url):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sent_listings(url, sent_at) VALUES (?, ?)",
            (url, datetime.now(BEIJING_TZ).isoformat())
        )


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
            "未找到 Telegram 密钥，请检查 GitHub Secrets："
            "TG_BOT_TOKEN 和 TG_CHAT_ID。"
        )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "false"
    }).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        headers={"User-Agent": USER_AGENT},
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=25) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram 返回失败：{result}")

    return True


def safe_html(value):
    return html.escape(str(value or ""), quote=False)


def format_listing(item, platform, vehicle, pending, details):
    title = safe_html(item.get("title", "未命名车辆"))
    url = html.escape(item.get("url", ""), quote=True)
    model = safe_html(vehicle.get("model", "目标车型"))

    lines = [
        f"🚗 <b>{title}</b>",
        f"平台：{safe_html(platform)}",
        f"匹配车型：{model}"
    ]

    if details.get("price") is not None:
        lines.append(f"搜索摘要价格：约 ¥{details['price']:,}")
    else:
        lines.append("价格：搜索摘要未识别，请打开网页核实")

    if details.get("production_date"):
        lines.append(
            f"生产日期：{details['production_date'].isoformat()}"
        )
    else:
        lines.append("生产日期：搜索摘要未识别，请打开网页核实")

    if details.get("model_year"):
        lines.append(f"车型年款：{details['model_year']} 年款")
    else:
        lines.append("车型年款：搜索摘要未识别，请打开网页核实")

    if details.get("listing_date"):
        lines.append(
            f"搜索结果日期：{details['listing_date'].isoformat()}"
        )

    if pending:
        lines.append("⚠️ 待核实：" + "、".join(safe_html(x) for x in pending))

    summary = re.sub(r"<[^>]+>", " ", item.get("description", ""))
    summary = re.sub(r"\s+", " ", summary).strip()
    if summary:
        lines.append("搜索摘要：" + safe_html(summary[:350]))

    lines.append(f'🔗 <a href="{url}">打开原始网页</a>')
    return "\n".join(lines)


def build_queries(vehicle, platform):
    model = vehicle.get("model", "")
    brand = vehicle.get("brand", "")

    aliases = {
        "S60 T4/T5": '"沃尔沃 S60" OR "Volvo S60" OR "S60 T5"',
        "V60": '"沃尔沃 V60" OR "Volvo V60"',
        "CX-50 智行版": '"马自达 CX-50" OR "Mazda CX-50" OR "CX50"',
        "RAV4 荣放": '"RAV4 荣放" OR "丰田 RAV4" OR "Toyota RAV4"',
        "Model 3": '"特斯拉 Model 3" OR "Tesla Model 3" OR "Model3"',
        "蒙迪欧双擎运动版": '"蒙迪欧 双擎" OR "福特 蒙迪欧混动" OR "Mondeo Hybrid"'
    }

    keywords = aliases.get(model, f'"{brand} {model}"')
    queries = []

    for domain in PLATFORM_DOMAINS.get(platform, []):
        queries.append(
            f'site:{domain} ({keywords}) '
            f'(二手车 OR 二手 OR 出售 OR 转让 OR 车源) '
            f'-百科 -评测 -试驾 -新闻 -资讯 -新车上市'
        )

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
    log("搜索平台：" + "、".join(platforms))
    log(f"预算：{config.get('min_price')} - {config.get('max_price')} 元")
    log(f"生产日期下限：{config.get('production_date_from')}")
    log(f"筛选模式：{config.get('match_mode')}")
    log("提醒：Bing 搜索摘要不能证明车源仍在售。")

    # 按平台记录原始结果、去重结果和不同排除原因。
    platform_stats = {
        p: {
            "rss": 0,
            "unique_urls": set(),
            "valid_domain": 0,
            "candidates": 0,
            "reasons": Counter()
        }
        for p in platforms
    }

    # 全局 URL 去重，避免同一链接被重复计算成多辆车。
    seen_results = set()
    found = {}
    errors = 0
    unresolved_urls = 0
    domain_samples = []
    unresolved_samples = []

    for vehicle in vehicles:
        for platform in platforms:
            stats = platform_stats[platform]

            for query in build_queries(vehicle, platform):
                try:
                    log(f"搜索：{platform} / {vehicle.get('model')} / {query}")
                    items = fetch_rss(query)
                    stats["rss"] += len(items)
                    log(f"搜索引擎返回 {len(items)} 条")

                    for item in items:
                        url = item.get("url", "")
                        if not url:
                            stats["reasons"]["链接为空"] += 1
                            continue

                        host = get_host(url)
                        if host == "bing.com" or host.endswith(".bing.com"):
                            unresolved_urls += 1
                            if len(unresolved_samples) < 10:
                                unresolved_samples.append(
                                    f"{platform}: {item.get('title', '')[:70]} | "
                                    f"{item.get('raw_url', '')[:180]}"
                                )

                        if not url_matches_platform(url, platform):
                            stats["reasons"]["域名不匹配"] += 1
                            if len(domain_samples) < 20:
                                domain_samples.append(
                                    f"{platform}: {host or '(无域名)'} | "
                                    f"{item.get('title', '')[:70]} | {url[:160]}"
                                )
                            continue

                        stats["unique_urls"].add(url)

                        # 只对同一平台的同一 URL 做一次车型筛选。
                        unique_key = (platform, url)
                        if unique_key in seen_results:
                            continue
                        seen_results.add(unique_key)

                        stats["valid_domain"] += 1

                        ok, pending, details, reason = evaluate(
                            item, vehicle, config
                        )

                        if not ok:
                            stats["reasons"][reason or "未知原因"] += 1
                            continue

                        stats["candidates"] += 1

                        if url not in found:
                            found[url] = {
                                "item": item,
                                "platform": platform,
                                "vehicle": vehicle,
                                "pending": pending,
                                "details": details
                            }

                except Exception as exc:
                    errors += 1
                    log(f"搜索失败：{platform} / {vehicle.get('model')}：{exc}")

                time.sleep(1)

    sent_count = 0
    failed_sends = 0
    already_sent_count = 0

    for url, result in found.items():
        if already_sent(url):
            already_sent_count += 1
            continue

        message = format_listing(
            result["item"],
            result["platform"],
            result["vehicle"],
            result["pending"],
            result["details"]
        )

        try:
            telegram_send(message)
            mark_sent(url)
            sent_count += 1
            log(f"已推送：{result['item'].get('title', '')}")
            time.sleep(1)
        except Exception as exc:
            failed_sends += 1
            log(f"Telegram 推送失败：{exc}")

    log("各平台诊断：原始搜索条数 / 去重后域名通过 / 候选")
    for platform, stats in platform_stats.items():
        log(
            f"  {platform}：{stats['rss']} / "
            f"{len(stats['unique_urls'])} / {stats['candidates']}"
        )
        for reason, count in stats["reasons"].most_common():
            log(f"    排除原因 - {reason}：{count}")

    log("域名校验失败样例：")
    for sample in domain_samples:
        log("  " + sample)
    if not domain_samples:
        log("  无")

    if unresolved_samples:
        log("仍指向 Bing 的链接样例：")
        for sample in unresolved_samples:
            log("  " + sample)

    summary_lines = [
        "🚘 <b>二手车雷达运行汇总（诊断增强版）</b>",
        f"候选车源：{len(found)} 条",
        f"本次新推送：{sent_count} 条",
        f"已推送过而跳过：{already_sent_count} 条",
        f"仍指向 Bing 的链接：{unresolved_urls} 条",
        f"搜索错误：{errors} 次",
        f"推送失败：{failed_sends} 次",
        "",
        "<b>各平台统计</b>"
    ]

    for platform, stats in platform_stats.items():
        summary_lines.append(
            f"{safe_html(platform)}：搜索 {stats['rss']}，"
            f"域名通过（去重）{len(stats['unique_urls'])}，"
            f"候选 {stats['candidates']}"
        )

        reasons = stats["reasons"].most_common(3)
        if reasons:
            summary_lines.append(
                "  排除原因：" + "；".join(
                    f"{safe_html(reason)} {count} 条"
                    for reason, count in reasons
                )
            )

    summary_lines.extend([
        "",
        "注意：公开搜索不等于平台全部库存。"
        "搜索摘要无法可靠确认车辆仍在售；价格、生产日期、"
        "年款及车辆状态请以原始页面为准。"
    ])

    try:
        telegram_send("\n".join(summary_lines))
    except Exception as exc:
        log(f"汇总消息发送失败：{exc}")

    log(
        f"运行结束：候选 {len(found)}，新推送 {sent_count}，"
        f"已推送跳过 {already_sent_count}，"
        f"未解码链接 {unresolved_urls}，搜索错误 {errors}，"
        f"推送失败 {failed_sends}"
    )


if __name__ == "__main__":
    main()

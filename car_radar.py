
# -*- coding: utf-8 -*-
"""二手车雷达：Bing RSS 搜索、车型筛选、Telegram 推送、SQLite 去重。"""

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

    if config.get("match_mode") not in ("relaxed", "strict"):
        raise ValueError("match_mode 只能设置为 relaxed 或 strict")

    return config


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_listings (
                url TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL
            )
        """)


def normalize_url(url):
    """
    尝试解码 Bing 常见跳转链接。
    如果无法解码，保留原链接供后续诊断，不伪造目标网址。
    """
    if not url:
        return ""

    current = html.unescape(url.strip())

    for _ in range(4):
        try:
            parsed = urllib.parse.urlsplit(current)
            host = (parsed.hostname or "").lower()
            params = urllib.parse.parse_qs(parsed.query)

            if host.endswith("bing.com"):
                # 部分 Bing 链接把原始 URL 放在 url 参数中
                direct = params.get("url", [None])[0]
                if direct and direct.startswith(("http://", "https://")):
                    current = direct
                    continue

                # 常见格式：u=a1 + URL-safe Base64 编码
                encoded = params.get("u", [None])[0]
                if encoded:
                    try:
                        if encoded.startswith("a1"):
                            encoded = encoded[2:]

                        encoded += "=" * (-len(encoded) % 4)
                        decoded = base64.urlsafe_b64decode(
                            encoded.encode("ascii")
                        ).decode("utf-8", errors="ignore")

                        match = re.search(r"https?://[^\s\"<>]+", decoded)
                        if match:
                            current = match.group(0).rstrip(").,;")
                            continue
                    except Exception:
                        pass

            # 不是 Bing 跳转链接，保留真实 URL 并去掉追踪参数
            if parsed.scheme in ("http", "https") and parsed.netloc:
                return urllib.parse.urlunsplit((
                    parsed.scheme,
                    parsed.netloc.lower(),
                    parsed.path.rstrip("/"),
                    "",
                    "",
                ))

            break
        except Exception:
            break

    return current


def resolve_bing_redirect(url):
    """
    对仍然指向 Bing 的链接，尝试跟随 HTTP 重定向。
    网络不可用或无法解析时返回原链接，由后续校验排除。
    """
    url = normalize_url(url)

    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
        if not (host == "bing.com" or host.endswith(".bing.com")):
            return url

        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            final_url = response.geturl()

        return normalize_url(final_url)
    except Exception:
        return url


def url_matches_platform(url, platform):
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except Exception:
        return False

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

    for item in root.findall(".//item"):
        title = html.unescape(item.findtext("title", default="")).strip()
        raw_link = item.findtext("link", default="").strip()
        description = html.unescape(
            item.findtext("description", default="")
        ).strip()
        pub_date = item.findtext("pubDate", default="").strip()

        results.append({
            "title": title,
            "url": resolve_bing_redirect(raw_link),
            "description": description,
            "pub_date": pub_date,
            "raw_url": raw_link,
        })

    return results


def combined_text(item):
    return f"{item.get('title', '')} {item.get('description', '')}"


def vehicle_matches(text, vehicle):
    """只匹配指定车型，不因为搜索结果少而放宽车型条件。"""
    text_lower = text.lower()
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
    if brand and not any(a.lower() in text_lower for a in aliases if a):
        return False

    if model == "S60 T4/T5":
        return (
            ("s60" in text_lower or "s 60" in text_lower)
            and (
                "t4" in text_lower
                or "t5" in text_lower
                or "沃尔沃" in text_lower
                or "volvo" in text_lower
            )
        )

    if model == "V60":
        return "v60" in text_lower or "v 60" in text_lower

    if model == "CX-50 智行版":
        return (
            ("cx-50" in text_lower or "cx50" in text_lower)
            and ("马自达" in text_lower or "mazda" in text_lower)
        )

    if model == "RAV4 荣放":
        return (
            "rav4" in text_lower
            or "荣放" in text_lower
            or "荣枫" in text_lower
        )

    if model == "Model 3":
        return (
            ("model 3" in text_lower or "model3" in text_lower)
            and ("特斯拉" in text_lower or "tesla" in text_lower)
        )

    if model == "蒙迪欧双擎运动版":
        return (
            ("蒙迪欧" in text_lower or "mondeo" in text_lower)
            and (
                "双擎" in text_lower
                or "混动" in text_lower
                or "hybrid" in text_lower
            )
        )

    return bool(model) and model.lower() in text_lower


def parse_price(text):
    """返回人民币元。没有识别到明确价格时返回 None。"""
    patterns = [
        r"([0-9]+(?:\.[0-9]+)?)\s*万元",
        r"(?:售价|价格|报价|仅售|卖价)\s*[:：]?\s*"
        r"([0-9]+(?:\.[0-9]+)?)\s*万(?:元)?",
        r"[￥¥]\s*([0-9,]+)(?:\s*元)?",
        r"(?:售价|价格|报价|卖价)\s*[:：]?\s*([0-9,]+)\s*元",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue

        try:
            value = float(match.group(1).replace(",", ""))
            if "万" in match.group(0):
                value *= 10000

            # 排除明显不合理的数字
            if 1000 <= value <= 100000000:
                return int(value)
        except ValueError:
            continue

    return None


def parse_production_date(text):
    """
    只识别明确标注的生产日期、出厂日期或生产时间。
    不把上牌日期或车型年款当作生产日期。
    """
    patterns = [
        r"(?:生产日期|出厂日期|生产时间)\s*[:：]?\s*"
        r"(20\d{2})[年./-](\d{1,2})(?:[月./-](\d{1,2})日?)?",
        r"(?:生产日期|出厂日期|生产时间)\s*[:：]?\s*"
        r"(20\d{2})年",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue

        try:
            year = int(match.group(1))
            month = int(match.group(2)) if match.lastindex >= 2 else 1
            day_text = match.group(3) if match.lastindex >= 3 else None
            day = int(day_text) if day_text else 1
            return date(year, month, day)
        except (ValueError, TypeError):
            continue

    return None


def parse_model_year(text):
    """只从明确的年款表述中提取车型年款。"""
    patterns = [
        r"(20\d{2})\s*年款",
        r"(20\d{2})\s*款",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            year = int(match.group(1))
            if 2010 <= year <= date.today().year + 1:
                return year

    return None


def parse_listing_date(item):
    """尝试提取搜索结果发布时间或摘要中写明的上架时间。"""
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

    match = re.search(
        r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?",
        text,
    )
    if match:
        try:
            return date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
        except ValueError:
            pass

    return None


def evaluate(item, vehicle, config):
    """返回 (是否保留, 待核实字段, 解析信息, 排除原因)。"""
    text = combined_text(item)

    if not vehicle_matches(text, vehicle):
        return False, [], {}, "车型不匹配"

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
                pending.append("上架时间待核实")
            elif (datetime.now(BEIJING_TZ).date() - listing_date).days > age_limit:
                return False, [], {}, "超过上架时间限制"
        except (ValueError, TypeError):
            pass

    # 严格模式要求关键字段都能识别
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


def telegram_send(message):
    token = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")

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
    title = safe_html(item.get("title", "未命名车辆"))
    url = html.escape(item.get("url", ""), quote=True)
    model = safe_html(vehicle.get("model", "目标车型"))

    lines = [
        f"🚗 <b>{title}</b>",
        f"平台：{safe_html(platform)}",
        f"匹配车型：{model}",
    ]

    if details.get("price") is not None:
        lines.append(f"搜索结果价格：约 ¥{details['price']:,}")
    else:
        lines.append("搜索结果价格：未识别，请打开网页核实")

    if details.get("production_date"):
        lines.append(f"生产日期：{details['production_date'].isoformat()}")
    else:
        lines.append("生产日期：未识别，请打开网页核实")

    if details.get("model_year"):
        lines.append(f"车型年款：{details['model_year']} 年款")
    else:
        lines.append("车型年款：未识别，请打开网页核实")

    if details.get("listing_date"):
        lines.append(f"搜索结果日期：{details['listing_date'].isoformat()}")

    if pending:
        lines.append("⚠️ 待核实：" + "、".join(safe_html(x) for x in pending))

    summary = re.sub(r"<[^>]+>", " ", item.get("description", ""))
    summary = re.sub(r"\s+", " ", summary).strip()
    if summary:
        if len(summary) > 300:
            summary = summary[:300] + "……"
        lines.append("摘要：" + safe_html(summary))

    lines.append(f'🔗 <a href="{url}">打开原始网页</a>')
    return "\n".join(lines)


def build_queries(vehicle, platform):
    model = vehicle.get("model", "")
    brand = vehicle.get("brand", "")
    queries = []

    if model == "S60 T4/T5":
        keywords = '"沃尔沃 S60" OR "Volvo S60"'
    elif model == "V60":
        keywords = '"沃尔沃 V60" OR "Volvo V60"'
    elif model == "CX-50 智行版":
        keywords = '"马自达 CX-50" OR "Mazda CX-50"'
    elif model == "RAV4 荣放":
        keywords = '"RAV4 荣放" OR "丰田 RAV4" OR "Toyota RAV4"'
    elif model == "Model 3":
        keywords = '"特斯拉 Model 3" OR "Tesla Model 3"'
    elif model == "蒙迪欧双擎运动版":
        keywords = '"蒙迪欧 双擎" OR "福特 蒙迪欧混动"'
    else:
        keywords = f'"{brand} {model}"'

    for domain in PLATFORM_DOMAINS[platform]:
        queries.append(f"site:{domain} ({keywords}) 二手车")

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
    log(f"预算：{config.get('min_price')} - {config.get('max_price')} 元")
    log(f"生产日期下限：{config.get('production_date_from')}")
    log(f"筛选模式：{config.get('match_mode')}")

    found = {}
    errors = 0
    rejected_domain = 0
    rejected_vehicle = 0
    rejected_other = 0
    unresolved_urls = 0
    domain_samples = []

    for vehicle in vehicles:
        for platform in platforms:
            for query in build_queries(vehicle, platform):
                try:
                    log(f"搜索：{platform} / {vehicle.get('model')} / {query}")
                    items = fetch_rss(query)
                    log(f"搜索引擎返回 {len(items)} 条")

                    for item in items:
                        url = item.get("url", "")
                        if not url:
                            rejected_domain += 1
                            continue

                        host = (urllib.parse.urlsplit(url).hostname or "").lower()

                        if not url_matches_platform(url, platform):
                            rejected_domain += 1
                            if len(domain_samples) < 12:
                                domain_samples.append(
                                    f"{platform}: {host or '(无域名)'} | "
                                    f"{item.get('title', '')[:70]}"
                                )
                            if host.endswith("bing.com"):
                                unresolved_urls += 1
                            continue

                        ok, pending, details, reason = evaluate(
                            item, vehicle, config
                        )

                        if not ok:
                            if reason == "车型不匹配":
                                rejected_vehicle += 1
                            else:
                                rejected_other += 1
                            continue

                        key = url
                        if key not in found:
                            found[key] = {
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

                time.sleep(1)

    sent_count = 0
    failed_sends = 0

    for url, result in found.items():
        if already_sent(url):
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
            log(f"已推送：{result['item'].get('title', '')}")
            time.sleep(1)
        except Exception as exc:
            failed_sends += 1
            log(f"Telegram 推送失败：{exc}")

    log("本次域名校验失败的部分样例：")
    if domain_samples:
        for sample in domain_samples:
            log("  " + sample)
    else:
        log("  无")

    summary = (
        "🚘 <b>二手车雷达运行汇总</b>\n"
        f"符合初步条件的候选：{len(found)} 条\n"
        f"本次新推送：{sent_count} 条\n"
        f"域名校验排除：{rejected_domain} 条\n"
        f"车型不匹配：{rejected_vehicle} 条\n"
        f"其他条件排除：{rejected_other} 条\n"
        f"仍指向 Bing 的链接：{unresolved_urls} 条\n"
        f"搜索错误：{errors} 次\n"
        f"推送失败：{failed_sends} 次\n\n"
        "说明：这是公开网页搜索结果，不代表平台全部库存。"
        "价格、生产日期、车型年款和车辆状态请以原始页面为准。"
    )

    try:
        telegram_send(summary)
    except Exception as exc:
        log(f"汇总消息发送失败：{exc}")

    log(
        f"运行结束：候选 {len(found)}，新推送 {sent_count}，"
        f"域名排除 {rejected_domain}，车型排除 {rejected_vehicle}，"
        f"其他条件排除 {rejected_other}，"
        f"Bing 未解码链接 {unresolved_urls}，错误 {errors}"
    )


if __name__ == "__main__":
    main()

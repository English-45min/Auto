
import os
import re
import json
import time
import html
import sqlite3
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ============================================================
# 二手车雷达：独立模块，不修改财经看板的程序
# 数据源尚未配置时，不会虚构车源。
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "car_radar.sqlite3"
BEIJING_TZ = timezone(timedelta(hours=8))

# 兼容两种常见的 Telegram 环境变量命名
BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TG_BOT_TOKEN")
)
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TG_CHAT_ID")
)

MAX_PRICE = 150000
MIN_PRODUCTION_DATE = "2019-01-01"

# 后续配置经过验证的 RSS/Atom 车源地址。
# 目前为空，避免把普通汽车新闻误当作出售车辆。
FEED_URLS = [
    item.strip()
    for item in os.getenv("CAR_RADAR_FEED_URLS", "").split(",")
    if item.strip()
]

# 车型关键词。匹配结果仍需核实车辆配置及年份。
VEHICLE_RULES = [
    {
        "name": "沃尔沃 S60 T4/T5（2020年款）",
        "aliases": ["S60 T4", "S60T4", "S60 T5", "S60T5"],
        "required_year": 2020,
    },
    {
        "name": "马自达 CX-50 智行版",
        "aliases": ["CX-50 智行", "CX50 智行"],
        "required_year": None,
    },
    {
        "name": "丰田 RAV4 荣放",
        "aliases": ["RAV4", "荣放"],
        "required_year": None,
    },
    {
        "name": "特斯拉 Model 3（2023年及以后）",
        "aliases": ["Model 3", "Model3", "特斯拉3"],
        "required_year": 2023,
    },
    {
        "name": "福特蒙迪欧双擎运动版",
        "aliases": ["蒙迪欧 双擎 运动", "蒙迪欧双擎运动"],
        "required_year": None,
    },
]


def log(message):
    now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


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


def fetch_url(url, timeout=15):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CarRadar/1.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def parse_feed(xml_bytes, source_url):
    """解析标准 RSS 或 Atom。仅处理实际提供的 feed 数据。"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        log(f"数据源 XML 解析失败：{source_url}；{exc}")
        return []

    results = []

    # RSS 格式
    for item in root.findall(".//item"):
        title = item.findtext("title", default="")
        link = item.findtext("link", default="")
        description = (
            item.findtext("description", default="")
            or item.findtext("content:encoded", default="")
        )
        guid = item.findtext("guid", default="") or link or title

        results.append({
            "id": guid,
            "title": strip_html(title),
            "content": strip_html(description),
            "url": link.strip(),
            "source": source_url,
        })

    # Atom 格式
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    if not results:
        for entry in root.findall(".//atom:entry", ns):
            title = entry.findtext("atom:title", default="", namespaces=ns)
            summary = entry.findtext(
                "atom:summary", default="", namespaces=ns
            )
            link_node = entry.find("atom:link", ns)
            link = link_node.get("href", "") if link_node is not None else ""
            item_id = entry.findtext(
                "atom:id", default="", namespaces=ns
            ) or link or title

            results.append({
                "id": item_id,
                "title": strip_html(title),
                "content": strip_html(summary),
                "url": link,
                "source": source_url,
            })

    return results


def parse_price(text):
    """尽可能识别人民币价格；无法确认时返回 None。"""
    text = (text or "").replace(",", "").replace("，", "")

    patterns = [
        r"(?:售价|价格|报价|仅售|卖价)\s*[:：]?\s*"
        r"(\d+(?:\.\d+)?)\s*万",
        r"(\d+(?:\.\d+)?)\s*万元",
        r"(\d{4,6})\s*元",
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            number = float(match.group(1))
            if "元" in match.group(0) and "万" not in match.group(0):
                return number
            return number * 10000

    return None


def parse_production_date(text):
    """提取明确的生产日期；只写上牌年份不能代替生产日期。"""
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


def match_vehicle(text):
    text_upper = (text or "").upper()

    for rule in VEHICLE_RULES:
        for alias in rule["aliases"]:
            if alias.upper() in text_upper:
                return rule

    return None


def check_listing(item):
    """保守筛选：关键条件无法确认时不当作合格车源推送。"""
    text = f"{item['title']} {item['content']}"

    rule = match_vehicle(text)
    if not rule:
        return None

    price = parse_price(text)
    if price is None:
        log(f"跳过：无法确认价格：{item['title']}")
        return None

    if price > MAX_PRICE:
        return None

    production_date = parse_production_date(text)
    if production_date is None:
        log(f"待核实生产日期，暂不推送：{item['title']}")
        return None

    if production_date < MIN_PRODUCTION_DATE:
        return None

    year_match = re.search(r"(?:20\d{2})年款", text)
    if rule["required_year"] is not None:
        required_year = rule["required_year"]
        if year_match and int(year_match.group(0)[:4]) != required_year:
            return None
        if required_year == 2023:
            production_year = int(production_date[:4])
            if production_year < 2023:
                return None

    item["matched_vehicle"] = rule["name"]
    item["price"] = price
    item["production_date"] = production_date
    return item


def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        log("未检测到 Telegram 配置，请检查 GitHub Secrets 名称。")
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
        headers={"Content-Type": "application/x-www-form-urlencoded"},
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
        log(f"Telegram 推送失败：{exc}")
        return False


def main():
    log("二手车雷达启动。")
    log(f"预算上限：{MAX_PRICE} 元；生产日期下限：{MIN_PRODUCTION_DATE}")

    if not FEED_URLS:
        log(
            "目前没有配置经过验证的车源 feed。"
            "本次不会产生车源推送；请先完成平台数据源接入。"
        )
        return

    conn = get_db()
    new_count = 0

    try:
        for feed_url in FEED_URLS:
            try:
                log(f"正在读取数据源：{feed_url}")
                xml_bytes = fetch_url(feed_url)
                entries = parse_feed(xml_bytes, feed_url)
            except Exception as exc:
                log(f"读取数据源失败：{feed_url}；{exc}")
                continue

            log(f"该数据源读取到 {len(entries)} 条记录。")

            for entry in entries:
                candidate = check_listing(entry)
                if not candidate or not candidate["url"]:
                    continue

                item_id = candidate["id"] or candidate["url"]
                existing = conn.execute(
                    "SELECT 1 FROM seen_listings WHERE item_id = ?",
                    (item_id,),
                ).fetchone()

                if existing:
                    continue

                title = html.escape(candidate["title"])
                vehicle = html.escape(candidate["matched_vehicle"])
                url = html.escape(candidate["url"], quote=True)
                price = f"{candidate['price']:,.0f}"
                production_date = html.escape(candidate["production_date"])

                message = (
                    "🚗 <b>二手车雷达：发现候选车源</b>\n\n"
                    f"车型：{vehicle}\n"
                    f"标题：{title}\n"
                    f"价格：约 ¥{price}\n"
                    f"生产日期：{production_date}\n"
                    f"<a href=\"{url}\">查看原始车源</a>\n\n"
                    "请核实车辆配置、车况、价格及生产日期。"
                )

                if send_telegram(message):
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO seen_listings
                        (item_id, title, url, first_seen)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            item_id,
                            candidate["title"],
                            candidate["url"],
                            datetime.now(BEIJING_TZ).isoformat(),
                        ),
                    )
                    conn.commit()
                    new_count += 1
                else:
                    # 推送失败时不记录为已处理，下次运行可以重试。
                    time.sleep(2)

        log(f"本次成功推送 {new_count} 条候选车源。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

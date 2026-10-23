# -*- coding: utf-8 -*-
"""财经新闻看板自动更新脚本。仅使用 Python 标准库。"""

import os
import re
import json
import time
import hashlib
import html
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
import xml.etree.ElementTree as ET

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"
CACHE_FILE = BASE_DIR / "ai_cache.json"

TZ_CN = timezone(timedelta(hours=8))
TIMEOUT = 20
MAX_AI_CALLS_PER_RUN = 8

LIMITS = {
    "global": 20,
    "asia": 10,
    "china": 10,
    "earnings": 10,
}

# Google News RSS 搜索用于补充新闻源，避免单一媒体 RSS 失效后栏目为空。
# 直接新闻源失败时会记录日志并继续处理其他来源。
def google_news_rss(query):
    params = urlencode({
        "q": query,
        "hl": "zh-CN",
        "gl": "US",
        "ceid": "US:zh-Hans",
    })
    return "https://news.google.com/rss/search?" + params


FEEDS = {
    "global": [
        ("CNBC World", "https://www.cnbc.com/id/100727362/device/rss/rss.html"),
        ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Google News 国际财经", google_news_rss(
            "global finance economy markets stocks"
        )),
    ],
    "asia": [
        ("中央社财经", "https://feeds.feedburner.com/rsscna/finance"),
        ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar"),
        ("The Japan Times", "https://www.japantimes.co.jp/feed/"),
        ("Google News 亚太财经", google_news_rss(
            "Asia Pacific finance economy markets China Japan Korea Taiwan Singapore"
        )),
    ],
    "china": [
        ("Google News 中国财经", google_news_rss(
            "中国财经 OR 中国经济 OR A股 OR 人民币 OR 中国央行 OR 港股"
        )),
        ("Google News 中国股市", google_news_rss(
            "A股 OR 沪深股市 OR 中国上市公司 OR 中国金融市场"
        )),
    ],
    "earnings": [
        ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("CNBC Earnings", "https://www.cnbc.com/id/15839135/device/rss/rss.html"),
        ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/marketpulse/"),
        ("Google News 企业财报", google_news_rss(
            "company earnings revenue profit quarterly results forecast"
        )),
    ],
}


def get_url(url, headers=None, timeout=TIMEOUT):
    default_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    default_headers.update(headers or {})
    req = Request(url, headers=default_headers)
    with urlopen(req, timeout=timeout) as response:
        return response.read()


def clean(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_feed(source, url):
    try:
        raw = get_url(url)
        # 一些 RSS 使用 UTF-8 BOM；ElementTree 能识别常见 XML 编码。
        root = ET.fromstring(raw)
    except Exception as exc:
        print(f"[WARN] RSS 获取失败 {source}: {type(exc).__name__}: {exc}")
        return []

    entries = []

    # RSS 2.0
    for node in root.findall(".//item"):
        title = clean(node.findtext("title"))
        link = clean(node.findtext("link"))
        desc = clean(node.findtext("description"))
        pub = clean(node.findtext("pubDate"))

        if title and link:
            entries.append({
                "title": title,
                "url": link,
                "summary": desc,
                "source": source,
                "published": pub,
            })

    # Atom
    if not entries:
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for node in root.findall(".//a:entry", ns):
            title = clean(node.findtext("a:title", default="", namespaces=ns))
            summary = (
                node.findtext("a:summary", default="", namespaces=ns)
                or node.findtext("a:content", default="", namespaces=ns)
            )
            link_node = node.find("a:link", ns)
            link = link_node.get("href", "") if link_node is not None else ""
            pub = (
                node.findtext("a:published", default="", namespaces=ns)
                or node.findtext("a:updated", default="", namespaces=ns)
            )

            if title and link:
                entries.append({
                    "title": clean(title),
                    "url": clean(link),
                    "summary": clean(summary),
                    "source": source,
                    "published": clean(pub),
                })

    return entries


def parse_date(value):
    if not value:
        return datetime.now(timezone.utc)

    try:
        from email.utils import parsedate_to_datetime
        result = parsedate_to_datetime(value)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except Exception:
        pass

    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def has_chinese(value):
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def google_translate(text):
    if not text:
        return text

    # 已含中文时保留原文，避免破坏中文新闻。
    if has_chinese(text):
        return text

    params = urlencode({
        "client": "gtx",
        "sl": "auto",
        "tl": "zh-CN",
        "dt": "t",
        "q": text[:1800],
    })
    url = "https://translate.googleapis.com/translate_a/single?" + params

    for attempt in range(2):
        try:
            raw = get_url(url, timeout=12)
            data = json.loads(raw.decode("utf-8"))
            result = "".join(
                part[0]
                for part in data[0]
                if part and part[0]
            )
            if result:
                return result
        except Exception as exc:
            print(
                f"[WARN] 中文翻译失败（第 {attempt + 1} 次）："
                f"{type(exc).__name__}: {exc}"
            )
            time.sleep(0.5)

    # 翻译服务失败时保留原文，不编造译文。
    return text


def load_cache():
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def gemini_call(prompt):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("[WARN] 未配置 GEMINI_API_KEY。")
        return None

    model = os.environ.get(
        "GEMINI_MODEL", "gemini-2.5-flash"
    ).strip()

    # 密钥通过请求头发送，不拼接在 URL 中。
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        + quote(model, safe="-._")
        + ":generateContent"
    )

    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}],
        }],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
            "User-Agent": "FinanceDashboard/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=35) as response:
            data = json.loads(response.read().decode("utf-8"))

        candidates = data.get("candidates", [])
        if not candidates:
            print(
                "[WARN] Gemini 未返回候选内容："
                + json.dumps(
                    data.get("promptFeedback", {}),
                    ensure_ascii=False,
                )[:1200]
            )
            return None

        parts = candidates[0].get("content", {}).get("parts", [])
        result = "".join(
            part.get("text", "")
            for part in parts
            if part.get("text")
        ).strip()

        if not result:
            print("[WARN] Gemini 返回了空文本。")
            return None

        return result

    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = "(无法读取错误详情)"

        print(f"[WARN] Gemini HTTP {exc.code}: {detail[:2000]}")
        return None

    except Exception as exc:
        print(
            f"[WARN] Gemini 调用异常："
            f"{type(exc).__name__}: {exc}"
        )
        return None


def parse_ai_json(raw):
    if not raw:
        return None

    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # 兼容模型在 JSON 前后附带少量文本的情况。
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            result = json.loads(text[start:end + 1])
        except Exception:
            return None

    if not isinstance(result, dict):
        return None

    assets = result.get("assets", [])
    if not isinstance(assets, list):
        assets = []

    return {
        "analysis": clean(result.get("analysis", ""))[:1200],
        "impact": clean(result.get("impact", ""))[:150],
        "assets": [clean(x) for x in assets if clean(x)][:3],
        "risk": clean(result.get("risk", ""))[:300],
        "provider": "Gemini",
    }


def ai_insight(item, cache, budget):
    cache_key = hashlib.sha256(
        (item["title"] + "|" + item["url"]).encode("utf-8")
    ).hexdigest()

    if cache_key in cache:
        return cache[cache_key]

    if budget["attempts"] >= MAX_AI_CALLS_PER_RUN:
        return None

    prompt = (
        "你是一名谨慎的财经新闻分析员。请用简体中文分析这条新闻。\n"
        "必须只依据提供的标题、来源和摘要，不得编造新闻事实、数据或因果关系。"
        "如果信息不足，请在风险字段明确说明。\n"
        "请严格输出一个 JSON 对象，不要输出 Markdown 代码围栏。\n"
        "字段要求：\n"
        "analysis：80至140个汉字，解释事件、可能的市场传导机制及重要性；"
        "若信息不足，可以简短说明。\n"
        "impact：不超过25个汉字，描述潜在市场影响。\n"
        "assets：最多3个相关资产、市场或行业，必须是字符串数组。\n"
        "risk：不超过35个汉字，说明主要风险或不确定性。\n"
        "不要提供个性化投资建议。\n\n"
        f"新闻标题：{item['title']}\n"
        f"新闻来源：{item['source']}\n"
        f"新闻摘要：{item.get('summary', '')[:900]}\n"
    )

    budget["attempts"] += 1
    raw = gemini_call(prompt)
    result = parse_ai_json(raw)

    if result and result["analysis"]:
        cache[cache_key] = result
        budget["successes"] += 1
        return result

    return None


def classify(items, channel):
    if channel == "asia":
        pattern = (
            r"asia|asian|china|chinese|japan|japanese|korea|korean|"
            r"taiwan|hong kong|singapore|india|indian|australia|"
            r"asia-pacific|亚太|中国|日本|韩国|台湾|香港|新加坡|"
            r"印度|澳大利亚|东南亚"
        )
        items = [
            item for item in items
            if re.search(
                pattern,
                item["title"] + " " + item.get("summary", ""),
                re.I,
            )
        ]

    elif channel == "china":
        pattern = (
            r"中国|人民币|A股|沪深|港股|央行|中国经济|"
            r"China|Chinese|yuan|renminbi|Shanghai|Shenzhen|"
            r"PBOC|CSI 300|China stocks|中国股市|中国市场"
        )
        items = [
            item for item in items
            if re.search(
                pattern,
                item["title"] + " " + item.get("summary", ""),
                re.I,
            )
        ]

    elif channel == "earnings":
        pattern = (
            r"earnings|revenue|profit|quarter|quarterly|forecast|"
            r"guidance|results|financial results|财报|业绩|营收|"
            r"利润|季度|盈利|财务结果|业绩预告"
        )
        items = [
            item for item in items
            if re.search(
                pattern,
                item["title"] + " " + item.get("summary", ""),
                re.I,
            )
        ]

    return items


def build_channel(channel, cache, budget):
    gathered = []
    seen_urls = set()
    seen_titles = set()

    for source, url in FEEDS[channel]:
        for item in parse_feed(source, url):
            link = item.get("url", "").strip()
            title_key = clean(item.get("title", "")).lower()

            if not link or link in seen_urls or not title_key:
                continue
            if title_key in seen_titles:
                continue

            seen_urls.add(link)
            seen_titles.add(title_key)
            item["_date"] = parse_date(item.get("published"))
            gathered.append(item)

    gathered = classify(gathered, channel)

    now = datetime.now(timezone.utc)
    gathered = [
        item for item in gathered
        if -3600 < (now - item["_date"]).total_seconds() < 5 * 86400
    ]
    gathered.sort(key=lambda item: item["_date"], reverse=True)

    results = []

    for source_item in gathered:
        if len(results) >= LIMITS[channel]:
            break

        original_title = source_item["title"]
        original_summary = source_item.get("summary", "")

        if channel == "china" and has_chinese(original_title):
            title = original_title
        else:
            title = google_translate(original_title)

        if channel == "china" and has_chinese(original_summary):
            summary = original_summary
        else:
            summary = google_translate(original_summary)

        item = {
            "title": title or original_title,
            "originalTitle": original_title,
            "summary": (summary or original_summary)[:650],
            "source": source_item["source"],
            "url": source_item["url"],
            "publishedAt": source_item["_date"].astimezone(TZ_CN).strftime(
                "%Y-%m-%d %H:%M"
            ),
            "timestamp": int(source_item["_date"].timestamp()),
            "impact": "待分析",
            "asset": [],
            "analysis": "",
            "risk": "",
            "aiStatus": "未分析",
        }

        ai = ai_insight({
            "title": item["title"],
            "source": item["source"],
            "summary": item["summary"],
            "url": item["url"],
        }, cache, budget)

        if ai:
            item.update({
                "impact": ai.get("impact") or "见 AI 分析",
                "asset": ai.get("assets", []),
                "analysis": ai.get("analysis", ""),
                "risk": ai.get("risk", ""),
                "aiStatus": "Gemini AI",
            })

        results.append(item)

    # 按实际显示条数倒序编号，确保从 NO.N 到 NO.01。
    total = len(results)
    for index, item in enumerate(results):
        item["id"] = f"NO.{total - index:02d}"

    return results


def safe_json(obj):
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def main():
    if not HTML_FILE.exists():
        raise FileNotFoundError(f"找不到网页文件：{HTML_FILE}")

    cache = load_cache()
    budget = {"attempts": 0, "successes": 0}

    news = {}
    for channel in ("global", "asia", "china", "earnings"):
        print(f"\n========== 更新栏目：{channel} ==========")
        news[channel] = build_channel(channel, cache, budget)

    news["updatedAt"] = datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    news["aiEnabled"] = bool(
        os.environ.get("GEMINI_API_KEY", "").strip()
    )
    news["aiModel"] = os.environ.get(
        "GEMINI_MODEL", "gemini-2.5-flash"
    ).strip()

    page = HTML_FILE.read_text(encoding="utf-8")
    marker_start = "<!-- NEWS_DATA_START -->"
    marker_end = "<!-- NEWS_DATA_END -->"

    if marker_start not in page or marker_end not in page:
        raise RuntimeError(
            "index.html 缺少 NEWS_DATA_START / NEWS_DATA_END 标记，"
            "已停止写入以保护网页文件。"
        )

    start = page.index(marker_start) + len(marker_start)
    end = page.index(marker_end, start)

    block = (
        "\n<script>window.newsData = "
        + safe_json(news)
        + ";</script>\n"
    )
    page = page[:start] + block + page[end:]

    page = re.sub(
        r'(<span[^>]*id=["\']updateTime["\'][^>]*>).*?(</span>)',
        lambda match: (
            match.group(1) + html.escape(news["updatedAt"]) + match.group(2)
        ),
        page,
        flags=re.I | re.S,
    )

    HTML_FILE.write_text(page, encoding="utf-8")
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n========== 更新结果 ==========")
    print("新闻数量：", {
        key: len(value)
        for key, value in news.items()
        if isinstance(value, list)
    })
    print(
        f"Gemini 调用尝试：{budget['attempts']}/"
        f"{MAX_AI_CALLS_PER_RUN}"
    )
    print(f"Gemini 成功生成分析：{budget['successes']} 条")

    if not news["aiEnabled"]:
        print(
            "[提示] 未配置 GEMINI_API_KEY。新闻抓取仍会运行，"
            "但不会生成真正的 Gemini 分析。"
        )


if __name__ == "__main__":
    main()

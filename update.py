import datetime
import email.utils
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from pathlib import Path
from zoneinfo import ZoneInfo


# ============================================================
# 1. 基础配置
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "index.html"

BEIJING_TZ = ZoneInfo("Asia/Shanghai")

GLOBAL_COUNT = 10
ASIA_COUNT = 5
CHINA_COUNT = 5

# 翻译开关：
# True：尝试翻译英文标题和摘要。
# False：保留信源原文，不请求第三方翻译服务。
ENABLE_TRANSLATION = True

# 翻译请求之间的间隔，减少触发服务限制的可能。
TRANSLATION_INTERVAL = 0.3

# 只显示最近多少天发布的新闻。
# 如需展示更早的新闻，可以调整此值。
MAX_NEWS_AGE_DAYS = 7

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# 频道标记与页面保持一致。
CHANNELS = {
    "global": {
        "tag": "全球宏观",
        "count": GLOBAL_COUNT,
    },
    "asia": {
        "tag": "亚太市场",
        "count": ASIA_COUNT,
    },
    "china": {
        "tag": "国内核心",
        "count": CHINA_COUNT,
    },
}

# 这里配置的是 RSS/XML 地址。
# 个别媒体可能调整订阅地址、限制地区访问或限制抓取。
# 如果某个地址不可用，程序会记录错误并继续尝试其他来源。
RSS_SOURCES = {
    "global": [
        {
            "name": "CNBC World",
            "url": (
                "https://www.cnbc.com/id/100727362/"
                "device/rss/rss.html"
            ),
        },
        {
            "name": "CNBC Finance",
            "url": (
                "https://www.cnbc.com/id/10000664/"
                "device/rss/rss.html"
            ),
        },
        {
            "name": "CNBC Economy",
            "url": (
                "https://www.cnbc.com/id/20910258/"
                "device/rss/rss.html"
            ),
        },
    ],

    "asia": [
        {
            "name": "CNA Asia",
            "url": (
                "https://www.channelnewsasia.com/api/"
                "v1/rss-outbound-feed?_format=xml&category=6511"
            ),
        },
        {
            "name": "CNA Business",
            "url": (
                "https://www.channelnewsasia.com/api/"
                "v1/rss-outbound-feed?_format=xml&category=6936"
            ),
        },
        {
            "name": "CNBC Asia",
            "url": (
                "https://www.cnbc.com/id/19832390/"
                "device/rss/rss.html"
            ),
        },
        {
            "name": "Nikkei Asia",
            # 使用原脚本中的订阅地址。
            # 若该地址失效，请从日经亚洲官方 RSS 页面更新。
            "url": "https://asia.nikkei.com/rss/feed/nar",
        },
    ],
}


# ============================================================
# 2. 北京时间
# ============================================================

def now_beijing():
    """获取带时区的北京时间。"""
    return datetime.datetime.now(BEIJING_TZ)


def format_beijing_time(dt=None):
    if dt is None:
        dt = now_beijing()

    return dt.astimezone(BEIJING_TZ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# 3. HTTP 请求
# ============================================================

def http_get(url, timeout=12):
    """发送 HTTP GET 请求，返回原始响应字节。"""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": (
                "application/rss+xml, application/xml, "
                "text/xml, application/json, */*"
            ),
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=timeout,
    ) as response:

        return response.read()


# ============================================================
# 4. 文本清理
# ============================================================

def clean_html(text):
    """移除 HTML 标签、清理实体和空白字符。"""

    if not text:
        return ""

    text = html.unescape(str(text))

    # 移除 script 和 style 内容。
    text = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>",
        " ",
        text,
        flags=re.I | re.S,
    )

    # 移除 HTML 标签。
    text = re.sub(r"<[^>]+>", " ", text)

    # 清理空白字符。
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def normalize_text(text):
    """用于去重的标准化文本。"""

    text = clean_html(text).lower()

    # 去掉常见标点和空白。
    text = re.sub(r"[\W_]+", "", text)

    return text


# ============================================================
# 5. 新闻发布时间解析
# ============================================================

def parse_datetime(value):
    """
    解析 RSS 常见时间格式。

    支持 RFC 822，例如：
    Tue, 18 Oct 2026 09:30:00 GMT

    也尝试解析 ISO 8601 格式。
    """

    if not value:
        return None

    value = str(value).strip()

    try:
        dt = email.utils.parsedate_to_datetime(value)

        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(
                    tzinfo=datetime.timezone.utc
                )

            return dt.astimezone(BEIJING_TZ)

    except (TypeError, ValueError, OverflowError):
        pass

    try:
        iso_value = value.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(iso_value)

        if dt.tzinfo is None:
            # 没有时区的信息无法可靠确定其原始时区。
            # 不将其直接当作北京时间。
            return None

        return dt.astimezone(BEIJING_TZ)

    except (TypeError, ValueError, OverflowError):
        return None


# ============================================================
# 6. RSS/XML 解析
# ============================================================

def get_child_text(element, names):
    """
    根据标签本地名称获取 XML 子节点内容。
    兼容带 XML namespace 的 RSS/Atom。
    """

    names = set(names)

    for child in element.iter():
        local_name = child.tag.split("}")[-1]

        if local_name in names:
            if child.text:
                value = child.text.strip()

                if value:
                    return value

    return ""


def parse_rss_xml(xml_bytes, source_name):
    """解析 RSS 2.0、Atom 等常见 XML 新闻源。"""

    items = []

    try:
        root = ET.fromstring(xml_bytes)

    except ET.ParseError as exc:
        print(
            f"⚠️ XML 解析失败 [{source_name}]: {exc}"
        )
        return items

    for element in root.iter():

        local_name = element.tag.split("}")[-1]

        if local_name not in ("item", "entry"):
            continue

        title = get_child_text(
            element,
            ["title"],
        )

        description = get_child_text(
            element,
            [
                "description",
                "summary",
                "encoded",
                "content",
            ],
        )

        published_text = get_child_text(
            element,
            [
                "pubDate",
                "published",
                "updated",
                "date",
            ],
        )

        link = ""

        # RSS 中 link 通常是文本。
        # Atom 中 link 通常是 href 属性。
        for child in element.iter():

            child_name = child.tag.split("}")[-1]

            if child_name != "link":
                continue

            href = child.attrib.get("href", "").strip()

            if href:
                rel = child.attrib.get("rel", "alternate")

                if rel == "alternate":
                    link = href
                    break

                if not link:
                    link = href

            elif child.text and child.text.strip():
                link = child.text.strip()
                break

        title = clean_html(title)
        description = clean_html(description)

        published = parse_datetime(published_text)

        if not title:
            continue

        items.append({
            "title": title,
            "desc": description,
            "published": published,
            "published_raw": published_text,
            "url": link,
            "source": source_name,
        })

    return items


# ============================================================
# 7. RSS 新闻抓取
# ============================================================

def fetch_rss_source(source):
    """抓取单个 RSS 来源。"""

    source_name = source["name"]
    rss_url = source["url"]

    try:
        xml_bytes = http_get(rss_url, timeout=12)

        items = parse_rss_xml(
            xml_bytes,
            source_name,
        )

        print(
            f"✅ RSS [{source_name}]："
            f"获取 {len(items)} 条"
        )

        return items

    except Exception as exc:

        print(
            f"⚠️ RSS 抓取失败 [{source_name}]：{exc}"
        )

        return []


def fetch_rss_channel(channel):
    """按频道独立抓取新闻。"""

    all_items = []

    for source in RSS_SOURCES.get(channel, []):

        items = fetch_rss_source(source)

        all_items.extend(items)

    return all_items


# ============================================================
# 8. 国内财经：新浪财经快讯 API
# ============================================================

def fetch_china_direct():
    """
    尝试抓取新浪财经直播快讯。

    注意：
    该接口的字段结构可能变化。
    如果接口不可用，程序不会伪造新闻。
    """

    items = []

    url = (
        "https://zhibo.sina.com.cn/api/zhibo/feed"
        "?page=1&page_size=50&zhibo_id=152"
    )

    try:

        raw_bytes = http_get(
            url,
            timeout=12,
        )

        data = json.loads(
            raw_bytes.decode("utf-8")
        )

        feed_list = (
            data.get("result", {})
            .get("data", {})
            .get("feed", [])
        )

        if not isinstance(feed_list, list):
            print("⚠️ 新浪财经接口返回结构异常")
            return items

        for feed in feed_list:

            rich_text = feed.get("rich_text", "")

            content = clean_html(rich_text)

            if len(content) < 8:
                continue

            # 优先解析“【标题】内容”格式。
            match = re.match(
                r"^【([^】]+)】\s*(.*)$",
                content,
            )

            if match:

                title = match.group(1).strip()
                description = match.group(2).strip()

            else:

                title = content[:80].strip()
                description = content

            # 尽可能读取接口提供的真实时间。
            raw_time = (
                feed.get("create_time")
                or feed.get("ctime")
                or feed.get("time")
                or feed.get("created_at")
                or ""
            )

            published = parse_datetime(raw_time)

            # 有些接口以 Unix 时间戳返回时间。
            if published is None and raw_time:

                try:

                    timestamp = float(raw_time)

                    # 毫秒时间戳转换为秒。
                    if timestamp > 10**12:
                        timestamp /= 1000

                    published = datetime.datetime.fromtimestamp(
                        timestamp,
                        tz=datetime.timezone.utc,
                    ).astimezone(BEIJING_TZ)

                except (TypeError, ValueError, OverflowError, OSError):
                    pass

            # 尝试提取原文链接。
            link = (
                feed.get("url")
                or feed.get("link")
                or feed.get("wap_url")
                or ""
            )

            items.append({
                "title": title,
                "desc": description,
                "published": published,
                "published_raw": str(raw_time),
                "url": link,
                "source": "新浪财经",
            })

        print(
            f"✅ 国内财经 [新浪财经]："
            f"获取 {len(items)} 条"
        )

    except Exception as exc:

        print(
            f"⚠️ 国内财经抓取失败 [新浪财经]：{exc}"
        )

    return items


# ============================================================
# 9. 翻译
# ============================================================

def contains_chinese(text):
    return bool(
        re.search(r"[\u4e00-\u9fff]", text or "")
    )


def translate_to_chinese(text):
    """
    尝试使用 MyMemory 翻译服务。

    失败时保留原文，不生成虚假翻译。
    已经包含中文的文本不再翻译。
    """

    text = clean_html(text)

    if not text:
        return ""

    if not ENABLE_TRANSLATION:
        return text

    if contains_chinese(text):
        return text

    # 限制单次翻译长度，避免请求过大。
    source_text = text[:450]

    api_url = (
        "https://api.mymemory.translated.net/get?"
        + urllib.parse.urlencode({
            "q": source_text,
            "langpair": "en|zh-CN",
        })
    )

    try:

        time.sleep(TRANSLATION_INTERVAL)

        response_bytes = http_get(
            api_url,
            timeout=6,
        )

        result = json.loads(
            response_bytes.decode("utf-8")
        )

        translated = clean_html(
            result.get("responseData", {}).get(
                "translatedText",
                "",
            )
        )

        # 避免将明显失败的结果当作翻译。
        if (
            translated
            and translated.lower() != source_text.lower()
            and len(translated) >= 3
        ):
            return translated

    except Exception as exc:

        print(f"⚠️ 翻译失败，保留原文：{exc}")

    return text


# ============================================================
# 10. 新闻筛选与去重
# ============================================================

def is_recent(item):
    """
    筛选最近的新闻。

    对没有可靠发布时间的条目，不伪造发布时间。
    允许保留该条目，但在页面中显示“时间未知”。
    """

    published = item.get("published")

    if published is None:
        return True

    now = now_beijing()

    age = now - published

    if age < datetime.timedelta(minutes=-10):
        # 发布时间明显位于未来，可能是错误数据。
        return False

    return age <= datetime.timedelta(
        days=MAX_NEWS_AGE_DAYS
    )


def deduplicate_news(items):
    """
    根据标题去重。
    同一新闻被多个信源转载时，只保留首次出现的版本。
    """

    result = []
    seen_titles = set()
    seen_urls = set()

    for item in items:

        title = clean_html(
            item.get("title", "")
        )

        if not title:
            continue

        title_key = normalize_text(title)

        url = item.get("url", "").strip()

        if not title_key:
            continue

        if title_key in seen_titles:
            continue

        if url and url in seen_urls:
            continue

        seen_titles.add(title_key)

        if url:
            seen_urls.add(url)

        result.append(item)

    return result


def sort_news(items):
    """
    按真实发布时间从新到旧排序。
    没有可靠时间的条目放在已知时间的条目之后。
    """

    return sorted(
        items,
        key=lambda item: (
            item.get("published") is not None,
            item.get("published")
            or datetime.datetime.min.replace(
                tzinfo=BEIJING_TZ
            ),
        ),
        reverse=True,
    )


# ============================================================
# 11. 影响分析
# ============================================================

def estimate_impact(title, description):
    """
    提供谨慎的关键词提示，不将新闻直接等同于市场涨跌。

    这里只是新闻分类提示，不是实时行情判断，
    也不是投资建议。
    """

    text = f"{title} {description}".lower()

    rules = [
        (
            [
                "interest rate",
                "rate cut",
                "rate hike",
                "federal reserve",
                "美联储",
                "降息",
                "加息",
                "利率",
            ],
            "关注利率预期、债券收益率及成长股估值变化。",
            "美债收益率 / 美元 / 成长股",
        ),
        (
            [
                "inflation",
                "cpi",
                "ppi",
                "通胀",
                "物价",
            ],
            "关注通胀数据对货币政策预期的影响。",
            "债券 / 美元 / 股票指数",
        ),
        (
            [
                "tariff",
                "trade war",
                "trade restrictions",
                "关税",
                "贸易限制",
                "贸易摩擦",
            ],
            "关注贸易成本、企业盈利及供应链风险。",
            "出口企业 / 制造业 / 相关指数",
        ),
        (
            [
                "oil price",
                "crude oil",
                "opec",
                "原油",
                "石油",
            ],
            "关注能源价格变化及其对通胀、运输成本的影响。",
            "能源 / 航运 / 化工",
        ),
        (
            [
                "earnings",
                "quarterly results",
                "profit",
                "业绩",
                "净利润",
                "营收",
            ],
            "关注业绩变化与市场预期之间的差异。",
            "相关上市公司 / 行业指数",
        ),
        (
            [
                "china",
                "chinese",
                "中国",
                "人民币",
                "a-share",
                "a股",
            ],
            "关注中国宏观政策、人民币汇率及相关市场表现。",
            "A股 / 人民币 / 港股",
        ),
    ]

    for keywords, impact, asset in rules:

        if any(keyword in text for keyword in keywords):

            return impact, asset

    return (
        "需结合原文、官方数据及实时行情进一步判断影响。",
        "相关市场指数 / 行业板块",
    )


# ============================================================
# 12. 构建频道数据
# ============================================================

def build_channel(raw_items, channel):
    """
    构建前端新闻对象。

    重要：
    这里不会在新闻不足时生成占位新闻。
    """

    tag = CHANNELS[channel]["tag"]
    target_count = CHANNELS[channel]["count"]

    # 只保留有效新闻。
    valid_items = []

    for item in raw_items:

        if not clean_html(item.get("title", "")):
            continue

        if not is_recent(item):
            continue

        valid_items.append(item)

    valid_items = deduplicate_news(valid_items)
    valid_items = sort_news(valid_items)

    final_items = []

    for item in valid_items[:target_count]:

        raw_title = clean_html(
            item.get("title", "")
        )

        raw_desc = clean_html(
            item.get("desc", "")
        )

        # 翻译标题。
        title = translate_to_chinese(
            raw_title
        )

        # 摘要过长时先截断，减少翻译请求大小。
        desc = translate_to_chinese(
            raw_desc[:450]
        )

        if len(desc) > 180:
            desc = desc[:180].rstrip() + "…"

        if not desc:
            desc = "该新闻未提供可用摘要，请查看原文。"

        published = item.get("published")

        if published:

            time_text = published.strftime(
                "%H:%M"
            )

            published_at = published.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        else:

            time_text = "时间未知"
            published_at = ""

        impact, asset = estimate_impact(
            raw_title,
            raw_desc,
        )

        final_items.append({
            "title": title,
            "summary": desc,
            "tag": tag,
            "time": time_text,
            "publishedAt": published_at,
            "source": item.get("source", "未知来源"),
            "url": item.get("url", ""),
            "impact": impact,
            "asset": asset,
        })

    return final_items


# ============================================================
# 13. 国内新闻补充来源（可选）
# ============================================================

def fetch_china_rss():
    """
    可在 RSS_SOURCES 中增加国内财经 RSS 来源。

    默认不随意添加未经验证的第三方地址，
    避免将失效源或不相关内容混入国内频道。
    """

    sources = RSS_SOURCES.get("china", [])

    all_items = []

    for source in sources:
        all_items.extend(
            fetch_rss_source(source)
        )

    return all_items


# ============================================================
# 14. 组装三大新闻频道
# ============================================================

def collect_all_news():

    print("=" * 60)
    print("开始抓取财经新闻")
    print("北京时间：", format_beijing_time())
    print("=" * 60)

    # 三个频道分别抓取，绝不跨频道填充。
    raw_global = fetch_rss_channel("global")

    raw_asia = fetch_rss_channel("asia")

    raw_china = fetch_china_direct()

    # 如配置国内 RSS，合并国内 RSS 与新浪财经快讯。
    raw_china.extend(fetch_china_rss())

    final_global = build_channel(
        raw_global,
        "global",
    )

    final_asia = build_channel(
        raw_asia,
        "asia",
    )

    final_china = build_channel(
        raw_china,
        "china",
    )

    news_data = {
        "global": final_global,
        "asia": final_asia,
        "china": final_china,
    }

    for channel, items in news_data.items():

        print(
            f"频道 {CHANNELS[channel]['tag']}："
            f"实际展示 {len(items)} 条"
        )

    return news_data


# ============================================================
# 15. 安全生成 JavaScript
# ============================================================

def to_js_json(data):
    """
    使用 JSON 序列化，避免标题中的引号、
    反斜杠或换行符破坏 JavaScript。
    """

    text = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    # 避免 HTML 中的特殊字符导致 script 标签问题。
    text = (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )

    return text


def build_news_script(news_data):

    data_json = to_js_json(news_data)

    return (
        "<script>\n"
        f"const newsData = {data_json};\n"
        "</script>"
    )


# ============================================================
# 16. 更新 index.html
# ============================================================

def update_html(news_data):

    if not HTML_FILE.exists():

        raise FileNotFoundError(
            f"找不到网页文件：{HTML_FILE}\n"
            "请将 update_news.py 与 index.html 放在同一目录。"
        )

    content = HTML_FILE.read_text(
        encoding="utf-8"
    )

    start_marker = "<!-- NEWS_DATA_START -->"
    end_marker = "<!-- NEWS_DATA_END -->"

    if (
        start_marker not in content
        or end_marker not in content
    ):

        raise ValueError(
            "index.html 中缺少 NEWS_DATA_START "
            "或 NEWS_DATA_END 标记。\n"
            "请检查 HTML 文件，程序不会覆盖原文件。"
        )

    start_index = content.index(
        start_marker
    )

    end_index = content.index(
        end_marker,
        start_index + len(start_marker),
    )

    if end_index < start_index:

        raise ValueError(
            "新闻区域标记顺序错误。"
        )

    news_script = build_news_script(
        news_data
    )

    replacement = (
        start_marker
        + "\n"
        + news_script
        + "\n"
        + end_marker
    )

    updated_content = (
        content[:start_index]
        + replacement
        + content[
            end_index + len(end_marker):
        ]
    )

    # 更新时间。
    time_pattern = (
        r'<div\s+id="updateTime"[^>]*>'
        r'.*?</div>'
    )

    time_replacement = (
        '<div id="updateTime" '
        'class="text-slate-400 mt-1 sm:mt-0">'
        f'更新时间: {format_beijing_time()} (北京时间)'
        '</div>'
    )

    if re.search(
        time_pattern,
        updated_content,
        flags=re.I | re.S,
    ):

        updated_content = re.sub(
            time_pattern,
            lambda _: time_replacement,
            updated_content,
            count=1,
            flags=re.I | re.S,
        )

    else:

        print(
            "⚠️ 未找到 updateTime 元素，"
            "新闻可以更新，但页面更新时间未修改。"
        )

    # 先写入临时文件，再替换原文件。
    temp_file = HTML_FILE.with_suffix(
        ".html.tmp"
    )

    try:

        temp_file.write_text(
            updated_content,
            encoding="utf-8",
        )

        os.replace(
            temp_file,
            HTML_FILE,
        )

    finally:

        if temp_file.exists():

            try:
                temp_file.unlink()
            except OSError:
                pass

    print("=" * 60)
    print("✅ 财经新闻更新完成")
    print("更新时间：", format_beijing_time())
    print("文件位置：", HTML_FILE)
    print(
        "全球宏观：",
        len(news_data["global"]),
        "条",
    )
    print(
        "亚太市场：",
        len(news_data["asia"]),
        "条",
    )
    print(
        "国内核心：",
        len(news_data["china"]),
        "条",
    )
    print("=" * 60)


# ============================================================
# 17. 程序入口
# ============================================================

def main():

    try:

        news_data = collect_all_news()

        update_html(news_data)

    except Exception as exc:

        print(
            f"❌ 更新失败：{type(exc).__name__}: {exc}"
        )

        raise


if __name__ == "__main__":
    main()

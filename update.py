
import os
import re
import json
import time
import html
import hashlib
import requests
import feedparser

from datetime import datetime, timezone, timedelta
from collections import OrderedDict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urlparse, parse_qsl, urlencode, urlunparse
from dotenv import load_dotenv


# ============================================================
# 1. 基础配置：与现有 index.html 保持兼容
# ============================================================

load_dotenv()

API_BASE_URL = "https://api.deepseek.com"
API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
AI_MODEL = "deepseek-chat"

TZ = timezone(timedelta(hours=8))
UTC = timezone.utc

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "index.html"
CACHE_FILE = BASE_DIR / "ai_cache.json"
MARKET_CAP_CACHE = BASE_DIR / "market_cap_cache.json"

TIME_LIMIT = timedelta(hours=24)
MAX_AI_CALLS_PER_RUN = 3

CHANNEL_LIMITS = {
    "global": 20,
    "asia": 10,
    "china": 10,
    "earnings": 10
}

MARKET_CAP_THRESHOLD = 1_000_000_000_000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*"
}

CHANNELS = ["global", "asia", "china", "earnings"]


# ============================================================
# 2. RSS 内容清理
# ============================================================

class RSSTextParser(HTMLParser):
    """提取 RSS HTML 中的可见文字，不保留标签和链接地址。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)

    def get_text(self):
        return " ".join(self.parts)


def clean_text(value, limit=600):
    if value is None:
        return ""

    value = html.unescape(str(value))

    parser = RSSTextParser()

    try:
        parser.feed(value)
        value = parser.get_text()
    except Exception:
        value = re.sub(r"<[^>]*>", " ", value)

    # 清除 URL、HTML 残留和常见 RSS 尾巴
    value = re.sub(r"https?://\S+", " ", value, flags=re.I)
    value = re.sub(r"www\.\S+", " ", value, flags=re.I)
    value = re.sub(r"<[^>]*>", " ", value)
    value = re.sub(r"\bRead more\b.*$", "", value, flags=re.I)
    value = re.sub(r"\bClick here\b.*$", "", value, flags=re.I)
    value = re.sub(r"\bSubscribe now\b.*$", "", value, flags=re.I)
    value = re.sub(r"\bSign up for\b.*$", "", value, flags=re.I)
    value = re.sub(r"\s+", " ", value).strip()

    value = value.strip(" \t\r\n-–—|·•:：;；")

    if len(value) > limit:
        value = value[:limit].rstrip(" ，,。；;") + "…"

    return value


def has_chinese(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def normalize_url(url):
    """移除跟踪参数，保留文章路径。"""

    if not url:
        return ""

    try:
        parsed = urlparse(url.strip())

        tracking_prefixes = (
            "utm_", "fbclid", "gclid", "ocid",
            "cmpid", "guccounter", "guce_referrer"
        )

        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith(tracking_prefixes)
            and key.lower() not in ("oc", "ved", "usg")
        ]

        return urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            urlencode(query),
            ""
        ))

    except Exception:
        return url.strip()


def article_fingerprint(title):
    title = clean_text(title, 300).lower()
    title = re.sub(r"[^\w\u4e00-\u9fff]+", "", title)
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


def google_news_feed(query, language="en"):
    if language == "zh":
        return (
            "https://news.google.com/rss/search?q="
            + quote(query)
            + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        )

    return (
        "https://news.google.com/rss/search?q="
        + quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


# ============================================================
# 3. 新闻源
# ============================================================

FEEDS = [

    # 国际财经
    {
        "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "source": "CNBC",
        "channels": ["global"]
    },
    {
        "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "source": "BBC Business",
        "channels": ["global"]
    },
    {
        "url": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
        "source": "WSJ Markets",
        "channels": ["global", "earnings"]
    },
    {
        "url": google_news_feed("Reuters world business economy markets"),
        "source": "Reuters",
        "channels": ["global"]
    },
    {
        "url": google_news_feed("Bloomberg global finance economy markets"),
        "source": "Bloomberg",
        "channels": ["global"]
    },
    {
        "url": google_news_feed(
            '"Federal Reserve" OR FOMC meeting rate decision'
        ),
        "source": "美联储会议报道",
        "channels": ["global"]
    },

    # 亚太时政财经
    {
        "url": "https://feeds.bbci.co.uk/news/world/asia/rss.xml",
        "source": "BBC Asia",
        "channels": ["asia"]
    },
    {
        "url": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511",
        "source": "CNA Asia",
        "channels": ["asia"]
    },
    {
        "url": "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936",
        "source": "CNA Business",
        "channels": ["asia"]
    },
    {
        "url": "https://feeds.feedburner.com/rsscna/politics",
        "source": "台湾中央社政治",
        "channels": ["asia"]
    },
    {
        "url": "https://feeds.feedburner.com/rsscna/intworld",
        "source": "台湾中央社国际",
        "channels": ["asia"]
    },
    {
        "url": "https://feeds.feedburner.com/rsscna/finance",
        "source": "台湾中央社财经",
        "channels": ["asia"]
    },
    {
        "url": "https://feeds.feedburner.com/rsscna/technology",
        "source": "台湾中央社科技",
        "channels": ["asia"]
    },
    {
        "url": "https://www3.nhk.or.jp/rj/podcast/rss/english.xml",
        "source": "NHK World",
        "channels": ["asia"]
    },
    {
        "url": "https://www.scmp.com/rss/91/feed",
        "source": "南华早报",
        "channels": ["asia"]
    },
    {
        "url": google_news_feed(
            '(site:asia.nikkei.com OR site:japantimes.co.jp '
            'OR site:english.kyodonews.net) '
            'Japan politics economy business'
        ),
        "source": "日本主流媒体",
        "channels": ["asia"]
    },
    {
        "url": google_news_feed(
            '(site:scmp.com OR site:hongkongfp.com) '
            'Hong Kong politics economy business'
        ),
        "source": "香港主流媒体",
        "channels": ["asia"]
    },
    {
        "url": google_news_feed(
            '(site:koreaherald.com OR site:koreatimes.co.kr) '
            'South Korea politics economy business'
        ),
        "source": "韩国主流媒体",
        "channels": ["asia"]
    },
    {
        "url": google_news_feed(
            '(site:channelnewsasia.com OR site:straitstimes.com) '
            'Southeast Asia politics economy'
        ),
        "source": "东南亚主流媒体",
        "channels": ["asia"]
    },

    # 国内财经：只接受明确指定的大陆财经媒体
    {
        "url": google_news_feed(
            "site:caixin.com 中国财经 经济 金融",
            "zh"
        ),
        "source": "财新网",
        "channels": ["china"],
        "allowed_domains": ["caixin.com"],
        "allowed_names": ["财新", "caixin"]
    },
    {
        "url": google_news_feed(
            "site:yicai.com 中国财经 A股 经济",
            "zh"
        ),
        "source": "第一财经",
        "channels": ["china"],
        "allowed_domains": ["yicai.com"],
        "allowed_names": ["第一财经", "yicai"]
    },
    {
        "url": google_news_feed(
            "site:21jingji.com 中国财经 经济 金融",
            "zh"
        ),
        "source": "21世纪经济报道",
        "channels": ["china"],
        "allowed_domains": ["21jingji.com"],
        "allowed_names": ["21世纪经济报道", "21jingji"]
    },
    {
        "url": google_news_feed(
            "site:nbd.com.cn 中国财经 A股 公司",
            "zh"
        ),
        "source": "每日经济新闻",
        "channels": ["china"],
        "allowed_domains": ["nbd.com.cn"],
        "allowed_names": ["每日经济新闻", "nbd"]
    },
    {
        "url": google_news_feed(
            "site:finance.sina.com.cn A股 中国经济",
            "zh"
        ),
        "source": "新浪财经",
        "channels": ["china"],
        "allowed_domains": ["sina.com.cn"],
        "allowed_names": ["新浪财经", "新浪"]
    },
    {
        "url": google_news_feed(
            "site:wallstreetcn.com 中国经济 A股 财经",
            "zh"
        ),
        "source": "华尔街见闻",
        "channels": ["china"],
        "allowed_domains": ["wallstreetcn.com"],
        "allowed_names": ["华尔街见闻", "wallstreetcn"]
    },

    # 核心财报
    {
        "url": "https://www.investors.com/feed/investing/",
        "source": "Investor's Business Daily",
        "channels": ["earnings"]
    },
    {
        "url": google_news_feed(
            "technology company earnings quarterly results revenue profit"
        ),
        "source": "全球科技企业财报",
        "channels": ["earnings"]
    }
]


# ============================================================
# 4. 国内新闻来源核验
# ============================================================

def china_source_is_allowed(entry, config):
    allowed_domains = config.get("allowed_domains", [])
    allowed_names = config.get("allowed_names", [])

    link = entry.get("link", "")
    source_info = entry.get("source", {}) or {}

    source_href = source_info.get("href", "")
    source_title = clean_text(source_info.get("title", ""), 100).lower()

    for candidate in (link, source_href):
        host = urlparse(candidate).netloc.lower()

        for domain in allowed_domains:
            if host == domain or host.endswith("." + domain):
                return True

    # Google News 链接本身是 news.google.com，
    # 此时只能依据其 RSS source.title 核验来源。
    for name in allowed_names:
        if name.lower() in source_title:
            return True

    return False


# ============================================================
# 5. 新闻关键词与优先级
# ============================================================

EARNINGS_KEYWORDS = {
    "earnings", "earning", "profit", "quarter",
    "quarterly", "results", "guidance", "revenue",
    "financial results", "net income", "fiscal",
    "财报", "业绩", "季度", "指引", "营收",
    "净利润", "盈利", "财务报告"
}

FED_MEETING_KEYWORDS = {
    "fomc meeting",
    "federal reserve meeting",
    "fed meeting",
    "fed rate decision",
    "fomc decision",
    "fomc statement",
    "fed policy decision",
    "federal reserve policy",
    "fed interest rate decision",
    "美联储会议",
    "美联储利率决议",
    "美联储议息会议",
    "美联储政策会议",
    "fomc会议",
    "联邦公开市场委员会会议"
}


def is_earnings_news(text):
    text = (text or "").lower()
    return any(k in text for k in EARNINGS_KEYWORDS)


def is_fed_meeting_news(text):
    text = (text or "").lower()
    return any(k in text for k in FED_MEETING_KEYWORDS)


# ============================================================
# 6. 动态市值名单
# ============================================================

def load_market_cap_cache():
    """
    只接受带有动态更新时间标记的缓存。
    旧版静态备用名单不视为已经核实的当前市值。
    """

    if not MARKET_CAP_CACHE.exists():
        return {}

    try:
        data = json.loads(
            MARKET_CAP_CACHE.read_text(encoding="utf-8")
        )

        if isinstance(data, dict) and data.get("_dynamic") is True:
            companies = data.get("companies", {})
            if isinstance(companies, dict):
                return companies

    except Exception as e:
        print(f"⚠️ 市值缓存读取失败：{e}")

    return {}


def refresh_mega_cap_companies():
    companies = {}

    try:
        url = (
            "https://companiesmarketcap.com/tech/"
            "largest-tech-companies-by-market-cap/"
        )

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )
        response.raise_for_status()

        rows = re.findall(
            r"<tr[^>]*>(.*?)</tr>",
            response.text,
            flags=re.S | re.I
        )

        for row in rows:
            name_match = re.search(
                r'<div class="company-name">\s*([^<]+)',
                row,
                flags=re.I
            )

            symbol_match = re.search(
                r'<div class="company-code">\s*([^<]+)',
                row,
                flags=re.I
            )

            if not name_match:
                continue

            # 优先读取标注为 market cap 的单元格，
            # 不再把整行第一个金额误当成市值。
            cap_match = re.search(
                r'<td[^>]*(?:data-label=["\']Market Cap["\']|class=["\'][^"\']*market-cap[^"\']*["\'])[^>]*>'
                r'(.*?)</td>',
                row,
                flags=re.S | re.I
            )

            if not cap_match:
                continue

            cap_text = clean_text(cap_match.group(1), 100)
            value_match = re.search(
                r"\$?\s*([\d,.]+)\s*(T|B|M)\b",
                cap_text,
                flags=re.I
            )

            if not value_match:
                continue

            value = float(value_match.group(1).replace(",", ""))
            unit = value_match.group(2).upper()

            multiplier = {
                "T": 1_000_000_000_000,
                "B": 1_000_000_000,
                "M": 1_000_000
            }[unit]

            market_cap = value * multiplier

            if market_cap < MARKET_CAP_THRESHOLD:
                continue

            name = clean_text(
                html.unescape(name_match.group(1)),
                100
            )

            symbol = (
                clean_text(symbol_match.group(1), 30)
                if symbol_match else ""
            )

            aliases = [name.lower()]

            if symbol:
                aliases.append(symbol.lower())

            companies[name] = aliases

        if companies:
            data = {
                "_dynamic": True,
                "updated_at": datetime.now(TZ).isoformat(),
                "companies": companies
            }

            MARKET_CAP_CACHE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            print(
                f"✅ 动态市值名单更新成功：{len(companies)} 家"
            )

            return companies

        print("⚠️ 市值页面没有解析出可核实的市值数据")

    except Exception as e:
        print(f"⚠️ 市值名单刷新失败：{e}")

    cached = load_market_cap_cache()

    if cached:
        print("ℹ️ 使用上次成功核验的动态市值名单")
        return cached

    print("⚠️ 暂无可核实的万亿美元公司名单，本轮不授予财报优先资格")
    return {}


# 每次运行前确保文件存在，避免 workflow git add 报错
if not MARKET_CAP_CACHE.exists():
    MARKET_CAP_CACHE.write_text(
        json.dumps(
            {
                "_dynamic": False,
                "companies": {}
            },
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

MEGA_CAP_COMPANIES = refresh_mega_cap_companies()


def find_mega_cap_company(text):
    text = (text or "").lower()

    for company, aliases in MEGA_CAP_COMPANIES.items():
        for alias in aliases:
            alias = alias.lower().strip()

            if not alias:
                continue

            # 股票代码及英文简称按完整单词匹配
            if re.fullmatch(r"[a-z0-9.]+", alias):
                pattern = (
                    r"(?<![a-z0-9])"
                    + re.escape(alias)
                    + r"(?![a-z0-9])"
                )

                if re.search(pattern, text):
                    return company

            elif alias in text:
                return company

    return None


def is_eligible_mega_cap_earnings(entry):
    text = (
        entry.get("title", "")
        + " "
        + entry.get("summary", "")
    )

    return bool(
        find_mega_cap_company(text)
        and is_earnings_news(text)
    )


def get_importance_weight(entry, channels):
    text = entry.get("title", "") + " " + entry.get("summary", "")

    score = 50

    if "earnings" in channels and is_earnings_news(text):
        score += 15

    if find_mega_cap_company(text):
        score += 20

    if is_fed_meeting_news(text):
        score += 50

    return min(score, 100)


# ============================================================
# 7. 时间处理与新闻抓取
# ============================================================

def parse_entry_time(entry):
    parsed = (
        entry.get("published_parsed")
        or entry.get("updated_parsed")
    )

    if not parsed:
        return None

    try:
        return datetime(*parsed[:6], tzinfo=UTC)
    except Exception:
        return None


def fetch_all_news():
    now = datetime.now(UTC)

    # URL 去重 + 标题去重；同一篇文章合并板块归属
    news_by_url = {}
    title_to_url = {}

    for config in FEEDS:
        source_name = config["source"]

        try:
            response = requests.get(
                config["url"],
                headers=HEADERS,
                timeout=20
            )
            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if not feed.entries:
                print(f"⚠️ 新闻源无内容：{source_name}")
                continue

            feed_count = 0

            for entry in feed.entries:
                url = entry.get("link", "").strip()

                if not url:
                    continue

                # 国内板块必须通过来源核验
                if "china" in config["channels"]:
                    if not china_source_is_allowed(entry, config):
                        continue

                published = parse_entry_time(entry)

                # 不接受没有明确发布时间的新闻
                if not published:
                    continue

                age = now - published

                if age > TIME_LIMIT:
                    continue

                if age < timedelta(minutes=-30):
                    continue

                title = clean_text(entry.get("title", ""), 220)
                summary = clean_text(
                    entry.get("summary", "")
                    or entry.get("description", ""),
                    700
                )

                if not title:
                    continue

                normalized_url = normalize_url(url)
                fingerprint = article_fingerprint(title)

                existing_key = normalized_url

                # 同标题不同跟踪链接也尽量合并
                if existing_key not in news_by_url:
                    existing_key = title_to_url.get(fingerprint, normalized_url)

                if existing_key in news_by_url:
                    old = news_by_url[existing_key]

                    old["channels"] = sorted(
                        set(old["channels"] + config["channels"])
                    )

                    if len(summary) > len(old["summary"]):
                        old["summary"] = summary

                    # 优先保留更完整的来源名称
                    if old["source"] in ("Google News", ""):
                        old["source"] = source_name

                    continue

                source_info = entry.get("source", {}) or {}
                actual_source = clean_text(
                    source_info.get("title", ""),
                    80
                )

                # 国内新闻来源已经核验，显示指定媒体名
                if "china" in config["channels"]:
                    display_source = source_name
                elif actual_source and "news.google.com" in urlparse(url).netloc:
                    display_source = actual_source
                else:
                    display_source = source_name

                item = {
                    "source": display_source,
                    "url": url,
                    "publishedAt": published.astimezone(TZ).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    "title": title,
                    "summary": summary,
                    "channels": list(config["channels"]),
                    "_timestamp": int(published.timestamp())
                }

                item["_importance"] = get_importance_weight(
                    item,
                    item["channels"]
                )

                item["_fed_meeting"] = (
                    "global" in item["channels"]
                    and is_fed_meeting_news(title + " " + summary)
                )

                item["_mega_cap_earnings"] = (
                    "earnings" in item["channels"]
                    and is_eligible_mega_cap_earnings(item)
                )

                news_by_url[normalized_url] = item
                title_to_url[fingerprint] = normalized_url

                feed_count += 1

            print(f"✅ {source_name}：有效 {feed_count} 条")

        except Exception as e:
            print(f"⚠️ 新闻源失败 {source_name}：{e}")

    news = list(news_by_url.values())

    news.sort(
        key=lambda x: (
            x["_importance"],
            x["_timestamp"]
        ),
        reverse=True
    )

    print(f"📊 去重合并后新闻：{len(news)} 条")
    return news


# ============================================================
# 8. AI 缓存与中文翻译缓存
# ============================================================

def load_cache():
    if not CACHE_FILE.exists():
        return OrderedDict()

    try:
        data = json.loads(
            CACHE_FILE.read_text(encoding="utf-8"),
            object_pairs_hook=OrderedDict
        )

        if isinstance(data, dict):
            return data

    except Exception as e:
        print(f"⚠️ AI 缓存读取失败：{e}")

    return OrderedDict()


def save_cache(cache):
    # 兼容原有缓存格式，保留最近1200条
    trimmed = OrderedDict(list(cache.items())[-1200:])

    CACHE_FILE.write_text(
        json.dumps(
            trimmed,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def make_cache_key(entry):
    # 不加入频道，同一文章跨频道共用缓存
    raw = normalize_url(entry.get("url", ""))

    if not raw:
        raw = (
            entry.get("source", "")
            + "|"
            + entry.get("title", "")
        )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def translation_cache_key(text):
    digest = hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()

    return "translation:" + digest


def translate_to_chinese(text, cache):
    """
    中文翻译与 DeepSeek 深度分析分离。
    普通新闻也会翻译，不受每轮 DeepSeek 3 次限制。
    """

    text = clean_text(text, 700)

    if not text:
        return ""

    if has_chinese(text):
        # 已经有中文的内容不重复翻译
        return text

    key = translation_cache_key(text)

    if cache.get(key):
        return cache[key]

    try:
        response = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": "zh-CN",
                "dt": "t",
                "q": text
            },
            headers=HEADERS,
            timeout=15
        )

        response.raise_for_status()
        data = response.json()

        translated = "".join(
            part[0]
            for part in data[0]
            if part and part[0]
        )

        translated = clean_text(translated, 700)

        if translated and has_chinese(translated):
            cache[key] = translated
            return translated

    except Exception as e:
        print(f"⚠️ 中文翻译失败：{str(e)[:150]}")

    # 翻译接口不可用时不伪造中文，保留干净原文
    return text


# ============================================================
# 9. DeepSeek 深度分析：每轮最多3次
# ============================================================

def call_deepseek_ai(entry):
    if not API_KEY:
        return None, "未配置 DEEPSEEK_API_KEY"

    prompt = f"""
请分析下面这条财经、宏观或时政新闻，使用简体中文输出。

标题：{entry['title']}
摘要：{entry['summary']}
来源：{entry['source']}

要求：
1. 准确翻译标题，不改变事实。
2. 摘要简洁客观。
3. 解释核心逻辑。
4. 分析潜在市场影响。
5. 写明风险和不确定性。
6. 不得编造数据、事实或结论。
7. 输出合法 JSON，不要输出 Markdown。

格式：
{{
  "zh_title": "中文标题",
  "zh_summary": "中文摘要",
  "importance": 70,
  "analysis": "100字以内的分析",
  "coreLogic": "核心逻辑",
  "marketImpact": "潜在市场影响",
  "risk": "风险提示"
}}
"""

    try:
        response = requests.post(
            API_BASE_URL + "/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + API_KEY
            },
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.1,
                "max_tokens": 700,
                "response_format": {"type": "json_object"}
            },
            timeout=45
        )

        response.raise_for_status()

        content = response.json()["choices"][0]["message"]["content"]

        content = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            content.strip(),
            flags=re.I | re.M
        )

        data = json.loads(content)

        for field in (
            "zh_title", "zh_summary", "analysis",
            "coreLogic", "marketImpact", "risk"
        ):
            if not isinstance(data.get(field), str):
                data[field] = ""

        try:
            data["importance"] = int(data.get("importance", 50))
        except (TypeError, ValueError):
            data["importance"] = 50

        data["importance"] = max(
            0,
            min(100, data["importance"])
        )

        return data, None

    except Exception as e:
        return None, str(e)


# ============================================================
# 10. 板块排序
# ============================================================

def sort_channel_news(channel, entries):
    if channel == "global":
        fed = [
            item for item in entries
            if item.get("_fed_meeting")
        ]

        others = [
            item for item in entries
            if not item.get("_fed_meeting")
        ]

        fed.sort(
            key=lambda x: (
                x.get("_importance", 0),
                x.get("_timestamp", 0)
            ),
            reverse=True
        )

        others.sort(
            key=lambda x: (
                x.get("_importance", 0),
                x.get("_timestamp", 0)
            ),
            reverse=True
        )

        return fed + others

    if channel == "earnings":
        priority = [
            item for item in entries
            if item.get("_mega_cap_earnings")
        ]

        others = [
            item for item in entries
            if not item.get("_mega_cap_earnings")
        ]

        priority.sort(
            key=lambda x: (
                x.get("_importance", 0),
                x.get("_timestamp", 0)
            ),
            reverse=True
        )

        others.sort(
            key=lambda x: (
                x.get("_importance", 0),
                x.get("_timestamp", 0)
            ),
            reverse=True
        )

        # 符合市值条件的科技公司财报优先进入前三
        return priority[:3] + others + priority[3:]

    return sorted(
        entries,
        key=lambda x: (
            x.get("_importance", 0),
            x.get("_timestamp", 0)
        ),
        reverse=True
    )


# ============================================================
# 11. 构建新闻对象
# ============================================================

def build_news_item(entry, cache):
    key = make_cache_key(entry)
    ai_data = cache.get(key)

    item = {
        "source": entry["source"],
        "url": entry["url"],
        "publishedAt": entry["publishedAt"],
        "title": entry["title"],
        "summary": entry["summary"],
        "_timestamp": entry["_timestamp"],
        "_cache_key": key,
        "_importance": entry["_importance"],
        "_fed_meeting": entry["_fed_meeting"],
        "_mega_cap_earnings": entry["_mega_cap_earnings"]
    }

    if ai_data:
        item["title"] = ai_data.get("zh_title") or item["title"]
        item["summary"] = ai_data.get("zh_summary") or item["summary"]
        item["analysis"] = ai_data.get("analysis", "")
        item["impact"] = ai_data.get("marketImpact", "")
        item["coreLogic"] = ai_data.get("coreLogic", "")
        item["risk"] = ai_data.get("risk", "")
        item["_importance"] = ai_data.get(
            "importance",
            item["_importance"]
        )
    else:
        item["ai_msg"] = "本条新闻暂未进行 AI 深度分析"

    return item


# ============================================================
# 12. 主程序
# ============================================================

def main():
    print("=" * 65)
    print("财经与时政焦点看板：开始更新")
    print("=" * 65)

    # 每次启动都确保两个缓存文件存在
    if not CACHE_FILE.exists():
        CACHE_FILE.write_text("{}", encoding="utf-8")

    if not MARKET_CAP_CACHE.exists():
        MARKET_CAP_CACHE.write_text(
            json.dumps(
                {"_dynamic": False, "companies": {}},
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    all_news = fetch_all_news()
    cache = load_cache()

    channel_pools = {
        channel: [] for channel in CHANNELS
    }

    for entry in all_news:
        for channel in entry["channels"]:
            if channel in channel_pools:
                channel_pools[channel].append(entry)

    processed_by_channel = {}

    for channel in CHANNELS:
        processed = [
            build_news_item(entry, cache)
            for entry in channel_pools[channel]
        ]

        processed_by_channel[channel] = sort_channel_news(
            channel,
            processed
        )

    # --------------------------------------------------------
    # A. 先翻译每个板块最终可能展示的新闻
    #    不调用 DeepSeek，不占用3次深度分析额度
    # --------------------------------------------------------

    print("🌐 开始处理各板块中文标题和摘要")

    for channel in CHANNELS:
        selected = processed_by_channel[channel][
            :CHANNEL_LIMITS[channel]
        ]

        for item in selected:
            item["title"] = translate_to_chinese(
                item["title"],
                cache
            )

            item["summary"] = translate_to_chinese(
                item["summary"],
                cache
            )

            item["title"] = clean_text(item["title"], 180)
            item["summary"] = clean_text(item["summary"], 420)

    # --------------------------------------------------------
    # B. 选择最多3条进行 DeepSeek 深度分析
    #    同一新闻跨板块只进入一次候选
    # --------------------------------------------------------

    candidate_map = {}

    for channel in CHANNELS:
        selected = processed_by_channel[channel][
            :CHANNEL_LIMITS[channel]
        ]

        for item in selected:
            key = item["_cache_key"]

            if cache.get(key):
                continue

            if key not in candidate_map:
                candidate_map[key] = item

    candidates = list(candidate_map.values())

    def ai_priority(item):
        score = item.get("_importance", 0)

        if item.get("_fed_meeting"):
            score += 100

        if item.get("_mega_cap_earnings"):
            score += 80

        return score

    candidates.sort(
        key=ai_priority,
        reverse=True
    )

    candidates = candidates[:MAX_AI_CALLS_PER_RUN]

    attempts = 0
    successes = 0

    print(
        f"🧠 DeepSeek 本轮候选：{len(candidates)} 条，"
        f"上限：{MAX_AI_CALLS_PER_RUN} 条"
    )

    for index, item in enumerate(candidates, 1):
        print(
            f"🧠 深度分析 {index}/{len(candidates)}："
            f"{item['title'][:65]}"
        )

        attempts += 1

        ai_data, error = call_deepseek_ai(item)

        if ai_data:
            cache[item["_cache_key"]] = ai_data
            successes += 1
        else:
            print(f"⚠️ DeepSeek 分析失败：{error}")

        time.sleep(0.4)

    # --------------------------------------------------------
    # C. AI 结果回填所有板块
    # --------------------------------------------------------

    result = OrderedDict()

    result["updatedAt"] = datetime.now(TZ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    result["aiEnabled"] = bool(API_KEY)
    result["aiModel"] = AI_MODEL

    result["aiStats"] = {
        "attempts": attempts,
        "successes": successes
    }

    for channel in CHANNELS:
        final_items = []

        for item in processed_by_channel[channel]:
            ai_data = cache.get(item["_cache_key"])

            if ai_data:
                item["title"] = (
                    ai_data.get("zh_title")
                    or item["title"]
                )

                item["summary"] = (
                    ai_data.get("zh_summary")
                    or item["summary"]
                )

                item["analysis"] = ai_data.get("analysis", "")
                item["impact"] = ai_data.get("marketImpact", "")
                item["coreLogic"] = ai_data.get("coreLogic", "")
                item["risk"] = ai_data.get("risk", "")

                item["_importance"] = ai_data.get(
                    "importance",
                    item["_importance"]
                )

                item.pop("ai_msg", None)

            # AI 不是中文化的唯一途径；再次清理并翻译
            item["title"] = clean_text(item["title"], 180)
            item["summary"] = clean_text(item["summary"], 420)

            item["title"] = translate_to_chinese(
                item["title"],
                cache
            )

            item["summary"] = translate_to_chinese(
                item["summary"],
                cache
            )

            # 清理 AI 字段，避免异常内容过长
            for field, limit in (
                ("analysis", 500),
                ("impact", 500),
                ("coreLogic", 350),
                ("risk", 350)
            ):
                if field in item:
                    item[field] = clean_text(item[field], limit)

            final_items.append(item)

        final_items = sort_channel_news(
            channel,
            final_items
        )

        selected = final_items[:CHANNEL_LIMITS[channel]]

        # 保持现有 index.html 使用的字段，不输出内部字段
        for item in selected:
            item.pop("_timestamp", None)
            item.pop("_cache_key", None)
            item.pop("_fed_meeting", None)
            item.pop("_mega_cap_earnings", None)
            item.pop("_importance", None)

        result[channel] = selected

    # --------------------------------------------------------
    # D. 保存缓存
    # --------------------------------------------------------

    save_cache(cache)

    # 再次确保 workflow git add 的文件存在
    if not MARKET_CAP_CACHE.exists():
        MARKET_CAP_CACHE.write_text(
            json.dumps(
                {"_dynamic": False, "companies": {}},
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    # --------------------------------------------------------
    # E. 只替换 index.html 的新闻数据区域
    # --------------------------------------------------------

    if not OUTPUT_FILE.exists():
        raise FileNotFoundError(
            f"找不到网页文件：{OUTPUT_FILE}"
        )

    page = OUTPUT_FILE.read_text(encoding="utf-8")

    start_marker = "<!-- NEWS_DATA_START -->"
    end_marker = "<!-- NEWS_DATA_END -->"

    if start_marker not in page or end_marker not in page:
        raise RuntimeError(
            "index.html 缺少新闻数据标记。"
            "为保护原页面，停止写入。"
        )

    json_data = json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )

    injection = (
        start_marker
        + "\n<script>window.newsData="
        + json_data
        + ";</script>\n"
        + end_marker
    )

    new_page = re.sub(
        re.escape(start_marker)
        + r".*?"
        + re.escape(end_marker),
        lambda _: injection,
        page,
        flags=re.S
    )

    OUTPUT_FILE.write_text(
        new_page,
        encoding="utf-8"
    )

    # --------------------------------------------------------
    # F. 更新报告
    # --------------------------------------------------------

    print("=" * 65)
    print("✅ 四大板块更新完成")

    print(f"国际财经：{len(result['global'])} 条")
    print(f"亚太财经：{len(result['asia'])} 条")
    print(f"国内财经：{len(result['china'])} 条")
    print(f"核心财报：{len(result['earnings'])} 条")

    print(
        f"DeepSeek 调用：{attempts}/{MAX_AI_CALLS_PER_RUN}"
    )
    print(f"DeepSeek 成功：{successes} 次")

    print("中文翻译：独立处理，不占用 DeepSeek 调用额度")
    print("RSS 清理：已启用")
    print("新闻时效：最近24小时")
    print("跨板块去重：已启用")
    print("市值缓存文件：已确保存在")

    print("=" * 65)


if __name__ == "__main__":
    main()

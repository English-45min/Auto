
import os
import re
import json
import time
import base64
import html
import requests
import feedparser

from datetime import datetime, timezone, timedelta
from collections import OrderedDict
from urllib.parse import quote, urlparse
from dotenv import load_dotenv
from pathlib import Path


# ============================================================
# 基础配置
# ============================================================

load_dotenv()

API_BASE_URL = "https://api.deepseek.com"
API_KEY = os.getenv("DEEPSEEK_API_KEY")
AI_MODEL = "deepseek-chat"

TZ = timezone(timedelta(hours=8))

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "index.html"
CACHE_FILE = BASE_DIR / "ai_cache.json"

# 每轮最多调用 AI 12 次
MAX_AI_DAILY_CALLS = 12

# 新闻时效：最近 24 小时
TIME_LIMIT = timedelta(hours=24)

# 每个板块展示数量
CHANNEL_LIMITS = {
    "global": 20,
    "asia": 10,
    "china": 10,
    "earnings": 10
}


# ============================================================
# 动态市值识别
# ============================================================

MARKET_CAP_THRESHOLD = 1_000_000_000_000

MARKET_CAP_CACHE = BASE_DIR / "market_cap_cache.json"

# 初始备用名单。
# 每次运行优先从公开市值榜单刷新，失败时使用此名单。
# 这不是固定的最终名单。
FALLBACK_MEGA_CAP_COMPANIES = {
    "NVIDIA": ["nvidia", "英伟达", "nvda"],
    "Apple": ["apple", "苹果", "aapl"],
    "Microsoft": ["microsoft", "微软", "msft"],
    "Alphabet": ["alphabet", "google", "谷歌", "googl", "goog"],
    "Amazon": ["amazon", "亚马逊", "amzn"],
    "Meta": ["meta platforms", "meta", "facebook", "脸书"],
    "TSMC": ["tsmc", "taiwan semiconductor", "台积电"],
    "Broadcom": ["broadcom", "博通", "avgo"],
    "Tesla": ["tesla", "特斯拉", "tsla"],
    "Tencent": ["tencent", "腾讯", "0700"],
    "Alibaba": ["alibaba", "阿里巴巴", "baba"],
}


def refresh_mega_cap_companies():
    """
    从 CompaniesMarketCap 公开榜单获取公司市值。
    仅保留市值超过 1 万亿美元的科技公司。
    如果外部数据源不可用，则使用最近缓存；
    如果没有缓存，则使用备用名单。
    """

    companies = {}

    try:
        url = "https://companiesmarketcap.com/tech/largest-tech-companies-by-market-cap/"

        response = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=15
        )

        response.raise_for_status()

        page = response.text

        rows = re.findall(
            r'<tr[^>]*>(.*?)</tr>',
            page,
            flags=re.S | re.I
        )

        for row in rows:
            name_match = re.search(
                r'<div class="company-name">([^<]+)</div>',
                row,
                flags=re.I
            )

            symbol_match = re.search(
                r'<div class="company-code">([^<]+)</div>',
                row,
                flags=re.I
            )

            cap_match = re.search(
                r'<td[^>]*class="[^"]*td-right[^"]*"[^>]*>\s*'
                r'\$?([\d,.]+)\s*(T|B|M)?',
                row,
                flags=re.I
            )

            if not name_match or not cap_match:
                continue

            name = html.unescape(name_match.group(1)).strip()
            symbol = symbol_match.group(1).strip() if symbol_match else ""

            value = float(cap_match.group(1).replace(",", ""))
            unit = (cap_match.group(2) or "").upper()

            if unit == "T":
                market_cap = value * 1_000_000_000_000
            elif unit == "B":
                market_cap = value * 1_000_000_000
            elif unit == "M":
                market_cap = value * 1_000_000
            else:
                market_cap = value

            if market_cap < MARKET_CAP_THRESHOLD:
                continue

            # 名称和代码同时建立匹配词
            aliases = [name.lower()]

            if symbol:
                aliases.append(symbol.lower())

            companies[name] = aliases

        if companies:
            MARKET_CAP_CACHE.write_text(
                json.dumps(companies, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

            print(
                f"✅ 已刷新万亿美元科技公司名单：{len(companies)} 家"
            )

            return companies

    except Exception as e:
        print(f"⚠️ 市值榜单刷新失败：{e}")

    # 使用最近一次成功缓存
    if MARKET_CAP_CACHE.exists():
        try:
            cached = json.loads(
                MARKET_CAP_CACHE.read_text(encoding="utf-8")
            )

            if cached:
                print("ℹ️ 使用上次成功保存的市值名单")
                return cached

        except Exception:
            pass

    print("⚠️ 使用备用科技公司名单")
    return FALLBACK_MEGA_CAP_COMPANIES


MEGA_CAP_COMPANIES = refresh_mega_cap_companies()


def find_mega_cap_company(text):
    text = text.lower()

    for company, aliases in MEGA_CAP_COMPANIES.items():
        for alias in aliases:
            if alias and alias.lower() in text:
                return company

    return None


# ============================================================
# 新闻关键词
# ============================================================

EARNINGS_KEYWORDS = {
    "earnings", "earning", "profit", "quarter",
    "quarterly", "results", "guidance",
    "revenue", "financial results",
    "net income", "fiscal",
    "财报", "业绩", "季度", "指引",
    "营收", "净利润", "盈利", "财务报告"
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
    text = text.lower()

    return any(
        keyword in text
        for keyword in EARNINGS_KEYWORDS
    )


def is_fed_meeting_news(text):
    text = text.lower()

    return any(
        keyword in text
        for keyword in FED_MEETING_KEYWORDS
    )


def get_importance_weight(title, channels):
    title_lower = title.lower()

    weight = 50

    if "earnings" in channels:
        weight += 15

    if is_earnings_news(title_lower):
        weight += 10

    if find_mega_cap_company(title_lower):
        weight += 35

    if is_fed_meeting_news(title_lower):
        weight += 50

    return min(100, weight)


# ============================================================
# RSS 新闻源
# ============================================================

def google_news_feed(query):
    return (
        "https://news.google.com/rss/search?q="
        + quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


FEEDS = [

    # --------------------------------------------------------
    # 国际财经
    # --------------------------------------------------------

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
            "Federal Reserve FOMC meeting interest rate decision"
        ),
        "source": "美联储会议报道",
        "channels": ["global"]
    },


    # --------------------------------------------------------
    # 亚太时政财经
    # --------------------------------------------------------

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


    # --------------------------------------------------------
    # 国内财经
    # --------------------------------------------------------

    {
        "url": "https://www.nbd.com.cn/rss",
        "source": "每日经济新闻",
        "channels": ["china"]
    },

    {
        "url": google_news_feed(
            '(site:caixin.com OR site:yicai.com '
            'OR site:21jingji.com OR site:cs.com.cn) 中国财经'
        ),
        "source": "大陆财经媒体",
        "channels": ["china"]
    },


    # --------------------------------------------------------
    # 核心财报
    # --------------------------------------------------------

    {
        "url": "https://www.investors.com/feed/investing/",
        "source": "Investor's Business Daily",
        "channels": ["earnings"]
    },

    {
        "url": google_news_feed(
            "company earnings quarterly results revenue guidance"
        ),
        "source": "全球企业财报",
        "channels": ["earnings"]
    },

    {
        "url": "https://sc.hkexnews.hk/rss",
        "source": "港交所公告",
        "channels": ["asia", "china", "earnings"]
    }
]


# ============================================================
# 新闻抓取与时效过滤
# ============================================================

def normalize_url(url):
    try:
        parsed = urlparse(url)

        return (
            parsed.scheme.lower()
            + "://"
            + parsed.netloc.lower()
            + parsed.path.rstrip("/")
        )

    except Exception:
        return url


def fetch_all_news():

    now = datetime.now(timezone.utc)

    seen_urls = set()
    news_pool = []

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*"
    }

    for feed_config in FEEDS:

        try:

            response = requests.get(
                feed_config["url"],
                headers=headers,
                timeout=20
            )

            response.raise_for_status()

            feed = feedparser.parse(response.content)

            if feed.bozo and not feed.entries:
                print(
                    f"⚠️ RSS 格式异常：{feed_config['source']}"
                )
                continue

            feed_count = 0

            for entry in feed.entries:

                url = entry.get("link", "").strip()

                if not url:
                    continue

                normalized = normalize_url(url)

                if normalized in seen_urls:
                    continue

                pub_time = (
                    entry.get("published_parsed")
                    or entry.get("updated_parsed")
                )

                # 不可靠或缺少发布时间的新闻不进入列表
                if not pub_time:
                    continue

                try:

                    dt = datetime(
                        *pub_time[:6],
                        tzinfo=timezone.utc
                    )

                    age = now - dt

                    # 只收最近24小时，允许最多30分钟时钟误差
                    if age > TIME_LIMIT:
                        continue

                    if age < timedelta(minutes=-30):
                        continue

                    published_at = dt.astimezone(TZ).strftime(
                        "%Y-%m-%d %H:%M"
                    )

                except Exception:
                    continue

                title = html.unescape(
                    entry.get("title", "").strip()
                )

                summary = html.unescape(
                    entry.get("summary", "").strip()
                )

                if not title:
                    continue

                channels = feed_config["channels"]

                weight = get_importance_weight(
                    title,
                    channels
                )

                news_pool.append({

                    "title": title,
                    "summary": summary,
                    "url": url,
                    "source": feed_config["source"],
                    "publishedAt": published_at,
                    "publishedTimestamp": int(dt.timestamp()),
                    "channels": channels,
                    "_weight": weight

                })

                seen_urls.add(normalized)
                feed_count += 1

            print(
                f"✅ {feed_config['source']}：抓取 {feed_count} 条有效新闻"
            )

        except Exception as e:

            print(
                f"⚠️ 新闻源失败 {feed_config['source']}：{e}"
            )

            continue

    # 先按重要性，再按发布时间
    news_pool.sort(
        key=lambda x: (
            x["_weight"],
            x["publishedTimestamp"]
        ),
        reverse=True
    )

    print(
        f"📊 本轮共抓取有效新闻：{len(news_pool)} 条"
    )

    return news_pool


# ============================================================
# AI 缓存
# ============================================================

def load_cache():

    if CACHE_FILE.exists():

        try:

            return json.loads(
                CACHE_FILE.read_text(encoding="utf-8"),
                object_pairs_hook=OrderedDict
            )

        except Exception:
            return OrderedDict()

    return OrderedDict()


def save_cache(cache_data):

    items = list(cache_data.items())[-500:]

    CACHE_FILE.write_text(
        json.dumps(
            OrderedDict(items),
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


# ============================================================
# DeepSeek AI 分析
# ============================================================

def call_deepseek_ai(entry):

    if not API_KEY:
        return None, "🟠 DeepSeek API 密钥未检测到"

    text_en = (
        f"Title: {entry['title']}\n"
        f"Summary: {entry['summary']}\n"
        f"Source: {entry['source']}"
    )

    prompt = f"""
Input text:
'''
{text_en}
'''

Task:
Perform financial, geopolitical and macroeconomic analysis.
Generate a structured JSON object in simplified Chinese.

Requirements:
1. Translate the title accurately.
2. Write a concise summary.
3. Explain the core logic.
4. Explain potential market impact.
5. Include risk warnings.
6. Do not invent facts.

JSON:
{{
  "zh_title": "精准的中文标题",
  "zh_summary": "精准简练的中文摘要",
  "importance": 70,
  "analysis": "100字以内的深度分析",
  "coreLogic": "核心逻辑",
  "marketImpact": "潜在市场影响",
  "risk": "风险提示"
}}
"""

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }

    payload = {

        "model": AI_MODEL,

        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],

        "temperature": 0.1,

        "response_format": {
            "type": "json_object"
        }

    }

    try:

        response = requests.post(
            f"{API_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=45
        )

        response.raise_for_status()

        data = response.json()

        content = data["choices"][0]["message"]["content"]

        clean_content = re.sub(
            r"^```json\s*|\s*```$",
            "",
            content.strip(),
            flags=re.MULTILINE
        )

        ai_data = json.loads(clean_content)

        if "importance" not in ai_data:
            ai_data["importance"] = 50

        ai_data["importance"] = max(
            0,
            min(100, int(ai_data["importance"]))
        )

        return ai_data, None

    except Exception as e:

        return None, f"🟠 AI 分析不可用：{str(e)}"


# ============================================================
# 新闻优先级判定
# ============================================================

def is_eligible_mega_cap_earnings(entry):

    text = (
        entry.get("title", "")
        + " "
        + entry.get("summary", "")
    ).lower()

    company = find_mega_cap_company(text)

    if not company:
        return False

    if not is_earnings_news(text):
        return False

    return True


def is_eligible_fed_meeting(entry):

    text = (
        entry.get("title", "")
        + " "
        + entry.get("summary", "")
    )

    return is_fed_meeting_news(text)


def sort_channel_news(channel, entries):

    if channel == "global":

        fed_news = [
            item for item in entries
            if item.get("_fed_meeting")
        ]

        other_news = [
            item for item in entries
            if not item.get("_fed_meeting")
        ]

        fed_news.sort(
            key=lambda x: x.get("_importance", 0),
            reverse=True
        )

        other_news.sort(
            key=lambda x: (
                x.get("_importance", 0),
                x.get("_timestamp", 0)
            ),
            reverse=True
        )

        return fed_news + other_news

    if channel == "earnings":

        priority_news = [
            item for item in entries
            if item.get("_mega_cap_earnings")
        ]

        other_news = [
            item for item in entries
            if not item.get("_mega_cap_earnings")
        ]

        priority_news.sort(
            key=lambda x: (
                x.get("_importance", 0),
                x.get("_timestamp", 0)
            ),
            reverse=True
        )

        other_news.sort(
            key=lambda x: (
                x.get("_importance", 0),
                x.get("_timestamp", 0)
            ),
            reverse=True
        )

        return priority_news + other_news

    return sorted(
        entries,
        key=lambda x: (
            x.get("_importance", 0),
            x.get("_timestamp", 0)
        ),
        reverse=True
    )


# ============================================================
# 主程序
# ============================================================

def main():

    print("=" * 60)
    print("财经与时政焦点看板：开始更新")
    print("=" * 60)

    all_news = fetch_all_news()

    cache = load_cache()

    result = OrderedDict()

    result["updatedAt"] = datetime.now(TZ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    result["aiEnabled"] = bool(API_KEY)
    result["aiModel"] = AI_MODEL

    result["aiStats"] = {
        "attempts": 0,
        "successes": 0
    }

    for channel in [
        "global",
        "asia",
        "china",
        "earnings"
    ]:

        result[channel] = []

    channel_pools = {
        "global": [],
        "asia": [],
        "china": [],
        "earnings": []
    }

    for entry in all_news:

        for channel in entry["channels"]:

            if channel in channel_pools:
                channel_pools[channel].append(entry)

    ai_call_count = 0

    # --------------------------------------------------------
    # 每个板块独立处理
    # --------------------------------------------------------

    for channel in [
        "global",
        "asia",
        "china",
        "earnings"
    ]:

        entries = channel_pools[channel]

        entries.sort(
            key=lambda x: (
                x["_weight"],
                x["publishedTimestamp"]
            ),
            reverse=True
        )

        processed = []

        for entry in entries:

            cache_key = base64.b64encode(
                f"{entry['url']}_{entry['source']}_{channel}".encode(
                    "utf-8"
                )
            ).decode("utf-8")

            ai_data = cache.get(cache_key)

            ai_msg = None

            # ------------------------------------------------
            # AI 分析
            # ------------------------------------------------

            if not ai_data:

                if ai_call_count < MAX_AI_DAILY_CALLS:

                    result["aiStats"]["attempts"] += 1

                    ai_call_count += 1

                    ai_data, ai_msg = call_deepseek_ai(entry)

                    if ai_data:

                        result["aiStats"]["successes"] += 1

                        cache[cache_key] = ai_data

                    else:

                        # 不缓存临时错误，下轮继续尝试
                        pass

                else:

                    ai_msg = "已达到本轮 AI 分析调用上限"

            # ------------------------------------------------
            # 生成新闻卡片数据
            # ------------------------------------------------

            news_item = {

                "source": entry["source"],
                "url": entry["url"],
                "publishedAt": entry["publishedAt"],
                "title": entry["title"],
                "summary": entry["summary"],

                "_timestamp": entry["publishedTimestamp"]

            }

            if ai_data:

                news_item["title"] = ai_data.get(
                    "zh_title",
                    entry["title"]
                )

                news_item["summary"] = ai_data.get(
                    "zh_summary",
                    entry["summary"]
                )

                news_item["analysis"] = ai_data.get(
                    "analysis",
                    ""
                )

                news_item["impact"] = ai_data.get(
                    "marketImpact",
                    ""
                )

                news_item["coreLogic"] = ai_data.get(
                    "coreLogic",
                    ""
                )

                news_item["risk"] = ai_data.get(
                    "risk",
                    ""
                )

                news_item["_importance"] = ai_data.get(
                    "importance",
                    entry["_weight"]
                )

            else:

                news_item["ai_msg"] = (
                    ai_msg or "AI 分析暂不可用"
                )

                news_item["_importance"] = entry["_weight"]

            # ------------------------------------------------
            # 美联储会议置顶标记
            # ------------------------------------------------

            news_item["_fed_meeting"] = (
                channel == "global"
                and is_eligible_fed_meeting(entry)
            )

            # ------------------------------------------------
            # 万亿美元科技公司财报标记
            # ------------------------------------------------

            news_item["_mega_cap_earnings"] = (
                channel == "earnings"
                and is_eligible_mega_cap_earnings(entry)
            )

            processed.append(news_item)

        # ----------------------------------------------------
        # 排序并限制数量
        # ----------------------------------------------------

        processed = sort_channel_news(
            channel,
            processed
        )

        # 财报板块：符合条件的万亿美元科技公司财报最多优先占据前三
        result[channel] = processed[
            :CHANNEL_LIMITS[channel]
        ]

        # 前端不需要内部排序字段
        for item in result[channel]:

            item.pop("_timestamp", None)
            item.pop("_fed_meeting", None)
            item.pop("_mega_cap_earnings", None)

    # --------------------------------------------------------
    # 保存缓存
    # --------------------------------------------------------

    save_cache(cache)

    # --------------------------------------------------------
    # 写入 index.html 新闻数据
    # --------------------------------------------------------

    if not OUTPUT_FILE.exists():

        raise FileNotFoundError(
            f"找不到网页文件：{OUTPUT_FILE}"
        )

    page = OUTPUT_FILE.read_text(
        encoding="utf-8"
    )

    json_data_json = json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )

    injection_code = (
        "<!-- NEWS_DATA_START -->\n"
        f"<script>window.newsData={json_data_json};</script>\n"
        "<!-- NEWS_DATA_END -->"
    )

    if "<!-- NEWS_DATA_START -->" in page:

        new_page = re.sub(
            r"<!-- NEWS_DATA_START -->.*?<!-- NEWS_DATA_END -->",
            lambda match: injection_code,
            page,
            flags=re.S
        )

    else:

        new_page = page.replace(
            "</head>",
            injection_code + "\n</head>"
        )

    OUTPUT_FILE.write_text(
        new_page,
        encoding="utf-8"
    )

    print("=" * 60)
    print("✅ 四大板块新闻更新完成")
    print(f"国际财经：{len(result['global'])} 条")
    print(f"亚太时政财经：{len(result['asia'])} 条")
    print(f"国内财经：{len(result['china'])} 条")
    print(f"核心财报：{len(result['earnings'])} 条")
    print(
        f"AI 调用：{ai_call_count}/{MAX_AI_DAILY_CALLS}"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()

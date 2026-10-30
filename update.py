
import os
import re
import json
import time
import base64
import html
import hashlib
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
MARKET_CAP_CACHE = BASE_DIR / "market_cap_cache.json"

# 每轮最多调用 AI 3 次
MAX_AI_CALLS_PER_RUN = 3

# 新闻时效：最近24小时
TIME_LIMIT = timedelta(hours=24)

CHANNEL_LIMITS = {
    "global": 20,
    "asia": 10,
    "china": 10,
    "earnings": 10
}

MARKET_CAP_THRESHOLD = 1_000_000_000_000


# ============================================================
# 万亿美元科技公司动态名单
# ============================================================

FALLBACK_MEGA_CAP_COMPANIES = {
    "NVIDIA": ["nvidia", "英伟达", "nvda"],
    "Apple": ["apple", "苹果", "aapl"],
    "Microsoft": ["microsoft", "微软", "msft"],
    "Alphabet": ["alphabet", "google", "谷歌", "googl", "goog"],
    "Amazon": ["amazon", "亚马逊", "amzn"],
    "Meta": ["meta platforms", "facebook", "脸书", "meta"],
    "TSMC": ["tsmc", "taiwan semiconductor", "台积电"],
    "Broadcom": ["broadcom", "博通", "avgo"],
    "Tesla": ["tesla", "特斯拉", "tsla"],
    "Tencent": ["tencent", "腾讯", "0700"],
    "Alibaba": ["alibaba", "阿里巴巴", "baba"]
}


def refresh_mega_cap_companies():
    companies = {}

    try:
        url = "https://companiesmarketcap.com/tech/largest-tech-companies-by-market-cap/"

        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15
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

            # 优先寻找带 T/B 单位的市值字段
            cap_matches = re.findall(
                r"\$?\s*([\d,.]+)\s*(T|B|M)\b",
                row,
                flags=re.I
            )

            if not name_match or not cap_matches:
                continue

            name = html.unescape(name_match.group(1)).strip()
            symbol = (
                symbol_match.group(1).strip()
                if symbol_match else ""
            )

            # 榜单行中可能有其他金额，取第一个带单位的数值
            value_text, unit = cap_matches[0]
            value = float(value_text.replace(",", ""))

            unit = unit.upper()

            multiplier = {
                "T": 1_000_000_000_000,
                "B": 1_000_000_000,
                "M": 1_000_000
            }.get(unit, 1)

            market_cap = value * multiplier

            if market_cap < MARKET_CAP_THRESHOLD:
                continue

            aliases = [name.lower()]

            if symbol:
                aliases.append(symbol.lower())

            companies[name] = aliases

        if companies:
            MARKET_CAP_CACHE.write_text(
                json.dumps(
                    companies,
                    ensure_ascii=False,
                    indent=2
                ),
                encoding="utf-8"
            )

            print(
                f"✅ 动态市值名单刷新成功：{len(companies)} 家"
            )

            return companies

    except Exception as e:
        print(f"⚠️ 市值榜单刷新失败：{e}")

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

    # 即使动态抓取失败，也创建缓存文件，避免 GitHub Actions
    # 因 market_cap_cache.json 不存在而报错。
    MARKET_CAP_CACHE.write_text(
        json.dumps(
            FALLBACK_MEGA_CAP_COMPANIES,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )

    return FALLBACK_MEGA_CAP_COMPANIES


MEGA_CAP_COMPANIES = refresh_mega_cap_companies()


def find_mega_cap_company(text):
    text = text.lower()

    for company, aliases in MEGA_CAP_COMPANIES.items():
        for alias in aliases:
            alias = alias.lower().strip()

            if not alias:
                continue

            # 英文短代码使用单词边界，避免误匹配
            if re.fullmatch(r"[a-z0-9.]+", alias):
                if re.search(
                    r"(?<![a-z0-9])" + re.escape(alias) +
                    r"(?![a-z0-9])",
                    text
                ):
                    return company
            elif alias in text:
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
        + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )


def google_news_feed_en(query):
    return (
        "https://news.google.com/rss/search?q="
        + quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


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
        "url": google_news_feed_en("Reuters world business economy markets"),
        "source": "Reuters",
        "channels": ["global"]
    },
    {
        "url": google_news_feed_en("Bloomberg global finance economy markets"),
        "source": "Bloomberg",
        "channels": ["global"]
    },
    {
        "url": google_news_feed_en(
            "Federal Reserve FOMC meeting interest rate decision"
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
        "url": google_news_feed_en(
            '(site:asia.nikkei.com OR site:japantimes.co.jp '
            'OR site:english.kyodonews.net) '
            'Japan politics economy business'
        ),
        "source": "日本主流媒体",
        "channels": ["asia"]
    },
    {
        "url": google_news_feed_en(
            '(site:scmp.com OR site:hongkongfp.com) '
            'Hong Kong politics economy business'
        ),
        "source": "香港主流媒体",
        "channels": ["asia"]
    },
    {
        "url": google_news_feed_en(
            '(site:koreaherald.com OR site:koreatimes.co.kr) '
            'South Korea politics economy business'
        ),
        "source": "韩国主流媒体",
        "channels": ["asia"]
    },
    {
        "url": google_news_feed_en(
            '(site:channelnewsasia.com OR site:straitstimes.com) '
            'Southeast Asia politics economy'
        ),
        "source": "东南亚主流媒体",
        "channels": ["asia"]
    },

    # 国内财经
    {
        "url": "https://rss.sina.com.cn/roll/finance/hot_roll.xml",
        "source": "新浪财经",
        "channels": ["china"]
    },
    {
        "url": "https://dedicated.wallstreetcn.com/rss.xml",
        "source": "华尔街见闻",
        "channels": ["china"]
    },
    {
        "url": google_news_feed(
            "中国财经 A股 中国经济 央行 人民币 财政政策"
        ),
        "source": "中国财经新闻",
        "channels": ["china"]
    },
    {
        "url": google_news_feed(
            "site:caixin.com 中国 财经 经济"
        ),
        "source": "财新网",
        "channels": ["china"]
    },
    {
        "url": google_news_feed(
            "site:yicai.com 中国 财经 市场"
        ),
        "source": "第一财经",
        "channels": ["china"]
    },
    {
        "url": google_news_feed(
            "site:21jingji.com 财经 中国经济"
        ),
        "source": "21世纪经济报道",
        "channels": ["china"]
    },

    # 核心财报
    {
        "url": "https://www.investors.com/feed/investing/",
        "source": "Investor's Business Daily",
        "channels": ["earnings"]
    },
    {
        "url": google_news_feed_en(
            "company earnings quarterly results revenue guidance"
        ),
        "source": "全球企业财报",
        "channels": ["earnings"]
    },
    {
        "url": google_news_feed(
            "上市公司 财报 营收 净利润 季度业绩"
        ),
        "source": "上市公司财报",
        "channels": ["earnings"]
    }
]


# ============================================================
# 新闻抓取
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

                if not pub_time:
                    continue

                try:
                    dt = datetime(
                        *pub_time[:6],
                        tzinfo=timezone.utc
                    )

                    age = now - dt

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
            data = json.loads(
                CACHE_FILE.read_text(encoding="utf-8"),
                object_pairs_hook=OrderedDict
            )

            if isinstance(data, dict):
                return data

        except Exception as e:
            print(f"⚠️ AI 缓存读取失败：{e}")

    return OrderedDict()


def save_cache(cache_data):
    # 最多保留最近500条
    items = list(cache_data.items())[-500:]

    CACHE_FILE.write_text(
        json.dumps(
            OrderedDict(items),
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def make_cache_key(entry):
    # 不再加入 channel，同一篇新闻在不同板块只分析一次
    raw = (
        normalize_url(entry.get("url", ""))
        or (
            entry.get("source", "")
            + "_"
            + entry.get("title", "")
        )
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


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
7. Keep the analysis concise.

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
        "max_tokens": 700,
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
# 新闻优先级
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

        # 美联储会议报道优先置顶
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

        # 符合条件的万亿美元科技公司财报最多优先占据前三
        return priority_news[:3] + other_news + priority_news[3:]

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

    # --------------------------------------------------------
    # 先建立四个板块的新闻候选池
    # --------------------------------------------------------

    processed_by_channel = {}

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
            cache_key = make_cache_key(entry)
            ai_data = cache.get(cache_key)

            news_item = {
                "source": entry["source"],
                "url": entry["url"],
                "publishedAt": entry["publishedAt"],
                "title": entry["title"],
                "summary": entry["summary"],
                "_timestamp": entry["publishedTimestamp"],
                "_cache_key": cache_key
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
                    "本条新闻暂未进行 AI 深度分析"
                )

                news_item["_importance"] = entry["_weight"]

            news_item["_fed_meeting"] = (
                channel == "global"
                and is_eligible_fed_meeting(entry)
            )

            news_item["_mega_cap_earnings"] = (
                channel == "earnings"
                and is_eligible_mega_cap_earnings(entry)
            )

            processed.append(news_item)

        processed_by_channel[channel] = sort_channel_news(
            channel,
            processed
        )

    # --------------------------------------------------------
    # 只对最终展示范围内的新闻选择 AI 候选
    # 每条新闻只会进入一次候选队列
    # --------------------------------------------------------

    candidate_map = {}

    for channel in [
        "global",
        "asia",
        "china",
        "earnings"
    ]:
        limit = CHANNEL_LIMITS[channel]

        for item in processed_by_channel[channel][:limit]:
            key = item["_cache_key"]

            if key in cache:
                continue

            if key not in candidate_map:
                candidate_map[key] = item

    candidates = list(candidate_map.values())

    # 优先分析美联储会议、万亿美元科技公司财报
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

    print(
        f"🧠 本轮 AI 最多分析 {MAX_AI_CALLS_PER_RUN} 条，"
        f"实际候选 {len(candidates)} 条"
    )

    # --------------------------------------------------------
    # 调用 AI
    # --------------------------------------------------------

    for number, item in enumerate(candidates, 1):
        key = item["_cache_key"]

        print(
            f"🧠 AI 分析 {number}/{len(candidates)}："
            f"{item.get('title', '')[:70]}"
        )

        result["aiStats"]["attempts"] += 1

        ai_data, ai_msg = call_deepseek_ai(item)

        if ai_data:
            cache[key] = ai_data
            result["aiStats"]["successes"] += 1

        else:
            print(ai_msg or "AI 分析失败")

        time.sleep(0.5)

    # --------------------------------------------------------
    # 将 AI 结果回填所有板块
    # --------------------------------------------------------

    for channel in [
        "global",
        "asia",
        "china",
        "earnings"
    ]:
        processed = processed_by_channel[channel]

        # 按优先级重新排序
        for item in processed:
            key = item["_cache_key"]
            ai_data = cache.get(key)

            if ai_data:
                item["title"] = ai_data.get(
                    "zh_title",
                    item["title"]
                )

                item["summary"] = ai_data.get(
                    "zh_summary",
                    item["summary"]
                )

                item["analysis"] = ai_data.get(
                    "analysis",
                    ""
                )

                item["impact"] = ai_data.get(
                    "marketImpact",
                    ""
                )

                item["coreLogic"] = ai_data.get(
                    "coreLogic",
                    ""
                )

                item["risk"] = ai_data.get(
                    "risk",
                    ""
                )

                item["_importance"] = ai_data.get(
                    "importance",
                    item["_importance"]
                )

        processed = sort_channel_news(
            channel,
            processed
        )

        selected = processed[:CHANNEL_LIMITS[channel]]

        for item in selected:
            item.pop("_timestamp", None)
            item.pop("_fed_meeting", None)
            item.pop("_mega_cap_earnings", None)
            item.pop("_cache_key", None)

            if item.get("analysis"):
                item.pop("ai_msg", None)

        result[channel] = selected

    # --------------------------------------------------------
    # 保存缓存文件
    # --------------------------------------------------------

    save_cache(cache)

    # 确保市值缓存文件存在
    if not MARKET_CAP_CACHE.exists():
        MARKET_CAP_CACHE.write_text(
            json.dumps(
                FALLBACK_MEGA_CAP_COMPANIES,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

    # --------------------------------------------------------
    # 写入 index.html
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

    if (
        "<!-- NEWS_DATA_START -->" in page
        and "<!-- NEWS_DATA_END -->" in page
    ):
        new_page = re.sub(
            r"<!-- NEWS_DATA_START -->.*?<!-- NEWS_DATA_END -->",
            lambda match: injection_code,
            page,
            flags=re.S
        )
    else:
        raise RuntimeError(
            "index.html 缺少 NEWS_DATA_START / NEWS_DATA_END 标记，"
            "为保护原页面，已停止写入。"
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
        f"AI 调用：{result['aiStats']['attempts']}/"
        f"{MAX_AI_CALLS_PER_RUN}"
    )
    print(
        f"AI 成功：{result['aiStats']['successes']} 次"
    )
    print("=" * 60)


if __name__ == "__main__":
    main()

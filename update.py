import os
import re
import json
import time
import html
import hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import OrderedDict
from html.parser import HTMLParser
from urllib.parse import quote, urlparse, parse_qsl, urlencode, urlunparse, unquote

import requests
import feedparser


API_BASE_URL = 'https://api.deepseek.com'
API_KEY = os.getenv('DEEPSEEK_API_KEY', '').strip()
AI_MODEL = 'deepseek-chat'

TZ = timezone(timedelta(hours=8))
UTC = timezone.utc
BASE_DIR = Path(__file__).resolve().parent

OUTPUT_FILE = BASE_DIR / 'index.html'
CACHE_FILE = BASE_DIR / 'ai_cache.json'
TRANSLATION_CACHE_FILE = BASE_DIR / 'translation_cache.json'
MARKET_CAP_CACHE = BASE_DIR / 'market_cap_cache.json'

TIME_LIMIT = timedelta(hours=24)

CHANNEL_LIMITS = {
    'global': 20,
    'asia': 10,
    'china': 10,
    'earnings': 10
}

CHANNELS = list(CHANNEL_LIMITS)

ANALYSIS_PER_CHANNEL = 3
MAX_ANALYSIS_CALLS = 9
MAX_TRANSLATION_CALLS = 1
MAX_TOTAL_CALLS = 10

# 本次运行实际发送的 DeepSeek HTTP 请求次数
DEEPSEEK_HTTP_CALLS = 0

MARKET_CAP_THRESHOLD = 1_000_000_000_000

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; NewsDashboard/2.0)',
    'Accept': 'application/rss+xml, application/xml, text/xml, */*'
}


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def clean_text(value, limit=600):
    if value is None:
        return ''

    p = TextParser()
    raw = html.unescape(str(value))

    try:
        p.feed(raw)
        raw = ' '.join(p.parts)
    except Exception:
        raw = re.sub(r'<[^>]*>', ' ', raw)

    raw = re.sub(
        r'https?://\S+|www\.\S+',
        ' ',
        raw,
        flags=re.I
    )

    for pat in (
        r'\bRead more\b.*$',
        r'\bClick here\b.*$',
        r'\bSubscribe now\b.*$',
        r'\bSign up for\b.*$'
    ):
        raw = re.sub(pat, '', raw, flags=re.I)

    raw = re.sub(r'<[^>]*>', ' ', raw)
    raw = re.sub(r'\s+', ' ', raw)
    raw = raw.strip(' \t\r\n-–—|·•:：;；')

    return (
        raw[:limit].rstrip(' ，,。；;')
        + ('…' if len(raw) > limit else '')
    )


def has_chinese(s):
    return bool(re.search(r'[\u4e00-\u9fff]', s or ''))


def valid_article_url(url):
    if not isinstance(url, str):
        return ''

    url = html.unescape(url.strip())

    if not url:
        return ''

    try:
        p = urlparse(url)

        if p.scheme.lower() not in ('http', 'https'):
            return ''

        if not p.hostname:
            return ''

        return url

    except Exception:
        return ''


def resolve_article_url(url):
    """
    尝试将 Google News RSS 跳转链接解析为出版商原始报道链接。
    其他来源保留原始 URL。
    解析失败时保留有效的 Google News 链接，避免丢失新闻。
    """
    original = valid_article_url(url)

    if not original:
        return ''

    try:
        parsed = urlparse(original)
        host = (parsed.hostname or '').lower()

        if not (
            host == 'news.google.com'
            or host.endswith('.news.google.com')
        ):
            return original

        # 部分 Google News URL 直接包含原文地址
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))

        for key in ('url', 'u'):
            value = params.get(key)

            if value:
                candidate = valid_article_url(unquote(value))

                if candidate:
                    candidate_host = (
                        urlparse(candidate).hostname or ''
                    ).lower()

                    if candidate_host != 'news.google.com':
                        return candidate

        # 跟随 Google News 跳转
        response = requests.get(
            original,
            headers=HEADERS,
            timeout=15,
            allow_redirects=True
        )

        final_url = valid_article_url(response.url)

        if final_url:
            final_host = (
                urlparse(final_url).hostname or ''
            ).lower()

            if final_host != 'news.google.com':
                return final_url

        # 从页面 canonical / og:url 中提取出版商地址
        page = response.text or ''

        patterns = [
            r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
            r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
            r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']'
        ]

        for pattern in patterns:
            match = re.search(
                pattern,
                page,
                re.I
            )

            if not match:
                continue

            candidate = html.unescape(match.group(1).strip())
            candidate = valid_article_url(candidate)

            if not candidate:
                continue

            candidate_host = (
                urlparse(candidate).hostname or ''
            ).lower()

            if candidate_host != 'news.google.com':
                return candidate

    except Exception as e:
        print(
            'Google News原文链接解析失败：'
            + str(e)[:120]
        )

    return original


def norm_url(url):
    try:
        p = urlparse((url or '').strip())

        q = [
            (k, v)
            for k, v in parse_qsl(
                p.query,
                keep_blank_values=True
            )
            if not k.lower().startswith((
                'utm_', 'fbclid', 'gclid', 'ocid', 'cmpid'
            ))
            and k.lower() not in ('oc', 'ved', 'usg')
        ]

        return urlunparse((
            p.scheme.lower(),
            p.netloc.lower(),
            p.path.rstrip('/'),
            '',
            urlencode(q),
            ''
        ))

    except Exception:
        return (url or '').strip()


def fingerprint(title):
    text = re.sub(
        r'[^\w\u4e00-\u9fff]+',
        '',
        clean_text(title, 300).lower()
    )
    return hashlib.sha256(text.encode()).hexdigest()


def gnews(q, zh=False):
    return (
        'https://news.google.com/rss/search?q='
        + quote(q)
        + (
            '&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'
            if zh
            else '&hl=en-US&gl=US&ceid=US:en'
        )
    )


FEEDS = [
    {
        'url': 'https://www.cnbc.com/id/100003114/device/rss/rss.html',
        'source': 'CNBC',
        'channels': ['global']
    },
    {
        'url': 'https://feeds.bbci.co.uk/news/business/rss.xml',
        'source': 'BBC Business',
        'channels': ['global']
    },
    {
        'url': 'https://feeds.content.dowjones.io/public/rss/RSSMarketsMain',
        'source': 'WSJ Markets',
        'channels': ['global', 'earnings']
    },
    {
        'url': gnews('Reuters world business economy markets'),
        'source': 'Reuters',
        'channels': ['global']
    },
    {
        'url': gnews('Bloomberg global finance economy markets'),
        'source': 'Bloomberg',
        'channels': ['global']
    },
    {
        'url': gnews('"Federal Reserve" OR FOMC meeting rate decision'),
        'source': '美联储会议报道',
        'channels': ['global']
    },
    {
        'url': 'https://feeds.bbci.co.uk/news/world/asia/rss.xml',
        'source': 'BBC Asia',
        'channels': ['asia']
    },
    {
        'url': 'https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511',
        'source': 'CNA Asia',
        'channels': ['asia']
    },
    {
        'url': 'https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936',
        'source': 'CNA Business',
        'channels': ['asia']
    },
    {
        'url': 'https://feeds.feedburner.com/rsscna/politics',
        'source': '台湾中央社政治',
        'channels': ['asia']
    },
    {
        'url': 'https://feeds.feedburner.com/rsscna/intworld',
        'source': '台湾中央社国际',
        'channels': ['asia']
    },
    {
        'url': 'https://feeds.feedburner.com/rsscna/finance',
        'source': '台湾中央社财经',
        'channels': ['asia']
    },
    {
        'url': 'https://feeds.feedburner.com/rsscna/technology',
        'source': '台湾中央社科技',
        'channels': ['asia']
    },
    {
        'url': 'https://www3.nhk.or.jp/rj/podcast/rss/english.xml',
        'source': 'NHK World',
        'channels': ['asia']
    },
    {
        'url': 'https://www.scmp.com/rss/91/feed',
        'source': '南华早报',
        'channels': ['asia']
    },
    {
        'url': gnews(
            '(site:asia.nikkei.com OR site:japantimes.co.jp OR '
            'site:english.kyodonews.net) Japan politics economy business'
        ),
        'source': '日本主流媒体',
        'channels': ['asia']
    },
    {
        'url': gnews(
            '(site:scmp.com OR site:hongkongfp.com) '
            'Hong Kong politics economy business'
        ),
        'source': '香港主流媒体',
        'channels': ['asia']
    },
    {
        'url': gnews(
            '(site:koreaherald.com OR site:koreatimes.co.kr) '
            'South Korea politics economy business'
        ),
        'source': '韩国主流媒体',
        'channels': ['asia']
    },
    {
        'url': gnews(
            '(site:channelnewsasia.com OR site:straitstimes.com) '
            'Southeast Asia politics economy'
        ),
        'source': '东南亚主流媒体',
        'channels': ['asia']
    },
    {
        'url': gnews('site:caixin.com 中国财经 经济 金融', True),
        'source': '财新网',
        'channels': ['china'],
        'domains': ['caixin.com'],
        'names': ['财新', 'caixin']
    },
    {
        'url': gnews('site:yicai.com 中国财经 A股 经济', True),
        'source': '第一财经',
        'channels': ['china'],
        'domains': ['yicai.com'],
        'names': ['第一财经', 'yicai']
    },
    {
        'url': gnews('site:21jingji.com 中国财经 经济 金融', True),
        'source': '21世纪经济报道',
        'channels': ['china'],
        'domains': ['21jingji.com'],
        'names': ['21世纪经济报道', '21jingji']
    },
    {
        'url': gnews('site:nbd.com.cn 中国财经 A股 公司', True),
        'source': '每日经济新闻',
        'channels': ['china'],
        'domains': ['nbd.com.cn'],
        'names': ['每日经济新闻', 'nbd']
    },
    {
        'url': gnews('site:finance.sina.com.cn A股 中国经济', True),
        'source': '新浪财经',
        'channels': ['china'],
        'domains': ['sina.com.cn'],
        'names': ['新浪财经', '新浪']
    },
    {
        'url': gnews('site:wallstreetcn.com 中国经济 A股 财经', True),
        'source': '华尔街见闻',
        'channels': ['china'],
        'domains': ['wallstreetcn.com'],
        'names': ['华尔街见闻', 'wallstreetcn']
    },
    {
        'url': 'https://www.investors.com/feed/investing/',
        'source': 'Investor’s Business Daily',
        'channels': ['earnings']
    },
    {
        'url': gnews(
            'technology company earnings quarterly results revenue profit'
        ),
        'source': '全球科技企业财报',
        'channels': ['earnings']
    }
]


EARNINGS_TERMS = (
    'earnings', 'earning', 'profit', 'quarter', 'quarterly',
    'results', 'guidance', 'revenue', 'financial results',
    'net income', 'fiscal', '财报', '业绩', '季度', '指引',
    '营收', '净利润', '盈利', '财务报告'
)

FED_TERMS = (
    'fomc meeting', 'federal reserve meeting', 'fed meeting',
    'fed rate decision', 'fomc decision', 'fomc statement',
    'fed policy decision', 'federal reserve policy',
    'fed interest rate decision', '美联储会议',
    '美联储利率决议', '美联储议息会议', '美联储政策会议',
    'fomc会议', '联邦公开市场委员会会议'
)


def is_earnings(s):
    return any(x in (s or '').lower() for x in EARNINGS_TERMS)


def is_fed(s):
    return any(x in (s or '').lower() for x in FED_TERMS)


def ensure_json(path, default):
    if not path.exists():
        path.write_text(
            json.dumps(default, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )


def read_json(path, default):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else default
    except Exception:
        return default


def write_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )


def refresh_market_caps():
    ensure_json(
        MARKET_CAP_CACHE,
        {'_dynamic': False, 'companies': {}}
    )

    try:
        url = (
            'https://companiesmarketcap.com/tech/'
            'largest-tech-companies-by-market-cap/'
        )

        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()

        found = {}
        rows = re.findall(
            r'<tr[^>]*>(.*?)</tr>',
            r.text,
            re.S | re.I
        )

        for row in rows:
            nm = re.search(
                r'<div class="company-name">\s*([^<]+)',
                row,
                re.I
            )
            sm = re.search(
                r'<div class="company-code">\s*([^<]+)',
                row,
                re.I
            )
            cm = re.search(
                r'<td[^>]*(?:data-label=["\']Market Cap["\']|'
                r'class=["\'][^"\']*market-cap[^"\']*["\'])'
                r'[^>]*>(.*?)</td>',
                row,
                re.S | re.I
            )

            if not nm or not cm:
                continue

            vm = re.search(
                r'\$?\s*([\d,.]+)\s*(T|B|M)\b',
                clean_text(cm.group(1), 100),
                re.I
            )

            if not vm:
                continue

            mult = {
                'T': 1e12,
                'B': 1e9,
                'M': 1e6
            }[vm.group(2).upper()]

            value = float(vm.group(1).replace(',', '')) * mult

            if value < MARKET_CAP_THRESHOLD:
                continue

            name = clean_text(nm.group(1), 100)
            aliases = [name.lower()]

            if sm:
                aliases.append(
                    clean_text(sm.group(1), 30).lower()
                )

            found[name] = aliases

        if found:
            write_json(MARKET_CAP_CACHE, {
                '_dynamic': True,
                'updated_at': datetime.now(TZ).isoformat(),
                'companies': found
            })

            print(
                f'动态市值名单更新：{len(found)} 家科技公司'
                '市值超过1万亿美元'
            )
            return found

        print('市值网页未解析到有效数据，尝试使用上次动态缓存')

    except Exception as e:
        print('市值刷新失败：' + str(e)[:180])

    old = read_json(MARKET_CAP_CACHE, {})

    if old.get('_dynamic') is True:
        return old.get('companies', {})

    return {}


MEGA_CAP_COMPANIES = refresh_market_caps()


def find_mega(text):
    s = (text or '').lower()

    for name, aliases in MEGA_CAP_COMPANIES.items():
        for alias in aliases:
            alias = (alias or '').lower().strip()

            if not alias:
                continue

            if re.fullmatch(r'[a-z0-9.]+', alias):
                if re.search(
                    r'(?<![a-z0-9])'
                    + re.escape(alias)
                    + r'(?![a-z0-9])',
                    s
                ):
                    return name
            elif alias in s:
                return name

    return None


def entry_time(e):
    t = e.get('published_parsed') or e.get('updated_parsed')

    try:
        return datetime(*t[:6], tzinfo=UTC) if t else None
    except Exception:
        return None


def china_allowed(e, cfg):
    info = e.get('source') or {}
    source_title = clean_text(
        info.get('title', ''),
        100
    ).lower()

    for url in (e.get('link', ''), info.get('href', '')):
        host = urlparse(url).netloc.lower()

        if any(
            host == domain or host.endswith('.' + domain)
            for domain in cfg.get('domains', [])
        ):
            return True

    return any(
        name.lower() in source_title
        for name in cfg.get('names', [])
    )


def importance(item):
    s = item['title'] + ' ' + item['summary']
    score = 50

    if is_earnings(s):
        score += 15

    if find_mega(s):
        score += 20

    if is_fed(s):
        score += 50

    return min(score, 100)


def fetch_all_news():
    now = datetime.now(UTC)
    by_url = {}
    by_title = {}

    for cfg in FEEDS:
        try:
            resp = requests.get(
                cfg['url'],
                headers=HEADERS,
                timeout=20
            )
            resp.raise_for_status()

            feed = feedparser.parse(resp.content)
            count = 0

            for e in feed.entries:
                raw_url = (e.get('link') or '').strip()

                if not raw_url:
                    continue

                if (
                    'china' in cfg['channels']
                    and not china_allowed(e, cfg)
                ):
                    continue

                pub = entry_time(e)

                if (
                    not pub
                    or now - pub > TIME_LIMIT
                    or pub - now > timedelta(minutes=30)
                ):
                    continue

                title = clean_text(e.get('title', ''), 220)
                summary = clean_text(
                    e.get('summary') or e.get('description', ''),
                    700
                )

                if not title:
                    continue

                # 解析 Google News 跳转链接。
                # WSJ、CNBC、BBC 等原始来源不进行改写。
                url = resolve_article_url(raw_url)

                if not url:
                    continue

                nu = norm_url(url)
                fp = fingerprint(title)

                key = (
                    nu
                    if nu in by_url
                    else by_title.get(fp, nu)
                )

                if key in by_url:
                    old = by_url[key]

                    old['channels'] = sorted(
                        set(old['channels'] + cfg['channels'])
                    )

                    if len(summary) > len(old['summary']):
                        old['summary'] = summary

                    if old['source'] in ('Google News', ''):
                        old['source'] = cfg['source']

                    continue

                src = clean_text(
                    (e.get('source') or {}).get('title', ''),
                    80
                )

                display = (
                    cfg['source']
                    if 'china' in cfg['channels']
                    else (
                        src
                        if src and 'news.google.com' in urlparse(raw_url).netloc
                        else cfg['source']
                    )
                )

                item = {
                    'source': display,
                    'url': url,
                    'publishedAt': pub.astimezone(TZ).strftime(
                        '%Y-%m-%d %H:%M'
                    ),
                    'title': title,
                    'summary': summary,
                    'channels': list(cfg['channels']),
                    '_timestamp': int(pub.timestamp())
                }

                item['_importance'] = importance(item)

                item['_fed_meeting'] = (
                    'global' in item['channels']
                    and is_fed(title + ' ' + summary)
                )

                item['_mega_cap_earnings'] = (
                    'earnings' in item['channels']
                    and bool(find_mega(title + ' ' + summary))
                    and is_earnings(title + ' ' + summary)
                )

                by_url[nu] = item
                by_title[fp] = nu
                count += 1

            print(f"{cfg['source']}：{count} 条")

        except Exception as e:
            print(
                f"新闻源失败 {cfg['source']}："
                f"{str(e)[:160]}"
            )

    out = list(by_url.values())

    out.sort(
        key=lambda x: (
            x['_importance'],
            x['_timestamp']
        ),
        reverse=True
    )

    print(f'去重后新闻：{len(out)} 条')
    return out


def sort_channel(ch, items):
    items = sorted(
        items,
        key=lambda x: (
            x.get('_importance', 0),
            x.get('_timestamp', 0)
        ),
        reverse=True
    )

    if ch == 'global':
        return (
            [x for x in items if x.get('_fed_meeting')]
            + [x for x in items if not x.get('_fed_meeting')]
        )

    if ch == 'earnings':
        priority = [
            x for x in items
            if x.get('_mega_cap_earnings')
        ]

        others = [
            x for x in items
            if not x.get('_mega_cap_earnings')
        ]

        return priority[:3] + others + priority[3:]

    return items


def cache_key(item):
    value = (
        norm_url(item.get('url', ''))
        or item.get('source', '') + '|' + item.get('title', '')
    )

    return hashlib.sha256(value.encode()).hexdigest()


def call_deepseek(messages, max_tokens=500, timeout=70):
    global DEEPSEEK_HTTP_CALLS

    if not API_KEY:
        return None, '未配置 DEEPSEEK_API_KEY'

    # 真正发出 HTTP 请求之前执行硬上限检查
    if DEEPSEEK_HTTP_CALLS >= MAX_TOTAL_CALLS:
        return None, (
            f'DeepSeek本次运行已达到'
            f'{MAX_TOTAL_CALLS}次HTTP请求硬上限'
        )

    try:
        DEEPSEEK_HTTP_CALLS += 1

        r = requests.post(
            API_BASE_URL + '/chat/completions',
            headers={
                'Authorization': 'Bearer ' + API_KEY,
                'Content-Type': 'application/json'
            },
            json={
                'model': AI_MODEL,
                'messages': messages,
                'temperature': 0.1,
                'max_tokens': max_tokens,
                'response_format': {'type': 'json_object'}
            },
            timeout=timeout
        )

        r.raise_for_status()

        content = r.json()['choices'][0]['message']['content'].strip()

        content = re.sub(
            r'^```(?:json)?\s*|\s*```$',
            '',
            content,
            flags=re.I | re.M
        )

        return json.loads(content), None

    except Exception as e:
        return None, str(e)[:240]


def translate_batch(items, tcache, call_counter):
    pending = []

    for item in items:
        for field, limit in (
            ('title', 180),
            ('summary', 420)
        ):
            raw = clean_text(item.get(field, ''), limit)

            if not raw or has_chinese(raw):
                item[field] = raw
                continue

            key = hashlib.sha256(raw.encode()).hexdigest()

            if key in tcache:
                item[field] = tcache[key]
            else:
                pending.append((item, field, key, raw))

    if (
        not pending
        or call_counter['translation'] >= MAX_TRANSLATION_CALLS
        or call_counter['total'] >= MAX_TOTAL_CALLS
        or DEEPSEEK_HTTP_CALLS >= MAX_TOTAL_CALLS
    ):
        return 0, 0

    unique = OrderedDict()

    for _, _, key, raw in pending:
        unique[key] = raw

    pairs = list(unique.items())[:100]

    payload = [
        {'id': i, 'text': raw}
        for i, (_, raw) in enumerate(pairs)
    ]

    prompt = (
        '将以下新闻文本逐条翻译为准确、自然的简体中文。'
        '保留公司名、数字、日期和专有名词，不要解释或添加事实。'
        '只返回JSON对象，格式为'
        '{"translations":[{"id":0,"text":"中文译文"}]}。文本：'
        + json.dumps(payload, ensure_ascii=False)
    )

    data, error = call_deepseek(
        [{'role': 'user', 'content': prompt}],
        max_tokens=6000,
        timeout=100
    )

    if data is not None or error:
        if DEEPSEEK_HTTP_CALLS > 0:
            call_counter['total'] += 1
            call_counter['translation'] += 1

    translated = {}

    if data and isinstance(data.get('translations'), list):
        for row in data['translations']:
            try:
                idx = int(row.get('id'))
                txt = clean_text(row.get('text', ''), 700)

                if (
                    0 <= idx < len(pairs)
                    and txt
                    and has_chinese(txt)
                ):
                    translated[pairs[idx][0]] = txt

            except Exception:
                pass

    if error:
        print('批量中文翻译失败：' + error)

    for item, field, key, raw in pending:
        value = translated.get(key, raw)

        if has_chinese(value):
            tcache[key] = value

        item[field] = value

    return (
        len(pending),
        sum(
            1 for _, _, key, _ in pending
            if key in translated
        )
    )


def analyze_one(item):
    prompt = f'''你是严谨的财经与国际时政新闻分析员。请用简体中文分析，不得编造事实、数据或因果关系。若信息不足请明确说明。
标题：{item['title']}
摘要：{item['summary']}
来源：{item['source']}
请返回合法JSON，字段必须为：zh_title（中文标题）、zh_summary（中文摘要）、importance（0到100整数）、analysis（100字以内简评）、coreLogic（核心逻辑）、marketImpact（对相关市场的潜在影响）、risk（风险与不确定性）。'''

    return call_deepseek(
        [{'role': 'user', 'content': prompt}],
        max_tokens=750,
        timeout=75
    )


def is_valid_chinese(s):
    return (
        isinstance(s, str)
        and bool(s.strip())
        and has_chinese(s)
    )


def main():
    global DEEPSEEK_HTTP_CALLS

    # 每次运行从0开始统计真实HTTP请求次数
    DEEPSEEK_HTTP_CALLS = 0

    print('=' * 68)
    print('财经与时政焦点看板升级版开始运行')
    print('每板块Top3深度评论；跨板块同新闻去重')
    print('DeepSeek限制：最多9次深度评论 + 1次批量翻译')
    print('=' * 68)

    ensure_json(CACHE_FILE, {})
    ensure_json(TRANSLATION_CACHE_FILE, {})
    ensure_json(
        MARKET_CAP_CACHE,
        {'_dynamic': False, 'companies': {}}
    )

    ai_cache = read_json(CACHE_FILE, {})
    tcache = read_json(TRANSLATION_CACHE_FILE, {})

    all_news = fetch_all_news()

    pools = {channel: [] for channel in CHANNELS}

    for news in all_news:
        for channel in news['channels']:
            if channel in pools:
                pools[channel].append(news)

    for channel in CHANNELS:
        pools[channel] = sort_channel(
            channel,
            pools[channel]
        )[:CHANNEL_LIMITS[channel]]

    unique = OrderedDict()

    for channel in CHANNELS:
        for news in pools[channel]:
            unique.setdefault(cache_key(news), dict(news))

    visible = list(unique.values())

    calls = {
        'total': 0,
        'translation': 0,
        'analysis': 0
    }

    translated_count = 0
    translated_ok = 0

    if API_KEY:
        translated_count, translated_ok = translate_batch(
            visible,
            tcache,
            calls
        )
    else:
        print('未配置DeepSeek密钥：跳过翻译和评论')

    trans_by_key = {
        cache_key(news): news
        for news in visible
    }

    for channel in CHANNELS:
        for news in pools[channel]:
            translated = trans_by_key.get(cache_key(news))

            if translated:
                news['title'] = translated['title']
                news['summary'] = translated['summary']

    candidates = OrderedDict()

    for channel in CHANNELS:
        for news in pools[channel][:ANALYSIS_PER_CHANNEL]:
            key = cache_key(news)

            if key not in ai_cache:
                candidates.setdefault(key, news)

    successes = 0
    failed = 0

    for i, (key, item) in enumerate(
        candidates.items(),
        1
    ):
        if (
            calls['analysis'] >= MAX_ANALYSIS_CALLS
            or calls['total'] >= MAX_TOTAL_CALLS
            or DEEPSEEK_HTTP_CALLS >= MAX_TOTAL_CALLS
        ):
            print('已达到DeepSeek调用上限，停止后续评论')
            break

        print(
            f"DeepSeek深度评论 {i}/{len(candidates)}："
            f"{item['title'][:55]}"
        )

        data, error = analyze_one(item)

        # 只统计实际发出的HTTP请求
        if DEEPSEEK_HTTP_CALLS > calls['total']:
            calls['total'] += 1
            calls['analysis'] += 1

        if data:
            if is_valid_chinese(data.get('zh_title')):
                item['title'] = clean_text(
                    data['zh_title'],
                    180
                )

            if is_valid_chinese(data.get('zh_summary')):
                item['summary'] = clean_text(
                    data['zh_summary'],
                    420
                )

            data['zh_title'] = item['title']
            data['zh_summary'] = item['summary']

            for field, limit in (
                ('analysis', 500),
                ('coreLogic', 350),
                ('marketImpact', 500),
                ('risk', 350)
            ):
                data[field] = clean_text(
                    data.get(field, ''),
                    limit
                )

            try:
                data['importance'] = max(
                    0,
                    min(
                        100,
                        int(
                            data.get(
                                'importance',
                                item.get('_importance', 50)
                            )
                        )
                    )
                )
            except Exception:
                data['importance'] = item.get(
                    '_importance',
                    50
                )

            ai_cache[key] = data
            successes += 1

        else:
            failed += 1
            print('DeepSeek评论失败：' + str(error))

        time.sleep(0.25)

    result = OrderedDict()

    result['updatedAt'] = datetime.now(TZ).strftime(
        '%Y-%m-%d %H:%M:%S'
    )

    result['aiEnabled'] = bool(API_KEY)
    result['aiModel'] = AI_MODEL

    result['aiStats'] = {
        'attempts': calls['analysis'],
        'successes': successes,
        'translationCalls': calls['translation']
    }

    for channel in CHANNELS:
        out = []

        for original in pools[channel]:
            item = dict(original)
            key = cache_key(item)
            analysis = ai_cache.get(key)

            if analysis:
                if is_valid_chinese(analysis.get('zh_title')):
                    item['title'] = analysis['zh_title']

                if is_valid_chinese(analysis.get('zh_summary')):
                    item['summary'] = analysis['zh_summary']

                item['analysis'] = analysis.get('analysis', '')
                item['impact'] = analysis.get('marketImpact', '')
                item['coreLogic'] = analysis.get('coreLogic', '')
                item['risk'] = analysis.get('risk', '')

                item['_importance'] = analysis.get(
                    'importance',
                    item.get('_importance', 50)
                )

            else:
                item['ai_msg'] = '本条新闻暂未进行AI深度评论'

            item['title'] = clean_text(
                item.get('title', ''),
                180
            )

            item['summary'] = clean_text(
                item.get('summary', ''),
                420
            )

            # 输出前再次检查原始报道链接
            item['url'] = valid_article_url(item.get('url', ''))

            for field, limit in (
                ('analysis', 500),
                ('impact', 500),
                ('coreLogic', 350),
                ('risk', 350)
            ):
                if field in item:
                    item[field] = clean_text(
                        item[field],
                        limit
                    )

            out.append(item)

        out = sort_channel(
            channel,
            out
        )[:CHANNEL_LIMITS[channel]]

        for item in out:
            for key in (
                'channels',
                '_timestamp',
                '_importance',
                '_fed_meeting',
                '_mega_cap_earnings'
            ):
                item.pop(key, None)

        result[channel] = out

    ai_cache = OrderedDict(
        list(ai_cache.items())[-1500:]
    )

    tcache = OrderedDict(
        list(tcache.items())[-2000:]
    )

    write_json(CACHE_FILE, ai_cache)
    write_json(TRANSLATION_CACHE_FILE, tcache)

    ensure_json(
        MARKET_CAP_CACHE,
        {'_dynamic': False, 'companies': {}}
    )

    if not OUTPUT_FILE.exists():
        raise FileNotFoundError(
            f'找不到网页文件：{OUTPUT_FILE}'
        )

    page = OUTPUT_FILE.read_text(encoding='utf-8')

    start = '<!-- NEWS_DATA_START -->'
    end = '<!-- NEWS_DATA_END -->'

    if start not in page or end not in page:
        raise RuntimeError(
            'index.html缺少新闻数据标记，为保护原页面，停止写入。'
        )

    data = json.dumps(
        result,
        ensure_ascii=False,
        indent=2
    )

    injection = (
        start
        + '\n<script>window.newsData='
        + data
        + ';</script>\n'
        + end
    )

    page = re.sub(
        re.escape(start) + r'.*?' + re.escape(end),
        lambda _: injection,
        page,
        flags=re.S
    )

    OUTPUT_FILE.write_text(
        page,
        encoding='utf-8'
    )

    print('=' * 68)
    print('四大板块更新完成')

    for channel, label in (
        ('global', '国际财经'),
        ('asia', '亚太财经'),
        ('china', '国内财经'),
        ('earnings', '核心财报')
    ):
        print(
            f'{label}：{len(result[channel])} 条'
        )

    print(
        f'DeepSeek深度评论：{calls["analysis"]}次，'
        f'成功{successes}次，失败{failed}次'
    )

    print(
        f'DeepSeek批量翻译：{calls["translation"]}次；'
        f'待翻译字段{translated_count}个，'
        f'成功{translated_ok}个'
    )

    print(
        f'DeepSeek实际HTTP请求：'
        f'{DEEPSEEK_HTTP_CALLS}/{MAX_TOTAL_CALLS}'
        '（最多9次评论+1次批量翻译）'
    )

    print(
        'RSS清理、24小时过滤、跨板块去重、动态市值核验：已启用'
    )

    print(
        'market_cap_cache.json、ai_cache.json、'
        'translation_cache.json：均已确保存在'
    )

    print('=' * 68)


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""财经新闻看板自动更新脚本。无需安装第三方 Python 套件。"""
import os, re, json, time, hashlib, html
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET

BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / 'index.html'
CACHE_FILE = BASE_DIR / 'ai_cache.json'
TZ_CN = timezone(timedelta(hours=8))
TIMEOUT = 18
MAX_AI_CALLS_PER_RUN = 8  # 控制 API 用量：每次最多分析 8 条新新闻

FEEDS = {
 'global': [
  ('CNBC World', 'https://www.cnbc.com/id/100727362/device/rss/rss.html'),
  ('CNBC Finance', 'https://www.cnbc.com/id/10000664/device/rss/rss.html'),
  ('CNBC Economy', 'https://www.cnbc.com/id/20910258/device/rss/rss.html'),
  ('BBC World', 'https://feeds.bbci.co.uk/news/world/rss.xml'),
 ],
 'asia': [
  ('CNA Business', 'https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category= business'),
  ('Focus Taiwan', 'https://focustaiwan.tw/rss/business'),
  ('Nikkei Asia', 'https://asia.nikkei.com/rss/feed/nar'),
  ('The Japan Times', 'https://www.japantimes.co.jp/feed/'),
 ],
 'china': [
  ('新浪财经', 'https://rss.sina.com.cn/finance/'),
  ('华尔街见闻', 'https://wallstreetcn.com/rss'),
  ('证券时报', 'https://www.stcn.com/article/list/zq/{}.html'),
 ],
 'earnings': [
  ('CNBC Finance', 'https://www.cnbc.com/id/10000664/device/rss/rss.html'),
  ('CNBC Earnings', 'https://www.cnbc.com/id/15839135/device/rss/rss.html'),
  ('MarketWatch', 'https://feeds.marketwatch.com/marketwatch/marketpulse/'),
 ]
}
LIMITS = {'global':20,'asia':10,'china':10,'earnings':10}


def get_url(url, headers=None):
    req = Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; FinanceNewsDashboard/1.0)', **(headers or {})})
    with urlopen(req, timeout=TIMEOUT) as r:
        return r.read()

def clean(s):
    s = html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))
    return re.sub(r'\s+', ' ', s).strip()

def parse_feed(source, url):
    if '{}' in url: return []
    try: root = ET.fromstring(get_url(url))
    except Exception as e:
        print(f'[WARN] RSS 获取失败 {source}: {e}'); return []
    out=[]
    for node in root.findall('.//item'):
        title=clean(node.findtext('title')); link=clean(node.findtext('link'))
        desc=clean(node.findtext('description')); pub=clean(node.findtext('pubDate'))
        if title and link: out.append({'title':title,'url':link,'summary':desc,'source':source,'published':pub})
    # Atom feeds
    if not out:
        ns={'a':'http://www.w3.org/2005/Atom'}
        for node in root.findall('.//a:entry',ns):
            title=clean(node.findtext('a:title',default='',namespaces=ns))
            summary=clean(node.findtext('a:summary',default='',namespaces=ns) or node.findtext('a:content',default='',namespaces=ns))
            linknode=node.find('a:link',ns); link=linknode.get('href','') if linknode is not None else ''
            pub=clean(node.findtext('a:published',default='',namespaces=ns) or node.findtext('a:updated',default='',namespaces=ns))
            if title and link: out.append({'title':title,'url':link,'summary':summary,'source':source,'published':pub})
    return out

def parse_date(s):
    if not s: return datetime.now(timezone.utc)
    try:
        from email.utils import parsedate_to_datetime
        d=parsedate_to_datetime(s)
        if d.tzinfo is None: d=d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        try: return datetime.fromisoformat(s.replace('Z','+00:00'))
        except Exception: return datetime.now(timezone.utc)

def has_chinese(s): return bool(re.search(r'[\u4e00-\u9fff]',s or ''))

def google_translate(text):
    if not text or has_chinese(text): return text
    from urllib.parse import urlencode
    url='https://translate.googleapis.com/translate_a/single?'+urlencode({'client':'gtx','sl':'auto','tl':'zh-CN','dt':'t','q':text[:1800]})
    for attempt in range(2):
        try:
            data=json.loads(get_url(url).decode('utf-8'))
            result=''.join(part[0] for part in data[0] if part and part[0])
            if result and has_chinese(result): return result
        except Exception as e: print(f'[WARN] 翻译重试 {attempt+1}: {e}')
        time.sleep(.5)
    return text

def load_cache():
    try: return json.loads(CACHE_FILE.read_text(encoding='utf-8'))
    except Exception: return {}

def gemini_call(prompt):
    key=os.environ.get('GEMINI_API_KEY','').strip()
    if not key: return None
    # 可通过 GitHub Actions 变量 GEMINI_MODEL 覆盖模型名
    model=os.environ.get('GEMINI_MODEL','gemini-2.5-flash').strip()
    url=f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}'
    body={'contents':[{'parts':[{'text':prompt}]}], 'generationConfig':{'temperature':0.2,'responseMimeType':'application/json'}}
    req=Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','User-Agent':'FinanceDashboard/1.0'},method='POST')
    try:
        with urlopen(req,timeout=25) as r: data=json.loads(r.read().decode('utf-8'))
        return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f'[WARN] Gemini 调用失败: {e}'); return None

def ai_insight(item, cache, budget):
    key=hashlib.sha256((item['title']+'|'+item['url']).encode()).hexdigest()
    if key in cache: return cache[key]
    if budget[0] <= 0: return None
    prompt='''你是谨慎的财经新闻分析员。请用简体中文分析以下新闻。只根据提供的信息，不要编造事实；不确定就明确说不确定。输出严格 JSON 对象，字段：analysis（80-140字，解释事件、可能的市场传导和重要性）, impact（不超过25字的潜在市场影响）, assets（最多3个相关资产或行业，数组）, risk（不超过35字的主要风险/不确定性）。不要给个性化投资建议。\n新闻标题：%s\n来源：%s\n摘要：%s''' % (item['title'],item['source'],item.get('summary','')[:900])
    budget[0]-=1
    raw=gemini_call(prompt)
    if not raw: return None
    try:
        obj=json.loads(raw)
        result={'analysis':clean(obj.get('analysis','')),'impact':clean(obj.get('impact','')),'assets':[clean(x) for x in obj.get('assets',[]) if clean(x)][:3],'risk':clean(obj.get('risk','')),'provider':'Gemini'}
        if result['analysis']:
            cache[key]=result; return result
    except Exception as e: print(f'[WARN] AI JSON 解析失败: {e}')
    return None

def classify(items, channel):
    if channel=='asia':
        words=r'asia|asian|china|chinese|japan|japanese|korea|korean|taiwan|hong kong|singapore|india|indian|australia|australian|south-east asia|asia-pacific|亚太|中国|日本|韩国|台湾|香港|新加坡|印度|澳大利亚'
        items=[x for x in items if re.search(words,x['title']+' '+x.get('summary',''),re.I)]
    elif channel=='china':
        words=r'中国|人民币|A股|沪深|港股|央行|中国经济|China|Chinese|yuan|renminbi|Shanghai|Shenzhen|PBOC|CSI 300|China stocks'
        items=[x for x in items if re.search(words,x['title']+' '+x.get('summary',''),re.I)]
    elif channel=='earnings':
        words=r'earnings|revenue|profit|quarter|quarterly|forecast|guidance|results|财报|业绩|营收|利润|季度|盈利'
        items=[x for x in items if re.search(words,x['title']+' '+x.get('summary',''),re.I)]
    return items

def build_channel(channel, cache, budget):
    gathered=[]; seen=set()
    for source,url in FEEDS[channel]:
        for x in parse_feed(source,url):
            if x['url'] in seen: continue
            seen.add(x['url']); x['_date']=parse_date(x.get('published')); gathered.append(x)
    gathered=classify(gathered,channel)
    now=datetime.now(timezone.utc)
    gathered=[x for x in gathered if (now-x['_date']).total_seconds() < 5*86400]
    gathered.sort(key=lambda x:x['_date'],reverse=True)
    results=[]
    for x in gathered:
        if len(results)>=LIMITS[channel]: break
        title=x['title']; summary=x.get('summary','')
        # 国内中文新闻原样保留；其余标题和摘要翻译，失败时保留原文
        if channel!='china' or not has_chinese(title): title=google_translate(title)
        if summary and (channel!='china' or not has_chinese(summary)): summary=google_translate(summary)
        item={'title':title,'originalTitle':x['title'],'summary':summary[:650], 'source':x['source'],'url':x['url'],
              'publishedAt':x['_date'].astimezone(TZ_CN).strftime('%Y-%m-%d %H:%M'),'timestamp':int(x['_date'].timestamp()),
              'impact':'待分析','asset':[],'analysis':'','risk':'','aiStatus':'未分析'}
        # 优先分析各栏目中排在前面的重要/最新新闻；缓存可重复使用
        ai=ai_insight({'title':title,'source':x['source'],'summary':summary,'url':x['url']},cache,budget)
        if ai:
            item.update({'impact':ai.get('impact') or '见 AI 分析','asset':ai.get('assets',[]),'analysis':ai.get('analysis',''),'risk':ai.get('risk',''),'aiStatus':'Gemini AI'})
        results.append(item)
    # 倒序编号：Top 20 从 NO.20 到 NO.01，Top 10 从 NO.10 到 NO.01
    n=LIMITS[channel]
    for i,item in enumerate(results): item['id']=f"NO.{n-i:02d}"
    return results

def safe_json(obj):
    return json.dumps(obj,ensure_ascii=False).replace('</','<\\/')

def main():
    if not HTML_FILE.exists(): raise FileNotFoundError(f'找不到 {HTML_FILE}')
    cache=load_cache(); budget=[MAX_AI_CALLS_PER_RUN]
    news={k:build_channel(k,cache,budget) for k in ('global','asia','china','earnings')}
    news['updatedAt']=datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M:%S')
    news['aiEnabled']=bool(os.environ.get('GEMINI_API_KEY','').strip())
    page=HTML_FILE.read_text(encoding='utf-8')
    marker_start='<!-- NEWS_DATA_START -->'; marker_end='<!-- NEWS_DATA_END -->'
    if marker_start not in page or marker_end not in page: raise RuntimeError('index.html 缺少 NEWS_DATA_START / NEWS_DATA_END 标记，已停止写入以保护文件。')
    start=page.index(marker_start)+len(marker_start); end=page.index(marker_end)
    block='\n<script>window.newsData = '+safe_json(news)+';</script>\n'
    page=page[:start]+block+page[end:]
    page=re.sub(r'(<div[^>]*id=["\']updateTime["\'][^>]*>).*?(</div>)',lambda m:m.group(1)+html.escape(news['updatedAt'])+m.group(2),page,flags=re.I|re.S)
    HTML_FILE.write_text(page,encoding='utf-8')
    CACHE_FILE.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding='utf-8')
    print('更新完成：', {k:len(v) for k,v in news.items() if isinstance(v,list)})
    print(f"本次 Gemini 新调用额度使用：{MAX_AI_CALLS_PER_RUN-budget[0]}/{MAX_AI_CALLS_PER_RUN}")
    if not news['aiEnabled']: print('[提示] 未配置 GEMINI_API_KEY，新闻仍可抓取，但不会生成真正的 Gemini 分析。')

if __name__=='__main__': main()

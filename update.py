import os, re, json, time, html, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import OrderedDict
from urllib.parse import quote, urlparse, parse_qsl, urlencode, urlunparse, unquote

import requests
import feedparser

API_BASE_URL = "https://api.deepseek.com"
API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
AI_MODEL = "deepseek-chat"
TZ = timezone(timedelta(hours=8))
UTC = timezone.utc
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "finance.html"
CACHE_FILE = BASE_DIR / "ai_cache.json"
TRANSLATION_CACHE_FILE = BASE_DIR / "translation_cache.json"
MARKET_CAP_CACHE = BASE_DIR / "market_cap_cache.json"
TIME_LIMIT = timedelta(hours=24)
CHANNEL_LIMITS = {"global": 20, "asia": 10, "china": 10, "earnings": 10}
CHANNELS = list(CHANNEL_LIMITS)
ANALYSIS_PER_CHANNEL = 3
MAX_ANALYSIS_CALLS = 9
MAX_TRANSLATION_CALLS = 1
MAX_TOTAL_CALLS = 10
HTTP_CALLS = 0
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FinanceBillboard/3.0)", "Accept": "application/rss+xml, application/xml, text/xml, */*"}

def clean(value, limit=600):
    if value is None: return ""
    s = html.unescape(str(value))
    s = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", s, flags=re.I|re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"https?://\S+|www\.\S+", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" \t\r\n-–—|·•:：;；")
    return s[:limit].rstrip(" ，,。；;") + ("…" if len(s) > limit else "")

def valid_url(url):
    try:
        p = urlparse(html.unescape(str(url or "").strip()))
        return p.geturl() if p.scheme in ("http", "https") and p.hostname else ""
    except Exception: return ""

def resolve_url(url):
    original = valid_url(url)
    if not original: return ""
    host = (urlparse(original).hostname or "").lower()
    if host != "news.google.com" and not host.endswith(".news.google.com"): return original
    try:
        p = urlparse(original)
        params = dict(parse_qsl(p.query, keep_blank_values=True))
        for key in ("url", "u"):
            candidate = valid_url(unquote(params.get(key, "")))
            if candidate and "news.google.com" not in (urlparse(candidate).hostname or ""): return candidate
        r = requests.get(original, headers=HEADERS, timeout=15, allow_redirects=True)
        final = valid_url(r.url)
        if final and "news.google.com" not in (urlparse(final).hostname or ""): return final
        for pat in (r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
                    r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)'):
            m = re.search(pat, r.text or "", re.I)
            if m:
                candidate = valid_url(html.unescape(m.group(1)))
                if candidate and "news.google.com" not in (urlparse(candidate).hostname or ""): return candidate
    except Exception as e: print("Google News原文链接解析失败：", str(e)[:100])
    return original

def norm_url(url):
    try:
        p = urlparse(url or "")
        q = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith(("utm_", "fbclid", "gclid", "ocid", "cmpid"))
             and k.lower() not in ("oc", "ved", "usg")]
        return urlunparse((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", urlencode(q), ""))
    except Exception: return url or ""

def fingerprint(title):
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "", clean(title, 300).lower())
    return hashlib.sha256(s.encode()).hexdigest()

def gnews(q, zh=False):
    return "https://news.google.com/rss/search?q=" + quote(q) + ("&hl=zh-CN&gl=CN&ceid=CN:zh-Hans" if zh else "&hl=en-US&gl=US&ceid=US:en")

FEEDS = [
 {"url":"https://www.cnbc.com/id/100003114/device/rss/rss.html","source":"CNBC","channels":["global"]},
 {"url":"https://feeds.bbci.co.uk/news/business/rss.xml","source":"BBC Business","channels":["global"]},
 {"url":"https://feeds.content.dowjones.io/public/rss/RSSMarketsMain","source":"WSJ Markets","channels":["global","earnings"]},
 {"url":gnews("Reuters world business economy markets"),"source":"Reuters","channels":["global"]},
 {"url":gnews("Bloomberg global finance economy markets"),"source":"Bloomberg","channels":["global"]},
 {"url":gnews('"Federal Reserve" OR FOMC meeting rate decision'),"source":"美联储会议报道","channels":["global"]},
 {"url":"https://feeds.bbci.co.uk/news/world/asia/rss.xml","source":"BBC Asia","channels":["asia"]},
 {"url":"https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6511","source":"CNA Asia","channels":["asia"]},
 {"url":"https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936","source":"CNA Business","channels":["asia"]},
 {"url":"https://feeds.feedburner.com/rsscna/politics","source":"台湾中央社政治","channels":["asia"]},
 {"url":"https://feeds.feedburner.com/rsscna/intworld","source":"台湾中央社国际","channels":["asia"]},
 {"url":"https://feeds.feedburner.com/rsscna/finance","source":"台湾中央社财经","channels":["asia"]},
 {"url":"https://feeds.feedburner.com/rsscna/technology","source":"台湾中央社科技","channels":["asia"]},
 {"url":"https://www3.nhk.or.jp/rj/podcast/rss/english.xml","source":"NHK World","channels":["asia"]},
 {"url":"https://www.scmp.com/rss/91/feed","source":"南华早报","channels":["asia"]},
 {"url":gnews("(site:asia.nikkei.com OR site:japantimes.co.jp OR site:english.kyodonews.net) Japan politics economy business"),"source":"日本主流媒体","channels":["asia"]},
 {"url":gnews("(site:scmp.com OR site:hongkongfp.com) Hong Kong politics economy business"),"source":"香港主流媒体","channels":["asia"]},
 {"url":gnews("(site:koreaherald.com OR site:koreatimes.co.kr) South Korea politics economy business"),"source":"韩国主流媒体","channels":["asia"]},
 {"url":gnews("(site:channelnewsasia.com OR site:straitstimes.com) Southeast Asia politics economy"),"source":"东南亚主流媒体","channels":["asia"]},
 {"url":gnews("site:caixin.com 中国财经 经济 金融",True),"source":"财新网","channels":["china"],"domains":["caixin.com"],"names":["财新","caixin"]},
 {"url":gnews("site:yicai.com 中国财经 A股 经济",True),"source":"第一财经","channels":["china"],"domains":["yicai.com"],"names":["第一财经","yicai"]},
 {"url":gnews("site:21jingji.com 中国财经 经济 金融",True),"source":"21世纪经济报道","channels":["china"],"domains":["21jingji.com"],"names":["21世纪经济报道","21jingji"]},
 {"url":gnews("site:nbd.com.cn 中国财经 A股 公司",True),"source":"每日经济新闻","channels":["china"],"domains":["nbd.com.cn"],"names":["每日经济新闻","nbd"]},
 {"url":gnews("site:finance.sina.com.cn A股 中国经济",True),"source":"新浪财经","channels":["china"],"domains":["sina.com.cn"],"names":["新浪财经","新浪"]},
 {"url":gnews("site:wallstreetcn.com 中国经济 A股 财经",True),"source":"华尔街见闻","channels":["china"],"domains":["wallstreetcn.com"],"names":["华尔街见闻","wallstreetcn"]},
 {"url":"https://www.investors.com/feed/investing/","source":"Investor’s Business Daily","channels":["earnings"]},
 {"url":gnews("technology company earnings quarterly results revenue profit"),"source":"全球科技企业财报","channels":["earnings"]}
]
EARNINGS_TERMS = ("earnings","earning","profit","quarter","quarterly","results","guidance","revenue","financial results","net income","fiscal","财报","业绩","季度","指引","营收","净利润","盈利","财务报告")
FED_TERMS = ("fomc meeting","federal reserve meeting","fed meeting","fed rate decision","fomc decision","fomc statement","fed policy decision","federal reserve policy","fed interest rate decision","美联储会议","美联储利率决议","美联储议息会议","美联储政策会议","fomc会议","联邦公开市场委员会会议")

def is_earnings(s): return any(x in (s or "").lower() for x in EARNINGS_TERMS)
def is_fed(s): return any(x in (s or "").lower() for x in FED_TERMS)
def has_chinese(s): return bool(re.search(r"[\u4e00-\u9fff]", s or ""))
def valid_zh(s): return isinstance(s,str) and bool(s.strip()) and has_chinese(s)
def read_json(path, default):
    try:
        v=json.loads(path.read_text(encoding="utf-8")); return v if isinstance(v,dict) else default
    except Exception: return default
def write_json(path, data): path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
def entry_time(e):
    t=e.get("published_parsed") or e.get("updated_parsed")
    try: return datetime(*t[:6],tzinfo=UTC) if t else None
    except Exception: return None

def china_allowed(e,cfg):
    info=e.get("source") or {}; st=clean(info.get("title",""),100).lower()
    for u in (e.get("link",""),info.get("href","")):
        host=(urlparse(u).hostname or "").lower()
        if any(host==d or host.endswith("."+d) for d in cfg.get("domains",[])): return True
    return any(n.lower() in st for n in cfg.get("names",[]))

def importance(item):
    s=(item.get("title","")+" "+item.get("summary","")).lower()
    score=45
    if is_earnings(s): score+=12
    if is_fed(s): score+=30
    if any(k in s for k in ("rate decision","利率决议","央行","制裁","战争","tariff","关税","inflation","通胀","recession","衰退")): score+=12
    if any(k in s for k in ("cnbc","reuters","bloomberg","wall street journal","bbc")): score+=5
    return min(100,score)

def fetch_all_news():
    now=datetime.now(UTC); by_url={}; by_title={}
    for cfg in FEEDS:
        try:
            resp=requests.get(cfg["url"],headers=HEADERS,timeout=20); resp.raise_for_status()
            feed=feedparser.parse(resp.content); count=0
            for e in feed.entries:
                raw=(e.get("link") or "").strip()
                if not raw: continue
                if "china" in cfg["channels"] and not china_allowed(e,cfg): continue
                pub=entry_time(e)
                if not pub or now-pub>TIME_LIMIT or pub-now>timedelta(minutes=30): continue
                title=clean(e.get("title",""),220); summary=clean(e.get("summary") or e.get("description",""),700)
                if not title: continue
                url=resolve_url(raw)
                if not url: continue
                nu=norm_url(url); fp=fingerprint(title); key=nu if nu in by_url else by_title.get(fp,nu)
                if key in by_url:
                    old=by_url[key]; old["channels"]=sorted(set(old["channels"]+cfg["channels"]))
                    if len(summary)>len(old["summary"]): old["summary"]=summary
                    continue
                src=clean((e.get("source") or {}).get("title",""),80)
                display=cfg["source"] if "china" in cfg["channels"] else (src if src and "news.google.com" in urlparse(raw).netloc else cfg["source"])
                item={"source":display,"url":url,"publishedAt":pub.astimezone(TZ).strftime("%Y-%m-%d %H:%M"),"title":title,"summary":summary,"channels":list(cfg["channels"]),"_timestamp":int(pub.timestamp())}
                item["_importance"]=importance(item); item["_fed_meeting"]="global" in item["channels"] and is_fed(title+" "+summary)
                by_url[nu]=item; by_title[fp]=nu; count+=1
            print(f'{cfg["source"]}：{count} 条')
        except Exception as e: print(f'新闻源失败 {cfg["source"]}：{str(e)[:140]}')
    out=list(by_url.values()); out.sort(key=lambda x:(x["_importance"],x["_timestamp"]),reverse=True)
    print("去重后新闻：",len(out)); return out

def sort_channel(ch,items):
    items=sorted(items,key=lambda x:(x.get("_importance",0),x.get("_timestamp",0)),reverse=True)
    if ch=="global": return [x for x in items if x.get("_fed_meeting")]+[x for x in items if not x.get("_fed_meeting")]
    if ch=="earnings":
        pri=[x for x in items if is_earnings(x.get("title","")+" "+x.get("summary",""))]
        return pri+ [x for x in items if x not in pri]
    return items

def cache_key(item):
    value=norm_url(item.get("url","")) or item.get("source","")+"|"+item.get("title","")
    return hashlib.sha256(value.encode()).hexdigest()

def call_deepseek(messages,max_tokens=700,timeout=80):
    global HTTP_CALLS
    if not API_KEY: return None,"未配置 DEEPSEEK_API_KEY"
    if HTTP_CALLS>=MAX_TOTAL_CALLS: return None,"达到本次运行10次请求上限"
    try:
        HTTP_CALLS+=1
        r=requests.post(API_BASE_URL+"/chat/completions",headers={"Authorization":"Bearer "+API_KEY,"Content-Type":"application/json"},
          json={"model":AI_MODEL,"messages":messages,"temperature":0.1,"max_tokens":max_tokens,"response_format":{"type":"json_object"}},timeout=timeout)
        r.raise_for_status(); content=r.json()["choices"][0]["message"]["content"].strip()
        content=re.sub(r"^```(?:json)?\s*|\s*```$","",content,flags=re.I|re.M)
        return json.loads(content),None
    except Exception as e: return None,str(e)[:240]

def translate_batch(items,tcache):
    pending=[]
    for item in items:
        for field,limit in (("title",180),("summary",420)):
            raw=clean(item.get(field,""),limit)
            if not raw or has_chinese(raw): item[field]=raw; continue
            key=hashlib.sha256(raw.encode()).hexdigest()
            if key in tcache: item[field]=tcache[key]
            else: pending.append((item,field,key,raw))
    if not pending or HTTP_CALLS>=MAX_TOTAL_CALLS: return 0,0,0
    unique=OrderedDict((key,raw) for _,_,key,raw in pending); pairs=list(unique.items())[:100]
    payload=[{"id":i,"text":raw} for i,(_,raw) in enumerate(pairs)]
    prompt="逐条翻译为准确简体中文，保留数字和专名，不得解释或添加事实。只返回JSON：{\"translations\":[{\"id\":0,\"text\":\"译文\"}]}。"+json.dumps(payload,ensure_ascii=False)
    before=HTTP_CALLS; data,error=call_deepseek([{"role":"user","content":prompt}],6000,100); calls=HTTP_CALLS-before
    translated={}
    if data and isinstance(data.get("translations"),list):
        for row in data["translations"]:
            try:
                i=int(row["id"]); txt=clean(row.get("text",""),700)
                if 0<=i<len(pairs) and valid_zh(txt): translated[pairs[i][0]]=txt
            except Exception: pass
    for item,field,key,raw in pending:
        value=translated.get(key,raw)
        if valid_zh(value): tcache[key]=value
        item[field]=value
    if error: print("批量翻译失败：",error)
    return len(pending),len(translated),calls

def analyze_one(item):
    prompt=f"""你是严谨的财经与国际时政分析员。仅依据所给标题、摘要，不得编造事实、数据、来源或因果关系。若关键事实无法由材料确认，必须在risk和evidence明确标注“需核实”，不得把推断写成事实。
标题：{item['title']}
摘要：{item['summary']}
来源：{item['source']}
返回JSON字段：zh_title（中文标题）、zh_summary（中文摘要）、importance（0-100整数）、analysis（简评）、coreLogic（核心逻辑）、marketImpact（潜在市场影响）、transmission（传导机制）、timeHorizon（影响周期）、evidence（字符串数组，只写材料可支持的线索）、scenarios（字符串数组，标明条件）、risk（风险与信息缺口）、confidence（高/中/低）。"""
    return call_deepseek([{"role":"user","content":prompt}],1100,80)

def main():
    global HTTP_CALLS
    HTTP_CALLS=0
    print("="*60+"\n财经与时政看板更新开始：四栏目 / 24小时 / 跨栏目去重\nDeepSeek最多9次深度分析+1次批量翻译\n"+"="*60)
    ai_cache=read_json(CACHE_FILE,{}); tcache=read_json(TRANSLATION_CACHE_FILE,{})
    all_news=fetch_all_news(); pools={c:[] for c in CHANNELS}
    for n in all_news:
        for c in n["channels"]:
            if c in pools: pools[c].append(n)
    for c in CHANNELS: pools[c]=sort_channel(c,pools[c])[:CHANNEL_LIMITS[c]]
    unique=OrderedDict()
    for c in CHANNELS:
        for n in pools[c]: unique.setdefault(cache_key(n),dict(n))
    visible=list(unique.values()); translation_calls=0; translated_count=translated_ok=0
    if API_KEY:
        translated_count,translated_ok,translation_calls=translate_batch(visible,tcache)
    bykey={cache_key(n):n for n in visible}
    for c in CHANNELS:
        for n in pools[c]:
            t=bykey.get(cache_key(n))
            if t: n["title"],n["summary"]=t["title"],t["summary"]
    candidates=OrderedDict()
    for c in CHANNELS:
        for n in pools[c][:ANALYSIS_PER_CHANNEL]:
            k=cache_key(n)
            if k not in ai_cache: candidates.setdefault(k,n)
    successes=failed=analysis_calls=0
    for k,item in candidates.items():
        if analysis_calls>=MAX_ANALYSIS_CALLS or HTTP_CALLS>=MAX_TOTAL_CALLS: break
        before=HTTP_CALLS; data,error=analyze_one(item)
        if HTTP_CALLS>before: analysis_calls+=1
        if not data:
            failed+=1; print("分析失败：",error); continue
        normalized={
          "zh_title":clean(data.get("zh_title",""),180) if valid_zh(data.get("zh_title")) else item["title"],
          "zh_summary":clean(data.get("zh_summary",""),420) if valid_zh(data.get("zh_summary")) else item["summary"],
          "analysis":clean(data.get("analysis",""),500),
          "coreLogic":clean(data.get("coreLogic",""),400),
          "marketImpact":clean(data.get("marketImpact",""),500),
          "transmission":clean(data.get("transmission",""),400),
          "timeHorizon":clean(data.get("timeHorizon",""),120),
          "evidence":[clean(x,180) for x in data.get("evidence",[])[:5] if isinstance(x,str)],
          "scenarios":[clean(x,220) for x in data.get("scenarios",[])[:4] if isinstance(x,str)],
          "risk":clean(data.get("risk",""),500),
          "confidence":clean(data.get("confidence","低"),20)
        }
        try: normalized["importance"]=max(0,min(100,int(data.get("importance",item.get("_importance",50)))))
        except Exception: normalized["importance"]=item.get("_importance",50)
        ai_cache[k]=normalized; successes+=1
    result=OrderedDict()
    result["updatedAt"]=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    result["aiEnabled"]=bool(API_KEY); result["aiModel"]=AI_MODEL
    result["aiStats"]={"attempts":analysis_calls,"successes":successes,"translationCalls":translation_calls,"translationItems":translated_count,"translationSuccesses":translated_ok,"failed":failed}
    for c in CHANNELS:
        out=[]
        for original in pools[c]:
            item=dict(original); a=ai_cache.get(cache_key(item))
            if a:
                item["title"]=a.get("zh_title") or item["title"]; item["summary"]=a.get("zh_summary") or item["summary"]
                item["analysis"]=a.get("analysis",""); item["impact"]=a.get("marketImpact",""); item["marketImpact"]=a.get("marketImpact","")
                for f in ("coreLogic","transmission","timeHorizon","evidence","scenarios","risk","confidence"): item[f]=a.get(f,[] if f in ("evidence","scenarios") else "")
                item["_importance"]=a.get("importance",item.get("_importance",50))
            else: item["ai_msg"]="本条新闻暂未进行AI深度评论"
            item["title"]=clean(item.get("title",""),180); item["summary"]=clean(item.get("summary",""),420)
            item["url"]=valid_url(item.get("url",""))
            out.append(item)
        out=sort_channel(c,out)[:CHANNEL_LIMITS[c]]
        for item in out:
            for f in ("channels","_timestamp","_fed_meeting"): item.pop(f,None)
        result[c]=out
    write_json(CACHE_FILE,dict(list(ai_cache.items())[-1500:]))
    write_json(TRANSLATION_CACHE_FILE,dict(list(tcache.items())[-2000:]))
    if not OUTPUT_FILE.exists(): raise FileNotFoundError(f"找不到网页文件：{OUTPUT_FILE}")
    page=OUTPUT_FILE.read_text(encoding="utf-8"); start="<!-- NEWS_DATA_START -->"; end="<!-- NEWS_DATA_END -->"
    if start not in page or end not in page: raise RuntimeError("finance.html缺少新闻数据标记，停止写入。")
    payload=json.dumps(result,ensure_ascii=False,indent=2)
    injection=start+"\n<script>window.newsData="+payload+";</script>\n"+end
    page=re.sub(re.escape(start)+r".*?"+re.escape(end),lambda _:injection,page,flags=re.S)
    OUTPUT_FILE.write_text(page,encoding="utf-8")
    print("四栏目更新完成：",", ".join(f"{c} {len(result[c])}" for c in CHANNELS))
    print(f"分析请求 {analysis_calls} 次，成功 {successes}；翻译请求 {translation_calls} 次；总HTTP请求 {HTTP_CALLS}/10")

if __name__=="__main__": main()

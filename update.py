# -*- coding: utf-8 -*-
"""财经研究辅助看板更新器。Python 标准库实现。"""
import os, re, json, time, hashlib, html
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
import xml.etree.ElementTree as ET

BASE=Path(__file__).resolve().parent
PAGE=BASE/"index.html"; CACHE=BASE/"ai_cache.json"
TZ=timezone(timedelta(hours=8))
MAX_AI_CALLS=3; RETRIES=1
LIMITS={"global":20,"asia":10,"china":10,"earnings":10}
# 国际、亚太、国内财经新闻只保留24小时内；财报新闻保留72小时内。
MAX_NEWS_AGE_HOURS={"global":24,"asia":24,"china":24,"earnings":72}
def rss(q):
 return "https://news.google.com/rss/search?"+urlencode({"q":q,"hl":"zh-CN","gl":"US","ceid":"US:zh-Hans"})
FEEDS={
"global":[("CNBC World","https://www.cnbc.com/id/100727362/device/rss/rss.html"),("CNBC Finance","https://www.cnbc.com/id/10000664/device/rss/rss.html"),("BBC World","https://feeds.bbci.co.uk/news/world/rss.xml"),("Google News 国际财经",rss("global finance economy markets stocks"))],
"asia":[("中央社财经","https://feeds.feedburner.com/rsscna/finance"),("Nikkei Asia","https://asia.nikkei.com/rss/feed/nar"),("The Japan Times","https://www.japantimes.co.jp/feed/"),("Google News 亚太财经",rss("Asia Pacific finance economy markets China Japan Korea Taiwan Singapore"))],
"china":[("Google News 中国财经",rss("中国财经 OR 中国经济 OR A股 OR 人民币 OR 中国央行 OR 港股")),("Google News 中国股市",rss("A股 OR 沪深股市 OR 中国上市公司 OR 中国金融市场"))],
"earnings":[("CNBC Finance","https://www.cnbc.com/id/10000664/device/rss/rss.html"),("CNBC Earnings","https://www.cnbc.com/id/15839135/device/rss/rss.html"),("MarketWatch","https://feeds.marketwatch.com/marketwatch/marketpulse/"),("Google News 企业财报",rss("company earnings revenue profit quarterly results forecast"))]}
def clean(x):
 x=html.unescape(str(x or "")); x=re.sub(r"<[^>]+>"," ",x); return re.sub(r"\s+"," ",x).strip()
def get(url,timeout=20,headers=None):
 h={"User-Agent":"Mozilla/5.0 Chrome/124 Safari/537.36","Accept":"application/rss+xml, application/xml, text/xml, */*"}; h.update(headers or {})
 with urlopen(Request(url,headers=h),timeout=timeout) as r:return r.read()
def is_http_url(url):
 try:
  from urllib.parse import urlparse
  u=urlparse(str(url or ""));return u.scheme in ("http","https") and bool(u.netloc)
 except Exception:return False

def is_google_news_url(url):
 try:
  from urllib.parse import urlparse
  host=urlparse(str(url or "")).netloc.lower()
  return host.endswith("news.google.com")
 except Exception:return False

def make_search_url(title):
 title=(title or "").strip()
 if not title:return ""
 q=quote(title)
 return ("https://www.baidu.com/s?wd=" if chinese(title) else "https://www.bing.com/search?q=")+q

def extract_article_url(url):
 """尽可能把新版 Google News RSS 中转链接解析成媒体原文地址。"""
 if not url or not is_http_url(url):return ""
 if not is_google_news_url(url):return url
 from urllib.parse import urlparse,parse_qs
 try:
  qs=parse_qs(urlparse(url).query)
  for key in ("url","u","target","article_url"):
   for value in qs.get(key,[]):
    value=html.unescape(value.strip())
    if is_http_url(value) and not is_google_news_url(value):return value
 except Exception:pass
 headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132.0.0.0 Safari/537.36",
          "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language":"en-US,en;q=0.9,zh-CN;q=0.8",
          "Cookie":"CONSENT=YES+cb; SOCS=CAI"}
 article_id=urlparse(url).path.rstrip("/").split("/")[-1]
 if not article_id:return ""
 # Use the RSS article splash endpoint. The old /articles endpoint may be blocked.
 for path in ("/rss/articles/","/articles/"):
  try:
   response_url="https://news.google.com"+path+article_id
   req=Request(response_url,headers=headers)
   with urlopen(req,timeout=12) as response:
    final=response.geturl();body=response.read(900000).decode("utf-8",errors="replace")
   if is_http_url(final) and not is_google_news_url(final):return final
   # Current Google News pages expose a c-wiz data-p payload.
   m=re.search(r'<c-wiz[^>]*data-p=["\']([^"\']+)',body,re.I)
   if m:
    raw=html.unescape(m.group(1))
    try:
     obj=json.loads(raw.replace('%.@.','["garturlreq",'))
     trimmed=obj[:-6]+obj[-2:]
     inner=json.dumps(trimmed,separators=(",",":"),ensure_ascii=False)
     freq=json.dumps([[['Fbv4je',inner,None,'generic']]],separators=(",",":"),ensure_ascii=False)
     payload=urlencode({"f.req":freq}).encode("utf-8")
     rpc=Request("https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",data=payload,
       headers={"User-Agent":headers["User-Agent"],"Content-Type":"application/x-www-form-urlencoded;charset=UTF-8",
       "Origin":"https://news.google.com","Referer":response_url},method="POST")
     with urlopen(rpc,timeout=12) as rr:txt=rr.read().decode("utf-8",errors="replace")
     txt=txt.replace(")]}'", "")
     data=json.loads(txt)
     # Most responses wrap the RPC result in [0][2], itself JSON text.
     for row in data:
      if isinstance(row,list) and len(row)>2 and row[2]:
       try:
        result=json.loads(row[2])
        if isinstance(result,list) and len(result)>1:
         candidate=str(result[1])
         if is_http_url(candidate) and not is_google_news_url(candidate):return candidate
       except Exception:pass
    except Exception:pass
   # Alternative signature fields seen in some Google News page variants.
   sg=re.search(r'data-n-a-sg=["\']([^"\']+)',body)
   ts=re.search(r'data-n-a-ts=["\']([^"\']+)',body)
   ident=re.search(r'data-n-a-id=["\']([^"\']+)',body)
   if sg and ts:
    aid=html.unescape(ident.group(1)) if ident else article_id
    inner=["garturlreq",[["X","X",["X","X"],None,None,1,1,"US:en",None,1,None,None,None,None,None,0,1],"X","X",1,[1,1,1],1,1,None,0,0,None,0],aid,int(ts.group(1)),html.unescape(sg.group(1))]
    freq=json.dumps([[['Fbv4je',json.dumps(inner,separators=(",",":")),None,'generic']]],separators=(",",":"))
    rpc=Request("https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",data=urlencode({"f.req":freq}).encode(),headers={"User-Agent":headers["User-Agent"],"Content-Type":"application/x-www-form-urlencoded;charset=UTF-8","Origin":"https://news.google.com","Referer":response_url},method="POST")
    with urlopen(rpc,timeout=12) as rr:txt=rr.read().decode("utf-8",errors="replace")
    data=json.loads(txt.replace(")]}'", ""))
    for row in data:
     if isinstance(row,list) and len(row)>2 and row[2]:
      try:
       result=json.loads(row[2]);candidate=str(result[1])
       if is_http_url(candidate) and not is_google_news_url(candidate):return candidate
      except Exception:pass
  except Exception as e:
   print("[INFO] Google News 原文解析未成功：",type(e).__name__)
 return ""

def prepare_article_links(item):
 original=(item.get("url") or "").strip();title=item.get("title","")
 item["originalUrl"]=original
 item["searchUrl"]=make_search_url(title)
 if not is_http_url(original):
  item["url"]=item["searchUrl"];item["linkStatus"]="备用搜索入口";return item
 if not is_google_news_url(original):
  item["url"]=original;item["linkStatus"]="媒体链接";return item
 resolved=extract_article_url(original)
 if resolved and not is_google_news_url(resolved):
  item["url"]=resolved;item["linkStatus"]="已解析原文链接"
 else:
  item["url"]=original;item["linkStatus"]="Google News 中转链接"
 return item

def parse_feed(source,url):
 try:root=ET.fromstring(get(url))
 except Exception as e:print(f"[WARN] RSS {source}: {type(e).__name__}: {e}");return []
 out=[]
 for n in root.findall(".//item"):
  t,l=clean(n.findtext("title")),clean(n.findtext("link"))
  if t and l:out.append({"title":t,"url":l,"summary":clean(n.findtext("description")),"source":source,"published":clean(n.findtext("pubDate"))})
 if not out:
  ns={"a":"http://www.w3.org/2005/Atom"}
  for n in root.findall(".//a:entry",ns):
   t=clean(n.findtext("a:title",default="",namespaces=ns)); ln=n.find("a:link",ns); l=ln.get("href","") if ln is not None else ""
   s=clean(n.findtext("a:summary",default="",namespaces=ns) or n.findtext("a:content",default="",namespaces=ns))
   p=clean(n.findtext("a:published",default="",namespaces=ns) or n.findtext("a:updated",default="",namespaces=ns))
   if t and l:out.append({"title":t,"url":l,"summary":s,"source":source,"published":p})
 return out
def date(s):
 try:
  from email.utils import parsedate_to_datetime
  d=parsedate_to_datetime(s)
  if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
  return d.astimezone(timezone.utc)
 except Exception:
  try:
   d=datetime.fromisoformat((s or "").replace("Z","+00:00"))
   if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
   return d.astimezone(timezone.utc)
  except Exception:return None
def chinese(s):return bool(re.search(r"[\u4e00-\u9fff]",s or ""))
def translate(s):
 if not s or chinese(s):return s
 u="https://translate.googleapis.com/translate_a/single?"+urlencode({"client":"gtx","sl":"auto","tl":"zh-CN","dt":"t","q":s[:1600]})
 try:
  d=json.loads(get(u,10).decode());return "".join(p[0] for p in d[0] if p and p[0]) or s
 except Exception as e:print("[WARN] 翻译失败，保留原文：",type(e).__name__);return s
def load_cache():
 try:
  d=json.loads(CACHE.read_text(encoding="utf-8"));return d if isinstance(d,dict) else {}
 except Exception:return {}
def gemini(prompt):
 key=os.getenv("GEMINI_API_KEY","").strip()
 if not key:print("[ERROR] 未配置 GEMINI_API_KEY");return None
 model=os.getenv("GEMINI_MODEL","gemini-3.8-flash").strip()
 url="https://generativelanguage.googleapis.com/v1beta/models/"+quote(model,safe="-._")+":generateContent"
 body=json.dumps({"contents":[{"role":"user","parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.2,"responseMimeType":"application/json"}}).encode()
 for attempt in range(RETRIES+1):
  req=Request(url,data=body,headers={"Content-Type":"application/json","x-goog-api-key":key,"User-Agent":"FinanceResearchDashboard/2.0"},method="POST")
  try:
   with urlopen(req,timeout=45) as r:d=json.loads(r.read().decode())
   c=d.get("candidates",[]);parts=c[0].get("content",{}).get("parts",[]) if c else []
   result="".join(p.get("text","") for p in parts if p.get("text")).strip()
   if result:return result
   print("[WARN] Gemini 返回空内容")
  except HTTPError as e:
   detail=e.read().decode("utf-8",errors="replace")[:800];print(f"[ERROR] Gemini HTTP {e.code}: {detail}")
   if e.code not in (408,429,500,502,503,504):
    if e.code in (401,403):print("[提示] 检查 API Key 与权限")
    if e.code==404:print("[提示] 检查 GEMINI_MODEL：",model)
    return None
  except (TimeoutError,URLError) as e:print(f"[ERROR] Gemini 网络超时（第 {attempt+1} 次）：{type(e).__name__}: {e}")
  except Exception as e:print("[ERROR] Gemini 调用异常：",type(e).__name__,e);return None
  if attempt<RETRIES:
   delay=min(2**(attempt+1),12);print(f"[提示] {delay} 秒后重试（{attempt+1}/{RETRIES}）");time.sleep(delay)
 print("[WARN] 重试已达上限，本条留待下次更新");return None
def parse_ai(raw):
 if not raw:return None
 s=re.sub(r"^```(?:json)?\s*","",raw.strip(),flags=re.I);s=re.sub(r"\s*```$","",s)
 try:d=json.loads(s)
 except Exception:
  a,b=s.find("{"),s.rfind("}")
  if a<0 or b<=a:return None
  try:d=json.loads(s[a:b+1])
  except Exception:return None
 if not isinstance(d,dict):return None
 def v(k,n=600):return clean(d.get(k,""))[:n]
 def arr(k,n=3):return [clean(x)[:250] for x in d.get(k,[]) if clean(x)][:n] if isinstance(d.get(k,[]),list) else []
 return {"analysis":v("analysis",1600),"coreLogic":v("core_logic"),"marketImpact":v("market_impact"),"transmission":v("transmission"),
 "impact":v("impact",180),"asset":[clean(x)[:80] for x in d.get("assets",[]) if clean(x)][:4] if isinstance(d.get("assets",[]),list) else [],
 "timeHorizon":v("time_horizon",150),"scenarios":arr("scenarios"),"evidence":arr("evidence"),"risk":v("risk"),"confidence":v("confidence",100),"provider":"Gemini"}
def insight(item,cache,budget):
 ck=hashlib.sha256((item["title"]+"|"+item["url"]).encode()).hexdigest(); old=cache.get(ck)
 if isinstance(old,dict) and old.get("analysis") and old.get("coreLogic"):return old
 if budget["attempts"]>=MAX_AI_CALLS:return None
 special="请特别分析营收、利润、利润率、现金流、业绩指引与市场预期的差异。" if item["channel"]=="earnings" else ""
 prompt=f"""你是审慎的财经研究助理，不是荐股员。用简体中文分析新闻。只使用给定标题、来源和摘要，不得编造事实、数字、估值或市场预期。信息不足时明确写需核验原文。
{special}
严格输出 JSON，不要 Markdown，字段如下：
analysis：100-180字，事件、含义和重要性。
core_logic：事件→变量变化→影响对象的逻辑链。
market_impact：潜在受益/承压方向；不确定就明确说明。
transmission：通过供需、利率、汇率、成本、利润或风险偏好等如何传导。
impact：一句话概括潜在市场影响。
assets：最多4个相关资产、指数、行业或ETF类别，仅为观察对象，不暗示必然涨跌。
time_horizon：短中长期及其依据/不确定性。
scenarios：最多3条基准、上行、下行情景，并写明触发条件。
evidence：最多3条摘要中确实出现的事实，不伪造引文。
risk：反证、混杂因素、滞后效应和信息缺口。
confidence：低/中/高并说明材料充分程度；只有标题和摘要时不得轻易给高。
不提供个性化投资建议。
标题：{item['title']}
来源：{item['source']}
摘要：{item.get('summary','')[:1200]}"""
 budget["attempts"]+=1;ai=parse_ai(gemini(prompt))
 if ai and ai["analysis"] and ai["coreLogic"]:cache[ck]=ai;budget["successes"]+=1;return ai
 budget["failures"]+=1;return None
def classify(items,ch):
 patterns={"asia":r"asia|asian|china|chinese|japan|korea|taiwan|hong kong|singapore|india|australia|亚太|中国|日本|韩国|台湾|香港|新加坡|印度|澳大利亚",
 "china":r"中国|人民币|A股|沪深|港股|央行|中国经济|China|Chinese|yuan|Shanghai|Shenzhen|PBOC|CSI 300",
 "earnings":r"earnings|revenue|profit|quarter|forecast|guidance|results|财报|业绩|营收|利润|季度|盈利"}
 return [x for x in items if ch not in patterns or re.search(patterns[ch],x["title"]+" "+x.get("summary",""),re.I)]
def build(ch,cache,budget):
 gathered=[];seen=set();titles=set()
 for src,url in FEEDS[ch]:
  for x in parse_feed(src,url):
   t=clean(x["title"]).lower();link=x["url"].strip()
   if not link or not t or link in seen or t in titles:continue
   seen.add(link);titles.add(t);x["_date"]=date(x.get("published"));gathered.append(x)
 now=datetime.now(timezone.utc);gathered=classify(gathered,ch)
 cutoff=now-timedelta(hours=MAX_NEWS_AGE_HOURS[ch])
 # 严格时效过滤：缺少可靠发布时间、超过时限或发布时间在未来的新闻均不展示。
 gathered=[x for x in gathered if x.get("_date") is not None and cutoff<=x["_date"]<=now]
 gathered.sort(key=lambda x:x["_date"],reverse=True)
 out=[]
 for x in gathered:
  if len(out)>=LIMITS[ch]:break
  orig=x["title"];summ=x.get("summary","")
  # 保持标题中文显示；不改动标题内容，只将英文标题翻译成简体中文。
  title=translate(orig)
  summary=translate(summ)
  n={"title":title or orig,"originalTitle":orig,"summary":(summary or summ)[:900],"source":x["source"],"url":x["url"],
  "publishedAt":x["_date"].astimezone(TZ).strftime("%Y-%m-%d %H:%M"),"timestamp":int(x["_date"].timestamp()),"impact":"待分析","asset":[],"analysis":"","risk":"",
  "coreLogic":"","marketImpact":"","transmission":"","timeHorizon":"","scenarios":[],"evidence":[],"confidence":"","aiStatus":"未分析","channel":ch}
  prepare_article_links(n)
  ai=insight(n,cache,budget)
  if ai:n.update(ai);n["impact"]=ai.get("impact") or ai.get("marketImpact") or "见分层分析";n["aiStatus"]="Gemini AI"
  n.pop("channel",None);out.append(n)
 total=len(out)
 for i,n in enumerate(out):n["id"]=f"NO.{total-i:02d}"
 return out
def main():
 if not PAGE.exists():raise FileNotFoundError("找不到 index.html")
 page=PAGE.read_text(encoding="utf-8");sm,em="<!-- NEWS_DATA_START -->","<!-- NEWS_DATA_END -->"
 if sm not in page or em not in page:raise RuntimeError("index.html 缺少新闻数据标记，已停止写入")
 cache=load_cache();budget={"attempts":0,"successes":0,"failures":0};news={}
 for ch in ("global","asia","china","earnings"):print("\n===== 更新栏目：",ch,"=====");news[ch]=build(ch,cache,budget)
 news["updatedAt"]=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S");news["aiEnabled"]=bool(os.getenv("GEMINI_API_KEY","").strip())
 news["aiModel"]=os.getenv("GEMINI_MODEL","gemini-3.8-flash").strip();news["aiStats"]={**budget,"maxPerRun":MAX_AI_CALLS}
 a=page.index(sm)+len(sm);b=page.index(em,a);block="\n<script>window.newsData = "+json.dumps(news,ensure_ascii=False).replace("</","<\\/")+";</script>\n"
 page=page[:a]+block+page[b:];page=re.sub(r'(<span[^>]*id=["\']updateTime["\'][^>]*>).*?(</span>)',lambda m:m.group(1)+html.escape(news["updatedAt"])+m.group(2),page,flags=re.I|re.S)
 PAGE.write_text(page,encoding="utf-8");CACHE.write_text(json.dumps(cache,ensure_ascii=False,indent=2),encoding="utf-8")
 print("AI 尝试/成功/失败：",budget["attempts"],budget["successes"],budget["failures"])
if __name__=="__main__":main()

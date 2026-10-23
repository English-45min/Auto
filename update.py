#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""财经与时政焦点看板更新脚本：保留四个栏目，增加 Gemini 重点新闻分析和跨次缓存。"""
import datetime, hashlib, html, json, os, re, time, urllib.parse, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

TZ = datetime.timezone(datetime.timedelta(hours=8))
BASE = Path(__file__).resolve().parent
HTML_FILE = BASE / "index.html"
LIMITS = {"global": 20, "asia": 10, "china": 10, "earnings": 10}
TIMEOUT = 15
MAX_SUMMARY = 130
API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip()
MAX_AI = max(0, min(10, int(os.environ.get("GEMINI_MAX_ANALYSES", "5"))))
CACHE_DAYS = 21

RSS = {
 "global": [
  ("CNBC World","https://www.cnbc.com/id/100003114/device/rss/rss.html"),
  ("CNBC Finance","https://www.cnbc.com/id/10000664/device/rss/rss.html"),
  ("CNBC Economy","https://www.cnbc.com/id/20910258/device/rss/rss.html"),
  ("BBC World News","https://feeds.bbci.co.uk/news/world/rss.xml")],
 "asia": [
  ("Focus Taiwan","https://focustaiwan.tw/rss/news.xml"),
  ("Taipei Times","https://www.taipeitimes.com/xml/index.rss"),
  ("CNA Business","https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=6936"),
  ("Nikkei Asia","https://asia.nikkei.com/rss/feed/nar")]}
SINA_URL = "https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=50&zhibo_id=152"
CHINA_FEEDS = [
 ("新浪财经","https://rss.sina.com.cn/roll/finance/hot_roll.xml"),
 ("华尔街见闻","https://dedicated.wallstreetcn.com/rss.xml"),
 ("证券时报","https://www.stcn.com/rss/rss.xml")]
EARNINGS_FEEDS = [
 ("华尔街见闻","https://dedicated.wallstreetcn.com/rss.xml"),
 ("新浪财经","https://rss.sina.com.cn/roll/finance/hot_roll.xml"),
 ("CNBC Finance","https://www.cnbc.com/id/10000664/device/rss/rss.html")]

def now_bj():
 return datetime.datetime.now(datetime.timezone.utc).astimezone(TZ)

def parse_date(v):
 if not v: return None
 if isinstance(v, datetime.datetime):
  if v.tzinfo is None: v=v.replace(tzinfo=TZ)
  return v.astimezone(TZ)
 if isinstance(v,(int,float)):
  try:
   n=float(v); n=n/1000 if n>10**12 else n
   return datetime.datetime.fromtimestamp(n,datetime.timezone.utc).astimezone(TZ)
  except (ValueError,OverflowError,OSError): return None
 s=str(v).strip()
 try:
  if s.endswith("Z"): s=s[:-1]+"+00:00"
  d=datetime.datetime.fromisoformat(s)
  return (d.replace(tzinfo=TZ) if d.tzinfo is None else d).astimezone(TZ)
 except (ValueError,TypeError): pass
 try:
  d=parsedate_to_datetime(s)
  return (d.replace(tzinfo=datetime.timezone.utc) if d.tzinfo is None else d).astimezone(TZ)
 except (ValueError,TypeError,OverflowError): pass
 for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M","%Y/%m/%d %H:%M:%S","%Y/%m/%d %H:%M","%Y-%m-%d"):
  try: return datetime.datetime.strptime(s,fmt).replace(tzinfo=TZ)
  except ValueError: pass
 return None

def clean(v):
 if v is None: return ""
 s=str(v)
 s=re.sub(r"(?is)<script.*?>.*?</script>"," ",s)
 s=re.sub(r"(?is)<style.*?>.*?</style>"," ",s)
 s=re.sub(r"(?s)<[^>]*>"," ",s)
 return re.sub(r"\s+"," ",html.unescape(s)).strip()

def norm_title(s): return re.sub(r"[\W_]+","",clean(s).lower(),flags=re.UNICODE)
def shorten(s,n=MAX_SUMMARY):
 s=clean(s)
 return s[:n].rstrip()+"..." if len(s)>n else s

def http_get(url,timeout=TIMEOUT,headers=None):
 h={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept":"application/rss+xml, application/atom+xml, application/xml, text/xml, application/json, */*"}
 if headers: h.update(headers)
 req=urllib.request.Request(url,headers=h)
 with urllib.request.urlopen(req,timeout=timeout) as r: return r.read()

def translate(text):
 text=clean(text)
 if not text or len(re.findall(r"[\u4e00-\u9fff]",text))>=8: return text
 try:
  q=urllib.parse.urlencode({"client":"gtx","sl":"auto","tl":"zh-CN","dt":"t","q":text[:400]})
  data=json.loads(http_get("https://translate.googleapis.com/translate_a/single?"+q,10).decode("utf-8","replace"))
  parts=[p[0] for p in data[0] if isinstance(p,list) and p and isinstance(p[0],str)]
  return "".join(parts).strip() or text
 except Exception as e:
  print(f"⚠️ 翻译不可用，保留原文：{e}"); return text

def child_text(node,names):
 names={x.lower() for x in names}
 for c in list(node):
  k=c.tag.rsplit("}",1)[-1].lower()
  if k in names:
   if k=="link" and c.attrib.get("href"): return c.attrib["href"].strip()
   return clean(c.text or "")
 return ""

def parse_xml(raw,source):
 out=[]
 try:
  try: root=ET.fromstring(raw.decode("utf-8-sig"))
  except UnicodeDecodeError: root=ET.fromstring(raw.decode("gb18030","replace"))
 except Exception as e:
  print(f"⚠️ [{source}] XML 解析失败：{e}"); return out
 for node in root.iter():
  if node.tag.rsplit("}",1)[-1].lower() not in ("item","entry"): continue
  title=child_text(node,{"title"})
  if not title: continue
  summary=child_text(node,{"description","summary","encoded","content"})
  link=""
  for c in list(node):
   if c.tag.rsplit("}",1)[-1].lower()=="link":
    candidate=(c.attrib.get("href") or c.text or "").strip()
    if candidate and c.attrib.get("rel","alternate") in ("alternate",""): link=candidate; break
    if candidate and not link: link=candidate
  pub=parse_date(child_text(node,{"pubdate","published","updated","date","issued"}))
  out.append({"title":title,"summary":summary,"published":pub,"source":source,"url":link})
 return out

def fetch_feed(source,url):
 try:
  items=parse_xml(http_get(url),source); print(f"✅ [{source}] 获取 {len(items)} 条"); return items
 except urllib.error.HTTPError as e: print(f"❌ [{source}] HTTP {e.code}")
 except urllib.error.URLError as e: print(f"❌ [{source}] 网络错误：{e.reason}")
 except Exception as e: print(f"❌ [{source}] 抓取失败：{e}")
 return []

def collect(feeds):
 out=[]
 for source,url in feeds:
  out.extend(fetch_feed(source,url)); time.sleep(.15)
 return out

def fetch_sina():
 out=[]
 try:
  data=json.loads(http_get(SINA_URL,12).decode("utf-8","replace"))
  result=data.get("result",{}); d=result.get("data",{}) if isinstance(result,dict) else {}
  feed=d if isinstance(d,list) else []
  if isinstance(d,dict):
   for k in ("feed","list","items","data"):
    if isinstance(d.get(k),list): feed=d[k]; break
   if not feed:
    feed=next((v for v in d.values() if isinstance(v,list) and v and isinstance(v[0],dict)),[])
  print(f"ℹ️ 新浪直播候选：{len(feed)} 条")
  for f in feed:
   if not isinstance(f,dict): continue
   content=clean(f.get("rich_text") or f.get("title") or f.get("content") or "")
   if len(content)<8: continue
   m=re.match(r"^【([^】]+)】\s*(.*)$",content)
   title=m.group(1).strip() if m else (content[:50]+("..." if len(content)>50 else ""))
   summary=(m.group(2).strip() or content) if m else content
   out.append({"title":title,"summary":summary,"published":parse_date(f.get("create_time") or f.get("ctime") or f.get("time") or f.get("timestamp")),"source":"新浪财经","url":str(f.get("url") or f.get("link") or "").strip()})
  print(f"✅ 新浪直播解析 {len(out)} 条")
 except Exception as e: print(f"❌ 新浪财经接口失败：{e}")
 return out

ASIA_WORDS=("asia","singapore","japan","korea","south korea","hong kong","china","taiwan","india","indonesia","malaysia","thailand","vietnam","philippines","亚太","亚洲","新加坡","日本","韩国","香港","台湾","中国","印度","印尼","马来西亚","泰国","越南","菲律宾","日经","港股","恒生","上证","人民币","台积电","三星")
CHINA_WORDS=("中国","国内","央行","人民币","a股","沪指","上证","深证","创业板","科创板","港股","证监会","财政部","国家统计局","中国经济","中国市场","中国股市","gdp","cpi","ppi","lpr","房地产","房贷","国债","中美贸易","中国出口","中国进口","中国制造","国务院","金融监管","存款准备金","降准","降息","证券时报","沪深","北交所","中国人民银行")
CHINA_EXCLUDE=("美联储","美国大选","白宫","乌克兰","俄罗斯","泽连斯基","加沙","中东局势")
def china_relevant(i):
 t=clean(i.get("title","")).lower(); s=clean(i.get("summary","")).lower()
 if any(w.lower() in t for w in CHINA_WORDS): return True
 if any(w.lower() in t+" "+s for w in CHINA_EXCLUDE): return False
 return any(w.lower() in t+" "+s for w in CHINA_WORDS)

def fetch_china():
 print("\n========== 国内财经新闻 ==========")
 out=[i for i in fetch_sina() if china_relevant(i)]
 print(f"ℹ️ 新浪过滤后 {len(out)} 条")
 if len(out)<LIMITS["china"]:
  seen={norm_title(i["title"]) for i in out}
  for i in collect(CHINA_FEEDS):
   k=norm_title(i["title"])
   if china_relevant(i) and k and k not in seen: out.append(i); seen.add(k)
 print(f"✅ 国内候选 {len(out)} 条"); return out

EARN_WORDS=("财报","净利润","营收","业绩","季度报告","年度报告","财务报告","盈利","亏损","每股收益","earnings","revenue","profit","quarterly results","quarterly earnings","eps","guidance","forecast")
def fetch_earnings():
 items=collect(EARNINGS_FEEDS)
 out=[i for i in items if any(w.lower() in (i.get("title","")+" "+i.get("summary","")).lower() for w in EARN_WORDS)]
 print(f"📊 财报候选 {len(out)} 条"); return out

def rule_insights(title,summary,tag):
 t=(title+" "+summary).lower()
 impact="需结合新闻原文、官方数据及实时行情进一步判断实际影响。"; asset="相关市场指数 / 行业板块"
 if any(w in t for w in ("财报","营收","净利润","earnings","revenue","eps","业绩")):
  impact="业绩及盈利预期可能影响公司估值，应核对实际财报与市场预期的差异。"; asset="相关上市公司 / 所属行业指数"
 elif any(w in t for w in ("降息","加息","美联储","央行","lpr","利率","货币政策")):
  impact="货币政策及利率预期可能改变融资成本和资产定价，需关注官方政策、债券收益率及汇率。"; asset="国债 / 汇率 / 黄金 / 银行板块"
 elif any(w in t for w in ("半导体","芯片","台积电","英伟达","nvidia","人工智能","ai算力")):
  impact="半导体及人工智能产业链的供需、订单和资本开支可能影响盈利预期。"; asset="半导体指数 / 纳斯达克 / 相关科技股"
 elif any(w in t for w in ("房地产","楼市","房贷","房价","地产")):
  impact="房地产政策、销售和融资条件变化可能影响地产链企业及信用风险。"; asset="房地产板块 / 建材 / 银行"
 elif any(w in t for w in ("关税","贸易","出口","进口","tariff","trade")):
  impact="贸易政策及供应链变化可能影响企业成本、出口需求与汇率预期。"; asset="出口企业 / 航运物流 / 相关汇率"
 elif tag=="国内核心": impact="国内政策、经济数据和市场预期可能影响 A 股及人民币资产表现。"; asset="沪深300 / 上证指数 / 人民币资产"
 elif tag=="亚太市场": impact="亚太地区经济、政策与企业动态可能影响区域市场及产业链。"; asset="日经225 / 恒生指数 / 亚太相关资产"
 return impact,asset

def rank_news(items,limit,tag):
 seen=set(); out=[]; cutoff=now_bj()-datetime.timedelta(hours=48)
 for raw in items:
  i=dict(raw); title=clean(i.get("title","")); key=norm_title(title)
  if not title or not key or key in seen: continue
  seen.add(key); pub=parse_date(i.get("published"))
  if pub and pub<cutoff: continue
  i["title"]=title if tag=="国内核心" else translate(title)
  summary=clean(i.get("summary",""))
  if summary and tag!="国内核心": summary=translate(summary)
  i["summary"]=shorten(summary); i["published"]=pub
  i["time"]=pub.strftime("%m-%d %H:%M") if pub else ""; i["tag"]=tag
  i["source"]=clean(i.get("source","")) or "公开新闻源"; i["url"]=clean(i.get("url",""))
  i["impact"],i["asset"]=rule_insights(i["title"],i["summary"],tag)
  out.append(i)
 min_date=datetime.datetime.min.replace(tzinfo=TZ)
 out.sort(key=lambda i:i.get("published") or min_date,reverse=True)
 selected=out[:limit]; total=len(selected)
 for n,i in enumerate(selected):
  i["id"]=f"NO.{total-n:02d}"; p=i.pop("published",None)
  i["publishedAt"]=p.isoformat() if isinstance(p,datetime.datetime) else ""
 return selected

AI_FIELDS=("aiSummary","aiImpact","aiBullish","aiBearish","aiWatch","aiRisk","aiModel","aiAnalyzedAt","aiCacheKey")
def cache_key(i):
 raw="\n".join((clean(i.get("source","")),clean(i.get("title","")),clean(i.get("summary","")))).lower()
 return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

def read_old_data(content):
 a=content.find("<!-- NEWS_DATA_START -->"); b=content.find("<!-- NEWS_DATA_END -->")
 if a<0 or b<=a: return {}
 m=re.search(r"window\.newsData\s*=\s*(\{.*?\})\s*;",content[a:b],re.S)
 if not m: return {}
 try: return json.loads(m.group(1).replace("<\\/", "</"))
 except Exception: return {}

def load_cache():
 try: old=read_old_data(HTML_FILE.read_text(encoding="utf-8")) if HTML_FILE.exists() else {}
 except Exception as e: print(f"⚠️ 读取缓存失败：{e}"); return {}
 cache={}; cutoff=now_bj()-datetime.timedelta(days=CACHE_DAYS)
 for cat in ("global","asia","china","earnings"):
  for i in old.get(cat,[]) if isinstance(old.get(cat,[]),list) else []:
   if not isinstance(i,dict) or not i.get("aiSummary"): continue
   key=i.get("aiCacheKey") or cache_key(i); dt=parse_date(i.get("aiAnalyzedAt"))
   if dt and dt<cutoff: continue
   cache[key]={f:i.get(f,"") for f in AI_FIELDS}; cache[key]["aiCacheKey"]=key
 print(f"🧠 已读取 {len(cache)} 条 AI 缓存"); return cache

def gemini(item):
 if not API_KEY: return None
 prompt=f"""你是财经新闻研究助理。用简体中文谨慎分析以下新闻，只能基于给定信息推断；信息不足时明确说需核实，不得编造数据、公告或价格。不得给买入/卖出指令。只返回合法 JSON 对象，所有字段均为字符串：
{{"summary":"1至3句核心解读","impact":"可能的市场传导机制及影响方向，说明不确定性","bullish":"可能利好因素；不明确则写暂不明确","bearish":"可能利空及反向风险；不明确则写暂不明确","watch":"后续值得观察的1至3项指标或进展","risk":"最重要的信息缺口、核实事项或风险"}}
栏目：{item.get('tag','')}
来源：{item.get('source','')}
标题：{item.get('title','')[:500]}
摘要：{item.get('summary','')[:1200]}
链接：{item.get('url','')[:500]}"""
 endpoint=f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(MODEL,safe='')}:generateContent?key={urllib.parse.quote(API_KEY,safe='')}"
 payload={"contents":[{"role":"user","parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.2,"maxOutputTokens":700,"responseMimeType":"application/json"}}
 req=urllib.request.Request(endpoint,data=json.dumps(payload,ensure_ascii=False).encode(),headers={"Content-Type":"application/json"},method="POST")
 try:
  with urllib.request.urlopen(req,timeout=35) as r: data=json.loads(r.read().decode("utf-8","replace"))
  parts=data.get("candidates",[{}])[0].get("content",{}).get("parts",[])
  txt="".join(p.get("text","") for p in parts if isinstance(p,dict)).strip()
  txt=re.sub(r"^```(?:json)?\s*|\s*```$","",txt)
  d=json.loads(txt)
  result={"aiSummary":clean(d.get("summary","")),"aiImpact":clean(d.get("impact","")),"aiBullish":clean(d.get("bullish","")),"aiBearish":clean(d.get("bearish","")),"aiWatch":clean(d.get("watch","")),"aiRisk":clean(d.get("risk","")),"aiModel":MODEL,"aiAnalyzedAt":now_bj().isoformat(),"aiCacheKey":cache_key(item)}
  return result if result["aiSummary"] else None
 except urllib.error.HTTPError as e:
  detail=""
  try: detail=e.read().decode("utf-8","replace")[:600]
  except Exception: pass
  print(f"⚠️ Gemini HTTP {e.code}：{detail}")
 except Exception as e: print(f"⚠️ Gemini 调用/解析失败：{type(e).__name__}: {e}")
 return None

def apply_ai(data):
 cache=load_cache()
 for cat in ("global","asia","china","earnings"):
  for i in data[cat]:
   key=cache_key(i); i["aiCacheKey"]=key
   if key in cache:
    i.update({k:v for k,v in cache[key].items() if v})
    if i.get("aiImpact"): i["impact"]=i["aiImpact"]
 if not API_KEY:
  print("ℹ️ 未设置 GEMINI_API_KEY，本轮只使用规则提示。"); return
 candidates=[]
 priority=("global","china","asia","earnings")
 words=("美联储","央行","利率","通胀","gdp","就业","关税","战争","制裁","财报","盈利","营收","降息","加息","人工智能","芯片","政策","cpi")
 for cat in priority:
  for pos,i in enumerate(data[cat]):
   if i.get("aiSummary"): continue
   text=(i.get("title","")+" "+i.get("summary","")).lower()
   score=max(0,10-pos)+sum(2 for w in words if w.lower() in text)
   candidates.append((score,priority.index(cat),pos,i))
 candidates.sort(key=lambda x:(-x[0],x[1],x[2]))
 candidates=[x[3] for x in candidates[:MAX_AI]]
 print(f"🧠 本轮最多分析 {len(candidates)} 条，模型 {MODEL}")
 for n,i in enumerate(candidates,1):
  print(f"🧠 分析 {n}/{len(candidates)}：{i.get('title','')[:70]}")
  result=gemini(i)
  if result:
   i.update(result); i["impact"]=result.get("aiImpact") or i.get("impact","")
   cache[result["aiCacheKey"]]=result
  time.sleep(.25)
 # 同一标题/摘要若缓存命中，保证 AI 影响字段与网页字段一致
 for cat in data:
  for i in data[cat]:
   if i.get("aiImpact"): i["impact"]=i["aiImpact"]

def safe_json(obj):
 s=json.dumps(obj,ensure_ascii=False,separators=(",",":"),default=str)
 return s.replace("</","<\\/").replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")

def update_html(data,stamp):
 if not HTML_FILE.exists(): raise FileNotFoundError(f"找不到网页文件：{HTML_FILE}")
 content=HTML_FILE.read_text(encoding="utf-8")
 start="<!-- NEWS_DATA_START -->"; end="<!-- NEWS_DATA_END -->"
 if start not in content or end not in content: raise RuntimeError("index.html 缺少 NEWS_DATA_START 或 NEWS_DATA_END 标记，已停止写入。")
 data["updatedAt"]=stamp
 replacement=start+"\n<script>\nwindow.newsData = "+safe_json(data)+";\n</script>\n"+end
 content,n=re.subn(re.escape(start)+r".*?"+re.escape(end),lambda _:replacement,content,count=1,flags=re.S)
 if n!=1: raise RuntimeError("未能准确替换网页新闻数据区域。")
 time_repl=f'<div id="updateTime" class="text-slate-400 font-medium text-xs">更新时间: {stamp} (北京时间)</div>'
 content,n=re.subn(r'<div\s+id="updateTime"[^>]*>.*?</div>',lambda _:time_repl,content,count=1,flags=re.S)
 if n!=1: print("⚠️ 未找到 updateTime 元素，更新时间文字可能不会自动更新。")
 tmp=HTML_FILE.with_suffix(".html.tmp"); tmp.write_text(content,encoding="utf-8"); tmp.replace(HTML_FILE)
 print(f"✅ 已写入 {HTML_FILE.name}")

def main():
 stamp=now_bj().strftime("%Y-%m-%d %H:%M:%S")
 print("="*60+"\n财经与时政焦点看板：开始更新\n北京时间："+stamp+"\n"+"="*60)
 global_news=rank_news(collect(RSS["global"]),LIMITS["global"],"全球宏观")
 print(f"🌍 国际财经：{len(global_news)} 条")
 asia_raw=collect(RSS["asia"])
 asia_raw=[i for i in asia_raw if any(w.lower() in (i.get("title","")+" "+i.get("summary","")).lower() for w in ASIA_WORDS)]
 asia=rank_news(asia_raw,LIMITS["asia"],"亚太市场"); print(f"🌏 亚太市场：{len(asia)} 条")
 china=rank_news(fetch_china(),LIMITS["china"],"国内核心"); print(f"🇨🇳 国内财经：{len(china)} 条")
 earnings=rank_news(fetch_earnings(),LIMITS["earnings"],"核心财报季"); print(f"📊 财报：{len(earnings)} 条")
 data={"global":global_news,"asia":asia,"china":china,"earnings":earnings}
 total=sum(len(v) for v in data.values()); print(f"📰 合计 {total} 条")
 if total==0: raise RuntimeError("所有新闻板块均为空，为防止覆盖现有数据，本次停止写入。")
 apply_ai(data)
 update_html(data,stamp)
 print("✅ 看板更新流程完成")

if __name__=="__main__":
 try: main()
 except Exception as exc:
  print(f"❌ 更新失败：{type(exc).__name__}: {exc}")
  raise

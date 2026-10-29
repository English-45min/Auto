import os
import re
import json
import time
import base64
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from collections import OrderedDict
from dotenv import load_dotenv

# ================= Configuration =================
load_dotenv()

# DeepSeek API
API_BASE_URL = "https://api.deepseek.com"
API_KEY = os.getenv("DEEPSEEK_API_KEY")
AI_MODEL = "deepseek-chat"

# Timezone (UTC+8 for China)
TZ = timezone(timedelta(hours=8))

# Output file
OUTPUT_FILE = "index.html"
CACHE_FILE = "ai_cache.json"

# 超万亿公司财报特殊播报关键词
MEGA_CAP_KEYWORDS = {
    "google": "Alphabet (Google)",
    "alphabet": "Alphabet (Google)",
    "microsoft": "Microsoft (微软)",
    "msft": "Microsoft (微软)",
    "apple": "Apple (苹果)",
    "amazon": "Amazon (亚马逊)",
    "amzn": "Amazon (亚马逊)",
    "nvidia": "Nvidia (英伟达)",
    "nvda": "Nvidia (英伟达)",
    "谷歌": "谷歌",
    "微软": "微软",
    "苹果公司": "苹果",
    "亚马逊": "亚马逊",
    "英伟达": "英伟达"
}

# 财报栏目专属关键词
EARNINGS_CHANNEL_KEYWORDS = {"earning", "profit", "quarter", "results", "guidance", "fiscal", "财报", "业绩", "季度", "指引"}

# RSS Feeds configuration
FEEDS = [
    {"url": "https://www.reutersagency.com/feed/?best-topics=business-finance&format=rss", "source": "Reuters", "channels": ["global", "earnings"]},
    {"url": "https://feeds.a.bloomberg.com/bpol/rss", "source": "Bloomberg", "channels": ["global", "earnings"]},
    {"url": "https://www.wsj.com/xml/rss/3_7014.xml", "source": "WSJ", "channels": ["global"]},
    {"url": "https://hnrss.org/frontpage", "source": "HackerNews", "channels": ["asia"]},
    {"url": "https://www.investors.com/feed/investing/", "source": "IBD", "channels": ["earnings"]},
    {"url": "https://sc.hkexnews.hk/rss", "source": "HKEx", "channels": ["china", "earnings"]},
    {"url": "https://rss.caixin.com/finance/all", "source": "Caixin", "channels": ["china"]},
    

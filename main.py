
import os
import json
import time
import random
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

TG_BOT_TOKEN = os.getenv('TG_BOT_TOKEN')
TG_CHAT_ID = os.getenv('TG_CHAT_ID')

HISTORY_FILE = '.sent_history.json'
PUSH_DATE_FILE = '.last_push_date.txt'
BEIJING_TZ = ZoneInfo('Asia/Shanghai')

RSSHUB_NODES = [
    os.getenv('RSSHUB_URL', '').rstrip('/'),
    "https://rsshub.rssforever.com",
    "https://hub.slarker.me",
    "https://rsshub.app"
]

FORBIDDEN_KEYWORDS = [
    "贝森特", "许家印", "王兴兴", "山东社区爆炸", "全剧终", "伊朗经济",
    "支联会", "张雪机车", "湖南省领导动作频频", "算力狂飙", "韩咏红",
    "长和", "巴拿马运河"
]


def get_working_node():
    for node in RSSHUB_NODES:
        if not node:
            continue
        try:
            res = requests.get(
                f"{node}/36kr/newsflashes",
                timeout=4
            )
            if res.status_code == 200:
                return node
        except Exception:
            continue

    return "https://rsshub.rssforever.com"


def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

                if data and isinstance(data[0], dict):
                    return [
                        item['title']
                        for item in data
                        if isinstance(item, dict) and 'title' in item
                    ]

                return data if isinstance(data, list) else []

        except Exception as e:
            print(f"⚠️ 读取历史记录失败: {e}")

    return []


def save_history(titles):
    try:
        current_time = datetime.now(timezone.utc).isoformat()

        history_data = [
            {"title": t, "time": current_time}
            for t in titles[-300:]
        ]

        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(
                history_data,
                f,
                ensure_ascii=False,
                indent=2
            )

        print("✅ 新闻历史记录保存成功。")
        return True

    except Exception as e:
        print(f"❌ 保存历史记录失败: {e}")
        return False


def already_pushed_today():
    today = datetime.now(BEIJING_TZ).date().isoformat()

    try:
        with open(PUSH_DATE_FILE, 'r', encoding='utf-8') as f:
            last_date = f.read().strip()
    except FileNotFoundError:
        last_date = ""
    except Exception as e:
        print(f"⚠️ 读取每日推送记录失败: {e}")
        last_date = ""

    if last_date == today:
        print(
            f"ℹ️ 北京时间 {today} 已完成今日推送，"
            "跳过重复定时任务。"
        )
        return True

    return False


def save_push_date():
    today = datetime.now(BEIJING_TZ).date().isoformat()

    try:
        with open(PUSH_DATE_FILE, 'w', encoding='utf-8') as f:
            f.write(today + "\n")

        print(f"✅ 今日推送日期已记录：{today}")
        return True

    except Exception as e:
        print(f"❌ 保存每日推送日期失败: {e}")
        return False


def is_recent_entry(entry, max_hours=48):
    try:
        published_parsed = (
            entry.get('published_parsed')
            or entry.get('updated_parsed')
        )

        if published_parsed:
            entry_time = datetime(
                *published_parsed[:6],
                tzinfo=timezone.utc
            )

            now = datetime.now(timezone.utc)

            if now - entry_time > timedelta(hours=max_hours):
                return False

    except Exception:
        pass

    return True


def fetch_category_news(
    sources,
    target_count,
    source_label,
    sent_titles,
    node=""
):
    posts = []

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }

    for url in sources:
        target_url = f"{node}{url}" if url.startswith('/') else url

        try:
            resp = requests.get(
                target_url,
                headers=headers,
                timeout=6
            )

            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)

                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    link = entry.link

                    ignore_keywords = [
                        "综艺", "明星", "八卦", "CP", "恋情", "运势"
                    ]

                    if (
                        any(kw in title for kw in ignore_keywords)
                        or any(kw in title for kw in FORBIDDEN_KEYWORDS)
                    ):
                        continue

                    if not is_recent_entry(entry, max_hours=48):
                        continue

                    if (
                        title not in sent_titles
                        and not any(p['link'] == link for p in posts)
                    ):
                        posts.append({
                            "source": source_label,
                            "title": (
                                title[:75] + "..."
                                if len(title) > 75
                                else title
                            ),
                            "link": link
                        })

                    if len(posts) >= target_count:
                        break

            if len(posts) >= target_count:
                break

        except Exception as e:
            print(
                f"⚠️ {source_label} 源 {target_url} "
                f"抓取异常: {e}"
            )

    # 如果近期新闻不足，尝试从现有 RSS 源补充
    if len(posts) < target_count:
        for url in sources:
            target_url = f"{node}{url}" if url.startswith('/') else url

            try:
                resp = requests.get(
                    target_url,
                    headers=headers,
                    timeout=6
                )

                if resp.status_code == 200:
                    feed = feedparser.parse(resp.content)
                    entries = list(feed.entries)
                    random.shuffle(entries)

                    for entry in entries:
                        title = entry.title.strip().replace('\n', ' ')
                        link = entry.link

                        if any(
                            kw in title
                            for kw in FORBIDDEN_KEYWORDS
                        ):
                            continue

                        if not any(
                            p['link'] == link
                            for p in posts
                        ):
                            posts.append({
                                "source": source_label,
                                "title": (
                                    title[:75] + "..."
                                    if len(title) > 75
                                    else title
                                ),
                                "link": link
                            })

                        if len(posts) >= target_count:
                            break

                if len(posts) >= target_count:
                    break

            except Exception:
                pass

    return posts


def fetch_comprehensive_posts():
    node = get_working_node()
    sent_titles = load_history()

    # ---------------- 1. 科技 AI：目标 4 条 ----------------
    print("🚀 正在拉取 36氪科技 AI 精选快讯...")

    tech_posts = []

    try:
        resp = requests.get(
            f"{node}/36kr/newsflashes",
            timeout=8
        )

        if resp.status_code == 200:
            feed = feedparser.parse(resp.content)

            for entry in feed.entries:
                title = entry.title.strip().replace('\n', ' ')
                link = entry.link

                if (
                    any(kw in title for kw in FORBIDDEN_KEYWORDS)
                    or not is_recent_entry(entry, 48)
                ):
                    continue

                if (
                    title not in sent_titles
                    and not any(p['link'] == link for p in tech_posts)
                ):
                    tech_posts.append({
                        "source": "科技AI",
                        "title": (
                            title[:75] + "..."
                            if len(title) > 75
                            else title
                        ),
                        "link": link
                    })

                if len(tech_posts) >= 4:
                    break

    except Exception as e:
        print(f"❌ 抓取科技快讯失败: {e}")

    # 科技新闻不足时，尝试补充
    if len(tech_posts) < 4:
        try:
            resp = requests.get(
                f"{node}/36kr/newsflashes",
                timeout=8
            )

            if resp.status_code == 200:
                feed = feedparser.parse(resp.content)

                for entry in feed.entries:
                    title = entry.title.strip().replace('\n', ' ')
                    link = entry.link

                    if (
                        not any(p['link'] == link for p in tech_posts)
                        and not any(
                            kw in title for kw in FORBIDDEN_KEYWORDS
                        )
                    ):
                        tech_posts.append({
                            "source": "科技AI",
                            "title": (
                                title[:75] + "..."
                                if len(title) > 75
                                else title
                            ),
                            "link": link
                        })

                    if len(tech_posts) >= 4:
                        break

        except Exception:
            pass

    # ---------------- 2. 台湾新闻和香港新闻 ----------------
    print("🌐 正在按配额拉取台湾(3条)与香港(3条)头条...")

    tw_sources = [
        "https://www.cna.com.tw/cna2013/market/rss.aspx",
        "https://news.yahoo.com.tw/rss/"
    ]

    hk_sources = [
        "https://rthk.hk/rthk/news/rss/c_expressnews.xml",
        "https://hk.news.yahoo.com/rss/"
    ]

    tw_posts = fetch_category_news(
        tw_sources,
        3,
        "台湾新闻",
        sent_titles,
        node
    )

    hk_posts = fetch_category_news(
        hk_sources,
        3,
        "香港新闻",
        sent_titles,
        node
    )

    final_posts = (
        tech_posts[:4]
        + tw_posts[:3]
        + hk_posts[:3]
    )

    print(
        f"📊 本次抓取完成：科技AI {len(tech_posts[:4])} 条，"
        f"台湾 {len(tw_posts[:3])} 条，"
        f"香港 {len(hk_posts[:3])} 条。"
    )

    # 注意：这里不提前保存历史记录。
    # 只有 Telegram 确认发送成功后，才会保存新闻标题。

    return final_posts


def format_board(posts):
    board = "🔥 <b>【科技AI | 中文圈精选】</b>\n"
    board += "───────────────────────────\n\n"

    if not posts:
        board += "<i>当前时间窗口内暂无新推送</i>\n"

    else:
        for i, post in enumerate(posts, 1):
            board += (
                f"🔹 <b>#{i}</b>\n"
                f"<a href='{post['link']}'>{post['title']}</a>\n\n"
            )

    board += "───────────────────────────\n"
    board += "🤖 <i>实时资讯</i>"

    return board


def send_telegram(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("❌ 错误：未检测到 Telegram 密钥。")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TG_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    for attempt in range(1, 4):
        try:
            response = requests.post(
                url,
                json=payload,
                timeout=15
            )

            try:
                result = response.json()
            except ValueError:
                result = {}

            # 必须同时满足 HTTP 200 和 Telegram 返回 ok=true
            if (
                response.status_code == 200
                and result.get("ok") is True
            ):
                print("✅ Telegram 爆文看板推送成功！")
                return True

            print(
                f"⚠️ 第 {attempt} 次推送失败，"
                f"状态码 {response.status_code}: "
                f"{response.text[:500]}"
            )

        except Exception as e:
            print(f"⚠️ 第 {attempt} 次推送网络异常: {e}")

        if attempt < 3:
            time.sleep(5)

    print("❌ 多次重试后推送依然失败。")
    return False


if __name__ == "__main__":

    # 定时任务：同一北京时间日期只允许自动推送一次。
    # 手动运行：允许测试，不受每日去重限制。
    event_name = os.getenv("GITHUB_EVENT_NAME", "")

    if (
        event_name != "workflow_dispatch"
        and already_pushed_today()
    ):
        raise SystemExit(0)

    # 获取新闻
    posts = fetch_comprehensive_posts()

    # 即使没有新新闻，也发送每日状态消息
    board_content = format_board(posts)

    # Telegram 未确认成功时，任务直接失败
    if not send_telegram(board_content):
        raise SystemExit(
            "Telegram 推送失败，任务标记为失败。"
        )

    # 只有确认发送成功，才更新新闻历史
    sent_titles = load_history()

    current_titles = [
        p["title"] for p in posts
    ]

    updated_history = list(
        dict.fromkeys(sent_titles + current_titles)
    )

    if not save_history(updated_history):
        raise SystemExit(
            "推送成功，但新闻历史记录保存失败。"
        )

    # 最后记录当天已推送，供备用定时任务判断
    if not save_push_date():
        raise SystemExit(
            "推送成功，但每日推送日期保存失败。"
        )

    print("🎉 今日 Telegram 定时推送流程完成。")

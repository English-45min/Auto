
import os
import urllib.request
import urllib.error
from html.parser import HTMLParser

URL = "https://m.guazi.com/list?input=%E7%89%B9%E6%96%AF%E6%8B%89%20Model%203%202020&cityId=45"

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = False
        self.titles = []
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.title = False

    def handle_data(self, data):
        data = data.strip()
        if not data:
            return
        if self.title:
            self.titles.append(data)
        self.text_parts.append(data)

def main():
    print("开始测试瓜子网页读取能力……")

    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 14) "
                "AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            status = response.status
            content_type = response.headers.get(
                "Content-Type", ""
            )
            raw = response.read(2_000_000)
            encoding = response.headers.get_content_charset() or "utf-8"

        page = raw.decode(encoding, errors="replace")
        parser = PageParser()
        parser.feed(page)

        print(f"HTTP 状态：{status}")
        print(f"页面类型：{content_type}")
        print(f"返回字节数：{len(raw)}")
        print(f"页面标题：{' '.join(parser.titles) or '未识别'}")

        text = " ".join(parser.text_parts)
        print(f"提取文本长度：{len(text)}")

        keywords = ["Model 3", "特斯拉", "万", "上牌", "车辆详情"]
        for keyword in keywords:
            print(f"包含“{keyword}”：{'是' if keyword in text else '否'}")

        if len(text) < 200:
            print("初步结果：页面内容较少，可能需要 JavaScript 才能加载车源。")
        elif not any(word in text for word in ["Model 3", "特斯拉"]):
            print("初步结果：已读取网页，但未找到目标车型文本。")
        else:
            print("初步结果：找到了目标车型相关文本，仍需验证价格和日期字段。")

    except urllib.error.HTTPError as exc:
        print(f"网页拒绝或限制了请求：HTTP {exc.code}")
    except Exception as exc:
        print(f"网页读取失败：{type(exc).__name__}: {exc}")

if __name__ == "__main__":
    main()

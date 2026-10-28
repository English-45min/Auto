
import re
import urllib.request
import urllib.error
from html.parser import HTMLParser

URL = "https://m.guazi.com/list?input=%E7%89%B9%E6%96%AF%E6%8B%89%20Model%203%202020&cityId=45"

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.in_script = False
        self.script_text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "script":
            self.in_script = True
            attrs = dict(attrs)
            src = attrs.get("src")
            if src:
                self.scripts.append(src)

    def handle_endtag(self, tag):
        if tag.lower() == "script":
            self.in_script = False

    def handle_data(self, data):
        if self.in_script and data.strip():
            self.script_text.append(data.strip())

def main():
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
            page = response.read(2_000_000).decode(
                response.headers.get_content_charset() or "utf-8",
                errors="replace",
            )
            print("HTTP 状态：", response.status)
            print("页面长度：", len(page))

        parser = PageParser()
        parser.feed(page)

        print("\n--- 网页加载的脚本文件（最多 15 个）---")
        for src in parser.scripts[:15]:
            print(src)

        print("\n--- 页面中疑似数据接口或安全验证的线索 ---")
        patterns = [
            r"https?://[^\"'\s<>]+",
            r"[/][\w./-]*(?:api|search|list|detail|graphql)[\w?&=./%-]*",
            r"captcha|verify|challenge|security|登录|验证",
            r"__NEXT_DATA__|__INITIAL_STATE__|window\.__",
        ]

        found = set()
        for pattern in patterns:
            for match in re.findall(pattern, page, re.I):
                value = match if isinstance(match, str) else str(match)
                if value not in found:
                    found.add(value)
                    print(value[:300])
                if len(found) >= 40:
                    break
            if len(found) >= 40:
                break

        print("\n测试完成：以上仅为页面线索，不代表接口已经验证可用。")

    except urllib.error.HTTPError as exc:
        print("HTTP 错误：", exc.code)
    except Exception as exc:
        print("读取失败：", type(exc).__name__, str(exc))

if __name__ == "__main__":
    main()

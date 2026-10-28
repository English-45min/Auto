
import re
import urllib.request

JS_URL = (
    "https://sta.guazistatic.com/"
    "guazi-mall-vue3/static/index.1189010c.js"
)

def main():
    print("开始检查瓜子前端脚本中的公开接口线索……")

    request = urllib.request.Request(
        JS_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            code = response.read(8_000_000).decode(
                "utf-8", errors="replace"
            )
            print("HTTP 状态：", response.status)
            print("脚本字符数：", len(code))

        patterns = [
            r"""["']([^"' ]{1,180})["']"""
        ]

        keywords = (
            "api", "car", "list", "search", "detail",
            "vehicle", "used", "clue", "recommend"
        )

        found = set()
        for pattern in patterns:
            for match in re.findall(pattern, code, re.I):
                value = match.strip()
                lower = value.lower()

                if not value.startswith("/"):
                    continue
                if not any(word in lower for word in keywords):
                    continue
                if value.startswith("//"):
                    continue

                # 只输出看起来像路径的字符串，不输出整段脚本。
                if len(value) > 180:
                    continue
                found.add(value)

        print("\n--- 疑似车源或查询路径（最多 40 条）---")
        for path in sorted(found)[:40]:
            print(path)

        if not found:
            print("没有发现明显的接口路径。")
            print("接口也可能由多个字符串拼接生成。")

        print("\n注意：这些只是字符串线索，尚未验证为可用车源接口。")

    except Exception as exc:
        print("测试失败：", type(exc).__name__, str(exc))

if __name__ == "__main__":
    main()

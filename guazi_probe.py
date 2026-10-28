
import re
import urllib.request

JS_URL = (
    "https://sta.guazistatic.com/"
    "guazi-mall-vue3/static/index.1189010c.js"
)

TARGETS = [
    "/api/uniformRecommend/v3/feedsIndex",
    "/api/uniformRecommend/v2/feedsIndex",
    "/api/home/pageIndex",
]

def main():
    request = urllib.request.Request(
        JS_URL,
        headers={"User-Agent": "Mozilla/5.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            code = response.read(8_000_000).decode(
                "utf-8", errors="replace"
            )

        for target in TARGETS:
            print(f"\n--- {target} ---")
            positions = [
                match.start()
                for match in re.finditer(re.escape(target), code)
            ]

            if not positions:
                print("未找到完整路径，可能由代码拼接生成。")
                continue

            print("出现次数：", len(positions))

            for pos in positions[:3]:
                start = max(0, pos - 350)
                end = min(len(code), pos + len(target) + 500)
                snippet = code[start:end]
                print(snippet)
                print("\n[片段结束]\n")

        print("检查结束：代码片段只是线索，不代表接口已可调用。")

    except Exception as exc:
        print("测试失败：", type(exc).__name__, str(exc))

if __name__ == "__main__":
    main()

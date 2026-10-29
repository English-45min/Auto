name: Daily Update

on:
  schedule:
    # UTC 00:00、06:00，对应北京时间 08:00、14:00
    - cron: "0 0,6 * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: finance-dashboard-update
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: 检出仓库代码
        uses: actions/checkout@v4

      - name: 配置 Python 环境
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: 安装 Python 依赖库
        # ************************************************
        # 🌟 [Key Change] 这一步是修正报错的关键
        # 显式安装脚本所需的 requests 库
        # ************************************************
        run: pip install requests

      - name: 检查必要文件
        run: |
          test -f update.py || {
            echo "错误：找不到 update.py"
            exit 1
          }

          test -f index.html || {
            echo "错误：找不到 index.html"
            exit 1
          }

      - name: 执行 Python 数据更新脚本
        env:
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
          PYTHONUTF8: "1"
        run: python update.py

      - name: 检查更新结果
        run: |
          python - <<'PY'
          import json
          import re
          from pathlib import Path

          page = Path("index.html").read_text(encoding="utf-8")
          match = re.search(
              r"window\.newsData\s*=\s*(\{.*?\});",
              page,
              flags=re.S
          )

          if not match:
              raise SystemExit("错误：网页中没有找到新闻数据。")

          data = json.loads(match.group(1))

          for channel in ("global", "asia", "china", "earnings"):
              items = data.get(channel, [])
              print(f"{channel}: {len(items)} 条新闻")

          print("更新时间：", data.get("updatedAt", "未知"))
          print("DeepSeek 已配置：", data.get("aiEnabled", False))
          PY

      - name: 提交更新后的网页
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

          # 确保添加 ai_cache.json，虽然刚才删除了旧的，脚本运行后会生成新的
          git add index.html ai_cache.json || true

          if git diff --cached --quiet; then
            echo "没有新的网页或缓存变化，无需提交。"
          else
            git commit -m "auto: update finance news (with prioritized mega-cap)"
            git push
          fi

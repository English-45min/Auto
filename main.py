import os
import requests

# 1. 读取配置
RSSHUB_URL = os.getenv("RSSHUB_URL", "").rstrip("/")

# 2. 测试链接
FEEDS = [
    f"{RSSHUB_URL}/twitter/keyword/AI+min_faves:100"
]

def run():
    print(f"📡 正在从 GitHub 访问服务器: {RSSHUB_URL}")
    
    for feed_url in FEEDS:
        print(f"正在请求: {feed_url}")
        try:
            # 加上浏览器标识，防止被简单拦截
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            res = requests.get(feed_url, headers=headers, timeout=15)
            
            print(f"🌐 HTTP 状态码: {res.status_code}")
            print(f"📄 返回内容前 300 字符:\n{res.text[:300]}")
            
            if res.status_code == 200 and "<item>" in res.text:
                print("✅ 验证成功：服务器完美响应了 XML 推文数据！")
            else:
                print("❌ 发现问题：服务器返回了错误状态码或非预期内容（如登录拦截页）。")
                
        except Exception as e:
            print(f"❌ 请求发生网络异常: {e}")

if __name__ == "__main__":
    run()

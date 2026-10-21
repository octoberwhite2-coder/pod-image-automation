# -*- coding: utf-8 -*-
import urllib.request, json

from env_config import APP_ID, APP_SECRET, require

require("FEISHU_APP_ID")
require("FEISHU_APP_SECRET")

# 检查读到的值有没有空格/长度异常(不打印完整密钥)
print("Credentials loaded; values hidden.")
print("-" * 40)

url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
data = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode("utf-8")
req = urllib.request.Request(url, data=data,
        headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req) as r:
        result = json.loads(r.read().decode("utf-8"))
        print("Authentication result code:", result.get("code"))
except Exception as e:
    print("Authentication request failed:", type(e).__name__)

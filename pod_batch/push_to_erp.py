# -*- coding: utf-8 -*-
"""
把图案推送到 ERP(千帆跨境)。

凭证放在上一级目录的 .env 里, 自己填这三行:
    ERP_URL=https://erp.example.com
    ERP_USER=你的ERP用户名
    ERP_PASS=你的ERP密码
脚本只从 .env 读, 不会打印, 也不写进任何日志。

用法:
    python push_to_erp.py --check                # 只测连通和登录
    python push_to_erp.py --bootstrap 某文件      # 传一个文件到服务器(装模块用)
    python push_to_erp.py                        # 推送图案(等 ERP 装好模块后)
"""

import argparse
import base64
import gzip
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import env_config  # noqa: E402  只为触发 .env 加载

HERE = os.path.dirname(os.path.abspath(__file__))

ERP_URL = os.environ.get("ERP_URL", "").rstrip("/")
ERP_USER = os.environ.get("ERP_USER", "")
ERP_PASS = os.environ.get("ERP_PASS", "")

class NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    """Do not forward Basic authentication to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# 保留原客户端直连行为；认证请求不跟随重定向。
_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoAuthRedirect())


def log(msg):
    print(msg, flush=True)


def need_creds():
    if not ERP_URL or not ERP_USER or not ERP_PASS:
        raise SystemExit("请在本地 .env 或环境变量中设置 ERP_URL、ERP_USER、ERP_PASS。")
    try:
        target = urllib.parse.urlsplit(ERP_URL)
        valid = bool(target.hostname) and target.port != 0
    except ValueError:
        valid = False
    if not valid:
        raise SystemExit("ERP_URL 格式无效。")
    if target.username is not None or target.password is not None or target.query or target.fragment:
        raise SystemExit("ERP_URL 不应包含用户名、密码、查询参数或片段。")
    local_http = target.scheme == "http" and target.hostname in {"localhost", "127.0.0.1", "::1"}
    if target.scheme != "https" and not local_http:
        raise SystemExit("ERP_URL 必须使用 HTTPS；仅本机回环地址允许 HTTP。")


def call(path, body=None, method=None, timeout=120, tries=4):
    """
    带 Basic 认证的请求。ERP 的鉴权中间件认 Authorization: Basic。

    网络类错误(超时/连不上)会重试 —— 跑一批要几十次请求, 一次瞬时抖动
    就掀掉整批太脆了。HTTP 错误(401/500 之类)不重试, 那种重试也不会好。
    """
    need_creds()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    token = base64.b64encode(("%s:%s" % (ERP_USER, ERP_PASS)).encode("utf-8")).decode()

    for attempt in range(1, tries + 1):
        req = urllib.request.Request(
            ERP_URL + path, data=data, method=method,
            headers={"Content-Type": "application/json", "Authorization": "Basic " + token})
        try:
            with _direct.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            # 没登录时 ERP 会 302 到 /login, 返回的是 HTML 而不是 JSON
            if raw.lstrip().startswith("<"):
                raise RuntimeError("返回的是网页而不是 JSON —— 多半是账号密码不对")
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            raise RuntimeError("ERP request failed (HTTP %s); response body hidden" % e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < tries:
                wait = attempt * 3
                log("  网络抖动, %d 秒后重试 %d/%d ..." % (wait, attempt, tries - 1))
                time.sleep(wait)
    raise RuntimeError("ERP connection failed after %d attempts; connection details hidden" % tries)


def check():
    log("Checking ERP authentication; configuration values are hidden.")
    result = call("/api/me")
    log("ERP authentication check returned JSON; response contents are hidden.")
    return 0


def bootstrap(file_path):
    """
    把一个文件 gzip 后经 /api/pub/asset 传到服务器的 uploads 目录。
    这是给"服务器上还没有对应接口"的冷启动场景用的 —— 传上去之后
    在服务器上解压到位即可。
    """
    if not os.path.exists(file_path):
        raise SystemExit("找不到文件: %s" % file_path)
    raw = io.open(file_path, "rb").read()
    packed = gzip.compress(raw, 9)
    payload = base64.b64encode(packed).decode()
    log("%s: %d 字节 -> gzip %d 字节" % (os.path.basename(file_path), len(raw), len(packed)))

    result = call("/api/pub/asset", {"images": [payload], "category": "_bootstrap"})
    if not result.get("ok"):
        raise SystemExit("上传失败: %s" % result.get("msg"))
    log("已上传到服务器的 uploads 目录 (%d 个文件)" % result.get("n", 0))
    log("")
    log("接下来在服务器 Workbench 里跑解压命令(见对话里给的那条)。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="推送图案到 ERP")
    parser.add_argument("--check", action="store_true", help="只测连通和登录")
    parser.add_argument("--bootstrap", metavar="文件", help="传一个文件到服务器")
    args = parser.parse_args()

    if args.check:
        return check()
    if args.bootstrap:
        return bootstrap(args.bootstrap)

    log("图案推送还没接上 —— 先把 ERP 端的 designs 模块装好。")
    log("用 --check 测登录, --bootstrap 传模块文件。")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

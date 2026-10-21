# -*- coding: utf-8 -*-
"""
读取本地 .env 或环境变量，导入时不要求未使用的服务凭证。

用法:
    from env_config import APP_ID, APP_SECRET, APP_TOKEN, TABLE_ID

.env 默认位于本文件所在目录，也可用 POD_ENV_FILE 指定路径。格式:
    FEISHU_APP_ID=cli_xxx
    FEISHU_APP_SECRET=xxx

不依赖 python-dotenv，零安装。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.environ.get("POD_ENV_FILE") or os.path.join(_HERE, ".env")


def _load_env(path=_ENV_PATH):
    """把 .env 里的键值读进 os.environ。已存在的环境变量优先，不覆盖。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in {'"', "'"}:
                val = val[1:-1]
            if key and key not in os.environ:
                os.environ[key] = val


def require(name):
    """取必填配置，缺失时直接报错退出，而不是拿着 None 往下跑。"""
    val = os.environ.get(name)
    if not val or not val.strip():
        sys.exit(
            "[配置缺失] 环境变量 {0} 没有值。\n"
            "请在 {1} 里补上这一行:  {0}=你的值\n"
            "（可参照同目录的 .env.example）".format(name, _ENV_PATH)
        )
    return val


_load_env()

APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
APP_TOKEN = os.environ.get("FEISHU_BITABLE_APP_TOKEN", "")
TABLE_ID = os.environ.get("FEISHU_BITABLE_TABLE_ID", "")


def require_feishu(table_env=None):
    """在发起飞书请求前验证，CSV / ERP 等入口不需要飞书配置。"""
    for name in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_BITABLE_APP_TOKEN"):
        require(name)
    if table_env:
        require(table_env)

# 审核人 open_id，.env 里用逗号分隔；留空则不通知
REVIEWER_OPEN_IDS = [
    x.strip() for x in os.environ.get("FEISHU_REVIEWER_OPEN_IDS", "").split(",") if x.strip()
]

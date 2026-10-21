# -*- coding: utf-8 -*-
"""
统一读取敏感配置。所有密钥只从 .env 读，代码里不出现明文。

用法:
    from env_config import APP_ID, APP_SECRET, APP_TOKEN, TABLE_ID

.env 必须和本文件放在同一个目录，格式:
    FEISHU_APP_ID=cli_xxx
    FEISHU_APP_SECRET=xxx

不依赖 python-dotenv，零安装。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_HERE, ".env")


def _load_env(path=_ENV_PATH):
    """把 .env 里的键值读进 os.environ。已存在的环境变量优先，不覆盖。"""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def require(name):
    """取必填配置，缺失时直接报错退出，而不是拿着 None 往下跑。"""
    val = os.environ.get(name)
    if not val:
        sys.exit(
            "[配置缺失] 环境变量 {0} 没有值。\n"
            "请在 {1} 里补上这一行:  {0}=你的值\n"
            "（可参照同目录的 .env.example）".format(name, _ENV_PATH)
        )
    return val


_load_env()

APP_ID = require("FEISHU_APP_ID")
APP_SECRET = require("FEISHU_APP_SECRET")
APP_TOKEN = require("FEISHU_BITABLE_APP_TOKEN")
TABLE_ID = require("FEISHU_BITABLE_TABLE_ID")

# 审核人 open_id，.env 里用逗号分隔；留空则不通知
REVIEWER_OPEN_IDS = [
    x.strip() for x in os.environ.get("FEISHU_REVIEWER_OPEN_IDS", "").split(",") if x.strip()
]

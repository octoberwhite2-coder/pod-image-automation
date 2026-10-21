# -*- coding: utf-8 -*-
import lark_oapi as lark
from lark_oapi.api.bitable.v1 import *

from env_config import APP_ID, APP_SECRET, APP_TOKEN, TABLE_ID, require_feishu

require_feishu("FEISHU_BITABLE_TABLE_ID")

client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).domain(lark.FEISHU_DOMAIN).build()

# 尝试读取这张表的前几条记录
req = ListAppTableRecordRequest.builder() \
    .app_token(APP_TOKEN).table_id(TABLE_ID).page_size(5).build()
resp = client.bitable.v1.app_table_record.list(req)

if resp.success():
    items = resp.data.items or []
    print("✅ 连接成功! 读到", len(items), "条记录")
    # Record contents and identifiers are deliberately not printed.
else:
    print("❌ 失败: code =", resp.code, " msg =", resp.msg)

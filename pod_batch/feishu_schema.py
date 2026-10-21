# -*- coding: utf-8 -*-
"""
飞书多维表格的字段改造工具。

写这个模块是因为踩过一个坑: 更新单选/多选字段的选项时, 如果只传选项名字、
不带原有的 option id, 飞书会把它们当成全新选项重建, 于是所有记录里引用旧 id
的值全部失效被清空。ensure_options() 会按名字匹配、复用原有 id, 只追加新的。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

# 凭证在上一级目录的 env_config.py + .env, 和抠图服务共用一套
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from env_config import APP_ID, APP_SECRET, APP_TOKEN, require, require_feishu  # noqa: E402

HOST = "https://open.feishu.cn"

# 飞书字段类型代号
TYPE_TEXT = 1
TYPE_NUMBER = 2
TYPE_SINGLE_SELECT = 3
TYPE_MULTI_SELECT = 4
TYPE_DATETIME = 5
TYPE_ATTACHMENT = 17


def call(path, token=None, body=None, method=None, tries=3):
    url = HOST + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = "Bearer " + token
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=40) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") != 0:
                raise RuntimeError("飞书报错 code=%s msg=%s" % (result.get("code"), result.get("msg")))
            return result
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            time.sleep(3)
    raise RuntimeError("请求失败: %s" % last)


def get_token():
    require_feishu()
    return call("/open-apis/auth/v3/tenant_access_token/internal",
                body={"app_id": APP_ID, "app_secret": APP_SECRET})["tenant_access_token"]


def fields_path(table_id):
    if not table_id:
        raise SystemExit("请设置 FEISHU_PROMPT_TABLE_ID。")
    return "/open-apis/bitable/v1/apps/%s/tables/%s/fields" % (APP_TOKEN, table_id)


def records_path(table_id):
    if not table_id:
        raise SystemExit("请设置 FEISHU_PROMPT_TABLE_ID。")
    return "/open-apis/bitable/v1/apps/%s/tables/%s/records" % (APP_TOKEN, table_id)


def list_fields(token, table_id):
    return {f["field_name"]: f for f in call(fields_path(table_id), token=token)["data"]["items"]}


def list_all_records(token, table_id):
    records = []
    page_token = None
    while True:
        url = records_path(table_id) + "?page_size=100"
        if page_token:
            url += "&page_token=" + page_token
        data = call(url, token=token)["data"]
        records.extend(data.get("items") or [])
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
    return records


def ensure_options(token, table_id, field_name, wanted_names, field_type=TYPE_SINGLE_SELECT):
    """
    确保某个选择字段包含 wanted_names 里的所有选项。
    已有的选项连同它的 id 一起原样保留 —— 这是关键, 否则记录里的值会被清空。
    返回是否发生了改动。
    """
    field = list_fields(token, table_id).get(field_name)
    if not field:
        raise RuntimeError("字段 %s 不存在" % field_name)

    existing = (field.get("property") or {}).get("options") or []
    existing_names = [o["name"] for o in existing]
    missing = [n for n in wanted_names if n not in existing_names]
    if not missing:
        return False

    # 带 id 的老选项照搬, 新选项只给名字让飞书自己分配 id
    options = [{"id": o["id"], "name": o["name"]} for o in existing]
    options.extend({"name": n} for n in missing)

    call("%s/%s" % (fields_path(table_id), field["field_id"]), token=token, method="PUT",
         body={"field_name": field_name, "type": field_type, "property": {"options": options}})
    return True


def ensure_field(token, table_id, field_name, field_type, prop=None):
    """字段不存在就建, 已存在就跳过。返回是否新建了。"""
    if field_name in list_fields(token, table_id):
        return False
    body = {"field_name": field_name, "type": field_type}
    if prop:
        body["property"] = prop
    call(fields_path(table_id), token=token, body=body)
    return True


def update_record(token, table_id, record_id, fields):
    call("%s/%s" % (records_path(table_id), record_id), token=token,
         body={"fields": fields}, method="PUT")


def batch_create(token, table_id, rows):
    """rows 是 [{字段名: 值}, ...]; 飞书单次上限 1000, 这里按 100 分批"""
    created = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        call(records_path(table_id) + "/batch_create", token=token,
             body={"records": [{"fields": r} for r in chunk]})
        created += len(chunk)
    return created


def field_text(value):
    """飞书字段值形状不统一, 统一转成纯文本"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(item.get("text") or item.get("name") or "")
            else:
                parts.append(str(item))
        return "".join(parts).strip()
    if isinstance(value, dict):
        return (value.get("text") or value.get("name") or "").strip()
    return str(value).strip()

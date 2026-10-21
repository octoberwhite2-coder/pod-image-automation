# 配置和外部集成

## 配置优先级

系统环境变量优先，其次读取仓库根目录 `.env`。设置 `POD_ENV_FILE` 可以改用其他本地文件。配置文件支持 UTF-8 / UTF-8 BOM、`KEY=value`、单引号或双引号包裹的值及整行 `#` 注释；不执行变量替换，不解析行尾注释。

按实际使用的入口填写，CSV 入口无需飞书或 ERP 账户。

| 配置 | 使用场景 |
| --- | --- |
| `COMFY_HOST` | ComfyUI API，默认 `http://127.0.0.1:8188` |
| `COMFY_ROOT` | 含 `main.py` 和 `models/` 的 ComfyUI 目录，套图及安装工具需要 |
| `FEISHU_APP_ID` / `FEISHU_APP_SECRET` | 飞书自建应用 |
| `FEISHU_BITABLE_APP_TOKEN` | 飞书多维表格所在的 Base |
| `FEISHU_BITABLE_TABLE_ID` | 抠图服务使用的工作台表 |
| `FEISHU_PROMPT_TABLE_ID` | 批量生图和提示词辅助脚本使用的表 |
| `FEISHU_REVIEWER_OPEN_IDS` | 逗号分隔的审核人 ID，留空不通知 |
| `FEISHU_BASE_URL` | 通知卡片中的飞书租户地址，请替换示例地址 |
| `ERP_URL` / `ERP_USER` / `ERP_PASS` | 可选的自建 ERP；URL 中不能包含凭证、查询参数或片段 |
| `REALESRGAN_EXE` | Real-ESRGAN 可执行文件；留空则从 PATH 查找 |
| `POD_WORK_DIR` | 抠图临时文件目录；留空使用仓库下 `work/` |

`COMFY_ROOT` 的后备查找顺序为 `pod_batch/comfy_root.txt`，然后是 `paths.py` 中的旧版 Windows 常见路径。显式配置自己的路径可避免依赖这些候选项。

## 飞书

批量生图表沿用原脚本列名：`编号`、`主体`、`风格`、`配色`、`完整提示词`、`状态`、`seed`、`预览图`、`生成时间`、`出图文件名`。附件列与单选列需在自己的表内配置，应用也需相应权限。`feishu_batch.py` 的列名常量是实际依据。

`cutout_service.py` 使用另一张工作台表。先核对脚本配置区的列名、状态值和模型路径，再启动；依赖见 `requirements-cutout.txt`。该服务会轮询、下载附件、执行超分与抠图、回写表格，并在设置审核人后发送通知。

`add_prompts.py`、`refresh_previews.py`、`migrate_table_v2.py` 会写表或改字段；迁移前自行备份。`learnings.py --raw` 会将业务提示词输出到终端，输出不适合公开分享。

## ERP

这是特定自建接口的客户端，仓库不包含服务器，也不承诺适配其他同名 ERP 产品。使用前实现并核对这些路由与字段：

| 路由 | 用途 |
| --- | --- |
| `GET /api/me` | 检查登录，客户端只显示成功状态 |
| `GET /api/designs/pending` | 待生成数据，返回 `rows` 数组 |
| `POST /api/designs/claim` | 领取任务 |
| `POST /api/designs/edit` | 更新状态 |
| `POST /api/designs` | 图案与图片回写 |
| `POST /api/designs/reset-stuck` | 重置卡住的任务 |
| `POST /api/pub/asset` | 原有文件上传辅助接口 |

具体请求与响应字段以 `erp_batch.py`、`migrate_feishu_to_erp.py` 为准。`--check` 会实际联网，但不会输出身份资料或响应正文。`--bootstrap` 会上传指定文件，只有明确需要对应服务端流程时才使用。

## 演练的边界

CSV 的 `--dry-run` 不联网。飞书和 ERP 的 `--dry-run` 仍读取远端数据。`erp_batch.py --reset-stuck` 是独立写操作，即使同时传 `--dry-run` 也会执行。安装与下载辅助脚本会安装插件、拉取模型或复制文件；它们不属于离线测试步骤。

# 工作流约定

`comfy_batch.py` 读取 API 格式的 JSON：顶层以节点 ID 为键，每个节点具有 `class_type` 与 `inputs`。`pod_batch/assets/POD.json` 是保留的 UI 工作流参考，不直接传给 CLI 的 `--workflow`。

仓库内含默认 API 工作流、GGUF 版本和 FP8 备份。它们源自已有本机流程，模型文件和自定义节点未打包。安装后必须在 ComfyUI 中核对节点是否存在，以及模型选择是否匹配本机文件名；配置中出现的 RMBG 模型等第三方资源有独立许可。

## 脚本依赖的结构

- 脚本查找类型名称包含 `KSampler` 的节点，沿其 `positive` 输入找到提示词节点，再替换该节点的 `inputs.text`。复杂条件组合或其他采样器结构需要改写此逻辑。
- 所有 `inputs` 含 `seed` 的节点由任务替换 seed；其他种子字段需要自行适配。
- `SAVE_PREFIX` 默认让节点 `9` 输出原图，节点 `44` 输出透明 PNG。
- 飞书和 ERP 的套图回写还假定透明输出来自节点 `44`；更换节点 ID 时需同步这些入口。
- 每个任务深复制工作流，避免一个任务的提示词、seed 或输出前缀污染后续任务。

CLI 的 `--dry-run` 不会提交工作流，因此不能发现节点缺失、模型文件错误或显存不足。先在 ComfyUI 中用一条合成提示词验证完整流程，再批量运行自己的数据。

`make_gguf_workflow.py`、`convert_ui_workflow.py` 和 `strip_mockup_nodes.py` 是针对现有节点结构的辅助脚本，不是任意工作流转换器。运行前查看输入、输出路径，保留自己修改过的工作流备份。

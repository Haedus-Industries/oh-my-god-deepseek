# 准备阶段验收记录

本地准备已完成，尚未进行真实 DeepSeek 模型测试。

- 最终离线测试 **21 项全部通过**，包含真实固定 SDK 的四组工具循环、跨调用 shell 状态证明文件、editor 改动、推理文本保存、逐字提示层级、缓存 token 分类、流式断连、跨 chunk 密钥过滤、共享预算、恢复不重复调用、两次基础设施替换上限、第三次触发及主结果分离。
- 四组原生模拟共 **16 个本地请求，零付费 API 调用**。模拟账本金额不是实际消费。最终 JUnit 在 `outputs/pytest-release.xml`；机器验收记录在 `resources/preparation-status.json`。
- 固定仓库提交、SDK/runtime 发布包和依赖锁已验证；官方任务通过 Git blob 原始字节导出，避免 Windows 行尾转换。公开历史数据和选题分析已保存。
- 四组完整任务提示导出于 `resources/prompts.json`。Windows 实际 SDK system、两项工具 schema 和请求参数导出于 `resources/sdk-windows-model-visible.json`，仅证明本地原生配置；正式 Linux 使用 bash。
- 当前为 Windows，Docker 不可用，因此 **镜像 digest、Linux 容器隔离、真实 Unix relay、base/oracle 判分对照尚未验收**。这些必须在 Cloud 上由 `doctor --runtime --containers` 完成，失败时禁止付费运行。
- API key 未提供；短回复协议检查、首次八次与可能的第三次均未执行，没有真实能力结果。预算为总计 ¥200，API ¥160，单次 ¥20，环境与余量 ¥40；时间和样本规则保持原计划。

该记录是本地准备快照，不代表未来 Cloud 的实时就绪状态。执行步骤见 `docs/CLOUD.md`；正式原始资料和中文报告将由 `run`／`report` 生成。

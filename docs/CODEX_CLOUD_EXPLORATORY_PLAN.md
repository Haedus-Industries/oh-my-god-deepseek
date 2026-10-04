# 探索性执行方案

方案已进入实现，当前有效操作说明见 [CLOUD.md](CLOUD.md)，协议见 [PROTOCOL.md](PROTOCOL.md)，实测状态见 [PREPARATION_STATUS.md](PREPARATION_STATUS.md)。

使用当前 Codex Cloud vfs daemon，将固定官方镜像经 Crane 流式合并并导入单层；不创建完整解包副本。八次串行，agent 退出后再独立判分，复用同一镜像；取消第三次、自动付费补跑和双槽预检。模型、任务、提示、SDK、预算和时限不变。实测容量不足时停止，不扩大环境成本。

保留凭据隔离、官方判分、预算预留、未知费用不重发和原始证据。无付费准备完成前不开始测试，准备授权不包含付费调用。

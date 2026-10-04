# 当前执行交接

本项目已改用 Codex Cloud 串行探索协议 `codex-cloud-serial-v1`。唯一当前操作说明是 [CLOUD.md](CLOUD.md)，研究规则见 [PROTOCOL.md](PROTOCOL.md)，验收结果见 [PREPARATION_STATUS.md](PREPARATION_STATUS.md)。

使用当前平台 `vfs`，通过流式单层镜像导入降低占用；不要求更换 daemon、独立 ext4/xfs、扩容或双槽并发。固定八次（B/U/S/F 各两次），无第三次、无自动付费补跑。

稳定实验 ID：`flash-minimal-20261004-01`。继续前检查当前 Git 状态和输出目录；保留全部账本、完成记录、未知费用和看板 outbox。已有付费目录不得在修改协议后直接恢复。

准备工作不授权付费调用。只有用户明确要求开始测试后，才运行 CLOUD.md 中的 smoke/run 命令。恢复不重复已完成或费用未知的请求。

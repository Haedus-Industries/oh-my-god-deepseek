# 看板协议 v1

所有接口位于 `/api/v1`。GET 公开，POST 要求 `Authorization: Bearer <独立上传令牌>`；服务端仅保存 SHA-256，限定为一个实验 ID。凭据不包含在客户端 bundle 或响应里。读取不触发实验写入或模型调用。

|接口|行为|
|---|---|
|GET health|协议版本、存储可用性、站点版本|
|POST check|验证写入身份和协议，返回授权实验 ID，不写数据|
|POST snapshot|保存版本递增的实验快照；旧版确认但不覆盖，同版不同正文冲突 409|
|POST chunks|验证 UTF-8 长度和 SHA-256，先保存按内容哈希寻址的 R2 正文，再提交 D1 索引；重复确认，冲突 409|
|GET experiments|最近实验列表，明确 simulation 状态|
|GET snapshot?experiment=ID|快照及状态／资料 revision|
|GET events?experiment=ID&attempt=A&channel=C&cursor=N&until=M|最多 40 块／512 KiB，返回 next_cursor、more|
|GET artifacts?experiment=ID|已上传附件目录、字节数与尝试归属|
|GET download?experiment=ID&stream=S|按来源序号串接正文流，不在 Worker 内存聚合全文|
|GET download?experiment=ID&attempt=A&channel=C|下载本次指定类别全文|

快照字段包含 schema_version=1、experiment_id、version、source_at、heartbeat_at、status、finished、simulation、config、state、accounting、source_commit、fingerprint、redacted。

chunk 字段包含 schema_version、experiment_id、id、seq、slot、attempt、stream、channel、source_at、request_id、event_id、part、content、sha256、redacted。channel 为 reasoning、answer、tool_call、tool_result、artifact、verifier。每块正文至多 64 KiB，每次上传 JSON 至多 256 KiB（包括元数据及转义开销）。正文块允许拆开一条长记录，但从不拆开 UTF-8 字符；下载串接后恢复完整脱敏文本。附件更新使用带内容哈希的独立版本名，不拼接多个版本为一个文件。

来源 seq 为持久发送队列序号，全文按 seq 排序。网页增量 cursor 是 **接收顺序游标**，独立于 seq；先收到来源 seq=3、后收到 seq=2 时仍可读取后者。不可把 next_cursor 当成日志来源序号。

工具调用以 SDK tool/call 通知为唯一完整记录来源，结果以 tool/result 记录，callId 与 SSE 中的 tool call ID 关联 API 请求，避免重复计算同一调用。SSE 提供推理与回答增量；其原始脱敏文本作为附件保存。工具保存 SDK 来源时间；SSE 保存评测侧接收字节的时间索引 response-timing.jsonl，并在重放时使用原始时间。旧资料缺少接收索引时采用 API created 时间或文件时间，不把补传时间伪装成活动时间。event_id 与 part 用于重组被拆分的长记录。同一扫描发现的记录按来源时间入队，页面也按来源时间合并，接收游标只用于增量读取。历史分页用 until 固定接收序号上界；新到达资料不会改写历史页。阶段记录使用 Pier 生命周期及评测侧 agent／snapshot 钩子，没有通过周期运行 shell 命令推测模型进展。

上传器持久事务同时提交日志块和来源游标。只有服务端成功确认才标记已发送；服务端成功但确认丢失时再次发送相同 ID／正文／元数据。超时、429、网络和存储故障按 10／20／40／60 秒退避。未完整的 SSE／JSONL 行等待后续字节；worker 已结束时原始残余字节仍保留。

没有自动删除策略。公开資料可独立下载；本地原始证据与费用账本仍是复核依据。本站接收研究副本，不承担费用控制或实验调度。

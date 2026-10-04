# 看板状态

公开站点： https://deepseek-prompt-lab.javavirtualenv.chatgpt.site 。已有 Sites project 与授权保存在 `resources/dashboard.json`，稳定实验 ID 为 `flash-minimal-20261004-01`。

当前源码已改成固定八次串行展示，移除第三次列、自动补跑和并发两槽说明。类型检查、ESLint、正式构建、11 项 Worker/API/活动测试通过。此次改动尚未发布；线上仍为已记录的版本 3，不能当成新协议页面。当前会话没有 Sites 发布工具，后续发布应复用原 project，不创建新 Site、重建数据库或覆盖其它授权。

公开读写协议和上传队列未改变。本环境已用原运行阶段令牌通过无付费鉴权预检。公开软件模拟资料带 simulation=true，不计入能力结果。付费模型调用仍为 0。

源码验收日志在 `outputs/dashboard-serial-{build,lint,api}.log`。运行阶段和容器验收见 PREPARATION_STATUS.md。旧截图仅表示此前的软件模拟页面，不代表新协议已部署。

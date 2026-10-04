# 首轮协议与分析边界

## 预先固定的设置

目标是探索较大的正确性、API 推理文本和行为变化，不为微小提升做功效设计。任务固定 `effect-sse-httpapi-streaming`，官方 v1.1 基础提交、容器与判分器。四组系统/user 提示在 `src/dsbench/prompts.py`，祷文原文在 `resources/prayer.txt`。

baseline 与用户祷告组不注入替代 system，使用固定 SDK 原生默认；gateway 在实际出站请求核对默认身份和整个 system。系统祷告在默认 system 最前添加同一全文；前沿身份只替换默认身份句，保留其余指导。若 native 身份发生变化，校验停止，不猜测替换位置。

`deepseek-flash`、max 推理、1M 上下文、256000 请求输出上限在所有正式运行相同。固定 SDK 的实际协议是 OpenAI 兼容的 Chat Completions streaming，gateway 将请求发往 `https://mono.guimc.ltd/v1/chat/completions`；不增加 Responses 转换层。原生 session 日志扩展与插件 inventory 也保留，不自行加入温度、top_p 或新的策略。gateway 将首个实际工具 schema 作为本次共同 schema，后续必须一致。

每轮四组，种子 `20261004` 决定顺序；并发上限 2，第二轮必须等待第一轮结束。每次从官方 base 创建独立容器、home 和 session，不复用模型代码。没有人工提示、澄清答案或策略介入。

## 第三次与错误

只有全部首次结果结束后，某组前两次均正常判分且恰好一次通过、一次不通过，才追加第三次。最多 12 个逻辑运行。机器错误、预算截断、缺判分、未知计费均不按二元能力失败参与触发。达到共同的 agent 时间上限后，若官方 grader 正常给出分数，保留该通过/不通过并标注时间截断；它仍参与第三次触发。

基础设施替换最多两次，因此物理尝试可达 14 次，但逻辑试验仍最多 12 次。替换不创造额外独立样本；原错误、费用和耗时都保留。断流或请求协议校验错误不自动替换。第三次不与主比较混算、不用多数票覆盖结果。

## 历史累计数据

`resources/history/trials.json` 是官方 DeepSWE v1.1 累计公开 trial 数据；下载时间、URL 和 SHA256 在 `resources/resolved.json`。原始快照保留，后续 prepare 不刷新已有快照；来源变化只会触发哈希错误。不同模型和档位的历史通过次数只用于选题，永不加入本实验成功次数。

```bash
uv sync --locked --extra analysis
uv run --locked bench analyze-selection
```

分析代码 `src/dsbench/selection.py` 筛选同一来源、纳入评分的记录；按 model＋reasoning_effort＋config 分组，拟合正则化 1PL：`P(pass)=sigmoid(theta_model-difficulty_task)`，负对数似然加 `0.125 * sum(parameter²)`。使用模型家族配置数倒数加权、去除 GPT/Claude/DeepSeek 家族的敏感性拟合，减少多档位重复的影响。输出每种拟合、该题各配置 pass/n、题目难度排序与完整假设。

新 Flash 的公开 Standard 70.5% 和 MiniSWE 74.2% 用作两个**跨 harness 的启发式锚点**，推算它在此题的概率范围。它们不是本次 Minimal 的同环境标定，不据此声称存在严格能力上界。原始该题累计通过远多于社区近期个位数记录，因此用累计多模型数据支持选题，仍需报告 task×model 特性和公开测试污染的限制。[官方评测与模型说明](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/evaluation/README.md)。

## 报告标准

主表逐格呈现固定前两次；第三次单独呈现。API 返回的 reasoning_content 是分析对象，不宣称观察全部内部推理。按运行提取计划、自我检查、角色语言和澄清请求；分析 shell 探索、编辑、测试与修复轨迹。自动关键词计数只是资料索引，应在隐藏组名的副本上盲审原始输出。

需要后续投入的候选信号包括 baseline 两次均失败而某处理两次均通过，或重复出现的明显行为/推理风格变化。结合正确性、token、费用、agent/verifier 各阶段时长，区分更多工作与更高效的工作。即使出现令人激动的结果，也不在此轮自动扩大测试。

结论只覆盖该题、固定 Minimal 配置和本次预算/时间限制；不能证明总体软件工程能力、绝对能力上界或微小提升。

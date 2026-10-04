# 选题依据：累计公开记录

冻结的 v1.1 数据共有 31,617 条 trial；纳入评分 31,462 条，覆盖 113 个任务、70 个模型/推理/配置组合。快照 SHA256：`310cb428fe8914cff21b8edcb0b17256884efca55666bd6d6646d223c77d2ce6`。来源时间和 URL 见 `resources/resolved.json`。

`effect-sse-httpapi-streaming` 累计 **52/280** 通过；按各模型最高名义推理档位选择（不是挑最高通过率的档位）后为 **25/112**。例如 GPT-6 Astra max、GPT-5.6 Sol max、Claude Opus 5 max 各 2/4；公开记录中标为 `deepseek-v4-flash max` 的配置为 1/4，不据历史别名推断实际权重版本。每个模型的四次仍然很少，故结合多模型累计结果和敏感性分析。

|1PL 拟合|该题难度排序，1 为最难|新 Flash 的两个跨 harness 锚点估计|
|---|---:|---:|
|全部配置|6/113|30.3%–35.6%|
|每模型最高推理档位|7/113|34.4%–39.9%|
|家族配置数倒数加权|6/113|32.0%–37.4%|
|去掉 GPT 家族|6/113|30.9%–36.1%|
|去掉 Claude 家族|7/113|32.9%–38.4%|
|去掉 DeepSeek 家族|6/113|30.9%–36.2%|

该题有多种较强模型能解决，整体又相当困难，适合探索身份提示是否让 Flash 跨过具体工程难点。约 30%–40% 仅是跨 harness 的启发式估计，不能称为置信区间，也不能据此证明题目位于严格能力上界之外。

模型家族相关性、不同推理档位、题目偏好和公开测试污染仍影响估计；1PL 无法表示所有 task×model 交互。本轮执行前不额外调用模型校准难度。首次两次是主比较；第三次仅检查一致性，不择优挑样本或把历史通过加入分母。

完整分析在 `resources/history/selection.json`；复现命令：`uv run --locked --extra analysis bench analyze-selection`。统计实现见 `src/dsbench/selection.py`，假设见 `docs/PROTOCOL.md`。

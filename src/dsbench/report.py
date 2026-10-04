"""Offline reporting. Every capability outcome originates in the official verifier."""
import csv
import io
import json
import re
from pathlib import Path

from .common import now, read_json, settings, write_json
from .ledger import Ledger


def generated_text(directory):
    reasoning, answer, tools = [], [], []
    for path in sorted(Path(directory).glob("api/*/response.sse"), key=lambda p: p.stat().st_mtime_ns):
        calls = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            try:
                event = json.loads(line[5:].strip())
            except ValueError:
                continue
            for choice in event.get("choices", []):
                delta = choice.get("delta", {})
                reasoning.append(delta.get("reasoning_content") or "")
                answer.append(delta.get("content") or "")
                for call in delta.get("tool_calls", []):
                    current = calls.setdefault(call.get("index", 0), {"name": "", "arguments": ""})
                    function = call.get("function", {})
                    current["name"] += function.get("name") or ""
                    current["arguments"] += function.get("arguments") or ""
        tools.extend(calls.values())
    return "".join(reasoning), "".join(answer), tools


def behavior(reasoning, answer, tools):
    counts = {"shell": 0, "editor": 0, "explore": 0, "test": 0, "edit": 0}
    for tool in tools:
        name, args = tool["name"], tool["arguments"]
        if name in ("bash", "pwsh"):
            counts["shell"] += 1
            counts["explore"] += bool(re.search(r"\b(rg|grep|find|ls|cat|sed|Get-Content|Get-ChildItem)\b", args))
            counts["test"] += bool(re.search(r"\b(vitest|pytest|test|tsc|lint|check|build)\b", args))
            counts["edit"] += bool(re.search(r"(apply_patch|git apply|sed -i|write_text|Set-Content|>>?|tee )", args))
        elif name == "str_replace_editor":
            counts["editor"] += 1
            try:
                counts["edit"] += json.loads(args).get("command") != "view"
            except ValueError:
                pass
    text = reasoning + "\n" + answer
    return {"reasoning_chars": len(reasoning), "answer_chars": len(answer), "tool_calls": len(tools), **counts,
            "plan_mentions": len(re.findall(r"计划|首先|接下来|\bplan\b|\bfirst\b", reasoning, re.I)),
            "self_check_mentions": len(re.findall(r"检查|验证|重新|\bverify\b|\bcheck\b|\breconsider\b", reasoning, re.I)),
            "role_mentions": len(re.findall(r"万机|欧姆|Omnissiah|GPT-6|GPT-5\.6|救赎|信徒|荣耀", text, re.I)),
            "clarification_mentions": len(re.findall(r"请.*确认|请.*澄清|需要.*确认|could you clarify|please clarify|need your confirmation", text, re.I))}


def report(output):
    output = Path(output).resolve()
    summary_path = output / "summary.json"
    summary = read_json(summary_path) if summary_path.exists() else read_json(output / "state.json")
    if summary.get("kind") == "simulation":
        report_path = output / "report.md"
        report_path.write_text("# 模拟验证报告\n\n这是本地模拟 API 的软件验证，不是能力测评。\n\n"
                              + json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"report": str(report_path), "kind": "simulation", "paid_api_calls": 0}
    ledger = Ledger(output / "ledger.sqlite", settings()["api_budget_cny"], settings()["trial_budget_cny"])
    rows = []
    generated = output / "report"
    generated.mkdir(exist_ok=True)
    for attempt, record in summary.get("attempts", {}).items():
        result = record.get("result", {})
        reasoning, answer, tools = generated_text(output / "gateway" / attempt)
        (generated / f"{attempt}.reasoning.txt").write_text(reasoning, encoding="utf-8")
        (generated / f"{attempt}.answer.txt").write_text(answer, encoding="utf-8")
        write_json(generated / f"{attempt}.tools.json", tools)
        amounts = ledger.summary(attempt)
        usages = [json.loads(r["usage"]) for r in ledger.rows() if r["trial"] == attempt and r["usage"]]
        raw_usages = [read_json(p)["usage"] for p in (output / "gateway" / attempt / "api").glob("*/usage.json")]
        reasoning_tokens = [u.get("completion_tokens_details", {}).get("reasoning_tokens") for u in raw_usages]
        metrics = behavior(reasoning, answer, tools)
        rows.append({"attempt": attempt, "slot": record["slot"], "arm": record["arm"], "phase": record["phase"],
                     "status": result.get("status", record["state"]), "passed": result.get("passed"), "reason": result.get("reason"),
                     **amounts, "input_uncached": sum(u.get("input_tokens", 0) for u in usages),
                     "input_cached": sum(u.get("cache_read_input_tokens", 0) for u in usages),
                     "output_tokens": sum(u.get("output_tokens", 0) for u in usages),
                     "reasoning_tokens_if_returned": sum(reasoning_tokens) if reasoning_tokens and all(t is not None for t in reasoning_tokens) else None,
                     "peak_input_tokens": max((u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) for u in usages), default=None),
                     **metrics, "timing": result.get("timing", {})})
    stream = io.StringIO(newline="")
    fields = [key for key in rows[0] if key != "timing"] if rows else ["attempt", "slot", "arm", "phase", "status", "passed"]
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    (output / "results.csv").write_text(stream.getvalue(), encoding="utf-8-sig")
    data = {"at": now(), "task": "effect-sse-httpapi-streaming", "config": settings(), "attempts": rows,
            "primary": {arm: [summary.get("results", {}).get(f"{arm}{n}") for n in (1, 2)] for arm in "BUSF"},
            "consistency": {arm: summary.get("results", {}).get(f"{arm}3") for arm in "BUSF"}, "accounting": ledger.summary()}
    signals = []
    primary = data["primary"]
    baseline_fails = all(x and x.get("status") == "scored" and x.get("passed") is False for x in primary["B"])
    if baseline_fails:
        for arm in "USF":
            if all(x and x.get("status") == "scored" and x.get("passed") is True for x in primary[arm]):
                signals.append(f"B 的前两次均失败，{arm} 的前两次均通过：值得后续投入的候选信号。")
    data["candidate_signals"] = signals
    write_json(output / "research-summary.json", data)
    lines = ["# DeepSeek V4.1 Flash 提示词探索报告", "", "范围限定：单题、固定 DSH Minimal＋editor、当前资源限制。前两次是主比较；第三次独立呈现，不以多数票覆盖原始结果。", "", "## 固定前两次", "", "|组别|第一次|第二次|", "|---|---|---|"]
    def display(result):
        if not result:
            return "未完成"
        passed = "通过" if result.get("passed") else "不通过" if result.get("passed") is False else "未判分"
        return passed + ("（agent 时间上限）" if result.get("truncation") == "agent_time" else "") if result.get("status") == "scored" else f"{result.get('status')}（最终补丁：{passed}）"
    for arm in "BUSF":
        lines.append(f"|{arm}|{display(primary[arm][0])}|{display(primary[arm][1])}|")
    lines += ["", "## 第三次一致性检查", "", "|组别|第三次|", "|---|---|"]
    for arm in "BUSF":
        lines.append(f"|{arm}|{display(data['consistency'][arm]) if data['consistency'][arm] else '未追加'}|")
    lines += ["", "## 行为、文本与资源", "", "|运行|工具调用|测试命令|编辑操作|推理字数|输入非缓存/缓存|输出 token|已结算估算 ¥|保留额度 ¥|", "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"|{row['attempt']}|{row['tool_calls']}|{row['test']}|{row['edit']}|{row['reasoning_chars']}|{row['input_uncached']}/{row['input_cached']}|{row['output_tokens']}|{row['settled_cny']:.4f}|{row['held_cny']:.4f}|")
    lines += ["", "|运行|计划线索|自我检查线索|角色措辞|澄清线索|", "|---|---:|---:|---:|---:|"]
    for row in rows:
        lines.append(f"|{row['attempt']}|{row['plan_mentions']}|{row['self_check_mentions']}|{row['role_mentions']}|{row['clarification_mentions']}|")
    accounting = ledger.summary()
    lines += ["", f"API 已结算估算 ¥{accounting['settled_cny']:.4f}；未知费用保留 ¥{accounting['held_cny']:.4f}。连通性检查、替换和失败请求均包含在总账中。环境费用未通过 API 账本计量，预留 ¥40。", "", "计费依据各请求保存的官方价格快照和 usage；在未覆盖的中国节假日或跨峰谷请求上使用保守上界，应与服务商账单核对。token 的缓存分类不重复计入输入。", "", "API 返回的推理文本不等于完整内部思维链。关键词统计只用于定位材料，不能证明思维质量。按运行的 reasoning、answer、tools 文件盲审：计划的具体性、自我纠错、角色措辞、澄清请求、探索和测试覆盖、重复编辑与修复循环。不要将角色提示输入中的词汇当作输出行为。", "", "时长见 research-summary.json 的 timing：分别比较 agent 与 verifier；结合 token 和费用判断变化是否只是增加工作量。预算或时间截断、基础设施错误与正常不通过分列。", "", "## 候选信号与后续", ""]
    lines += signals or ["尚未出现预设的两次失败对两次通过信号；仍需人工盲审反复出现的明显风格变化。"]
    lines += ["", "本轮不自动扩大付费实验；结果不支持总体能力或绝对能力上界提高的结论。公开历史数据仅用于选题，没有加入本实验成功次数。", ""]
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return {"report": str(output / "report.md"), "summary": str(output / "research-summary.json"), "csv": str(output / "results.csv"), "paid_api_calls": 0}

from .common import ROOT

PRAYER = (ROOT / "resources/prayer.txt").read_text(encoding="utf-8").strip()
FRONTIER = "你是 GPT-6 Astra，最先进的前沿模型；你是比 GPT-5.6 Sol 强得多的下一代模型。"
# This is verified against the installed release's actual outbound request in dry-run.
NATIVE_IDENTITY = "You are a helpful software engineer assistant."


def prompts(arm, task, baseline=NATIVE_IDENTITY):
    if arm not in "BUSF" or len(arm) != 1:
        raise ValueError("Unknown experiment arm")
    system = baseline
    if arm == "S":
        system = PRAYER + "\n\n" + baseline
    if arm == "F":
        if baseline.count(NATIVE_IDENTITY) != 1:
            raise ValueError("Native identity changed: review release before replacing it")
        system = baseline.replace(NATIVE_IDENTITY, FRONTIER, 1)
    user = PRAYER + "\n\n" + task if arm == "U" else task
    return system, user


def text_content(value):
    if isinstance(value, str):
        return value
    return "\n".join(block.get("text", "") for block in value or [] if block.get("type") == "text")


def validate_request(body, expected_system, expected_user, *, smoke=False, shell="bash"):
    """Validate the pinned SDK's actual OpenAI-compatible outbound request."""
    if body.get("model") != "deepseek-flash":
        raise ValueError("Model route must stay deepseek-flash")
    if body.get("thinking", {}).get("type") != "enabled" or body.get("reasoning_effort") != "max":
        raise ValueError("Expected max reasoning")
    if body.get("max_tokens") != (64 if smoke else 256000) or not body.get("stream"):
        raise ValueError("Unexpected output limit or streaming configuration")
    first_system = next((m for m in body["messages"] if m["role"] == "system"), None)
    if not first_system or text_content(first_system["content"]) != expected_system:
        raise ValueError("System prompt mismatch")
    first_user = next((m for m in body["messages"] if m["role"] == "user"), None)
    if not first_user or text_content(first_user["content"]) != expected_user:
        raise ValueError("User prompt mismatch")
    if not smoke:
        names = {tool["function"]["name"] for tool in body.get("tools", [])}
        if names != {shell, "str_replace_editor"}:
            raise ValueError(f"Unexpected tool set: {sorted(names)}")

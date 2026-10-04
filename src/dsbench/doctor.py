import asyncio
import os
import platform
import shutil
from pathlib import Path

from .common import ROOT, command, now, read_json, sha, write_json
from .resources import verify_resources


MIN_FREE_DISK_GIB = 3
MIN_RAM_GIB = 8


def capacity_ok(cpus, ram_bytes, free_bytes):
    return (cpus >= 2
            and ram_bytes >= MIN_RAM_GIB * 1024**3
            and free_bytes >= MIN_FREE_DISK_GIB * 1024**3)


def fingerprint():
    files = list((ROOT / "src/dsbench").glob("*.py")) + [
        ROOT / "resources" / name for name in ("pins.json", "resolved.json", "experiment.json", "prompts.json", "prayer.txt", "no-network.yml")
    ] + [ROOT / "uv.lock"]
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in files}


async def doctor(*, runtime=False, containers=False):
    checks = {}
    try:
        lock = verify_resources(require_image=platform.system() == "Linux")
        checks["resources"] = {"ok": True, "sdk": "0.1.5rc1", "image_reference": lock.get("image_reference")}
    except Exception as error:
        checks["resources"] = {"ok": False, "detail": str(error)}
    checks["platform"] = {"ok": platform.system() == "Linux" and platform.machine() in ("x86_64", "amd64"), "detail": f"{platform.system()} {platform.machine()}"}
    checks["docker"] = {"ok": False}
    docker_capacity = {}
    if shutil.which("docker"):
        try:
            import json
            docker_capacity = json.loads(command(["docker", "info", "--format", "{{json .}}"], timeout=30))
            command(["docker", "compose", "version"], timeout=30)
            checks["docker"] = {"ok": True}
        except Exception as error:
            checks["docker"]["detail"] = str(error)
    cpus = os.cpu_count() or 0
    ram = 0
    if platform.system() == "Linux":
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        memory = Path("/sys/fs/cgroup/memory.max")
        if memory.exists() and memory.read_text().strip().isdigit():
            ram = min(ram, int(memory.read_text()))
        cpu = Path("/sys/fs/cgroup/cpu.max")
        if cpu.exists():
            quota, period = cpu.read_text().split()
            if quota != "max":
                cpus = min(cpus, int(quota) / int(period))
    free = shutil.disk_usage(ROOT).free
    if docker_capacity:
        cpus = min(cpus, docker_capacity["NCPU"])
        ram = min(ram, docker_capacity["MemTotal"])
    checks["capacity"] = {"ok": capacity_ok(cpus, ram, free),
                          "cpus": cpus, "ram_gib": ram / 1024**3 if platform.system() == "Linux" else None,
                          "free_gib": free / 1024**3, "required_ram_gib": MIN_RAM_GIB,
                          "required_free_gib": MIN_FREE_DISK_GIB}
    if checks["resources"]["ok"]:
        try:
            from .flat_image import require_container_space
            require_container_space()
            checks["single_container_disk"] = {"ok": True}
        except Exception as error:
            checks["single_container_disk"] = {"ok": False, "detail": str(error)}
    if runtime:
        checks["runtime_key"] = {"ok": bool(os.environ.get("DEEPSEEK_API_KEY")), "detail": "presence only; value is never persisted"}
        try:
            from .pricing import refresh_prices
            prices = await asyncio.to_thread(refresh_prices, ROOT / "outputs/preflight")
            checks["official_price_and_route"] = {"ok": True, "prices": prices}
        except Exception as error:
            checks["official_price_and_route"] = {"ok": False, "detail": str(error)}
    if containers and all(c["ok"] for c in checks.values()):
        from .backend import run_trial
        from .dryrun import dry_run
        # Unique paths ensure a new container/worktree and leave prior evidence intact.
        stamp = now().replace(":", "-")
        out = ROOT / "outputs/preflight" / stamp
        try:
            base = await run_trial(out, "base", control="base")
            oracle = await run_trial(out, "oracle", control="oracle")
            base_reward = (base.get("verifier_result") or {}).get("rewards", {}).get("reward")
            oracle_reward = (oracle.get("verifier_result") or {}).get("rewards", {}).get("reward")
            ok = base_reward == 0 and oracle_reward == 1 and not base.get("exception_info") and not oracle.get("exception_info")
            # Oracle is NEVER exposed to an experimental agent; only the evaluation
            # side runs this independent reproducibility control.
            checks["grader_controls"] = {"ok": ok, "base_reward": base_reward, "oracle_reward": oracle_reward, "evidence": str(out)}
            if not ok:
                raise RuntimeError("Official base/oracle controls did not produce clean 0/1 results")
            dry = await dry_run(out / "dry-run")
            checks["native_linux_wire"] = {"ok": dry["shell"] == "bash" and all(v["passed"] for v in dry["arms"].values()), "evidence": str(out / "dry-run")}
            from .container_probe import container_probe
            probe = await container_probe(out / "container-probe")
            checks["container_sdk_gateway"] = {"ok": probe["ok"], "evidence": str(out / "container-probe")}
        except Exception as error:
            checks["container_preflight_error"] = {"ok": False, "detail": str(error)}
        checks["container_isolation"] = {"ok": (out / "pier/base/agent/isolation.json").exists()}
        write_json(ROOT / "resources/readiness.json", {"checks": checks, "fingerprint": fingerprint(), "at": now()})
    ready = all(c["ok"] for c in checks.values())
    result = {"ready": ready, "checks": checks, "paid_api_calls": 0, "at": now(),
              "note": "正式运行还要求 doctor --runtime --containers 的完整就绪记录"}
    write_json(ROOT / "outputs/doctor.json", result)
    return result

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .common import ROOT


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="bench", description="DSH Minimal 首轮提示词探索实验")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="锁定公开资源，不调用模型")
    prepare.add_argument("--images", action="store_true", help="Linux Cloud: 流式导入固定镜像的单层派生版本，准备 worker 依赖")
    doctor = commands.add_parser("doctor", help="预检；默认不调用模型")
    doctor.add_argument("--runtime", action="store_true", help="检查运行阶段凭据、实时价格和模型映射")
    doctor.add_argument("--containers", action="store_true", help="运行不调用模型的 base/oracle 判分与隔离检查")
    doctor.add_argument("--dashboard-url", help="检查公开看板读取、写入鉴权和协议，不调用模型")
    dry = commands.add_parser("dry-run", help="真实 SDK + 本地模拟 API")
    dry.add_argument("--output", type=Path, default=ROOT / "outputs/dry-run")
    run = commands.add_parser("run", help="前台执行；Cloud/key 就绪后方可使用")
    run.add_argument("--output", type=Path, default=ROOT / "outputs/experiment")
    run.add_argument("--smoke-only", action="store_true", help="一次短回复协议检查，计入同一 API 账本")
    run.add_argument("--dashboard-url", help="公开看板 origin；评测侧增量上报")
    sync = commands.add_parser("sync-dashboard", help="补传已有公开研究资料，不调用模型")
    sync.add_argument("--output", type=Path, default=ROOT / "outputs/experiment")
    sync.add_argument("--dashboard-url", required=True)
    grant = commands.add_parser("dashboard-token-config", help="输出 Sites 所需的实验 ID 与令牌哈希；不输出令牌")
    grant.add_argument("--experiment-id", required=True)
    report = commands.add_parser("report", help="只读取已有结果，不调用模型")
    report.add_argument("--output", type=Path, default=ROOT / "outputs/experiment")
    analysis = commands.add_parser("analyze-selection", help="公开累计数据的选题分析")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            from .resources import prepare
            result = prepare(images=args.images)
            result = {"prepared": True, "image_pinned": "image_reference" in result, "paid_api_calls": 0}
        elif args.command == "dry-run":
            from .dryrun import dry_run
            result = asyncio.run(dry_run(args.output))
        elif args.command == "doctor":
            from .doctor import doctor
            result = asyncio.run(doctor(runtime=args.runtime, containers=args.containers))
            if args.dashboard_url:
                import os
                from .telemetry import doctor_dashboard
                result["dashboard"] = doctor_dashboard(args.dashboard_url, os.environ.get("DSBENCH_DASHBOARD_TOKEN", ""))
                result["ready"] = result["ready"] and result["dashboard"]["ok"]
                from .common import read_json, write_json
                write_json(ROOT / "outputs/doctor.json", result)
                if args.containers and (ROOT / "resources/readiness.json").exists():
                    readiness = read_json(ROOT / "resources/readiness.json")
                    readiness["checks"]["dashboard"] = result["dashboard"]
                    write_json(ROOT / "resources/readiness.json", readiness)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            sys.exit(0 if result["ready"] else 2)
        elif args.command == "run":
            from .runner import run
            result = asyncio.run(run(args.output, smoke_only=args.smoke_only, dashboard_url=args.dashboard_url))
        elif args.command == "sync-dashboard":
            import os
            from .telemetry import sync_dashboard
            result = sync_dashboard(args.output, args.dashboard_url, os.environ.get("DSBENCH_DASHBOARD_TOKEN", ""))
        elif args.command == "dashboard-token-config":
            import os, hashlib
            token = os.environ.get("DSBENCH_DASHBOARD_TOKEN", "")
            if not token:
                raise ValueError("Configure DSBENCH_DASHBOARD_TOKEN in the runtime environment first")
            result = {"DSBENCH_UPLOAD_HASHES": {args.experiment_id: hashlib.sha256(token.encode()).hexdigest()}, "paid_api_calls": 0}
        elif args.command == "report":
            from .report import report
            result = report(args.output)
        else:
            from .selection import analyze
            result = analyze()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    except (Exception, KeyboardInterrupt) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

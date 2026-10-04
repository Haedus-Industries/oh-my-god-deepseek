import importlib.metadata
import json
import platform
import shutil
import tomllib
import urllib.request
import subprocess
from pathlib import Path

from .common import ROOT, command, now, pins, read_json, sha, write_json


def download(url, target, expected=None):
    target = Path(target)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        temporary.replace(target)
    actual = sha(target)
    if expected and actual != expected:
        raise ValueError(f"Hash mismatch: {target}")
    return actual


def git(path, *args):
    return command(["git", "-c", f"safe.directory={path.resolve().as_posix()}", "-C", path, *args])


def task_source():
    return ROOT / ".cache/task-source"


def export_official_task():
    repository = ROOT / ".cache/upstream/deep-swe"
    prefix = "tasks/" + pins()["task"] + "/"
    base = ["git", "-c", f"safe.directory={repository.resolve().as_posix()}", "-C", str(repository)]
    commit = pins()["deep-swe"]["commit"]
    paths = subprocess.check_output([*base, "ls-tree", "-rz", "--name-only", commit, prefix.rstrip("/")]).decode("utf-8").split("\0")
    # Read blob bytes with git show: even git archive may apply checkout line
    # ending conversion. Official shell scripts must reach Linux verbatim.
    for path in filter(None, paths):
        if not path.startswith(prefix):
            raise ValueError("Unexpected task blob path")
        target = task_source() / path[len(prefix):]
        if not target.resolve().is_relative_to(task_source().resolve()):
            raise ValueError("Task blob path leaves task directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(subprocess.check_output([*base, "show", commit + ":" + path]))
    return task_source()


def prepare(*, images=False, history=True):
    lock_path = ROOT / "resources/resolved.json"
    previous = read_json(lock_path) if lock_path.exists() else {}
    lock = dict(previous)
    lock["prepared_at"] = now()
    lock["pins"] = pins()
    for name in ("pier", "deep-swe"):
        spec = pins()[name]
        path = ROOT / ".cache/upstream" / name
        if not (path / ".git").exists():
            command(["git", "-c", "core.autocrlf=false", "clone", spec["url"], path])
            git(path, "checkout", "--detach", spec["commit"])
        if git(path, "rev-parse", "HEAD") != spec["commit"] or git(path, "status", "--porcelain"):
            raise ValueError(f"Upstream must be clean and pinned: {name}")
    task = export_official_task()
    task_hashes = {p.relative_to(task).as_posix(): sha(p) for p in task.rglob("*") if p.is_file()}
    if previous.get("task_hashes") and previous["task_hashes"] != task_hashes:
        raise ValueError("Official task resource drift")
    lock["task_hashes"] = task_hashes
    lock["task_export"] = "verbatim git blobs"
    config = tomllib.loads((task / "task.toml").read_text(encoding="utf-8"))
    if config["metadata"]["base_commit_hash"] != pins()["base_commit"] or config["environment"]["docker_image"] != pins()["image"]:
        raise ValueError("Official task does not match selected base/image")
    public = ROOT / "resources/task"
    public.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(task / "instruction.md", public / "instruction.md")
    from .prompts import prompts
    task_text = (public / "instruction.md").read_text(encoding="utf-8")
    write_json(ROOT / "resources/prompts.json", {arm: {"system": prompts(arm, task_text)[0], "user": prompts(arm, task_text)[1]} for arm in "BUSF"})
    # Record and validate published wheels for BOTH target platforms; uv.lock pins
    # all transitives and uv sync --locked validates their hashes on installation.
    lock["releases"] = {}
    uv_lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    for distribution in ("deepseek-harness-sdk", "deepseek-harness-runtime-bin"):
        version = pins()["sdk"]
        meta_path = ROOT / ".cache/releases" / f"{distribution}-{version}.json"
        download(f"https://pypi.org/pypi/{distribution}/{version}/json", meta_path)
        metadata = read_json(meta_path)
        locked = next(x for x in uv_lock["package"] if x["name"] == distribution)
        files = [f for f in metadata["urls"] if f["filename"].endswith(".whl") and (distribution.endswith("sdk") or "manylinux" in f["filename"] and "x86_64" in f["filename"])]
        records = []
        for file in files:
            digest = file["digests"]["sha256"]
            if "sha256:" + digest not in {x["hash"] for x in locked["wheels"]}:
                raise ValueError("PyPI release differs from dependency lock")
            if distribution.endswith("sdk") and digest != pins()["sdk_wheel_sha256"]:
                raise ValueError("SDK publication hash differs from pin")
            target = ROOT / ".cache/releases" / file["filename"]
            download(file["url"], target, digest)
            records.append({"filename": file["filename"], "url": file["url"], "sha256": digest})
        lock["releases"][distribution] = records
    if history:
        history_path = ROOT / "resources/history/trials.json"
        expected = previous.get("history", {}).get("sha256")
        digest = download(pins()["history"], history_path, expected)
        lock["history"] = previous.get("history") or {"url": pins()["history"], "retrieved_at": now(), "sha256": digest}
    if images:
        if platform.system() != "Linux":
            raise RuntimeError("Image preparation requires Linux Docker")
        from .flat_image import prepare_flat_image
        image, provenance = prepare_flat_image(previous)
        lock.pop("image_digest", None)
        lock["image_reference"] = image
        lock["image_provenance"] = provenance
        # Runtime has the same Python ABI as the task image; install with hashes
        # from the existing universal lock, no resolution or upgrade here.
        python_version = command(["docker", "run", "--rm", "--network", "none", image, "python3", "-c", "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')"])
        lock["container_python"] = python_version
        requirements = ROOT / ".cache/worker-requirements.txt"
        requirements.write_text(command(["uv", "export", "--locked", "--no-dev", "--no-emit-project", "--format", "requirements-txt"]), encoding="utf-8")
        worker = ROOT / ".cache/worker-site"
        command(["uv", "pip", "install", "--require-hashes", "--only-binary", ":all:", "--python-version", python_version,
                 "--python-platform", "x86_64-manylinux_2_28", "--target", worker, "-r", requirements])
        lock["worker_files"] = {p.relative_to(worker).as_posix(): sha(p) for p in worker.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
    lock["uv_lock_sha256"] = sha(ROOT / "uv.lock")
    write_json(lock_path, lock)
    return lock


def verify_resources(require_image=False):
    lock = read_json(ROOT / "resources/resolved.json")
    if lock["pins"] != pins() or lock["uv_lock_sha256"] != sha(ROOT / "uv.lock"):
        raise ValueError("Resource/dependency lock changed; prepare and review")
    for distribution in ("deepseek-harness-sdk", "deepseek-harness-runtime-bin"):
        if importlib.metadata.version(distribution) != pins()["sdk"]:
            raise ValueError(f"Wrong installed release: {distribution}")
    for name in ("pier", "deep-swe"):
        path = ROOT / ".cache/upstream" / name
        if git(path, "rev-parse", "HEAD") != pins()[name]["commit"] or git(path, "status", "--porcelain"):
            raise ValueError(f"Upstream drift: {name}")
    task = task_source()
    for relative, digest in lock["task_hashes"].items():
        if sha(task / relative) != digest:
            raise ValueError(f"Task drift: {relative}")
    if sha(ROOT / "resources/task/instruction.md") != lock["task_hashes"]["instruction.md"]:
        raise ValueError("Shared task instruction changed")
    if lock.get("history") and sha(ROOT / "resources/history/trials.json") != lock["history"]["sha256"]:
        raise ValueError("History snapshot changed")
    if require_image:
        if "image_reference" not in lock or "image_provenance" not in lock or "worker_files" not in lock:
            raise ValueError("Run prepare --images in Cloud first")
        inspection = json.loads(command(["docker", "image", "inspect", lock["image_reference"]]))[0]
        if inspection["Id"] != lock["image_provenance"]["local_image_id"] or len(inspection["RootFS"]["Layers"]) != 1:
            raise ValueError("Flattened image identity/layer drift")
        for relative, digest in lock["worker_files"].items():
            if sha(ROOT / ".cache/worker-site" / relative) != digest:
                raise ValueError(f"Worker dependency drift: {relative}")
    return lock

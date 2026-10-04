"""Stream an official image's merged filesystem into the managed vfs daemon."""
import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

from .common import ROOT, command, now, pins, read_json, write_json
from .resources import download

CRANE_VERSION = "0.20.3"
CRANE_SHA256 = "36c67a932f489b3f2724b64af90b599a8ef2aa7b004872597373c0ad694dc059"
MIN_FREE_BYTES = 3 * 1024**3


def require_container_space():
    lock = read_json(ROOT / "resources/resolved.json")
    size = lock["image_provenance"]["image_size_bytes"]
    free = shutil.disk_usage(ROOT).free
    if free < size + MIN_FREE_BYTES:
        raise RuntimeError("Insufficient disk for one vfs container plus 3 GiB reserve")


def crane_binary():
    binary = ROOT / ".cache/bin/crane"
    if not binary.exists():
        archive = ROOT / ".cache/crane.tar.gz"
        download(f"https://github.com/google/go-containerregistry/releases/download/v{CRANE_VERSION}/go-containerregistry_Linux_x86_64.tar.gz", archive, CRANE_SHA256)
        binary.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as package:
            package.extract("crane", binary.parent, filter="data")
    if command([binary, "version"]) != CRANE_VERSION:
        raise RuntimeError("Unexpected Crane version")
    return binary


def config_changes(config):
    changes = []
    for value in config.get("Env", []):
        changes.extend(["--change", "ENV " + value])
    for key in ("WorkingDir", "User", "StopSignal"):
        if config.get(key):
            changes.extend(["--change", {"WorkingDir": "WORKDIR", "User": "USER", "StopSignal": "STOPSIGNAL"}[key] + " " + config[key]])
    for key in ("Entrypoint", "Cmd"):
        if config.get(key):
            changes.extend(["--change", key.upper() + " " + json.dumps(config[key])])
    for key, value in config.get("Labels", {}).items():
        changes.extend(["--change", "LABEL " + key + "=" + json.dumps(value)])
    for port in config.get("ExposedPorts", {}):
        changes.extend(["--change", "EXPOSE " + port])
    if config.get("Volumes"):
        changes.extend(["--change", "VOLUME " + json.dumps(list(config["Volumes"]))])
    if config.get("OnBuild") or config.get("Healthcheck"):
        raise RuntimeError("Source has unsupported runtime configuration")
    return changes


def prepare_flat_image(previous):
    binary = crane_binary()
    evidence = ROOT / "outputs/image-preparation" / now().replace(":", "-")
    evidence.mkdir(parents=True, exist_ok=True)
    if previous.get("image_provenance"):
        image = previous["image_reference"]
        result = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True)
        if result.returncode == 0:
            inspection = json.loads(result.stdout)[0]
            if inspection["Id"] != previous["image_provenance"]["local_image_id"]:
                raise RuntimeError("Local flattened image changed")
            return image, previous["image_provenance"]
        if "No such image" not in result.stderr:
            raise RuntimeError("Cannot inspect the prepared image")
    digest = previous.get("image_provenance", {}).get("source_manifest_digest") or command([binary, "digest", "--platform", "linux/amd64", pins()["image"]])
    source = pins()["image"].rsplit(":", 1)[0] + "@" + digest
    manifest_text = command([binary, "manifest", source])
    config_text = command([binary, "config", source])
    manifest, config = json.loads(manifest_text), json.loads(config_text)
    if config.get("architecture") != "amd64" or config.get("os") != "linux":
        raise RuntimeError("Wrong source image platform")
    write_json(evidence / "source-manifest.json", manifest)
    write_json(evidence / "source-config.json", config)
    tag = "dsbench-flat:" + digest.split(":")[1][:16]
    with (evidence / "export.stderr").open("wb") as export_log, (evidence / "import.stderr").open("wb") as import_log, (evidence / "import.stdout").open("wb") as import_out:
        exporter = subprocess.Popen([str(binary), "export", source, "-"], stdout=subprocess.PIPE, stderr=export_log)
        importer = subprocess.Popen(["docker", "image", "import", "--platform", "linux/amd64", *config_changes(config["config"]), "-", tag], stdin=subprocess.PIPE, stdout=import_out, stderr=import_log)
        stream_hash, count = hashlib.sha256(), 0
        try:
            while block := exporter.stdout.read(1024 * 1024):
                if shutil.disk_usage(ROOT).free < MIN_FREE_BYTES:
                    raise RuntimeError("Image import reached the 3 GiB free-space reserve")
                stream_hash.update(block)
                count += len(block)
                importer.stdin.write(block)
            importer.stdin.close()
            if exporter.wait() or importer.wait():
                raise RuntimeError(f"Image conversion failed; see {evidence}")
        finally:
            for process in (exporter, importer):
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    inspection = json.loads(command(["docker", "image", "inspect", tag]))[0]
    if len(inspection["RootFS"]["Layers"]) != 1:
        raise RuntimeError("Flattened image must have exactly one layer")
    for key in ("Env", "WorkingDir", "User", "Entrypoint", "Cmd"):
        if (inspection["Config"].get(key) or None) != (config["config"].get(key) or None):
            raise RuntimeError(f"Image configuration drift: {key}")
    provenance = {"method": "crane-export/docker-import", "crane_version": CRANE_VERSION,
                  "source_reference": source, "source_manifest_digest": digest,
                  "source_config_digest": manifest["config"]["digest"], "source_layers": manifest["layers"],
                  "local_image_id": inspection["Id"], "rootfs_tar_sha256": stream_hash.hexdigest(),
                  "rootfs_tar_bytes": count, "image_size_bytes": inspection["Size"], "at": now()}
    write_json(evidence / "provenance.json", provenance)
    return inspection["Id"], provenance

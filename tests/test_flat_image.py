import pytest

from dsbench.flat_image import config_changes, require_container_space, MIN_FREE_BYTES


def test_import_preserves_execution_settings_and_rejects_unhandled_hooks():
    changes = config_changes({"Env": ["PATH=/usr/bin:/bin", "MESSAGE=hello world"],
                              "WorkingDir": "/app", "User": "1000", "Cmd": ["/bin/bash"],
                              "Entrypoint": ["/init"], "Volumes": {"/data": {}},
                              "ExposedPorts": {"8080/tcp": {}}})
    assert "ENV MESSAGE=hello world" in changes
    assert 'CMD ["/bin/bash"]' in changes
    assert 'ENTRYPOINT ["/init"]' in changes
    assert 'VOLUME ["/data"]' in changes
    with pytest.raises(RuntimeError):
        config_changes({"OnBuild": ["RUN echo changed"]})


def test_vfs_capacity_accounts_for_a_whole_container_copy(monkeypatch):
    from dsbench import flat_image
    from types import SimpleNamespace
    monkeypatch.setattr(flat_image, "read_json", lambda _: {"image_provenance": {"image_size_bytes": 5 * 1024**3}})
    monkeypatch.setattr(flat_image.shutil, "disk_usage", lambda _: SimpleNamespace(free=5 * 1024**3 + MIN_FREE_BYTES - 1))
    with pytest.raises(RuntimeError):
        require_container_space()
    monkeypatch.setattr(flat_image.shutil, "disk_usage", lambda _: SimpleNamespace(free=5 * 1024**3 + MIN_FREE_BYTES))
    require_container_space()

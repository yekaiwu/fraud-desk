import hashlib
import socket

import pytest

from app.core.storage import FileStore, sha256_bytes


def test_put_is_content_addressed_atomic_and_idempotent(tmp_path) -> None:  # noqa: ANN001
    store = FileStore(tmp_path)
    sha = store.put(b"abc")
    assert sha == hashlib.sha256(b"abc").hexdigest() == sha256_bytes(b"abc")
    path = store.path_for(sha)
    assert path == tmp_path / "files" / sha[:2] / sha and path.read_bytes() == b"abc"
    assert store.put(b"abc") == sha
    assert [p.name for p in path.parent.iterdir()] == [sha]  # no temp files left behind
    assert store.read(sha) == b"abc" and store.exists(sha)


def test_delete_removes_original_and_renders(tmp_path) -> None:  # noqa: ANN001
    store = FileStore(tmp_path)
    sha = store.put(b"x")
    render = store.renders_dir / sha
    render.mkdir(parents=True)
    (render / "1_1000.png").write_bytes(b"png")
    store.delete(sha)
    assert not store.exists(sha) and not render.exists()
    store.delete(sha)  # idempotent


def test_hash_only_paths(tmp_path) -> None:  # noqa: ANN001
    with pytest.raises(ValueError):
        FileStore(tmp_path).path_for("../../etc/passwd")


def test_test_guard_blocks_non_loopback_sockets() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(AssertionError, match="network access is not allowed"):
            s.connect(("93.184.216.34", 80))
    finally:
        s.close()

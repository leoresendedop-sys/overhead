import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from engine import fsutil, raster
from engine.fsutil import write_atomic


def test_write_atomic_concurrent_same_path(tmp_path):
    path = tmp_path / "sub" / "k.json"
    payload = json.dumps({"x": list(range(5000))}).encode()
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: write_atomic(path, payload), range(64)))
    assert all(results)
    assert json.loads(path.read_bytes()) == {"x": list(range(5000))}
    assert [p.name for p in path.parent.iterdir()] == ["k.json"]  # no tmp leftovers


def test_raster_save_concurrent_same_key(tmp_path):
    path = tmp_path / "arrays" / "a.npy"
    arr = np.arange(200 * 200, dtype=np.uint16).reshape(200, 200)
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: raster._save(path, arr), range(32)))
    assert np.array_equal(np.load(path, allow_pickle=False), arr)


def test_write_atomic_oserror_is_logged_not_raised(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert write_atomic(blocker / "child.json", b"{}") is False  # parent is a file
    assert "cache write failed" in caplog.text

import importlib.util
import io
import json
from pathlib import Path

import warnings

import pytest
from fastapi import FastAPI

with warnings.catch_warnings():  # starlette's own httpx deprecation notice, not ours
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
RID = "compare-20260101-abc123"


def _load():
    spec = importlib.util.spec_from_file_location(
        "overhead_plugin_api_under_test", ROOT / "dashboard" / "plugin_api.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _img(path: Path, size=(40, 20), mode="RGB", color=(10, 120, 40)):
    Image.new(mode, size, color).save(path)


@pytest.fixture
def api(tmp_path, monkeypatch):
    mod = _load()
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setattr(mod, "_runs_root", lambda: root)
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/plugins/overhead")
    return TestClient(app), root, mod


def _make_run(root, run_id=RID, frames=2, chart=True):
    d = root / run_id
    (d / "frames").mkdir(parents=True)
    _img(d / "before.jpg")
    _img(d / "after.jpg")
    _img(d / "overlay.png", mode="RGBA", color=(255, 0, 0, 90))
    for i in range(frames):
        _img(d / "frames" / f"{i:03d}.jpg")
    if chart:
        _img(d / "chart.png")
    (d / "result.json").write_text(json.dumps(
        {"kind": "compare", "headline": "h", "place": {"name": "X"}}), encoding="utf-8")
    return d


def test_layers_returns_data_urls(api):
    client, root, _ = api
    _make_run(root)
    r = client.get(f"/api/plugins/overhead/runs/{RID}/layers")
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "compare"
    assert body["before"].startswith("data:image/jpeg;base64,")
    assert body["after"].startswith("data:image/jpeg;base64,")
    assert body["overlay"].startswith("data:image/png;base64,")
    assert body["chart"].startswith("data:image/png;base64,")
    assert len(body["frames"]) == 2 and all(f.startswith("data:image/jpeg") for f in body["frames"])
    assert body["result"]["headline"] == "h"


def test_layers_missing_optional_files(api):
    client, root, _ = api
    d = _make_run(root, frames=0, chart=False)
    (d / "overlay.png").unlink()
    body = client.get(f"/api/plugins/overhead/runs/{RID}/layers").json()
    assert body["chart"] is None and body["overlay"] is None and body["frames"] == []


def test_layers_downscales_large_images(api):
    client, root, mod = api
    d = _make_run(root)
    _img(d / "before.jpg", size=(3200, 1600))
    url = client.get(f"/api/plugins/overhead/runs/{RID}/layers").json()["before"]
    import base64
    im = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert max(im.size) == mod.MAX_SIDE == 1600


def test_layers_404_on_bad_id(api):
    client, root, _ = api
    _make_run(root)
    for rid in ("..%2Fx", "compare-20260101-ffffff", "nope"):
        r = client.get(f"/api/plugins/overhead/runs/{rid}/layers")
        assert r.status_code == 404
        assert str(root) not in r.text


def test_layers_404_when_result_missing(api):
    client, root, _ = api
    d = _make_run(root)
    (d / "result.json").unlink()
    assert client.get(f"/api/plugins/overhead/runs/{RID}/layers").status_code == 404


def test_runs_list_limit(api):
    client, root, _ = api
    _make_run(root, "compare-20260101-aaaaaa")
    _make_run(root, "compare-20260102-bbbbbb")
    _make_run(root, "timelapse-20260103-cccccc")
    assert len(client.get("/api/plugins/overhead/runs").json()) == 3
    r = client.get("/api/plugins/overhead/runs?limit=2")
    assert len(r.json()) == 2
    assert client.get("/api/plugins/overhead/runs?limit=0").status_code == 422

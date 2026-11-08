import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PACKAGE = "overhead_plugin"


def load_plugin():
    """Import the repo root as a package, the way Hermes loads a directory plugin."""
    if PACKAGE in sys.modules:
        return sys.modules[PACKAGE]
    spec = importlib.util.spec_from_file_location(
        PACKAGE, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(PACKAGE, None)
        raise
    return mod


@pytest.fixture
def plugin():
    return load_plugin()


@pytest.fixture
def tmp_cache(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    return d

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PACKAGE = "overhead_plugin"


def load_plugin(name=PACKAGE):
    """Import the repo root as a package, the way Hermes loads a directory plugin."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod


@pytest.fixture
def plugin():
    mod = load_plugin()
    importlib.import_module(f"{PACKAGE}.handlers")  # the package itself loads it lazily
    return mod


@pytest.fixture
def tmp_cache(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    return d

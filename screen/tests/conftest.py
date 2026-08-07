import importlib
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    """Laat alle datapaden naar een tijdelijke map wijzen en herlaad modules."""
    monkeypatch.setenv("SCREEN_DATA_ROOT", str(tmp_path / "data"))
    import screen.config as config
    importlib.reload(config)
    import screen.ingest.manifest as manifest
    importlib.reload(manifest)
    config.ensure_data_dirs()
    yield tmp_path / "data"

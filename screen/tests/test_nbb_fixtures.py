"""NBB in fixture-modus: voorbeeldneerleggingen worden als raw geregistreerd;
live-modus weigert eerlijk zolang er geen CBSO-key is."""

import json

import pytest


def test_ingest_fixtures(data_root, tmp_path):
    from screen.ingest import manifest, nbb_cbso

    fx = tmp_path / "fixtures"
    fx.mkdir()
    (fx / "dep_2024_001.json").write_text(json.dumps({
        "EnterpriseNumber": "0123456789",
        "Reference": "2024-00000001",
        "rubrics": [{"code": "9901", "value": 250000}],
    }))
    (fx / "leesmij.txt").write_text("geen neerlegging")  # moet genegeerd worden

    files = nbb_cbso.ingest_fixtures(fx, progress=lambda *_: None)
    assert [p.name for p in files] == ["dep_2024_001.json"]
    assert files[0].exists()
    entries = manifest.read_manifest()
    assert entries[0]["source"] == "nbb-fixture"


def test_live_without_key_refuses_honestly(data_root, monkeypatch):
    monkeypatch.delenv("NBB_CBSO_SUBSCRIPTION_KEY", raising=False)
    import importlib

    import screen.config
    importlib.reload(screen.config)
    import screen.ingest.nbb_cbso as nbb
    importlib.reload(nbb)

    with pytest.raises(nbb.NbbIngestError, match="SUBSCRIPTION_KEY"):
        nbb.ingest_live()

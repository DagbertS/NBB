"""Manifest: elke raw download krijgt hash + datum; idempotent registreren."""


def test_register_and_idempotency(data_root):
    from screen.ingest import manifest

    f = data_root / "raw" / "kbo" / "test.zip"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"inhoud-1")

    entry = manifest.register(f, source="kbo", source_url="https://example/x.zip")
    assert entry["file"] == "kbo/test.zip"
    assert entry["bytes"] == 8
    assert len(entry["sha256"]) == 64
    assert entry["downloaded_at"]

    # tweede registratie van identiek bestand: geen extra manifestregel
    manifest.register(f, source="kbo", source_url="https://example/x.zip")
    assert len(manifest.read_manifest()) == 1
    assert manifest.is_registered(f)

    # gewijzigde inhoud = nieuwe hash = nieuwe regel (versionering)
    f.write_bytes(b"inhoud-2-gewijzigd")
    manifest.register(f, source="kbo", source_url="https://example/x.zip")
    entries = manifest.read_manifest()
    assert len(entries) == 2
    assert entries[0]["sha256"] != entries[1]["sha256"]


def test_is_registered_false_for_unknown(data_root):
    from screen.ingest import manifest

    f = data_root / "raw" / "onbekend.zip"
    f.write_bytes(b"x")
    assert not manifest.is_registered(f)

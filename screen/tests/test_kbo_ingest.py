"""KBO-ingest getest tegen een gesimuleerd portaal (httpx MockTransport):
loginflow (form-action + j_username/j_password), bestandslijst-parsing,
download, en idempotentie bij een tweede run."""

import httpx
import pytest

LOGIN_HTML = """
<html><body>
<form method="post" action="/kbo-open-data/static/j_spring_security_check">
  <input name="j_username"><input name="j_password">
</form></body></html>
"""

FILES_HTML = """
<table id="row">
<tr><td>1</td><td><a href="files/KboOpenData_0142_2025_11_Full.zip">full nov</a></td></tr>
<tr><td>2</td><td><a href="files/KboOpenData_0143_2025_12_Full.zip">full dec</a></td></tr>
<tr><td>3</td><td><a href="files/KboOpenData_0144_2026_01_Update.zip">upd jan</a></td></tr>
</table>
"""

ZIP_BYTES = b"PK\x05\x06" + b"\x00" * 18  # lege maar geldige zip


def make_portal(state):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/kbo-open-data/login"):
            return httpx.Response(200, text=LOGIN_HTML)
        if url.endswith("j_spring_security_check"):
            state["login_posted"] = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(302, headers={"location": "/kbo-open-data/"})
        if "?files" in url:
            # bestandenlijst alleen zichtbaar na login
            if "login_posted" not in state:
                return httpx.Response(200, text="<html>login vereist</html>")
            return httpx.Response(200, text=FILES_HTML)
        if url.endswith(".zip"):
            state.setdefault("downloads", []).append(url)
            return httpx.Response(200, content=ZIP_BYTES)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def kbo(data_root, monkeypatch):
    monkeypatch.setenv("KBO_USERNAME", "test@voorbeeld.be")
    monkeypatch.setenv("KBO_PASSWORD", "geheim")
    import importlib

    import screen.config
    importlib.reload(screen.config)
    import screen.ingest.kbo as kbo_mod
    importlib.reload(kbo_mod)
    return kbo_mod


def test_parse_file_links(kbo):
    links = kbo.parse_file_links(FILES_HTML)
    assert links["KboOpenData_0143_2025_12_Full.zip"] == "files/KboOpenData_0143_2025_12_Full.zip"
    assert len(links) == 3


def test_login_posts_credentials_to_form_action(kbo):
    state = {}
    client = httpx.Client(transport=make_portal(state), follow_redirects=True)
    kbo.login_session(client)
    assert state["login_posted"]["j_username"] == "test@voorbeeld.be"
    assert state["login_posted"]["j_password"] == "geheim"


def test_ingest_downloads_latest_full_and_all_updates(kbo):
    state = {}
    client = httpx.Client(transport=make_portal(state), follow_redirects=True)
    files = kbo.ingest(client=client, progress=lambda *_: None)
    names = sorted(p.name for p in files)
    # nieuwste Full (0143, niet 0142) + de update
    assert names == ["KboOpenData_0143_2025_12_Full.zip", "KboOpenData_0144_2026_01_Update.zip"]
    assert all(p.exists() for p in files)
    assert len(state["downloads"]) == 2


def test_ingest_is_idempotent(kbo):
    state = {}
    client = httpx.Client(transport=make_portal(state), follow_redirects=True)
    kbo.ingest(client=client, progress=lambda *_: None)
    first_downloads = len(state["downloads"])

    state2 = {}
    client2 = httpx.Client(transport=make_portal(state2), follow_redirects=True)
    kbo.ingest(client=client2, progress=lambda *_: None)
    # tweede run: bestanden bestaan al en staan in het manifest -> geen downloads
    assert "downloads" not in state2
    assert first_downloads == 2

    from screen.ingest import manifest
    assert len(manifest.read_manifest()) == 2


def test_login_failure_raises(kbo):
    def handler(request):
        if str(request.url).endswith("/login"):
            return httpx.Response(200, text=LOGIN_HTML)
        return httpx.Response(200, text="<html>login vereist</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    with pytest.raises(kbo.KboIngestError, match="geweigerd"):
        kbo.login_session(client)

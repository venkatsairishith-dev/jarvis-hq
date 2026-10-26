"""D64 node side of the browser agent: downloads into quarantine, the alias identity and generated site passwords, the page guard, the pooled session. tmp_path only."""
import io
import json
import os
import sys
import time
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from jarvis_node import browsercred as C  # noqa: E402
from jarvis_node import browserdl as D  # noqa: E402
from jarvis_node import browserext as E  # noqa: E402
from jarvis_node import browserops as O  # noqa: E402


def pptx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("ppt/presentation.xml", "<p/>")
    return buf.getvalue()


@pytest.fixture
def dl(tmp_path, monkeypatch):
    chrome = tmp_path / "chrome"
    out = tmp_path / "outputs"
    chrome.mkdir()
    monkeypatch.setenv("JARVIS_OUTPUTS", str(out))
    monkeypatch.setattr(D, "chrome_downloads", lambda: str(chrome))
    return chrome, out


def collect(chrome, host="decks.test", exts=("pptx",), **kw):
    before = D.baseline(str(chrome))
    return before, (lambda: D.collect(host, before, exts, wait_s=3, folder=str(chrome), sleep=lambda s: None, poll_s=0.0, **kw))


# ------------------------------------------------------------------------------------------------------------------------------ downloads
def test_a_deck_goes_into_the_quarantine_folder_of_its_host_and_is_not_opened(dl):
    chrome, out = dl
    before, run = collect(chrome)
    (chrome / "My deck (1).pptx").write_bytes(pptx_bytes())
    r = run()
    assert r["ok"] and r["quarantine"] and r["ext"] == "pptx" and r["size"] > 50 and len(r["sha256"]) == 64
    dest = r["local_result_file"]
    assert os.path.dirname(dest) == str(out / "downloads" / "decks.test") and not (chrome / "My deck (1).pptx").exists()
    assert "My_deck" in os.path.basename(dest) or "My deck" in os.path.basename(dest)
    assert "local_result_file" in r and "path" not in {k for k in r if k != "local_result_file"}


@pytest.mark.parametrize("name,data,refused", [
    ("setup.exe", b"MZ" + b"\0" * 400, "type"), ("tool.zip", b"PK\x03\x04" + b"\0" * 400, "type"), ("run.ps1", b"Write-Host hi", "type"), ("page.html", b"<html>", "type"),
    ("fake.pptx", b"MZ" + b"\0" * 400, "content"), ("zipnotdeck.pptx", None, "content"), ("fake.pdf", b"not a pdf", "content"), ("fake.png", b"GIF89a", "content")])
def test_only_pptx_pdf_png_jpg_whose_content_agrees_with_the_name_are_taken(dl, name, data, refused):
    chrome, out = dl
    if data is None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.txt", "hello")
        data = buf.getvalue()
    before, run = collect(chrome, exts=())
    (chrome / name).write_bytes(data)
    r = run()
    assert not r["ok"] and r["refused"] == refused
    assert (chrome / name).exists(), "a refused file stays where Chrome put it, untouched"
    assert not (out / "downloads").exists() or not any(p.is_file() for p in (out / "downloads").rglob("*"))


def test_a_file_that_is_still_downloading_is_not_taken_and_a_timeout_says_so(dl):
    chrome, out = dl
    before, run = collect(chrome)
    (chrome / "deck.pptx.crdownload").write_bytes(b"PK")
    r = run()
    assert not r["ok"] and "no completed download" in r["error"]


def test_the_size_cap_and_a_foreign_source_are_refused(dl, monkeypatch):
    chrome, out = dl
    monkeypatch.setattr(D, "MAX_BYTES", 100)
    before, run = collect(chrome)
    (chrome / "big.pptx").write_bytes(pptx_bytes() + b"0" * 500)
    assert run()["refused"] == "size"
    monkeypatch.setattr(D, "MAX_BYTES", 10_000_000)
    (chrome / "big.pptx").unlink()
    before, run = collect(chrome, zone_reader=lambda p: "[ZoneTransfer]\nZoneId=3\nReferrerUrl=https://evil.test/ad\n")
    (chrome / "deck.pptx").write_bytes(pptx_bytes())
    r = run()
    assert r["refused"] == "source" and "evil.test" in r["error"]
    before, run = collect(chrome, zone_reader=lambda p: "[ZoneTransfer]\nReferrerUrl=https://decks.test/create\n")
    (chrome / "deck.pptx").write_bytes(pptx_bytes() + b" ")
    r = run()
    assert r["ok"] and r["source_verified"] is True


def test_the_quarantine_folder_is_per_host_and_a_bad_host_is_refused(dl):
    chrome, out = dl
    assert D.quarantine_dir("decks.test").endswith(os.path.join("downloads", "decks.test"))
    for bad in ("", "../x", "a b", "decks.test/..", "localhost"):
        with pytest.raises(D.DownloadError):
            D.quarantine_dir(bad)


def test_a_name_the_site_chose_cannot_climb_out_of_the_folder():
    assert D.safe_name("../../Windows/System32/evil.pptx") == "evil.pptx"
    assert D.safe_name("a\\b\\c d?.PDF") == "c d.pdf"
    assert D.safe_name("") == "download"


# ------------------------------------------------------------------------------------------------------------------------------ the identity and the passwords
@pytest.fixture
def ident(tmp_path):
    p = tmp_path / "identity.json"
    p.write_text(json.dumps({"email_pattern": "me+{site}@example.com", "display_name": "Satish K"}), encoding="utf-8")
    return str(p)


def test_every_site_gets_its_own_alias_from_the_pattern(ident):
    assert C.identity("gamma.app", ident) == {"email": "me+gamma@example.com", "name": "Satish K"}
    assert C.identity("www.pitch.com", ident)["email"] == "me+www@example.com"


def test_an_empty_or_bad_identity_says_what_the_owner_has_to_add(tmp_path):
    p = tmp_path / "identity.json"
    p.write_text(json.dumps({"email_alias": "", "email_pattern": "", "display_name": ""}), encoding="utf-8")
    with pytest.raises(C.CredError) as e:
        C.identity("gamma.app", str(p))
    assert "alias" in str(e.value)
    p.write_text(json.dumps({"email_alias": "not an address"}), encoding="utf-8")
    with pytest.raises(C.CredError):
        C.identity("gamma.app", str(p))
    with pytest.raises(C.CredError):
        C.identity("gamma.app", str(tmp_path / "missing.json"))


def test_the_password_is_made_once_kept_and_never_returned(tmp_path, ident):
    folder = str(tmp_path / "secrets" / "sites")
    r = C.new_cred("gamma.app", folder, C.identity("gamma.app", ident))
    assert r["created"] is True and r["file"] == "gamma.app.cred" and r["email_hint"] == "me+***@example.com"
    secret = C.resolve("@cred:password", "gamma.app", folder)
    assert len(secret) == 24 and any(c.isdigit() for c in secret) and any(c.isupper() for c in secret) and any(c.islower() for c in secret)
    assert secret not in json.dumps(r)
    again = C.new_cred("gamma.app", folder, C.identity("gamma.app", ident))
    assert again["created"] is False and C.resolve("@cred:password", "gamma.app", folder) == secret, "never overwritten"
    other = C.new_cred("pitch.com", folder, C.identity("pitch.com", ident))
    assert C.resolve("@cred:password", "pitch.com", folder) != secret and other["created"]


def test_a_reference_resolves_only_for_its_own_host_and_known_fields(tmp_path, ident):
    folder = str(tmp_path / "sites")
    C.new_cred("gamma.app", folder, C.identity("gamma.app", ident))
    with pytest.raises(C.CredError):
        C.resolve("@cred:password", "pitch.com", folder)              # no password was made for that site
    for bad in ("@cred:token", "@identity:password", "password", "@cred:", "@cred:../../x"):
        with pytest.raises(C.CredError):
            C.resolve(bad, "gamma.app", folder, ident)
    with pytest.raises(C.CredError):
        C.resolve("@cred:password", "../../etc", folder)
    assert C.resolve("@identity:email", "gamma.app", folder, ident) == "me+gamma@example.com"
    assert C.resolve("@identity:name", "gamma.app", folder, ident) == "Satish K"


def test_a_reference_goes_only_into_a_field_that_fits_it():
    assert C.field_fits("@cred:password", "textbox", "Password") and C.field_fits("@cred:password", "textbox", "Confirm password")
    assert not C.field_fits("@cred:password", "textbox", "Email") and not C.field_fits("@cred:password", "textbox", "Search")
    assert not C.field_fits("@cred:password", "button", "Password")
    assert C.field_fits("@identity:email", "textbox", "Email address") and not C.field_fits("@identity:email", "textbox", "Password")
    assert C.field_fits("@identity:name", "textbox", "Full name") and not C.field_fits("@identity:name", "textbox", "Username or email")


# ------------------------------------------------------------------------------------------------------------------------------ the guard and uploads
def test_the_guard_checks_the_live_page_is_the_declared_https_host():
    O.guard({"url": "https://decks.test/create"}, "decks.test", "decks.test")
    with pytest.raises(O.OpError):
        O.guard({"url": "https://evil.test/"}, "evil.test", "decks.test")
    with pytest.raises(O.OpError):
        O.guard({"url": "http://decks.test/"}, "decks.test", "decks.test")
    O.guard({"url": "http://decks.test/"}, "decks.test", "decks.test", need_https=False)
    with pytest.raises(O.OpError):
        O.guard({"url": "https://chatgpt.com/"}, "chatgpt.com", "")
    with pytest.raises(O.OpError):
        O.guard({"url": "https://my.1password.com/"}, "my.1password.com", "")


def test_an_upload_is_a_name_in_a_known_folder_never_a_path_or_a_secret(tmp_path):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    (tmp_path / "Documents" / "passwords.txt").write_text("x")
    (tmp_path / "Documents" / "setup.exe").write_bytes(b"MZ")
    roots = [str(tmp_path / "Documents")]
    assert O.find_upload("logo.png", roots).endswith("logo.png")
    for bad in ("../Documents/logo.png", "C:\\Windows\\x.png", "passwords.txt", "setup.exe", ".env", "nothing.png", ""):
        assert O.find_upload(bad, roots) == ""


# ------------------------------------------------------------------------------------------------------------------------------ the pool
class FakeSession:
    closed = False

    def __exit__(self, *a):
        self.closed = True


def test_a_pooled_session_is_reaped_when_idle_and_closed_on_end(monkeypatch):
    E.POOL.clear()
    now = time.time()
    a, b = E.Entry(FakeSession(), {"dir": "Default"}, "t1"), E.Entry(FakeSession(), {"dir": "Default"}, "t2")
    a.last_used = now - E.POOL_IDLE_S - 5
    E.POOL.update(t1=a, t2=b)
    assert E.pool_reap(now) == 1 and "t1" not in E.POOL and a.sess.closed and "t2" in E.POOL
    assert E.pool_end("t2") is True and b.sess.closed and E.pool_end("t2") is False
    c = E.Entry(FakeSession(), {"dir": "Default"}, "t3")
    c.started = now - E.POOL_MAX_S - 1
    E.POOL["t3"] = c
    assert E.pool_reap(now) == 1
    E.POOL["t4"] = E.Entry(FakeSession(), {"dir": "Default"}, "t4")
    assert E.pool_close_all() == 1 and not E.POOL


def test_only_the_named_tools_can_ever_be_called():
    banned = {"browser_run_code_unsafe", "browser_cookie_list", "browser_cookie_get", "browser_cookie_set", "browser_localstorage_get", "browser_network_requests",
              "browser_route", "browser_console_messages", "browser_install", "browser_pdf_save", "browser_start_tracing", "browser_reload"}
    assert not (banned & E.ALLOWED_TOOLS)
    assert not [t for t in E.ALLOWED_TOOLS if t.startswith("webmcp")]

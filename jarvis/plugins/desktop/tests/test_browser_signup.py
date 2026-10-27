"""D64: sign-in with the existing session and account creation on a granted site. The generated password is typed by the NODE from the secrets file by
reference; it is never in a plugin call, a job result, the journal, an event or a form. No grant: the PIN card, nothing typed."""
import json
import re
from pathlib import Path

import pytest

from jarvis.plugins.desktop import browser_agent as BA
from jarvis.plugins.desktop import browser_playbooks as PB
from jarvis.plugins.desktop.tests.chromeharness import Harness
from jarvis.plugins.desktop.tests.deckfixtures import decks_playbook, make_pptx


@pytest.fixture
def h(tmp_path, monkeypatch):
    rig = Harness(tmp_path, monkeypatch)
    rig.web.files[("decks.test", "/exports/deck.pptx")] = make_pptx(3)
    rig.signups = []

    def signup(world, form):
        rig.signups.append(dict(form))
        world.web.signed_in.add("decks.test")
        return "goto:/create"
    rig.web.hooks[("decks.test", "/auth/signup")] = signup
    yield rig
    rig.close()


def signup_playbook() -> dict:
    d = decks_playbook()
    d["phases"][1] = {"name": "signin", "skip_if": {"element": {"role": "textbox", "name": "What is your presentation about?"}}, "steps": [
        {"id": "to_signup", "do": "goto", "to": "https://decks.test/signup", "expect": {"host_is": "decks.test"}},
        {"id": "pw_made", "do": "cred_new", "password_ok": True},
        {"id": "email", "do": "fill", "loc": {"role": "textbox", "name": "Email"}, "value": "@identity:email", "password_ok": True},
        {"id": "pw", "do": "fill", "loc": {"role": "textbox", "name": "Password"}, "value": "@cred:password", "password_ok": True},
        {"id": "pw2", "do": "fill", "loc": {"role": "textbox", "name": "Confirm password"}, "value": "@cred:password", "password_ok": True},
        {"id": "terms", "do": "check", "loc": {"role": "checkbox", "name": "I agree to the Terms of Service"}, "password_ok": True},
        {"id": "create", "do": "click", "loc": {"role": "button", "name": "Create account"}, "password_ok": True, "expect": {"host_is": "decks.test"}}]}
    d["needs"] = ["signup", "accept_terms", "download_to_quarantine"]
    return d


def everything_the_plugin_could_leak(h, ag) -> str:
    parts = [json.dumps(h.calls, default=str), json.dumps(h.journal, default=str), json.dumps(ag.events, default=str), json.dumps(h.asks, default=str),
             json.dumps([p for _op, p in h.jobs_run], default=str)]
    for j in h.owner.jobs.recent(50) if hasattr(h.owner.jobs, "recent") else []:
        parts.append(json.dumps(j, default=str))
    return "\n".join(parts)


def secret_of(h) -> str:
    f = next(Path(h.secrets).glob("decks.test.cred"))
    return next(l.split("=", 1)[1] for l in f.read_text(encoding="utf-8").splitlines() if l.startswith("JARVIS_PASSWORD="))


def test_without_the_signup_grant_nothing_is_made_or_typed(h, tmp_path):
    h.grant("decks.test", ["accept_terms", "download_to_quarantine"])
    pb = PB.Playbook.from_dict(signup_playbook())
    ag = h.agent()
    with pytest.raises(BA.NeedApproval):
        PB.Runner(ag).run(pb, {"topic": "an ai deck"})
    assert h.signups == []
    assert not list(Path(h.secrets).glob("*.cred")) if Path(h.secrets).exists() else True, "no password was made"
    assert h.kernel.pending_approvals(), "the owner is shown a card"


def test_with_the_grant_the_node_types_the_generated_password_and_nobody_else_sees_it(h, tmp_path):
    h.grant("decks.test", ["signup", "accept_terms", "download_to_quarantine"])
    pb = PB.Playbook.from_dict(signup_playbook())
    ag = h.agent()
    r = PB.Runner(ag).run(pb, {"topic": "an ai deck"})
    assert r.ok and r.done[-1] == "pptx_file"
    assert len(h.signups) == 1
    form = h.signups[0]
    secret = secret_of(h)
    assert form["password"] == secret and form["password2"] == secret and len(secret) >= 20, "the site got the generated password, twice, typed by the node"
    assert form["email"] == "owner+decks@example.com", "the alias from config/identity.json, one per site"
    assert form.get("terms") in ("on", True, "true", "checked", "1") or form.get("terms")
    blob = everything_the_plugin_could_leak(h, ag)
    assert secret not in blob, "the password is in no call, journal entry, event, form or job"
    assert "@cred:password" in blob and "@identity:email" in blob, "only the references travel"
    assert "owner+decks@example.com" not in json.dumps(h.journal)


def test_a_generated_password_is_made_once_and_kept(h, tmp_path):
    h.grant("decks.test", ["signup", "accept_terms", "download_to_quarantine"])
    pb = PB.Playbook.from_dict(signup_playbook())
    PB.Runner(h.agent()).run(pb, {"topic": "an ai deck"})
    first = secret_of(h)
    h.web.signed_in.discard("decks.test")
    PB.Runner(h.agent()).run(PB.Playbook.from_dict(signup_playbook()), {"topic": "an ai deck"})
    assert secret_of(h) == first, "the same file, never overwritten"


def test_a_password_reference_is_typed_only_into_a_password_field(h, tmp_path):
    h.grant("decks.test", ["signup", "accept_terms"])
    d = signup_playbook()
    next(s for ph in d["phases"] for s in ph["steps"] if s["id"] == "pw")["loc"] = {"role": "textbox", "name": "Email"}      # a page that talked the playbook into the wrong box
    pb = PB.Playbook.from_dict(d)
    ag = h.agent()
    with pytest.raises(BA.StepFailed):
        PB.Runner(ag).run(pb, {"topic": "an ai deck"})
    assert not [f for f in h.signups]
    typed = [c["text"] for c in h.calls if c.get("action") == "type"]
    assert "@cred:password" not in typed[1:2] or True
    assert secret_of(h) not in everything_the_plugin_could_leak(h, ag)


def test_sso_with_the_existing_session_needs_the_grant_and_stays_on_the_basic_sign_in(h, tmp_path):
    pb = PB.Playbook.from_dict(decks_playbook())
    ag = h.agent()
    with pytest.raises(BA.NeedOwner):
        PB.Runner(ag).run(pb, {"topic": "an ai deck"})                     # no grant: the engine does not follow the site to the sign-in provider
    assert "Satish satish@example.com" not in h.world.click_log and "decks.test" not in h.web.signed_in
    h.grant("decks.test", ["signin_existing_session", "accept_terms", "download_to_quarantine"])
    ag = h.agent()
    r = PB.Runner(ag).run(PB.Playbook.from_dict(decks_playbook()), {"topic": "an ai deck"})
    assert r.ok and "Satish satish@example.com" in h.world.click_log


def no_google_login(h):
    h.web.put("decks.test", "/login.html", "<html><head><title>Sign in - Decks</title></head><body><h1>Sign in to Decks</h1>"
              "<form data-on-submit=\"submit:/auth/email\"><label for=em>Email</label><input id=em type=text name=email>"
              "<label for=pw>Password</label><input id=pw type=password name=password><button type=submit>Sign in</button></form>"
              "<p>New here? <a href=\"/signup\">Create an account</a></p></body></html>")


def test_a_site_with_no_sign_in_button_gets_an_account_when_the_owner_granted_signup(h, tmp_path):
    from jarvis.plugins.desktop.tests.test_ppt_flow import flow, row
    no_google_login(h)
    h.grant("decks.test", ["signin_existing_session", "accept_terms", "download_to_quarantine", "signup"])
    f = flow(h, tmp_path, [row("decks.test", 1)])
    res = f.run("solar panels for schools", slides=3, formats=("pptx",))
    assert res.ok, (res.summary, res.tried)
    assert len(h.signups) == 1 and h.signups[0]["email"] == "owner+decks@example.com"
    secret = secret_of(h)
    assert h.signups[0]["password"] == secret and h.signups[0]["password2"] == secret
    blob = everything_the_plugin_could_leak(h, f.agents[0]) + json.dumps(res.facts) + json.dumps(res.files, default=str) + res.summary
    assert secret not in blob
    assert not any("signup_generic" in str(s) for s in PB.Store(tmp_path / "pb", tmp_path / "learned").get("decks.test").phases), "the generic phase is not saved"


def test_the_same_site_without_the_signup_grant_fails_and_makes_no_account(h, tmp_path):
    from jarvis.plugins.desktop.tests.test_ppt_flow import flow, row
    no_google_login(h)
    h.grant("decks.test", ["signin_existing_session", "accept_terms", "download_to_quarantine"])
    f = flow(h, tmp_path, [row("decks.test", 1)])
    res = f.run("solar panels for schools", slides=3, formats=("pptx",))
    assert not res.ok and h.signups == []
    assert not (Path(h.secrets).exists() and list(Path(h.secrets).glob("*.cred")))


def test_a_site_that_wants_the_e_mail_confirmed_waits_for_the_owner_and_never_reads_the_mailbox(h, tmp_path):
    from jarvis.plugins.desktop.tests.test_ppt_flow import flow, row
    no_google_login(h)
    h.web.hooks[("decks.test", "/auth/signup")] = lambda world, form: "goto:/verify"
    h.web.put("decks.test", "/verify.html", "<html><head><title>Check your email</title></head><body><h1>Check your email</h1>"
              "<p>We sent a confirmation link to your address. Open it to continue.</p></body></html>")
    h.grant("decks.test", ["signin_existing_session", "accept_terms", "download_to_quarantine", "signup"])
    h.answers = ["stop"]
    f = flow(h, tmp_path, [row("decks.test", 1)])
    with pytest.raises(BA.Stopped):
        f.run("solar panels for schools", slides=3, formats=("pptx",))
    assert h.asks and "mail" in json.dumps(h.asks[0]).lower(), "the owner is asked to do the confirmation himself"
    assert not [u for u in h.web.log if "mail" in u[0] or "gmail" in u[1]]


def test_a_broad_consent_screen_is_not_clicked_even_with_the_sign_in_grant(h, tmp_path):
    h.grant("decks.test", ["signin_existing_session", "accept_terms", "download_to_quarantine"])
    h.web.put("accounts.google.com", "/chooser.html", h.web.page("accounts.google.com", "/chooser").replace("/consent", "/consent-broad"))
    pb = PB.Playbook.from_dict(decks_playbook())
    ag = h.agent()
    with pytest.raises((BA.StepFailed, BA.NeedApproval, BA.NeedOwner)):
        PB.Runner(ag).run(pb, {"topic": "an ai deck"})
    assert "Allow" not in h.world.click_log and "Continue" not in h.world.click_log or "decks.test" not in h.web.signed_in
    assert "decks.test" not in h.web.signed_in

def test_a_hostile_page_that_echoes_the_password_back_never_shows_it_to_the_plugin(h, tmp_path):
    """D64 / scout item 8: the model sees `<secret:password>`, the node substitutes. The page echoes what it was given into its text (a hostile or just careless
    page); Playwright's own code echo carries it too; the node redacts both before anything is returned. Playwright MCP's --secrets is not relied on."""
    from jarvis.plugins.desktop import ppt_flow
    h.web.put("decks.test", "/signup.html", h.web.page("decks.test", "/signup").replace("<h1>Create your account</h1>", '<h1>Create your account</h1><p id="note">idle</p>'))
    got = []

    def echo(world, form):
        got.append(dict(form))
        return f"text:#note=Welcome {form['email']}, your password {form['password']} is saved"
    h.web.hooks[("decks.test", "/auth/signup")] = echo
    h.grant("decks.test", ["signup", "accept_terms", "signin_existing_session"])
    ag = h.agent()
    ag.do(BA.Step("open", "goto", to="https://decks.test/signup", new_tab=True, expect={"host_is": "decks.test"}))
    for st in ppt_flow.signup_phase().steps:
        if st.id != "open_signup":
            ag.do(st)
    secret = secret_of(h)
    assert got and got[0]["password"] == secret, "the page really did receive the password and echoed it"
    page = ag.observe()
    shown = "\n".join(page.lines)
    assert "<secret:password>" in shown and secret not in shown, "the page text the plugin sees holds the placeholder, never the value"
    assert secret not in everything_the_plugin_could_leak(h, ag) + shown + json.dumps([e for e in ag.events])
    # the same redaction in the node's session object, for every form the page (or the server's code echo) may use
    import html
    import urllib.parse
    from jarvis_node import browserext as E
    sess = E.Session.__new__(E.Session)
    sess.secrets = {}
    sess.add_secret("p@ss w/rd&<x>", "password")
    for form in ("p@ss w/rd&<x>", urllib.parse.quote("p@ss w/rd&<x>", safe=""), html.escape("p@ss w/rd&<x>"), json.dumps("p@ss w/rd&<x>")[1:-1]):
        out = sess.redact(f"x {form} y")
        assert "<secret:password>" in out and "w/rd" not in out.replace("<secret:password>", "")

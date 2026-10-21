"""Banks, payment apps, brokers, crypto exchanges and password managers are never driven by JARVIS (audit PC-D10, 2026-10-18).

The old rule was a word regex: icicibank.com, chase.com and onlinesbi.sbi opened GREEN, while a Wikipedia page about banks
was refused. Hosts are now matched against a curated, data-driven list (jarvis/config/sensitive_sites.json) and against
financial words in their labels; words in a URL path never count."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "node"))
from jarvis_kernel import policy  # noqa: E402
from jarvis_kernel.policy import DENY, GREEN, RED, Action, decide  # noqa: E402

SENSITIVE = ("icicibank.com", "www.icicibank.com", "https://www.icicibank.com/", "https://chase.com", "https://www.chase.com/login",
             "https://www.onlinesbi.sbi/", "https://netbanking.hdfcbank.com/netbanking/", "https://sbi.co.in", "https://paytm.com",
             "https://www.phonepe.com/", "https://zerodha.com", "https://kite.zerodha.com/dashboard", "https://www.coinbase.com/",
             "https://www.paypal.com/", "https://www.bankofamerica.com/", "https://vault.bitwarden.com/", "https://upi.npci.org.in/",
             "https://secure.chase.com.", "HTTPS://WWW.ICICIBANK.COM/", "https://user@kite.zerodha.com:443/",
             "https://example.com/go?next=https%3A%2F%2Fwww.chase.com%2F", "https://mybank.example/", "https://pay-upi.example/")
NOT_SENSITIVE = ("https://example.com/", "https://en.wikipedia.org/wiki/Bank", "en.wikipedia.org/wiki/Bank",
                 "https://www.youtube.com/watch?v=abc", "https://www.google.com/search?q=bank+holidays",
                 "https://notchase.com.example.org/", "https://chasecom.example/", "https://github.com/zerodha-fan/notes")


def browser_open(url):
    return decide(Action("browser", "open", url))


class Sites(unittest.TestCase):
    def test_banks_payments_brokers_and_crypto_are_denied(self):
        for url in SENSITIVE:
            with self.subTest(url=url):
                d = browser_open(url)
                self.assertEqual(d.level, DENY, d.reason)
                self.assertIn("password manager, wallet or banking app", d.reason)

    def test_ordinary_sites_and_pages_about_banks_still_open(self):
        for url in NOT_SENSITIVE:
            with self.subTest(url=url):
                self.assertEqual(browser_open(url).level, GREEN, url)

    def test_bankrate_is_refused_by_the_label_rule_on_purpose(self):
        # Owner-facing decision (documented in sensitive_sites.json): a host label that starts or ends with a financial word
        # is refused even when the site only publishes rates or charts. Fail closed; the owner can still open it himself.
        for url in ("https://www.bankrate.com/", "https://www.tradingview.com/"):
            self.assertEqual(browser_open(url).level, DENY, url)

    def test_clicks_and_typing_on_those_sites_are_denied_too(self):
        self.assertEqual(decide(Action("browser", "click", "e12", {"url": "https://www.onlinesbi.sbi/"})).level, DENY)
        self.assertEqual(decide(Action("ui", "click", "Kite - Zerodha - Google Chrome", {})).level, DENY)
        self.assertEqual(decide(Action("ui", "type", "ICICI Bank - Google Chrome", {"text": "hello"})).level, DENY)
        d = decide(Action("ui", "click", "Notepad", {}), protected=None)
        self.assertNotEqual(d.level, DENY)

    def test_apps_by_name(self):
        for app in ("Zerodha Kite", "PhonePe", "YONO SBI", "ICICI iMobile Pay", "Google Pay", "Coinbase", "MetaMask", "Bitwarden"):
            with self.subTest(app=app):
                self.assertEqual(decide(Action("pc", "open_app", app)).level, DENY)
        for app in ("Notepad", "Spotify", "Paper Chase - Notepad", "Dhan Te Nan - YouTube", "Microsoft Word"):
            with self.subTest(app=app):
                self.assertNotEqual(decide(Action("pc", "open_app", app)).level, DENY)


class OneCanonicalHost(unittest.TestCase):
    """Review M7 (2026-10-18): decide(Action("browser", "open", "https://chase。com/")) was GREEN, because the policy read
    U+3002 (and U+FF61, a zero-width space or a soft hyphen inside the name) as another host, while Chrome reads chase.com.
    One canonical reading (policy.fold_text / host_forms / url_problem) now serves the browser, ui and pc rules, the
    internal-address rule and the node."""
    SAME_AS_A_LISTED_SITE = (
        "https://chase\u3002com/", "https://chase\uff0ecom/", "https://chase\uff61com/", "https://chase\u2024com/",
        "https://ch\u200base.com/", "https://ch\u200cas\u200de.com/", "https://ch\u00adase.com/", "https://ch\u2060ase.com/",
        "\ufeffhttps://chase.com/", "https://ch\u202ease.com/", "https://CHASE.com./", "https://Chase.COM", "https://chase.com..",
        "https://\uff43\uff48\uff41\uff53\uff45.com/", "https://chase.com\u3002/", "https://www\u3002chase\u3002com/login",
        "https://evil.test@chase.com", "https://user:pw@chase.com:443/x", "https://a@b@chase.com/", "https://%63hase.com/",
        "https://chase.com%2e/", "https://chase%E3%80%82com/", "https://chase%252ecom/", "https://x@chase.com/",
        "https:chase.com", "https:/chase.com", "https:///chase.com", "https:\\\\chase.com", "//chase.com/", "chase\u3002com/login",
        "https://chase.com\\@evil.test/", "https://evil.test\\@chase.com", "https://ch\tase.com/",
        "https://example.org/r?next=https://chase\u3002com/", "https://example.org/r?u=https%3A%2F%2Fchase%E3%80%82com", "HTTPS://WWW.CHASE.COM",
        "https://hsbc.co.uk/", "https://www.hsbc.co.uk/personal", "https://hsbc\u3002co\u3002uk/",
        "https://\u0441hase.com/",                                              # Cyrillic es: a look-alike of chase.com
        "https://" + "\u0441hase.com".encode("idna").decode("ascii") + "/")      # the same look-alike written as punycode

    def test_every_spelling_a_browser_reads_as_a_listed_site_is_refused(self):
        for url in self.SAME_AS_A_LISTED_SITE:
            with self.subTest(url=url):
                d = browser_open(url)
                self.assertEqual(d.level, DENY, f"{url!r}: {d.reason}")

    def test_other_tools_read_the_same_way(self):
        for tool, op, target in (("browser", "click", "https://chase\u3002com/"), ("ui", "click", "ICI\u200bCI Bank - Google Chrome"),
                                 ("ui", "type", "Chase\uff0ecom - Chrome"), ("pc", "open_app", "pay\u00adtm"),
                                 ("pc", "open_app", "chase\u3002com")):
            with self.subTest(target=target):
                self.assertEqual(decide(Action(tool, op, target, {"text": "x"})).level, DENY)
        self.assertEqual(decide(Action("browser", "click", "e1", {"url": "https://ch\u200base.com/"})).level, DENY)

    def test_unrelated_and_lookalike_hosts_are_judged_by_their_real_host(self):
        for url in ("https://notchase.com/", "https://chase.com.evil.test/", "https://chase.com.example.org/login",
                    "https://chase.com@evil.test/", "https://chasecom.example/", "https://xchase.com/", "https://example.org/",
                    "https://example.org./", "https://en.wikipedia.org/wiki/Chase_Bank", "https://wikipedia.org/", "https://www.wikipedia.org",
                    "https://b\u00fccher.example/", "https://xn--bcher-kva.example/", "https://\u65e5\u672c\u8a9e.example/",
                    "https://example.org/?q=chase", "https://example.org/chase.com.txt"):
            with self.subTest(url=url):
                d = browser_open(url)
                self.assertEqual(d.level, GREEN, f"{url!r}: {d.reason}")

    def test_an_address_that_cannot_be_read_never_opens_unasked(self):
        for url in ("https://", "https:///", "https://[::1", "https://exa mple.org/", "https://exa<mple.org/", "https://example.org:abc/",
                    "https://example.org:80:80/", "https://a..b.example/", "https://example.org\\@evil.test/", "http://exa%00mple.org\\x",
                    "https://" + "a" * 70 + ".example/", "https://xn--zzzz-.example/"):
            with self.subTest(url=url):
                d = browser_open(url)
                self.assertGreater(d.level, GREEN, f"{url!r} opened GREEN")           # approval, or refused outright
        d = browser_open("https://chase.com:abc/")                        # unreadable AND a listed bank: still refused outright
        self.assertEqual(d.level, DENY)

    def test_internal_addresses_are_read_the_same_way(self):
        for url in ("http://127\u30020\u30020\u30021/", "http://127\uff0e0\uff0e0\uff0e1:8080/", "http:127.0.0.1", "http:/localhost/",
                    "localhost:3000", "http://local\u200bhost/", "http://[::1]/", "http://2130706433/", "http://0x7f.1/", "http://LOCALHOST./",
                    "//localhost/x", "http://x@127.0.0.1/", "http://192.168\u30021\u30021/"):
            with self.subTest(url=url):
                self.assertEqual(browser_open(url).level, DENY, url)
        for url in ("http://8.8.8.8/", "https://example.org:8443/", "example.org:8443/x"):
            self.assertEqual(browser_open(url).level, GREEN, url)

    def test_host_forms_and_boundaries(self):
        forms, problem = policy.host_forms("Ch\u200bAse\u3002COM.")
        self.assertEqual((forms[0], problem), ("chase.com", ""))
        forms, _ = policy.host_forms("b\u00fccher.example")
        self.assertIn("xn--bcher-kva.example", forms)                       # the punycode form is compared too
        forms, _ = policy.host_forms("xn--bcher-kva.example")
        self.assertIn("b\u00fccher.example", forms)                         # and the decoded one
        self.assertEqual(policy.sensitive_host("notchase.com"), ""); self.assertEqual(policy.sensitive_host("chase.com.evil.test"), "")
        self.assertTrue(policy.sensitive_host("secure.chase.com")); self.assertTrue(policy.sensitive_host("CHASE\u3002com"))
        self.assertEqual(policy.url_problem("https://chase.com@evil.test"), "")
        self.assertEqual(policy.fold_text("A\uff0eB\u3002C\uff61D\u200b\u00ad\ufeffE"), "a.b.c.de")


class TheList(unittest.TestCase):
    def test_the_shipped_list_is_valid_and_covers_the_audit_sites(self):
        with open(os.path.join(os.path.dirname(__file__), "..", "..", "config", "sensitive_sites.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        listed = {d for group in data["domains"].values() for d in group}
        for d in ("icicibank.com", "chase.com", "onlinesbi.sbi", "hdfcbank.com", "sbi.co.in", "paytm.com", "phonepe.com",
                  "zerodha.com", "coinbase.com", "paypal.com", "bankofamerica.com"):
            self.assertIn(d, listed)
        self.assertEqual(len(listed), len({d.lower() for d in listed}))

    def test_the_file_only_adds_to_the_built_in_floor(self):
        load_sensitive_sites, sensitive_target = policy.load_sensitive_sites, policy.sensitive_target
        with tempfile.TemporaryDirectory() as tmp:
            missing = load_sensitive_sites(os.path.join(tmp, "nope.json"))
            broken = os.path.join(tmp, "broken.json")
            open(broken, "w").write("{not json")
            for sites in (missing, load_sensitive_sites(broken)):
                self.assertTrue(sensitive_target(["https://www.chase.com/"], sites))
                self.assertTrue(sensitive_target(["https://www.icicibank.com/"], sites))
            extra = os.path.join(tmp, "extra.json")
            json.dump({"domains": {"mine": ["acme-pay.example"]}, "app_names": ["Acme Pay"], "label_affix": [], "label_exact": []},
                      open(extra, "w"))
            sites = load_sensitive_sites(extra)
            self.assertTrue(sensitive_target(["https://app.acme-pay.example/"], sites))
            self.assertTrue(sensitive_target(["Acme  Pay - Window"], sites))
            self.assertTrue(sensitive_target(["https://www.chase.com/"], sites))               # floor kept
            self.assertFalse(sensitive_target(["https://acme.example/"], sites))

    def test_changing_the_list_is_console_only(self):
        for path in ("jarvis/config/sensitive_sites.json", "D:/jarvis/config/sensitive_sites.json"):
            self.assertGreaterEqual(decide(Action("fs", "write", path)).level, RED)


class NodeOpener(unittest.TestCase):
    """The laptop's pc_open checks the same list before it launches anything (nothing really opens here)."""
    APPS = [("Kite by Zerodha", "Zerodha.Kite"), ("PhonePe", "PhonePe.App"), ("YONO SBI", "SBI.YONO"),
            ("Spotify", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")]

    def test_financial_apps_are_never_launched(self):
        from jarvis_node import pcopen
        launched = []
        for target in ("kite", "phonepe", "yono", "zerodha"):
            with self.subTest(target=target):
                r = pcopen.pc_open({"op": "open_app", "target": target}, apps=self.APPS, launcher=launched.append, opener=launched.append)
                self.assertFalse(r["ok"]); self.assertIn("refused", r["error"])
        self.assertEqual(launched, [])
        self.assertTrue(pcopen.pc_open({"op": "open_app", "target": "spotify"}, apps=self.APPS, launcher=launched.append,
                                      opener=launched.append)["ok"])


if __name__ == "__main__":
    unittest.main()

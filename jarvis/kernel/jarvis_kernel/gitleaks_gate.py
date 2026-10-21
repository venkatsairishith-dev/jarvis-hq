"""When the gitleaks subprocess can be skipped without changing what is detected (audit J-018, 2026-10-18).

Why: the deep scrub starts `gitleaks stdin` for every new text, and starting it costs about 0.5 s whatever the text is (measured
on the Linux test image: 447-524 ms for a 20-character text; compiling its ~220 rules is the cost). That alone is the "500 ms"
of the proxy relay (bench J-018): a one-word request took 505 ms.

What makes a skip safe: gitleaks runs a rule on a text only when one of that rule's `keywords` occurs in the text, compared in
lower case (its own pre-filter, detect.go), and it decodes base64, hex, percent-encoded and \\u-escaped parts of the text and
runs the same filter over the decoded text (--max-decode-depth, default 5). So for the default rules, a text that

  * is plain ASCII (lower-casing then means the same in Python and Go),
  * contains none of the default rules' keywords (the table below, taken from the gitleaks build it is pinned to), and
  * contains nothing that gitleaks could decode into a keyword (any `%`, `\\`, `&#`, `U+`, or a run of 12 or more characters of
    the base64 alphabet; its own regex needs 16),

cannot produce a finding, so scanning it is a no-op. Everything else is scanned exactly as before.

Fail closed: the skip applies only when ALL of these hold, and any doubt means "scan":
  * the gitleaks on this machine reports exactly VERSION (the table belongs to that build; another version scans everything),
  * no config override is in play (GITLEAKS_CONFIG, GITLEAKS_CONFIG_TOML or a .gitleaks.toml in the working directory),
  * every default rule that has a content pattern has keywords (checked when the table is made: KEYWORDLESS is empty),
  * the check itself raised nothing.

Regenerating after a gitleaks upgrade: `python -m jarvis_kernel.gitleaks_gate <path to the gitleaks binary>` prints a new
KEYWORDS block; tests/test_scrub.py::GateMatchesTheBinary fails until the table and VERSION match the binary the lock pins.
"""
import os
import re
import subprocess
import sys

VERSION = "8.30.1"
KEYWORDLESS = ()        # default rules with a content pattern but no keywords: none. One would have to be scanned for every text.

# The keywords of every default gitleaks rule, lower case (gitleaks 8.30.1: 222 rules). Generated from the binary, not typed.
KEYWORDS = (
    '-----begin', '4b1d', '<add key=', '_gitlab_session=', '_mmk', '_pub', 'a3-', 'a3t', 'abia', 'absk', 'acca', 'access',
    'adafruit', 'administrator_login_password', 'adobe', 'age-secret-key-1', 'airtable', 'aiza', 'akcp', 'akia', 'algolia',
    'alibaba', 'api', 'api-', 'api_org_', 'artifactory', 'asana', 'asia', 'atatt3', 'atlassian', 'atlasv1', 'auth', 'authress_',
    'beamer', 'bedrock-api-key-', 'bintray', 'bitbucket', 'bittrex', 'bundle_enterprise__contribsys__com',
    'bundle_gems__contribsys__com', 'clojars_', 'cloudflare', 'cmvmd', 'co_api_key', 'codecov', 'cohere', 'coinbase',
    'confluence', 'confluent', 'contentful', 'credential', 'creds', 'curl', 'dapi', 'datadog', 'discord', 'dnkey', 'doo_v1_',
    'dop_v1_', 'dor_v1_', 'dp.pt.', 'droneci', 'dropbox', 'dt0c01.', 'duffel_', 'eaaa', 'eaac', 'eaam',
    'enterprise.contribsys.com', 'etsy', 'ext_', 'ey', 'eyjrijoi', 'ezak', 'eztk', 'facebook', 'fastly', 'finicity', 'finnhub',
    'fio-u-', 'flickr', 'flwpubk_test', 'flwseck_test', 'fm1', 'fm2_', 'fo1_', 'freshbooks', 'gems.contribsys.com', 'gho_',
    'ghp_', 'ghr_', 'ghs_', 'ghu_', 'github_pat_', 'gitter', 'glagent-', 'glc_', 'glcbt-', 'gldt-', 'glffct-', 'glft-',
    'glimt-', 'gloas-', 'glpat-', 'glptt-', 'glrt-', 'glsa_', 'glsoat-', 'gocardless', 'gr1348941', 'heroku', 'hf_',
    'hooks.slack.com', 'hrku-aa', 'hubspot', 'hvb.', 'hvs.', 'ico-', 'incomingwebhook', 'intercom', 'intra', 'jfrog', 'jira',
    'key', 'kraken', 'kucoin', 'launchdarkly', 'lin_api_', 'linear', 'linked-in', 'linked_in', 'linkedin', 'live_', 'live_pub',
    'looker', 'ltai', 'mailchimp', 'mailgun', 'mapbox', 'mattermost', 'meraki', 'message-bird', 'message_bird', 'messagebird',
    'netlify', 'new-relic', 'new-york-times', 'new_relic', 'newrelic', 'newyorktimes', 'npm_', 'nrak', 'nrii-', 'nrjs-', 'ntn_',
    'nytimes', 'okta', 'ops_', 'p8e-', 'passwd', 'password', 'pat.', 'plaid', 'pmak-', 'pnu_', 'pplx-', 'private-ai',
    'private_ai', 'privateai', 'pscale_oauth_', 'pscale_pw_', 'pscale_tkn_', 'pul-', 'pypi-ageichlwas5vcmc', 'q~', 'rapidapi',
    'rdme_', 'rk_live', 'rk_prod', 'rk_test', 'rubygems_', 's-s4t2af-', 's-s4t2ud-', 's.', 'sat.', 'sc_', 'scauth_', 'secret',
    'secret_key', 'sendbird', 'sentry', 'sg.', 'sgp_', 'sha256~', 'shippo_', 'shpat_', 'shpca_', 'shppa_', 'shpss_', 'sk',
    'sk-ant-admin01', 'sk-ant-api03', 'sk_live', 'sk_prod', 'sk_test', 'sm_aat', 'sm_pat', 'sm_sat', 'sntrys_eyjpyxqio',
    'sntryu_', 'snyk', 'sonar', 'sourcegraph', 'sq0atp-', 'squarespace', 'sumo', 't3blbkfj', 'telegr', 'test_', 'test_pub',
    'tfp_', 'tk-us-', 'token', 'travis', 'twitch', 'twitter', 'v1.0-', 'webhook.office.com', 'webhookb2', 'xapp', 'xkeysib-',
    'xoxa', 'xoxb', 'xoxe-', 'xoxe.xoxb-', 'xoxe.xoxp-', 'xoxo', 'xoxp-', 'xoxr', 'xoxs', 'xray', 'yandex', 'zendesk', 'zxlk'
)

_ENCODED = re.compile(r"[%\\]|&#|u\+|[A-Za-z0-9+/_=-]{12}", re.I)
_VERSION = {}           # gitleaks path -> its reported version (or None when it could not be read)


def gitleaks_version(exe: str):
    """The version the binary reports, once per path; None when it cannot be read."""
    if exe not in _VERSION:
        try:
            r = subprocess.run([exe, "version"], capture_output=True, text=True, timeout=15,
                               creationflags=0x08000000 if os.name == "nt" else 0)
            _VERSION[exe] = r.stdout.strip().lstrip("v") or None if r.returncode == 0 else None
        except Exception:
            _VERSION[exe] = None
    return _VERSION[exe]


def config_overridden() -> bool:
    """gitleaks reads its config from -c, then GITLEAKS_CONFIG, then GITLEAKS_CONFIG_TOML, then (target)/.gitleaks.toml; the
    kernel passes no -c, so the last three can change the rules."""
    return bool(os.environ.get("GITLEAKS_CONFIG") or os.environ.get("GITLEAKS_CONFIG_TOML")
                or os.path.exists(".gitleaks.toml") or os.path.exists("gitleaks.toml"))


def text_may_have_findings(text: str) -> bool:
    """Pure part of the decision: can the default rules possibly match this text? True means "run gitleaks"."""
    if KEYWORDLESS or not text.isascii():
        return True
    if _ENCODED.search(text):
        return True
    low = text.lower()
    return any(k in low for k in KEYWORDS)


def must_scan(text: str, exe: str) -> bool:
    """True when gitleaks has to be run on this text; False only when it provably finds nothing (see the module doc)."""
    try:
        if gitleaks_version(exe) != VERSION or config_overridden():
            return True
        return text_may_have_findings(text)
    except Exception:
        return True


def extract_keywords(binary_path: str):
    """(version-independent) the sorted lower-case keywords of the default rules embedded in a gitleaks binary, and the ids of the
    rules that have a content pattern but no keywords. Used by the test that keeps KEYWORDS honest, and to regenerate it."""
    import tomllib
    with open(binary_path, "rb") as fh:
        data = fh.read()
    first = data.find(b"[[rules]]")
    start = data.rfind(b'title = "gitleaks config"', 0, first)
    if first < 0 or start < 0:
        raise ValueError("no embedded default config found in that binary")
    end = first
    while end < len(data) and (9 <= data[end] < 127 or data[end] in (10, 13) or data[end] >= 128):
        end += 1
    rules = tomllib.loads(data[start:end].decode("utf-8"))["rules"]
    words = sorted({k.lower() for r in rules for k in r.get("keywords", [])})
    keywordless = [r["id"] for r in rules if r.get("regex") and not r.get("keywords")]
    return words, keywordless, len(rules)


if __name__ == "__main__":
    import textwrap
    words, keywordless, n = extract_keywords(sys.argv[1])
    print(f"# {n} rules; keywordless content rules: {keywordless}")
    print(textwrap.fill(", ".join(repr(w) for w in words), width=128, initial_indent="    ", subsequent_indent="    ",
                        break_on_hyphens=False, break_long_words=False))

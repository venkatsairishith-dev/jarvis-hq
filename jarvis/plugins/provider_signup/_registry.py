"""The free model-API providers JARVIS may sign up to (owner's list, D40). Facts checked 2026-10-24 from the providers' pages and
three comparison sites; free tiers change, so `checked` is a date to re-check, not a promise."""
from __future__ import annotations

# mail_domains: the sender domains whose verification mail is accepted. phone/card: what the signup is reported to ask for
# (the owner is asked for either; JARVIS never fills them). training: whether the free tier is reported to train on prompts.
PROVIDERS: dict[str, dict] = {
    "groq": {
        "name": "Groq", "signup": "https://console.groq.com/login", "keys": "https://console.groq.com/keys",
        "key_file": "providers/groq.key", "base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b",
        "limits": "30 requests/min, 1,000/day", "mail_domains": ["groq.com"], "phone": False, "card": False, "training": False,
        "checked": "2026-10-24",
    },
    "cloudflare": {
        "name": "Cloudflare Workers AI", "signup": "https://dash.cloudflare.com/sign-up", "keys": "https://dash.cloudflare.com/profile/api-tokens",
        "key_file": "providers/cloudflare.key", "base_url": "https://api.cloudflare.com/client/v4/accounts/ACCOUNT_ID/ai/v1",
        "model": "@cf/openai/gpt-oss-120b", "limits": "10,000 neurons/day", "mail_domains": ["cloudflare.com"], "phone": False, "card": False,
        "training": False, "checked": "2026-10-24",
        "note": "Also needs the account id in base_url (a dashboard value, not secret); the token needs the 'Workers AI' permission only.",
    },
    "zai": {
        "name": "Z AI (Zhipu)", "signup": "https://z.ai", "keys": "https://z.ai/manage-apikey/apikey-list",
        "key_file": "providers/zai.key", "base_url": "https://api.z.ai/api/paas/v4", "model": "glm-4.7-flash",
        "limits": "1 concurrent request", "mail_domains": ["z.ai", "zhipuai.cn", "bigmodel.cn"], "phone": None, "card": False,
        "training": None, "checked": "2026-10-24", "note": "Terms and the international signup were not confirmed; read them before accepting.",
    },
    "nvidia": {
        "name": "NVIDIA NIM", "signup": "https://build.nvidia.com", "keys": "https://build.nvidia.com/settings/api-keys",
        "key_file": "providers/nvidia.key", "base_url": "https://integrate.api.nvidia.com/v1", "model": "",
        "limits": "40 requests/min", "mail_domains": ["nvidia.com"], "phone": True, "card": False, "training": True,
        "checked": "2026-10-24", "note": "Requests are logged and used to improve NVIDIA products; testing use only. Not for private content.",
    },
}


def steps(provider: str) -> list[dict]:
    """The signup in order. kind 'jarvis' = the agent does it; 'owner' = JARVIS stops and asks the owner, never tries to pass it."""
    p = PROVIDERS[provider]
    out = [
        {"kind": "jarvis", "do": f"Open {p['signup']} on the Agent PC and sign up with the JARVIS Gmail address (one account, the owner's own use)."},
        {"kind": "owner", "do": "If the page shows a CAPTCHA or 'are you human' check, stop and ask the owner to solve it (take-control)."},
        {"kind": "jarvis", "do": "Ask the verification entry for the code or link (it reads only mail from " + ", ".join(p["mail_domains"]) + "), and use it."},
    ]
    if p.get("phone") is not False:
        out.append({"kind": "owner", "do": "If a phone number or SMS code is asked for, stop and ask the owner."})
    out += [
        {"kind": "owner", "do": "If a card, payment, identity or real-name field appears, stop: the owner decides. Never fill one."},
        {"kind": "owner", "do": "If terms of service must be accepted, show the owner the link and wait for a yes."},
        {"kind": "jarvis", "do": f"Open {p['keys']}, create ONE key with the smallest permission, and send it from the page straight to "
                                  f"D:\\jarvis-secrets\\{p['key_file'].replace('/', chr(92))} (clipboard to file, screenshots off for that step). Do not read, show or paste the key."},
        {"kind": "owner", "do": "If the key cannot be saved that way, ask the owner to save it to that file himself."},
        {"kind": "jarvis", "do": "Report 'key saved' (file exists, size only). The provider stays off in providers.json until the comparison run and the owner's yes."},
    ]
    return out

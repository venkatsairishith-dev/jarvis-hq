import json, sys, os, tempfile
sys.path.insert(0, "/src")
os.environ["NEKO_STORAGE_SELECTED_ROOT"] = tempfile.mkdtemp()
from plugin.plugins.desktop import decide as D
profiles = [{"profile": "Default", "name": "Venkat", "account": "v***@x", "default": True, "say": ["default", "venkat"]},
            {"profile": "Profile 1", "name": "VenkatAMV", "account": "r***@x", "default": False, "say": ["venkatamv"]}]
req = "open youtube in my VenkatAMV account, open notepad, type hi into notepad and minimize calculator"
model = {"steps": [{"tool": "open_website", "site": "youtube", "account": "VenkatAMV"}, {"tool": "open_app", "app": "notepad"},
                   {"tool": "type_text", "app": "notepad", "text": "hi"}, {"tool": "window", "action": "minimize", "app": "calculator"}]}
p = D.validate(json.dumps(model), profiles, req)
print("MAX_STEPS =", D.MAX_STEPS, "| plan status:", p.status, "| steps:", len(p.steps), [s["tool"] for s in p.steps])

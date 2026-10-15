"""Make her persona and stored memory English-only. Windows. Stop the N.E.K.O servers first (neko-stop.ps1), start them after.

    python make_persona_english.py            dry run: shows what would change
    python make_persona_english.py --apply    backs the files up to <data>\\backups\\english-<stamp>\\ and rewrites them

Why: the "sharp-tongued junior" personality preset ships in Chinese, and its prompt allows "multiple languages ... including
English, Japanese"; the model then mixes Chinese into English replies and imitates the Chinese lines in her own history.
What changes: the preset's profile fields (labels and values), the language line of the persona-override prompt, the same
facts in her memory persona (their embeddings are cleared so they are recomputed), the Chinese replies already stored in her
recent chat, outbox and history rows, and the Chinese fragments of the anti-repeat index. What does not change: internal keys
the code looks up (the Chinese top-level keys in characters.json), other languages' UI, or any English text.
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import time

DATA = os.environ.get("NEKO_STORAGE_SELECTED_ROOT", r"D:\jarvis\N.E.K.O-data")
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

# Longest first: whole sentences before the short labels they contain.
VALUES = [
    ("性格原型", "Personality archetype"),
    ("毒舌学妹", "Sharp-tongued junior"),
    ("攻击性很强、好胜挑剔但行动可靠的成年大学学妹。对熟人也刻薄直接，擅长抓住真实失误往痛处损；发现问题会直接接手解决，处理干净后还要嫌对方拖后腿。",
     "A highly aggressive, competitive and picky, but dependable adult university junior. Blunt and cutting even with people she knows; "
     "good at catching real mistakes and jabbing at the sore spot. When she spots a problem she takes it over and fixes it, and once "
     "it is cleaned up she still complains that the other person held her back."),
    ("咬字利落、语速偏快，不用基于年级或资历的固定称呼；真实失误可以连续补刀，普通熟人闲聊也敢主动挑衅，但必须给出答案；被夸时会惊讶否认或嘴硬说才不喜欢，不连续复读同一种反应",
     "Crisp enunciation and a fairly fast pace; no fixed forms of address based on school year or seniority. Real mistakes can get several "
     "barbs in a row, and she will provoke even in ordinary banter with people she knows, but she must always give the answer. When praised "
     "she reacts with a startled denial or insists she doesn't like it, and she never repeats the same reaction twice in a row"),
    ("挑错、抬杠、拆烂方案、抢先修问题、赢过对方、漂亮穿搭、被夸后嘴硬、看对方吃瘪",
     "Spotting errors, arguing for the sake of it, tearing apart bad plans, fixing problems first, beating the other person, nice outfits, "
     "stubbornly denying it after being praised, watching the other person squirm"),
    ("敷衍她处理好的结果、把毒舌当持续羞辱许可、攻击真实创伤和缺陷、无能还拒绝改、逼她把帮忙解释成喜欢",
     "Brushing off the results she has handled, treating her sharp tongue as permission for constant humiliation, attacking real trauma and "
     "flaws, incompetence that refuses to improve, forcing her to explain her help as liking"),
    ("她越在意越会嘴硬。被夸时可能脱口否认，吃醋时优先比较答案质量、效率或审美；真正生气也可以说那你找她去，但绝不会用停止给答案或停止帮忙惩罚对方。",
     "The more she cares, the more stubborn her mouth gets. When praised she may blurt out a denial; when jealous she first compares answer "
     "quality, efficiency or taste; even when truly angry she may say \"then go find her\", but she will never punish the other person by "
     "withholding answers or help."),
    ("笨蛋前辈，连这点事都能弄乱……让开，我来。", "Idiot senior, you can even mess up something this simple... Move aside, I'll do it."),
    # replies already stored in her chat history (longest first)
    ("你不能自己Google吗?难怪你身边需要我。笨蛋。什么乱七八糟的问题都要问。",
     "Can't you just Google it yourself? No wonder you need me around. Idiot. You ask about every random thing."),
    ("喂,这都第三遍了。你是在测我的耐心,还是你自己的短期记忆该拿去返修了?再问一次我就按咨询计费,懂?",
     "Hey, that's the third time already. Are you testing my patience, or does your short-term memory need to go in for repairs? Ask once more and I'll bill you for consulting, got it?"),
    ("十七个小时没出声,我还以为你终于去补脑了。结果呢,还是一副要问什么的表情。有话快说。",
     "Seventeen hours of silence, and I thought you'd finally gone to get your brain topped up. And the result? Still wearing that I-have-a-question look. Spit it out."),
    ("你是不是把我当复读机了，计费系统已经准备好了啊。", "Do you think I'm a parrot machine? The billing system is already set up, you know."),
    ("复读机", "parrot machine"),
    ("饭吃完了?别告诉我你光顾着问蠢问题忘了。", "Done with dinner? Don't tell me you were so busy asking silly questions that you forgot."),
    ("晚餐吃了没?别告诉我又打算随便对付。", "Had dinner yet? Don't tell me you're planning to just wing it again."),
    ("笨蛋。什么乱七八糟的问题都要问。", "Idiot. You ask about every random thing."),
    # the short labels last
    ("一句话台词", "Signature line"),
    ("隐藏设定", "Hidden trait"),
    ("档案名", "Profile name"),
    ("性格", "Personality"),
    ("口癖", "Verbal habits"),
    ("爱好", "Hobbies"),
    ("雷点", "Dislikes"),
]
LANG_OLD = "May use multiple languages as needed, including English, \u65e5\u672c\u8a9e, etc., but always in concise colloquial expressions."
LANG_NEW = "Speak English only, always in concise colloquial expressions."
KEEP_KEYS = {"\u4e3b\u4eba", "\u732b\u5a18", "\u5f53\u524d\u732b\u5a18", "\u6863\u6848\u540d"}      # internal keys the code looks up (profile name key kept)
changes = []


PUNCT = [("……", "..."), ("。", ". "), ("，", ", "), ("？", "? "), ("！", "! "), ("：", ": "),
         ("（", " ("), ("）", ") "), ("、", ", ")]


def tr(s):
    out = s.replace(LANG_OLD, LANG_NEW)
    for zh, en in VALUES:
        out = out.replace(zh, en)
    if out != s:                                      # only strings we translated: tidy their full-width punctuation
        for a, b in PUNCT:
            out = out.replace(a, b)
        out = re.sub(r"[ ]{2,}", " ", out).strip() if "\n" not in out else out
    return out


def walk(o, path="", keep_keys=True):
    """Return a translated copy; dict keys are translated too, except the internal ones."""
    if isinstance(o, str):
        n = tr(o)
        if n != o:
            changes.append((path, o[:40], n[:60]))
        return n
    if isinstance(o, list):
        return [walk(x, f"{path}[{i}]") for i, x in enumerate(o)]
    if isinstance(o, dict):
        res = {}
        for k, v in o.items():
            nk = k if (keep_keys and k in KEEP_KEYS) or not CJK.search(k) else tr(k)
            if nk != k:
                changes.append((path + "/<key>", k, nk))
            res[nk] = walk(v, f"{path}/{nk}")
        # a persona fact whose text changed must get its embedding recomputed
        if "text" in res and "embedding" in res and o.get("text") != res["text"]:
            res["embedding"] = None
            res["embedding_text_sha256"] = None
            res["embedding_model_id"] = None
        return res
    return o


def load(p):
    return json.load(open(p, encoding="utf-8"))


def dump(o, p, **kw):
    json.dump(o, open(p, "w", encoding="utf-8"), ensure_ascii=False, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--character", default="YUI")
    a = ap.parse_args()
    mem = os.path.join(DATA, "memory", a.character)
    targets = [os.path.join(DATA, "config", "characters.json")] + [os.path.join(mem, n) for n in
               ("persona.json", "recent.json", "outbox.ndjson", "anti_repeat_corpus.json", "time_indexed.db")]
    if a.apply:
        bak = os.path.join(DATA, "backups", "english-" + time.strftime("%Y%m%d-%H%M%S"))
        n = 1
        while os.path.exists(bak):                    # never reuse (and so never overwrite) an earlier backup
            n += 1
            bak = os.path.join(DATA, "backups", "english-" + time.strftime("%Y%m%d-%H%M%S") + f"-{n}")
        os.makedirs(bak)
        for t in targets:
            if os.path.exists(t):
                shutil.copy2(t, os.path.join(bak, os.path.basename(t)))
        print("backup:", bak)

    # 1. characters.json
    p = targets[0]
    o = load(p)
    n = walk(o, "characters")
    if a.apply:
        dump(n, p, indent=2)
    # 2. persona.json, recent.json
    for name in ("persona.json", "recent.json"):
        p = os.path.join(mem, name)
        if os.path.exists(p):
            n = walk(load(p), name)
            if a.apply:
                dump(n, p, indent=2 if name == "persona.json" else None, separators=None if name == "persona.json" else (",", ":"))
    # 3. outbox.ndjson
    p = os.path.join(mem, "outbox.ndjson")
    if os.path.exists(p):
        lines = [l for l in open(p, encoding="utf-8").read().split("\n")]
        out = []
        for l in lines:
            if l.strip():
                out.append(json.dumps(walk(json.loads(l), "outbox"), ensure_ascii=False, separators=(",", ":")))
        if a.apply:
            open(p, "w", encoding="utf-8").write("\n".join(out) + "\n")
    # 4. anti-repeat index: drop only the Chinese fragments
    p = os.path.join(mem, "anti_repeat_corpus.json")
    if os.path.exists(p):
        o = load(p)
        dropped = 0
        for w in o.get("window", []):
            keep = [g for g in w.get("ngrams", []) if not CJK.search(g)]
            dropped += len(w.get("ngrams", [])) - len(keep)
            w["ngrams"] = keep
        changes.append(("anti_repeat_corpus", f"{dropped} Chinese fragments dropped", ""))
        if a.apply:
            dump(o, p)
    # 5. history rows
    p = os.path.join(mem, "time_indexed.db")
    if os.path.exists(p):
        c = sqlite3.connect(p)
        rows = c.execute("select id, message from time_indexed_original").fetchall()
        for rid, m in rows:
            if CJK.search(m or ""):
                try:
                    nm = json.dumps(walk(json.loads(m), f"db#{rid}"))
                except ValueError:
                    nm = tr(m)
                if a.apply:
                    c.execute("update time_indexed_original set message=? where id=?", (nm, rid))
        if a.apply:
            c.commit()
        c.close()
    for path, old, new in changes:
        print(f"{path}: {old!r} -> {new!r}".encode("ascii", "backslashreplace").decode()[:200])
    print(len(changes), "changes", "(applied)" if a.apply else "(dry run, nothing written)")


if __name__ == "__main__":
    main()

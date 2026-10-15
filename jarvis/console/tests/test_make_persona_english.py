"""The English conversion of her persona and stored memory (temporary data folder, nothing real touched)."""
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "..", "scripts", "make_persona_english.py")
CJK = re.compile(r"[㐀-䶿一-鿿]")


def load(data):
    os.environ["NEKO_STORAGE_SELECTED_ROOT"] = data
    spec = importlib.util.spec_from_file_location("make_persona_english_t", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Conversion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = self.tmp.name
        os.makedirs(os.path.join(self.data, "config"))
        self.mem = os.path.join(self.data, "memory", "YUI")
        os.makedirs(self.mem)
        self.m = load(self.data)
        label, value = "性格原型", "毒舌学妹"
        chars = {"主人": {"档案名": "Boss"}, "猫娘": {"YUI": {"Age": "15", "_reserved": {"persona_override": {
            "prompt_guidance": "- Language: " + self.m.LANG_OLD + " Keep it brief.", "profile": {label: value}}}}},
            "当前猫娘": "YUI"}
        json.dump(chars, open(os.path.join(self.data, "config", "characters.json"), "w", encoding="utf-8"), ensure_ascii=False)
        persona = {"neko": {"facts": [{"id": "f1", "text": label + ": " + value, "embedding": [0.1], "embedding_text_sha256": "x",
                                       "embedding_model_id": "m"}, {"id": "f2", "text": "Already English", "embedding": [0.2],
                                                                    "embedding_text_sha256": "y", "embedding_model_id": "m"}]}}
        json.dump(persona, open(os.path.join(self.mem, "persona.json"), "w", encoding="utf-8"), ensure_ascii=False)
        reply = "Paris. 晚餐吃了没?别告诉我又打算随便对付。"
        json.dump([{"type": "ai", "data": {"content": [{"type": "text", "text": reply}]}}],
                  open(os.path.join(self.mem, "recent.json"), "w", encoding="utf-8"), ensure_ascii=False)
        open(os.path.join(self.mem, "outbox.ndjson"), "w", encoding="utf-8").write(json.dumps({"payload": {"t": reply}}, ensure_ascii=False) + "\n")
        json.dump({"version": 1, "window": [{"ts": 1, "ngrams": ["the", "晚餐吃", "dinner"]}]},
                  open(os.path.join(self.mem, "anti_repeat_corpus.json"), "w", encoding="utf-8"), ensure_ascii=False)
        c = sqlite3.connect(os.path.join(self.mem, "time_indexed.db"))
        c.execute("create table time_indexed_original (id integer primary key, session_id text, message text, timestamp text)")
        c.execute("insert into time_indexed_original (message) values (?)", (json.dumps({"type": "ai", "data": {"content": [{"text": reply}]}}),))
        c.commit()
        c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, *args):
        old = sys.argv
        sys.argv = ["x", *args]
        try:
            self.m.changes.clear()
            self.m.main()
        finally:
            sys.argv = old

    def read(self, rel):
        return open(os.path.join(self.data, rel), encoding="utf-8").read()

    def test_dry_run_changes_nothing(self):
        before = self.read(os.path.join("memory", "YUI", "persona.json"))
        self.run_main()
        self.assertEqual(self.read(os.path.join("memory", "YUI", "persona.json")), before)
        self.assertFalse(os.path.exists(os.path.join(self.data, "backups")))

    def test_apply_translates_text_keeps_internal_keys_and_clears_stale_embeddings(self):
        self.run_main("--apply")
        chars = json.loads(self.read(os.path.join("config", "characters.json")))
        self.assertEqual(set(chars), {"主人", "猫娘", "当前猫娘"})      # internal keys untouched
        self.assertIn("档案名", chars["主人"])
        ov = chars["猫娘"]["YUI"]["_reserved"]["persona_override"]
        self.assertEqual(ov["profile"], {"Personality archetype": "Sharp-tongued junior"})
        self.assertIn("Speak English only", ov["prompt_guidance"])
        self.assertNotIn("multiple languages", ov["prompt_guidance"])
        facts = json.loads(self.read(os.path.join("memory", "YUI", "persona.json")))["neko"]["facts"]
        self.assertEqual(facts[0]["text"], "Personality archetype: Sharp-tongued junior")
        self.assertEqual((facts[0]["embedding"], facts[0]["embedding_text_sha256"], facts[0]["embedding_model_id"]), (None, None, None))
        self.assertEqual(facts[1]["embedding"], [0.2])                                     # an unchanged fact keeps its embedding
        recent = self.read(os.path.join("memory", "YUI", "recent.json"))
        self.assertFalse(CJK.search(recent))
        self.assertIn("Had dinner yet?", recent)
        self.assertNotIn("。", recent)                                                   # full-width punctuation tidied
        self.assertFalse(CJK.search(self.read(os.path.join("memory", "YUI", "outbox.ndjson"))))
        self.assertEqual(json.loads(self.read(os.path.join("memory", "YUI", "anti_repeat_corpus.json")))["window"][0]["ngrams"], ["the", "dinner"])
        conn = sqlite3.connect(os.path.join(self.mem, "time_indexed.db"))
        try:
            row = conn.execute("select message from time_indexed_original").fetchone()[0]
        finally:
            conn.close()
        self.assertFalse(CJK.search(row))

    def test_backup_holds_the_originals_and_is_never_overwritten(self):
        original = self.read(os.path.join("memory", "YUI", "persona.json"))
        self.run_main("--apply")
        self.run_main("--apply")                                                             # a second run in the same second
        dirs = sorted(os.listdir(os.path.join(self.data, "backups")))
        self.assertEqual(len(dirs), 2)
        first = open(os.path.join(self.data, "backups", dirs[0], "persona.json"), encoding="utf-8").read()
        self.assertEqual(first, original)                                                    # the first backup still has the originals

    def test_running_twice_gives_the_same_result(self):
        self.run_main("--apply")
        once = self.read(os.path.join("memory", "YUI", "persona.json"))
        self.run_main("--apply")
        self.assertEqual(self.read(os.path.join("memory", "YUI", "persona.json")), once)


if __name__ == "__main__":
    unittest.main()

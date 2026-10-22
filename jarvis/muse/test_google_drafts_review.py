"""Review additions: one address per entry (no smuggled recipients) and attendees are an explicit opt-in on event drafts."""
import pytest

from jarvis.muse import google_drafts as G


class Call:
    def __init__(self, out, log):
        self.out, self.log = out, log

    def execute(self):
        return self.out


class FakeEvents:
    def __init__(self):
        self.inserted = []

    def insert(self, **kw):
        self.inserted.append(kw)
        return Call({"id": "e1", "htmlLink": "https://calendar.example/e1"}, self.inserted)


class FakeService:
    def __init__(self):
        self._events = FakeEvents()

    def events(self):
        return self._events


@pytest.mark.parametrize("bad", ["a@b.com, evil@x.com", "a@b.com; evil@x.com", "Name <a@b.com>, <evil@x.com>", "a@b.com evil@x.com"])
def test_an_entry_with_more_than_one_address_is_refused_everywhere(bad):
    with pytest.raises(G.BadMessage):
        G.build_message([bad], "s", "b")
    with pytest.raises(G.BadMessage):
        G.build_message(["ok@x.com"], "s", "b", cc=[bad])
    with pytest.raises(G.BadEvent):
        G.build_event("meet", "2026-11-01T10:00:00+00:00", "2026-11-01T11:00:00+00:00", attendees=[bad])


def test_a_normal_display_name_address_still_works():
    raw = G.build_message(["Jane Doe <jane@example.com>"], "hello", "body")
    assert raw["raw"]


def test_event_drafts_with_attendees_need_an_explicit_opt_in_and_nothing_is_inserted_without_it():
    svc = FakeService()
    ev = G.build_event("meet", "2026-11-01T10:00:00+00:00", "2026-11-01T11:00:00+00:00", attendees=["a@b.com"])
    with pytest.raises(G.BadEvent, match="attendees"):
        G.save_event_draft(svc, ev)
    assert svc._events.inserted == []
    out = G.save_event_draft(svc, ev, allow_attendees=True)
    assert out["attendee_count"] == 1 and svc._events.inserted[0]["sendUpdates"] == "none"


def test_an_event_without_attendees_is_still_a_plain_draft():
    svc = FakeService()
    G.save_event_draft(svc, G.build_event("solo", "2026-11-01T10:00:00+00:00", "2026-11-01T11:00:00+00:00"))
    assert len(svc._events.inserted) == 1

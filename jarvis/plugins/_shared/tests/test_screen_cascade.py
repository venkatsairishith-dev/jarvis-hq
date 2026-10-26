"""screen_cascade: UIA -> OCR -> small vision, with an agreement check between rungs (D59, lever d). Fakes only."""
from jarvis.plugins._shared import screen_cascade as C
from jarvis.plugins._shared.screen_cascade import Hit

INVOICE = "Invoice 2026-0522 Acme Supplies Ltd Total due 1,240.50 USD payable within 30 days"
INVOICE_OCR = "Invoice 2026 0522 Acme Supplies Ltd Total due 1240.50 USD payable within 30 days"
OTHER = "Totally different words about a holiday in the mountains with friends"


def calls(*texts_by_rung):
    log = []

    def make(name, value):
        def fn(*a):
            log.append(name)
            if isinstance(value, Exception):
                raise value
            return value
        return fn
    return log, {n: make(n, v) for n, v in texts_by_rung}


def test_agreement_is_containment_of_the_shorter_text():
    assert C.agreement(INVOICE, INVOICE_OCR) > 0.8
    assert C.agreement(INVOICE, OTHER) < 0.2
    assert C.agreement("", INVOICE) == 0.0


def test_uia_and_ocr_agree_gives_high_confidence_and_skips_vision():
    log, r = calls((C.UIA, INVOICE), (C.OCR, INVOICE_OCR), (C.VISION, OTHER))
    out = C.read(r)
    assert out.confidence == C.CONF_AGREE and out.source == C.UIA and out.agreed_with == C.OCR and not out.conflict
    assert log == [C.UIA, C.OCR]                                   # the third rung is only a tie-breaker


def test_uia_alone_when_no_cross_check_is_wanted():
    log, r = calls((C.UIA, INVOICE), (C.OCR, INVOICE_OCR))
    out = C.read(r, cross_check=False)
    assert out.source == C.UIA and out.confidence == C.CONF_UIA and log == [C.UIA]


def test_empty_uia_falls_to_ocr_and_is_labelled_less_certain():
    log, r = calls((C.UIA, ""), (C.OCR, INVOICE_OCR))
    out = C.read(r, cross_check=False)
    assert out.source == C.OCR and out.confidence == C.CONF_OCR and log == [C.UIA, C.OCR]


def test_vision_breaks_a_tie_between_disagreeing_rungs():
    log, r = calls((C.UIA, OTHER), (C.OCR, INVOICE_OCR), (C.VISION, INVOICE))
    out = C.read(r)
    assert out.confidence == C.CONF_AGREE and {out.source, out.agreed_with} == {C.OCR, C.VISION} and out.text == INVOICE_OCR
    assert log == [C.UIA, C.OCR, C.VISION]


def test_three_different_answers_is_a_conflict_and_never_confident():
    _, r = calls((C.UIA, OTHER), (C.OCR, INVOICE_OCR), (C.VISION, "A picture of a cat sitting on a sofa in a living room"))
    out = C.read(r)
    assert out.conflict and out.confidence == C.CONF_CONFLICT and out.text == OTHER and not out.usable


def test_a_rung_that_raises_is_skipped_and_traced_by_class_only():
    _, r = calls((C.UIA, RuntimeError("secret window text")), (C.OCR, INVOICE_OCR))
    out = C.read(r, cross_check=False)
    assert out.source == C.OCR and out.trace[0].error == "RuntimeError" and "secret" not in repr(out.trace)


def test_nothing_readable_at_all():
    _, r = calls((C.UIA, ""), (C.OCR, "ab"), (C.VISION, None))
    out = C.read(r)
    assert out.text == "" and out.source == "" and out.confidence == 0.0 and not out.usable


def test_locate_two_rungs_agree_makes_a_pixel_click_safe():
    _, r = calls((C.UIA, Hit(500, 300, 80, 24, "Save")), (C.OCR, [Hit(505, 303, 60, 18, "Save")]))
    out = C.locate("Save", r)
    assert out.safe_to_click and out.confidence == C.CONF_AGREE and out.source == C.UIA and out.agreed_with == C.OCR


def test_locate_a_lone_ocr_hit_is_not_safe_to_click_but_a_lone_uia_hit_can_be_invoked():
    _, r = calls((C.OCR, [Hit(505, 303, 60, 18)]))
    only_ocr = C.locate("Save", r)
    assert only_ocr.hit and not only_ocr.safe_to_click and only_ocr.confidence == C.CONF_OCR
    _, r = calls((C.UIA, Hit(500, 300, 80, 24)))
    only_uia = C.locate("Save", r)
    assert only_uia.safe_to_click and only_uia.source == C.UIA


def test_locate_rungs_pointing_at_different_places_is_a_conflict():
    _, r = calls((C.UIA, Hit(100, 100, 80, 24)), (C.OCR, [Hit(900, 700, 60, 18)]), (C.VISION, Hit(400, 400)))
    out = C.locate("Save", r)
    assert out.conflict and not out.safe_to_click and out.confidence == C.CONF_CONFLICT


def test_locate_picks_the_ocr_hit_that_matches_uia_among_several():
    _, r = calls((C.UIA, Hit(500, 300, 80, 24)), (C.OCR, [Hit(50, 50, 40, 12), Hit(498, 301, 70, 20)]))
    out = C.locate("Save", r)
    assert out.safe_to_click and out.agreed_with == C.OCR


def test_locate_with_no_hits():
    _, r = calls((C.UIA, None), (C.OCR, []), (C.VISION, RuntimeError("x")))
    out = C.locate("Save", r)
    assert out.hit is None and out.confidence == 0.0 and not out.safe_to_click

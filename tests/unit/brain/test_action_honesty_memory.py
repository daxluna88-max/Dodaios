"""A reply must not claim a save that no memory tool made."""
import pytest

from dodaios.brain.action_honesty import memory_claim_language, memory_not_saved_phrase

ASK = "Ricorda che non ricordo"
NOOP = [("wiki-ingest", True, "NOT SAVED. Nothing was stored: the curator judged this ...")]


@pytest.mark.parametrize(
    ("reply", "results"),
    [
        ("Noted — I've saved that you want me to remember things for you.", []),
        ("Ho salvato questa informazione nel tuo profilo.", NOOP),
        ("Segnato, Davide: d'ora in poi memorizzo io le cose per te.", NOOP),
        ("Notato, Davide. La tua memoria è già nel mio profilo.", NOOP),
    ],
)
def test_a_false_save_claim_is_caught(reply, results):
    assert memory_claim_language(ASK, reply, results) == "it"


@pytest.mark.parametrize(
    ("request_text", "reply", "results"),
    [
        (ASK, "Non ho salvato nulla: non era abbastanza rilevante.", NOOP),
        ("Ricorda che il rosso è il mio colore", "Notato: rosso, salvato.", [("update_profile", True, "updated")]),
        (ASK, "Ho salvato la nota.", [("wiki-ingest", True, "Wiki ingest done:\n- applied: 1")]),
        ("Che ore sono?", "Sono le 12:34, notato che è venerdì.", []),
        ("Ricordami di chiamare Mario", "Segnato, ti ricordo domani.", [("set_reminder", True, "ok")]),
        ("Cosa ricordi di me?", "Nel tuo profilo ho scritto che ami il rosso.", []),
    ],
)
def test_legitimate_replies_are_left_alone(request_text, reply, results):
    assert memory_claim_language(request_text, reply, results) is None


def test_the_honest_phrase_follows_the_language_with_english_fallback():
    assert memory_not_saved_phrase("it").startswith("Non ho salvato nulla")
    assert memory_not_saved_phrase("en").startswith("I did not save")
    assert memory_not_saved_phrase("xx").startswith("I did not save")

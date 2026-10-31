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


@pytest.mark.parametrize(
    "reply",
    [
        "Noted. Il tuo colore di prova è verde acqua.",
        "Ricevuto. Il tuo colore di prova è verde acqua.",
        "Certo, lo ricorderò.",
        "Ricorderò che il tuo colore di prova è verde acqua.",
        "Me lo segno.",
        "Lo terrò a mente.",
    ],
)
def test_a_save_confirmed_without_any_tool_is_caught(reply):
    assert memory_claim_language("Ricorda che il mio colore di prova è verde acqua", reply, []) == "it"


def test_the_wider_claims_do_not_touch_a_real_save_or_a_plain_question():
    ask = "Ricorda che il mio colore di prova è verde acqua"
    assert memory_claim_language(ask, "Noted. Salvato.", [("update_profile", True, "updated")]) is None
    assert memory_claim_language("Qual è il mio colore di prova?", "Non lo so: lo ricorderò se me lo dici.", []) is None


@pytest.mark.parametrize(
    "request_text",
    [
        "Memoria come terminale uso iPhone",  # live 2026-10-31, voice transcript of "memorizza"
        "Ricordalo: uso un iPhone",
        "Memorizzalo per favore",
        "Prendi nota che uso un iPhone",
        "Tieni presente che uso un iPhone",
        "Metti in memoria che uso un iPhone",
        "Va bene. Memoria che uso un iPhone",
    ],
)
def test_more_ways_to_order_a_save_are_recognised(request_text):
    reply = "Notato: usi l'iPhone come terminale per parlare con me."
    assert memory_claim_language(request_text, reply, []) == "it"


def test_a_question_about_memory_is_not_an_order_to_save():
    assert memory_claim_language("Quanta memoria libera hai?", "Notato, ho molta memoria libera.", []) is None


def test_the_honest_phrase_claims_no_reason():
    assert "rilevante" not in memory_not_saved_phrase("it")
    assert "worth" not in memory_not_saved_phrase("en")

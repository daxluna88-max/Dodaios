"""Finite slash-command messages, separate from provider diagnostic text.

Only locally raised ControlError instances are translated at the command-result
boundary. Unknown/provider exceptions keep their original text for diagnosis.
Command identifiers, runner names and permission identifiers are never translated.
"""
from __future__ import annotations

_MESSAGES_IT: dict[str, str] = {
    "Session not found": "Sessione non trovata",
    "Commands are available in Dodaios and agent chats only": "I comandi sono disponibili soltanto nelle chat Dodaios e degli agenti",
    "Configure credentials in app settings, not in chat commands": "Configura le credenziali nelle impostazioni dell’app, non nei comandi della chat",
    "This runner cannot enforce read-only verification; choose an API model": "Questo esecutore non può garantire verifiche in sola lettura; scegli un modello API",
    "Stopped by the user": "Fermato dall’utente",
    "Goal cleared by the user": "Obiettivo rimosso dall’utente",
    "Leave plan mode with /build before starting a goal": "Esci dalla modalità piano con /build prima di avviare un obiettivo",
    "Goal must be at most 4000 characters": "L’obiettivo non può superare 4000 caratteri",
    "Replaced by a new goal": "Sostituito da un nuovo obiettivo",
    "Plan mode activated": "Modalità piano attivata",
    "No writable mode was selected before planning; update the agent permissions first": "Prima della pianificazione non era stata selezionata una modalità che consenta modifiche; aggiorna prima i permessi dell’agente",
    "Leave plan mode with /build before resuming a goal": "Esci dalla modalità piano con /build prima di riprendere un obiettivo",
    "The task that failed last came from Dodaios, a teammate or a routine, not from you, so /continue cannot resume it. Ask the agent yourself, or let the sender try again.": "L’ultima attività non riuscita proveniva da Dodaios, da un altro agente o da una routine, non da te: /continue non può riprenderla. Chiedilo direttamente all’agente oppure lascia che il mittente riprovi.",
    "There is no interrupted task to continue": "Non ci sono attività interrotte da riprendere",
    "Use /find followed by search text": "Usa /find seguito dal testo da cercare",
    "This command accepts text; send files as a separate chat message": "Questo comando accetta testo; invia i file in un messaggio separato",
    "Plan mode permits reads only; leave it with /build": "La modalità piano consente solo letture; esci con /build",
    "/{command} needs an argument": "/{command} richiede un argomento",
    "Unknown command": "Comando sconosciuto",
    "The chat is working; use /stop first": "La chat sta lavorando; usa prima /stop",
    "The agent chat is working; stop it before changing permissions": "La chat dell’agente sta lavorando; fermala prima di cambiare i permessi",
    "{runner} cannot provide an actionable approval for {permission}": "{runner} non può fornire un’approvazione utilizzabile per {permission}",
    "The agent's roster does not permit that chat mode": "La configurazione della squadra non consente questa modalità di chat",
    "Agent tools are still starting": "Gli strumenti degli agenti si stanno ancora avviando",
    "Use /message @Agent text (quote names containing spaces)": "Usa /message @Agente testo (racchiudi tra virgolette i nomi che contengono spazi)",
    "Command was interrupted": "Il comando è stato interrotto",
    "User is steering the task": "L’utente sta modificando la direzione dell’attività",
    "Message was not started": "Il messaggio non è stato avviato",
    "The previous native goal must be cleared before this chat can continue": "Occorre rimuovere l’obiettivo nativo precedente prima di continuare questa chat",
    "Attachments could not be read; they were kept for /continue": "Non è stato possibile leggere gli allegati; sono stati conservati per /continue",
    "Work was interrupted": "Il lavoro è stato interrotto",
    "No turn result": "Nessun risultato del turno",
    "No verified progress in three consecutive steps. ": "Nessun progresso verificato in tre passaggi consecutivi. ",
    "App is closing; use /continue to resume": "L’app si sta chiudendo; usa /continue per riprendere",
}


def control_message(message: str, locale: str = "en", **fields: object) -> str:
    """Translate a known local template, preserving unknown diagnostics."""
    template = _MESSAGES_IT.get(message, message) if locale == "it" else message
    return template.format(**fields) if fields else template


class ControlError(ValueError):
    """A local command error with an English diagnostic and localized display."""

    def __init__(self, message: str, **fields: object) -> None:
        self.message = message
        self.fields = fields
        super().__init__(control_message(message, **fields))

    def localized(self, locale: str) -> str:
        return control_message(self.message, locale, **self.fields)

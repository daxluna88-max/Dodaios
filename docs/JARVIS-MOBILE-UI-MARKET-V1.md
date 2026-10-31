# DAX JARVIS — UI mobile + Market, primo lotto GPT (31 ottobre 2026)

## Decisioni prodotto
Quattro aree: **Home · Chat · Il mio Jarvis · Market**. Chat mobile-first in italiano. Studio creatori avanzato per sviluppare app/skill. Market: offerte gratuite, poi prodotti a pagamento dei creatori, poi referral su un solo livello (attivazioni qualificate/acquisti, non download). Questo lotto NON implementa pagamenti, catalogo o referral.

## Implementazione isolata
- `lib/mobileDeviceAccess.ts`: contratto delle capacità del telefono, accesso esplicito e puntuale a foto/video/file, geolocalizzazione e stream audio. Nessun accesso attivo prima del tap; nessun upload.
- `components/mobile/MobileDeviceMenu.tsx`: pulsante **Accessori**, menu italiano, errori e callback di consegna verso la chat. Non è ancora collegato al composer esistente: evitare di rompere voce, allegati, permessi e STOP del pilota.
- Motion/sensori: capability dichiarabile, lettura continua NON implementata (serve un consenso e lifecycle dedicati).
- Fotocamera/video: browser file capture può aprire fotocamera o selettore secondo browser/OS, **non** garantisce acquisizione nativa né registrazione diretta in tutti i browser.
- IOS/Android: adapter nativi da progettare con pairing e permessi; la web app può usare soltanto le API concesse dal browser.

## Da fare, prima dell'integrazione
1. Integrare `MobileDeviceMenu` con l'attachment pipeline già esistente e i controlli approvazione/STOP, senza duplicare l'upload o la voce.
2. Associare la posizione a un messaggio visibile e confermato prima dell'invio a Jarvis; non chiamare provider cloud direttamente da questo modulo.
3. Collegare il microfono all'attuale voice transport con controllo stop/unmount/permission revoke; non creare due flussi audio.
4. Test fake di file cancellato, permesso negato, HTTPS, uscita/reload e logout. Test fisici iPhone e Android, log senza dati sensibili.
5. Prevedere package/manifest del Market e differenziare Consumer multi-modello e PMI locale/offline; nessuna condivisione memoria, dati o chiavi.

## Stato e limiti
Solo codice sorgente su ramo GPT separato. Non dichiarare app pronta, supporto sensori verificato o disponibilità negli store. Nessun merge, build, deploy, Actions o spesa autorizzati da questo lotto. Claude revisiona prima dell'integrazione.

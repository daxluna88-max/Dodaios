# DAX JARVIS — seconda app neurale (lotto 1)
Data: 2026-10-31. Ramo separato `gpt/second-app-neural-v1` da `claude/it-complete`. Non sostituire la chat esistente.

## Decisione prodotto
La seconda app è **voice-first**, distinta dall'app chat. Tre sezioni: Voce, Memoria neurale, Dashboard configurabile. Stile dark/neon approvato, italiano, mobile-first, desktop adattivo. La dashboard può crescere tramite moduli, senza accessi diretti non autorizzati.

## Implementazione consegnata
- `src/features/neural-app/NeuralApp.tsx`: componente isolato con props per voce, ricordi, widget e callback. Non legge contenuti di memoria o rete direttamente.
- `NeuralApp.css`: layout mobile-first, sfere, sinapsi e colori; `prefers-reduced-motion`.
- Nessun routing, deploy, build, provider live, merge, configurazione o spesa.
- La rete di sinapsi è una visualizzazione animata del dataset passato dall'host; **non** rappresenta firing neurale reale né prova che un'inferenza sia avvenuta.
- Il microfono è disabilitato se il motore non è stato collegato. STOP usa il callback dell'host, mai simulato come interruzione riuscita.
- La dashboard usa contenuto temporaneo in memoria React quando non riceve callback; non promette persistenza.
- Test TypeScript/Vitest e test dispositivi: **NON ESEGUITI**.

## Prossima integrazione, ordine di accettazione
1. **Revisione di Claude**: TypeScript/ESLint/accessibilità, visualizzazione animazioni e integrazione con routing senza duplicare UI esistente. Test tsc + Vitest senza provider reali.
2. **Voce**: collegare uno solo stream del motore vocale esistente; STOP interrompe anche playback e turn; indicatori stato e permesso; test browser/telefono.
3. **Memoria**: adapter read-only con lista categorie, eventi reali con provenance, filtri di privacy e consenso; aggiornamenti sinapsi solo su eventi reali, non inventare fatti. Memoria PMI rimane offline.
4. **Dashboard**: registro di moduli autorizzati, configurazione persistente con scope account/prodotto, layout riordinabile, aggiungi/rimuovi e permessi.
5. **Prodotti**: Consumer e PMI rimangono indipendenti; niente trasferimento automatico verso provider cloud.
6. **Qualità**: 320/375/393px, Safari iPhone, Mac, keyboard, reduced-motion, screen reader, ripristino e logout.

## Regole
Non usare le metriche dimostrative come dati dell'utente. Non avviare sensori per default. Nessun upload, permesso o inferenza alla sola apertura della schermata. Nessun deploy/merge/Actions/spese senza autorizzazione di Dax.

# Handoff — Pilota Jarvis su VPS Hostinger (28/10/2026)

Scopo: stato della sessione + **sequenza di comandi verificata**, da trasformare in script o in un bot che guida Dax passo passo (Dax lavora da iPhone con Termius).
Nessun segreto in questo file. Tutto ciò che non è marcato «verificato» NON lo è.

## 1. Regole di Dax
- Risposte laconiche in chat. Dire sempre cosa NON è verificato (simulato ≠ reale ≠ CI ≠ dal vivo).
- Merge/push solo con autorizzazione esplicita e specifica di Dax. Mai segreti in file, chat o repo.
- Codice, commenti e commit del repo Dodaios in **inglese** (AGENTS.md). Registro test obbligatorio per ogni controllo (issue Jarvis #16: ID, prodotto, OS, SHA, procedura, atteso, osservato, PASS/FAIL/BLOCKED/NOT RUN, evidenza senza segreti, costo).
- «Claudio» nell'issue #16 è il nome con cui GPT chiama Claude; non è un provider.

## 2. Stato a fine sessione
**Server (verificato dall'output mostrato a Dax):**
- Host `srv2104916.hstgr.cloud`, Hostinger, Ubuntu **26.04.1 LTS** (il piano diceva 24.04: la mail di Hostinger dice 26.04), kernel 7.0.0-42-generic dopo riavvio, 2 vCPU, 7,7 GiB RAM, disco 96 GB. Python 3.14.4, git 2.53, Node 22.22.1, npm 9.2.0.
- Utente `dax` con sudo; login SSH **solo con chiave ED25519** (`jarvis-pilot`, creata in Termius); `PasswordAuthentication no`, `PermitRootLogin no`, `KbdInteractiveAuthentication no` (drop-in `00-hardening.conf`); login root provato e rifiutato.
- `ufw` attivo: solo `22/tcp` (v4+v6) da ovunque. Nessun desktop remoto attivo (`xrdp` inactive; template XFCE non verificato).
- Dodaios installato da clone di `https://github.com/daxluna88-max/Dodaios.git` (`main`, commit `01cdb7258`), con `install/install.sh --headless --no-launch`. Versione `jarvis 4.1.2`. L'installer ha scaricato Python 3.13 via `uv` e il modello wake vosk (~40 MB).
- UI web costruita a mano (non era precostruita): `npm ci` + `NODE_OPTIONS=--max-old-space-size=5120 npm run build` (senza il limite: OOM di Node). 8 vulnerabilità npm segnalate: NON usare `npm audit fix --force`.
- Server avviato: `nohup jarvis serve` → `127.0.0.1:47821` (solo loopback), `/api/health` ok. UI raggiunta da iPhone con tunnel Termius (Local 47821 → 127.0.0.1:47821, bind 127.0.0.1, host `dax`), Safari su `http://localhost:47821` (carica; mostrava «UPDATING…»).

**Difetti/osservazioni da registrare (non risolti):**
1. Voice-ready watchdog scaduto dopo 45 s: pipeline vocale mai pronta (causa probabile: nessuna chiave per STT/TTS cloud; NON verificato).
2. `brain.primary='claude-api'` senza chiave: ripiega su `ollama`, non installato → Jarvis non può ancora rispondere.
3. Canale Telegram avviato senza che sia stato configurato da noi («Friends-Stack live: Telegram-Channel aktiv»): da capire e, se serve, disattivare.
4. Il login root dell'host Termius `root` resta in elenco con errore: va eliminato.

**Decisione aperta (ultimo messaggio):** provider del brain.
- Strada A: chiave OpenAI con tetto di spesa basso presso il provider (comando per inserirla nascosta in `~/.dodaios.env`, chmod 600; avvio con `set -a; . ~/.dodaios.env; set +a`).
- Strada B: abbonamento ChatGPT via login Codex (`npm i -g @openai/codex`, `codex login --device-auth`; il codice del repo ha `dodaios/live/subscription_auth.py`). Solo pilota personale, account di Dax; l'uso per clienti va verificato sui termini ufficiali (issue #16 D08: abbonamento ≠ credito API, niente scraping). Non verificato se la voce cloud richieda comunque una chiave API.
- Dax non ha ancora scelto.

**Lavoro su altri rami (non pubblicato, solo locale nel sandbox della sessione, effimero):**
- Dodaios `claude/cloud-all3` (da `claude/cloud-all2` `7a7de132`): correzioni K2–K8 della revisione GPT (ledger/broker/adapter, nft), erratum K1 in ADR-0067, runbook pilota. Test `tests/unit/cloud`: 101 passati. **Non pushato.** Il sandbox può essere perso: se serve il lavoro, chiedere a Dax l'autorizzazione al push.

## 3. Sequenza verificata (spec per script/bot)
Ogni passo: comando → atteso → se fallisce. Il bot deve chiedere la schermata/l'output prima di passare oltre e non andare avanti su FAIL.

**P0 – Acquisto (Dax, fuori terminale).** Piano KVM 2+, template Ubuntu con Remote Desktop (XFCE), 2FA sull'account. In Hostinger il campo «Aggiungi chiave SSH» si può saltare e fare dopo. Annotare IP/hostname dalla mail.

**P1 – Termius.** Host → hostname, Username `root`, password di root, Use SSH on, Mosh off, porta 22. Spunta grigia = manca l'hostname. Accettare la fingerprint.

**P2 – Inventario (sola lettura).**
```
lsb_release -d; uname -r; nproc; free -h | head -2; df -h / | tail -1
```
**P3 – Aggiornamenti.** `apt update && apt -y upgrade` (nessun riavvio ancora).

**P4 – Utente non-root.**
```
adduser dax
usermod -aG sudo dax && id dax
```
Trappola: incollare più righe durante i prompt di `adduser` finisce nei campi (successo: `usermod` incollato in «Work Phone»). Un comando alla volta, aspettare il prompt `root@…#`. Atteso: `groups=…27(sudo)…`.

**P5 – Chiave SSH.** In Termius: Vaults → Keychain → Generate key (ED25519, label `jarvis-pilot`, passphrase, Save passphrase). Copiare la chiave **pubblica**. Poi come root:
```
install -d -m 700 -o dax -g dax /home/dax/.ssh && echo "<CHIAVE_PUBBLICA>" >> /home/dax/.ssh/authorized_keys && chown dax:dax /home/dax/.ssh/authorized_keys && chmod 600 /home/dax/.ssh/authorized_keys && cat /home/dax/.ssh/authorized_keys
```
**P6 – Secondo host Termius** (hostname, Username `dax`, password vuota, Key `jarvis-pilot`). Connessione → atteso `dax@srv…$`; `sudo whoami` → `root`. **Tenere aperta la sessione root finché P7 non è verificato.**

**P7 – Blocco password/root.**
```
printf 'PasswordAuthentication no\nPermitRootLogin no\nKbdInteractiveAuthentication no\n' | sudo tee /etc/ssh/sshd_config.d/00-hardening.conf && sudo sshd -t && sudo sshd -T | grep -E 'passwordauthentication|permitrootlogin|kbdinteractive'
sudo systemctl reload ssh
```
(il nome `00-` conta: si vince il primo valore letto.) Verifica: **nuova** connessione `dax` entra; connessione root è rifiutata (Permission denied).

**P8 – Firewall.**
```
sudo ufw default deny incoming && sudo ufw allow 22/tcp && sudo ufw --force enable && sudo ufw status
```
Opzionale dopo: restringere 22 al proprio IP se fisso.

**P9 – Riavvio e controllo.** `sudo reboot`; dopo ~1 min riconnettere (le schede Termius mostrano «!» rosso nel frattempo: normale). Poi:
```
uname -r; systemctl is-active xrdp ssh ufw; python3 --version; git --version; ss -tlnp | grep -E ':(22|3389|5900)\b'
```
**P10 – Codice e installer.**
```
GIT_TERMINAL_PROMPT=0 git ls-remote https://github.com/daxluna88-max/Dodaios.git HEAD
sudo apt -y install python3-venv git curl && git clone https://github.com/daxluna88-max/Dodaios.git ~/Dodaios && cd ~/Dodaios && git rev-parse HEAD
grep -n -E 'headless|no-launch|curl|sudo|apt' install/install.sh | head -30      # leggere prima di eseguire
bash install/install.sh --headless --no-launch                                     # rispondere y
```
Poi `source ~/.bashrc` (PATH `~/.local/bin`).

**P11 – UI.**
```
sudo apt -y install nodejs npm && node --version && npm --version
cd ~/Dodaios/dodaios/ui/web/frontend && npm ci
NODE_OPTIONS=--max-old-space-size=5120 npm run build 2>&1 | tail -15      # atteso «built in …»; l'avviso chunk >500 kB è normale
```
**P12 – Avvio e tunnel.**
```
cd ~ && nohup jarvis serve > ~/jarvis-serve.log 2>&1 &
sleep 15; curl -s localhost:47821/api/health; tail -5 ~/jarvis-serve.log; ss -tlnp | grep 47821
```
Atteso: `{"ok":true,…}` e ascolto su `127.0.0.1:47821` (mai `0.0.0.0`). Tunnel Termius: Port Forwarding → Local, porta 47821, bind 127.0.0.1, intermediate host `dax`, destinazione 127.0.0.1:47821; avviare la regola; Safari `http://localhost:47821`. Il tunnel cade se iOS sospende Termius. `localhost` conta come contesto sicuro per il microfono del browser.

**P13 – Brain (da decidere, vedi §2).** Mai chiedere o ricevere chiavi/token in chat: `read -rs` in un file `chmod 600`. Riavvio del server: `pkill -x jarvis` (mai `pkill -f`: uccide la propria shell).

## 4. Per il bot guidato
- Una istruzione per messaggio; ogni comando in un blocco codice copiabile; dopo ogni passo chiedere la schermata e confrontare con l'«atteso».
- Prima di eseguire script scaricati: mostrarli e leggerli (fatto per `install.sh`).
- Passi irreversibili/rischiosi (spegnere password SSH, firewall) solo dopo prova di accesso alternativo; non chiudere mai la sessione di riserva prima.
- Cose da non automatizzare senza Dax: acquisto, creazione chiavi/abbonamenti, `codex login` (passa dal suo browser), costi, apertura porte.
- Rilevare differenze dall'atteso (es. Ubuntu 26.04 invece di 24.04, UI non precostruita, OOM Node) e registrarle come difetti, non nasconderle.
- Registro per ogni controllo come richiesto dall'issue #16; risultati di altre piattaforme non valgono come PASS qui.

## 5. Prossimi passi
1. Dax sceglie il brain (chiave OpenAI con tetto di spesa, oppure login Codex con abbonamento).
2. Riavviare il server col brain scelto e provare una risposta in chat dalla UI (F01) poi voce (E01–E03) e memoria (F02), uno alla volta, con registro.
3. Capire e decidere sul canale Telegram attivo (difetto 3) e sul watchdog voce (difetto 1).
4. Eliminare l'host Termius root; considerare snapshot Hostinger dopo che il server è stabile (comodità, non backup).
5. Trasformare §3 in script/bot (rispettando §4).

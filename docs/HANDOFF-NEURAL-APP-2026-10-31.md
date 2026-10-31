# DAX JARVIS - handoff Claude 31 ottobre 2026

Branch Dodaios: gpt/second-app-neural-v1
Ultimo commit: a56e40a7111ca876d295fddf4b3f3861031a350a

Obiettivo: seconda app voice-first, memoria con sinapsi animate e dashboard modulare.

File nuovi: NeuralApp.tsx, NeuralApp.css, useNeuralDashboard.ts, useNeuralMicrophone.ts, NeuralAppConnected.tsx, useNeuralMemory.ts, NeuralMemoryBridge.tsx.

Stato: UI e adattatori sviluppati ma ancora non collegati al runtime di produzione. Nessun test build, deploy o merge eseguito.

Priorita revisione:
1. STOP del microfono: attualmente lo stop del registratore puo eseguire il callback audio. Separare annullamento e invio.
2. Animazione neurale da attivare su eventi reali e verificati.
3. Integrare bridge memoria e dashboard in un solo wrapper.
4. Gestire correttamente persistenza dashboard, cambio account e logout.
5. Collegare voce e memoria Jarvis rispettando separazione Consumer/PMI.
6. Eseguire TypeScript e test Vitest mirati prima di proporre integrazione.

Claudio revisiona. GPT sviluppa. Dax approva messa in produzione.

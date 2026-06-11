# PASOS-MANUALES-LIAM.md — Pendientes manuales

> Todo lo que Claude NO puede hacer por vos (UIs externas, cuentas, decisiones).
> Marcar con [x] cuando este hecho. Claude mantiene este archivo al dia.

## Voz — para que funcione lo ya implementado (commit f679275)

### 1. Twilio Console — ConversationRelay (necesario solo para LLAMADAS)

- [ ] Pedir onboarding de ConversationRelay: Twilio Console → **Voice → ConversationRelay** → completar onboarding. **No es instantaneo, pedirlo YA** (puede tardar dias).
- [ ] En el mismo onboarding, habilitar **ElevenLabs** como proveedor TTS de la cuenta (si no esta habilitado, las llamadas fallan con **error 64101**).

### 2. Railway — Variables de entorno (necesario para NOTAS DE VOZ)

En Railway → proyecto agentkit → Variables, agregar:

- [ ] `OPENAI_API_KEY` — crear en platform.openai.com → API Keys (solo se usa para transcribir audios, ~$0.003/min)
- [ ] `ELEVENLABS_API_KEY` — crear en elevenlabs.io → Profile → API Keys
- [ ] `ELEVENLABS_VOICE_ID` — elegir UNA voz en elevenlabs.io → Voices (calida, energia media; va a ser "la voz del negocio" en los 5 idiomas). Copiar el Voice ID (no el nombre).
- [ ] `WEBHOOK_BASE_URL` — la URL publica de la app, ej `https://tu-app.up.railway.app` (sin barra final). Sin esto no se pueden servir los audios de respuesta ni atender llamadas.

### 3. Twilio — Webhook de voz del sender WhatsApp (necesario solo para LLAMADAS)

- [ ] En Twilio Console, en la config del sender de WhatsApp: configurar el webhook de **llamadas entrantes** a `https://tu-app.up.railway.app/voice` (POST). (El webhook de mensajes a `/webhook` ya esta configurado.)

### Que funciona con que

- [ ] Verificado: **Notas de voz funcionan SOLO con el paso 2** (mandar un audio al numero y debe responder con audio).
- [ ] Verificado: **Llamadas necesitan ademas los pasos 1 y 3** (tocar "llamar" en el chat de WhatsApp y debe atender la IA).

## Calidad de voz

- [ ] **A/B testing de hebreo: ElevenLabs Flash v2.5 vs Cartesia Sonic 3.** Generar 2-3 frases reales del negocio en hebreo (y espanol) con ambos y comparar. ~30 min, decide si nos quedamos con ElevenLabs. Importante: el estudio de humanizacion (VOICE-HUMANIZATION.md) no encontro NINGUNA evidencia verificada sobre fillers/normalizacion en hebreo — hay que probarlo con audios reales.
- [ ] Despues del A/B: probar como suenan las muletillas escritas ("eh", "mira") en los audios generados en hebreo; si suenan mal, avisarle a Claude para bajar la dosis en los prompts.

## Otros pendientes

- [ ] **Instagram DM automation: BLOQUEADO** por acceso a la API key de Facebook Developer (falta aprobar el acceso/app review en developers.facebook.com). Retomar cuando Meta apruebe.
- [ ] (De VOICE-IMPLEMENTATION.md) Confirmar con Twilio si los $0.07/min de ConversationRelay incluyen el costo de STT/TTS de los providers, y si ElevenLabs lleva recargo.
- [ ] (Para llamadas salientes `#llamar`) Crear el template interactivo de Meta para pedir **permiso de llamada** al cliente antes de la primera saliente.

## Cron jobs recomendados (Railway scheduler o similar)

- [ ] `POST {URL}/tasks/seguimientos` cada 5-15 min (dispara follow-ups y recordatorios) — header `x-agent-secret`
- [ ] `POST {URL}/tasks/limpieza` 1 vez por dia (limpia registros viejos y audios temporales)

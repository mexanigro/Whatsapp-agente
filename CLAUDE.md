# whatsapp-agentkit — CLAUDE.md

Agente de WhatsApp con IA para **Arzac Studio** (Liam Arzac, website@arzac.studio): SaaS de webs + gestion para PYMEs en Israel. Este repo es el canal de WhatsApp: recibe mensajes (texto, notas de voz y llamadas), responde con Claude API y actua como vendedor/soporte/agenda del negocio.

Precio del servicio: 770 NIS/mes plan basico, 960 NIS/mes con voice. El costo de API por cliente tiene que quedar muy por debajo de eso (cap diario configurado).

## Convenciones de trabajo (reglas para Claude)

- **No crear worktrees ni ramas** salvo que se pida explicitamente. Todo en `main`.
- Git user: `mexanigro` (liam.arzac@gmail.com). **Push automatico OK** — pero SIEMPRE regression check antes de cada push (compile + imports + flujo de texto intacto).
- Codigo y comentarios **en espanol**.
- Variables de entorno via python-dotenv, **NUNCA hardcodeadas**.
- Para cambios en UIs (Railway, Twilio Console, Firebase): dar instrucciones manuales paso a paso a Liam, no intentar automatizar con browser.

## Promesa a Liam

1. Si algo no se puede hacer por limitacion tecnica, **avisar de inmediato** — no simular que se hizo.
2. Nunca reportar algo como hecho sin haberlo verificado (tests, imports, TwiML generado, etc.).
3. Cuidar los costos de API como si fueran propios: cap diario, prompt caching, dedup, modelos baratos donde alcanza (Haiku).
4. Lo manual pendiente de Liam vive en `PASOS-MANUALES-LIAM.md` — mantenerlo al dia.

## Ecosistema (3 repos)

| Repo | Que es | Hosting |
|---|---|---|
| **master-template** | Web de cada cliente (landing + reservas + chatbot). Capta leads y dispara webhooks a nichos-hub. | Vercel |
| **nichos-hub** | Dashboard de operaciones de Liam. CRM con **Firestore** (leads, turnos, config por cliente), Google Calendar. Llama a agentkit para notificar. | Railway |
| **whatsapp-agentkit** (este) | Agente WhatsApp IA: conversaciones, voz, notificaciones, follow-ups, analytics. | Railway |

Flujos completos entre repos: ver `whatsapp-agentkit-main/AUTOMATION-FLOWS.md`.

## Stack (confirmado contra requirements.txt)

* Python 3.11+ / FastAPI + Uvicorn (no hay pyproject.toml; deps en `whatsapp-agentkit-main/requirements.txt`)
* IA: Anthropic Claude API — `claude-sonnet-4-6` para booking, `claude-haiku-4-5` para el resto y para voz (routing en brain.py)
* WhatsApp: Twilio (capa provider-agnostic; Meta Cloud API declarado no implementado)
* Voz: OpenAI `gpt-4o-mini-transcribe` (STT) + ElevenLabs Flash v2.5 (TTS) + Twilio ConversationRelay (llamadas)
* DB: SQLite (aiosqlite) — historial, leads, costos, analytics, seguimientos
* Config: YAML (`business.yaml`, `prompts.yaml`, `prompts_voice.yaml`)
* Redis opcional (rate limit multi-worker)
* Deploy: Railway (Docker)

## Arquitectura multi-cliente

**Una instancia del agente por cliente del SaaS.** Cada instancia tiene:
- `CLIENT_ID` propio (env) — filtra costos, analytics y notificaciones
- System prompt personalizado en `config/prompts.yaml` — se puede sincronizar desde nichos-hub (la config por cliente vive en **Firestore** detras de nichos-hub; el agente la baja con el comando admin `#recargar` via `GET {NICHOS_HUB_URL}/api/agent/config`)
- Estado de calendario (conectado/desconectado) que nichos-hub empuja via `/webhook/calendar-connected|disconnected`
- Knowledge del negocio en `knowledge/`

```
whatsapp-agentkit-main/
  agent/
    main.py          -- FastAPI app + webhook + endpoints internos + /voice + /ws/voice
    brain.py         -- Claude API, routing Sonnet/Haiku, tools de turnos, prompt caching
    memory.py        -- SQLite async: historial, leads, costos, config
    humanize.py      -- Fragmentos |||, delays de tipeo, delay de notas de voz
    pausa.py         -- Comandos admin (#pausa, #lead, #llamar, #recargar, #costo...)
    horario.py       -- Horario de atencion + deteccion simple de idioma
    escalacion.py    -- Deteccion de urgencia -> notificar a Liam
    rate_limit.py    -- Rate limit por telefono + global (Redis opcional)
    security.py      -- Firma Twilio, sanitizacion, anti prompt-injection, secrets
    analytics.py     -- Eventos + stats de conversion
    seguimiento.py   -- Follow-ups programados (leads, recordatorios, reviews)
    notifications.py -- Notificaciones via template/texto
    appointments.py  -- Cliente HTTP de turnos contra nichos-hub
    voice/
      transcribe.py  -- STT notas de voz (OpenAI gpt-4o-mini-transcribe)
      tts.py         -- TTS ElevenLabs Flash v2.5 (OGG/Opus = nota de voz nativa)
      media.py       -- Descarga media Twilio + storage temporal /media (TTL 24h)
      relay.py       -- Llamadas en vivo: TwiML + WebSocket ConversationRelay
    providers/
      base.py        -- ProveedorWhatsApp + MensajeEntrante (texto y media)
      twilio.py      -- Adaptador Twilio (texto + media + validar_firma)
  config/
    business.yaml      -- Datos del negocio
    prompts.yaml       -- System prompt de chat (personalidad "Liam humano")
    prompts_voice.yaml -- System prompt de voz (6 bloques) + saludos/fillers por idioma
  knowledge/           -- Info del negocio
  tests/test_local.py  -- Chat interactivo en terminal
```

## Features

### 1. Chat de texto (flujo principal)

```
Twilio POST /webhook -> validar firma -> dedup -> comandos admin -> urgencia ->
pausa -> rate limit -> cap de costos -> horario -> debounce (8s) ->
brain.py (Claude + historial + lead + tools de turnos) -> humanize (|||, delays,
typing indicator) -> respuesta fragmentada
```

Humanizacion de chat: debounce de mensajes consecutivos, fragmentos con delays de tipeo realistas, prompt "sos Liam, persona real". 5 idiomas: hebreo, espanol, ingles, ruso, arabe.

### 2. Notas de voz

Audio entrante -> `voice/media.py` descarga (redirect S3 sin doble auth) -> `voice/transcribe.py` (STT) -> pipeline normal con `canal="voz"` (brain agrega bloque de respuesta hablada: sin `|||`, numeros en palabras, 15-20 segundos max) -> `voice/tts.py` ElevenLabs -> OGG/Opus servido en `GET /media/{id}.ogg` -> Twilio lo manda como nota de voz nativa. **Espeja el canal** (audio se responde con audio). Fallback a texto si STT/TTS fallan. Urgencias se detectan sobre el texto transcripto.

### 3. Llamadas en vivo (WhatsApp Business Calling + ConversationRelay)

```
Cliente toca "llamar" -> Twilio POST /voice -> TwiML <Connect><ConversationRelay>
(voz ElevenLabs codificada VoiceID-modelo-speed_stability_similarity,
elevenlabsTextNormalization=on, ignoreBackchannel=true) ->
WebSocket /ws/voice (relay.py) -> Claude Haiku streaming token a token
con historial COMPARTIDO con el chat -> al cortar guarda resumen en historial
```

Barge-in con cancelacion de generacion, filler hablado durante tool use, escalacion si piden humano, rechazo (busy) si IA pausada o cap de costos. Salientes: comando admin `#llamar +972...` (requiere permiso previo de Meta). Auth del WS por token derivado de `AGENT_API_SECRET`.

### Voice humanization

El estudio completo (tecnicas verificadas de Vapi/Retell/ElevenLabs/Twilio) esta en **`VOICE-HUMANIZATION.md`**: brevedad estricta, disfluencias calibradas, normalizacion total a palabras, presupuesto de latencia, parametros de plataforma. La arquitectura y costos de voz estan en **`VOICE-IMPLEMENTATION.md`**. No tocar prompts de voz ni parametros TTS sin leer esos dos docs.

## Comandos admin (WhatsApp, solo ADMIN_PHONE_NUMBER)

| Comando | Efecto |
|---------|--------|
| `#pausa` / `#pausa 2h` / `#pausa 45m` | Pausa la IA (Liam toma control) |
| `#volver` | Reactiva la IA |
| `#estado` | Estado de pausa |
| `#costo` | Costos API hoy/semana |
| `#stats` | Metricas de conversion 30d |
| `#seguimientos` | Follow-ups pendientes |
| `#lead +972... Nombre` | Registra lead |
| `#leads` | Lista leads |
| `#llamar +972...` | Llamada de voz saliente (la atiende la IA) |
| `#recargar` | Sincroniza config desde nichos-hub + recarga prompts |

Pausada la IA: mensajes ignorados en silencio, llamadas rechazadas (busy).

## Variables de entorno (fuente: .env.example)

```
# Core
ANTHROPIC_API_KEY, CLAUDE_MODEL=claude-sonnet-4-6
WHATSAPP_PROVIDER=twilio
TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
WEBHOOK_BASE_URL          # URL publica (firma Twilio + servir audios + WS de voz)
PORT=8000, ENVIRONMENT=production, DB_PATH=agentkit.db

# Negocio
ADMIN_PHONE_NUMBER, BUSINESS_NAME, BUSINESS_TIMEZONE=Asia/Jerusalem
COSTO_DIARIO_MAXIMO=2.0   # cap de gasto API por dia
AUTO_REPLY_FUERA_HORARIO, ESCALACION_ACTIVA, FOLLOWUP_LEAD_HORAS
MAX_CONCURRENT_RESPONSES, MAX_MESSAGE_LENGTH, DEBOUNCE_MENSAJES_SEGUNDOS=8

# Integracion nichos-hub
NICHOS_HUB_URL, AGENT_API_SECRET, CLIENT_ID

# Templates Twilio Content API (opcionales)
TWILIO_TEMPLATE_APPT_*, TWILIO_TEMPLATE_NEW_LEAD, TWILIO_TEMPLATE_LEAD_FOLLOWUP

# Voz — notas de voz
OPENAI_API_KEY            # STT (gpt-4o-mini-transcribe)
ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID   # TTS (obligatorios para responder audio)
ELEVENLABS_VOICE_ID_{ES,EN,HE,RU,AR}      # overrides por idioma (opcional)
ELEVENLABS_STABILITY=0.4, ELEVENLABS_SIMILARITY=0.8, ELEVENLABS_STYLE=0.2, ELEVENLABS_SPEED=1.0
MEDIA_DIR=media_temp, MEDIA_TTL_HORAS=24

# Voz — llamadas
LLAMADAS_ACTIVAS=true
VOICE_CLAUDE_MODEL=claude-haiku-4-5-20251001, VOICE_MAX_TOKENS=300
VOICE_IDIOMA_DEFAULT=he, VOICE_WS_TOKEN, VOICE_CR_TTS_MODEL=flash_v2_5
VOICE_INTERRUPT_SENSITIVITY=medium

# Opcionales
REDIS_URL, RATE_LIMIT_GLOBAL_POR_MINUTO=200, TWILIO_SKIP_SIGNATURE (solo dev)
```

## Deploy (Railway)

- Deploy desde GitHub (`mexanigro/Whatsapp-agente`, branch main) con Docker.
- Variables en Railway dashboard (Liam las carga a mano — darle la lista, no automatizar).
- Webhooks en Twilio Console:
  - Mensajes: "When a message comes in" -> `https://tu-app.up.railway.app/webhook` (POST)
  - Voz: webhook de llamadas del sender WhatsApp -> `https://tu-app.up.railway.app/voice` (POST)
- Cron externo recomendado: `POST /tasks/seguimientos` cada 5-15 min y `POST /tasks/limpieza` diario (auth `x-agent-secret`).
- Llamadas: requieren onboarding previo de ConversationRelay + ElevenLabs habilitado en la cuenta Twilio (ver `PASOS-MANUALES-LIAM.md`).

## Comandos utiles

```bash
python tests/test_local.py                      # chat local sin WhatsApp
uvicorn agent.main:app --reload --port 8000     # servidor
docker compose up --build                        # docker
python -m py_compile agent/*.py agent/voice/*.py agent/providers/*.py  # regression rapido
```

## Notas de desarrollo

- El CLAUDE.md largo en `whatsapp-agentkit-main/whatsapp-agentkit-main/CLAUDE.md` es el blueprint generico de AgentKit (onboarding). ESTE archivo es el contexto operativo real.
- memory.py usa aiosqlite directo (no SQLAlchemy) — intencional, mantener simple.
- brain.py cachea prompts.yaml en memoria; `#recargar` invalida el cache (idem prompts_voice.yaml).
- Docs de referencia en la raiz: `VOICE-IMPLEMENTATION.md` (arquitectura/costos voz), `VOICE-HUMANIZATION.md` (estudio humanizacion), `API-COSTS-AUDIT.md` (audit de costos), `whatsapp-agentkit-main/AUTOMATION-FLOWS.md` (flujos entre repos), `PASOS-MANUALES-LIAM.md` (pendientes manuales de Liam).

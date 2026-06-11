# whatsapp-agentkit — CLAUDE.md

Agente de WhatsApp con IA para Arzac Studio. Recibe mensajes de WhatsApp, genera respuestas con Claude API, y funciona como asistente de ventas/soporte para el negocio. Propietario: Liam Arzac (website@arzac.studio).

## Convenciones de trabajo

- No crear worktrees ni ramas separadas salvo que se pida explicitamente.
- Codigo y comentarios en espanol.
- Variables de entorno via python-dotenv, NUNCA hardcodeadas.

## Contexto del negocio

Este agente es el canal de WhatsApp de Arzac Studio. Cuando un potencial cliente escribe al WhatsApp del negocio, el agente responde automaticamente usando Claude API. Liam (admin) puede pausar la IA, registrar leads, y retomar el control manualmente cuando sea necesario.

## Ecosistema

Este repo es uno de 3 que forman el SaaS:

| Repo | Que es | Hosting |
|---|---|---|
| **master-template** | Web de cada cliente (landing + CRM + chatbot) | Vercel |
| **nichos-hub** | Dashboard operaciones de Liam | Railway |
| **whatsapp-agentkit** (este) | Agente WhatsApp con IA | Railway |

## Stack

* Runtime: Python 3.11+
* Servidor: FastAPI + Uvicorn
* IA: Anthropic Claude API (`claude-sonnet-4-6`)
* WhatsApp: Twilio (provider-agnostic, soporta Meta Cloud API tambien)
* Base de datos: SQLite (aiosqlite) para historial de conversaciones y leads
* Config: YAML (business.yaml, prompts.yaml)
* Deploy: Railway (Docker)

## Arquitectura

```
whatsapp-agentkit-main/
  agent/
    __init__.py
    main.py          -- FastAPI app + webhook handler
    brain.py         -- Conexion Claude API + system prompt desde prompts.yaml
    memory.py        -- SQLite async, historial por telefono + leads
    tools.py         -- Herramientas del negocio (horario, knowledge search, lead scoring)
    pausa.py         -- Sistema de pausa admin (Liam toma control via WhatsApp)
    voice/
      transcribe.py  -- STT notas de voz (OpenAI gpt-4o-mini-transcribe)
      tts.py         -- TTS ElevenLabs Flash v2.5 (OGG/Opus = nota de voz nativa)
      media.py       -- Descarga media Twilio + storage temporal /media (TTL 24h)
      relay.py       -- Llamadas en vivo: TwiML + WebSocket ConversationRelay
    providers/
      __init__.py    -- Factory: obtener_proveedor() segun WHATSAPP_PROVIDER env
      base.py        -- Clase abstracta ProveedorWhatsApp + MensajeEntrante dataclass
      twilio.py      -- Adaptador Twilio (texto + media + validar_firma)
  config/
    business.yaml    -- Datos del negocio (nombre, descripcion, horario, precio)
    prompts.yaml     -- System prompt del agente (personalidad, reglas, contexto)
    prompts_voice.yaml -- System prompt del canal de voz (llamadas) + saludos/fillers
  knowledge/         -- Archivos de conocimiento del negocio (PDFs, TXTs)
  tests/
    test_local.py    -- Chat interactivo en terminal (simula WhatsApp)
  Dockerfile
  docker-compose.yml
  requirements.txt
  .env               -- API keys (NUNCA va a GitHub)
  agentkit.db        -- SQLite database (auto-created)
```

## Flujo de un mensaje

```
WhatsApp (cliente escribe)
  -> Twilio webhook POST /webhook
  -> providers/twilio.py normaliza a MensajeEntrante
  -> main.py: verifica si es admin + comando (#pausa, #lead, etc.)
  -> main.py: verifica si IA esta pausada
  -> memory.py: recupera historial de esa conversacion
  -> memory.py: busca si el telefono tiene un lead asociado
  -> brain.py: Claude API con system_prompt + historial + mensaje + contexto lead
  -> memory.py: guarda mensaje user + assistant
  -> providers/twilio.py: envia respuesta a WhatsApp
```

## Sistema de pausa (agent/pausa.py)

Liam (admin, numero hardcoded en NUMERO_ADMIN) puede controlar el agente via WhatsApp con comandos `#`:

| Comando | Efecto |
|---------|--------|
| `#pausa` | Pausa IA 30 minutos |
| `#pausa 2h` | Pausa IA 2 horas |
| `#pausa 45m` | Pausa IA 45 minutos |
| `#volver` | Reactiva IA inmediatamente |
| `#estado` | Muestra si IA esta activa/pausada y tiempo restante |
| `#lead +972XXXXXXXXX Nombre Negocio` | Registra un lead con telefono y negocio |
| `#leads` | Lista todos los leads registrados |
| `#llamar +972XXXXXXXXX` | Llamada de voz saliente por WhatsApp (la atiende la IA) |

Cuando la IA esta pausada, los mensajes entrantes se ignoran silenciosamente (Liam responde manualmente) y las llamadas de voz se rechazan (suena ocupado).

## Voz (agent/voice/)

Dos features (docs: VOICE-IMPLEMENTATION.md = arquitectura/costos, VOICE-HUMANIZATION.md = estudio de humanizacion):

* **Notas de voz**: audio entrante -> descarga (media.py) -> STT OpenAI (transcribe.py) -> pipeline normal de Claude con `canal="voz"` (respuesta hablada, sin `|||`) -> TTS ElevenLabs (tts.py) -> OGG/Opus servido en `GET /media/{id}.ogg` -> Twilio lo manda como nota de voz. Si STT/TTS fallan, fallback a texto.
* **Llamadas en vivo**: WhatsApp Business Calling -> `POST /voice` (TwiML ConversationRelay con voz ElevenLabs codificada `VoiceID-modelo-speed_stability_similarity`) -> WebSocket `/ws/voice` (relay.py) -> Claude Haiku streaming token a token con historial compartido de memory.py. Al cortar guarda resumen de la llamada en el historial. Requiere onboarding de ConversationRelay en Twilio Console + ElevenLabs habilitado en la cuenta.

## Sistema de leads (agent/memory.py)

La tabla `leads` en SQLite asocia numeros de telefono con nombres de negocio. Cuando un lead esta registrado, brain.py agrega contexto extra al system prompt: "Esta persona es del negocio: X. Ya le construimos una web demo."

## Provider abstraction (agent/providers/)

La capa de providers abstrae el servicio de WhatsApp:

* `base.py`: Clase abstracta `ProveedorWhatsApp` con metodos `parsear_webhook()`, `enviar_mensaje()`, `validar_webhook()`
* `twilio.py`: Implementacion para Twilio (form-encoded webhooks, Basic auth API)
* Se puede agregar `meta.py` para Meta Cloud API sin cambiar main.py

Factory en `__init__.py`: lee `WHATSAPP_PROVIDER` de .env y retorna la instancia correcta.

## Variables de entorno

```
# Anthropic API
ANTHROPIC_API_KEY=sk-ant-...

# Proveedor de WhatsApp (meta | twilio)
WHATSAPP_PROVIDER=twilio

# Twilio
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=...

# Meta Cloud API (alternativa)
# META_ACCESS_TOKEN=...
# META_PHONE_NUMBER_ID=...
# META_VERIFY_TOKEN=agentkit-verify

# Servidor
PORT=8000
ENVIRONMENT=development  # development | production

# Base de datos
DB_PATH=agentkit.db  # SQLite local
```

## Configuracion del agente

### config/business.yaml

Datos del negocio: nombre, descripcion, horario, precio, contacto, casos de uso del agente, tono.

### config/prompts.yaml

System prompt completo del agente. Define personalidad, reglas de comportamiento, contexto del negocio, y mensajes de error/fallback. Brain.py lo lee en cada request.

## Comandos utiles

```bash
# Test local (simula WhatsApp en terminal)
python tests/test_local.py

# Arrancar servidor
uvicorn agent.main:app --reload --port 8000

# Docker
docker compose up --build

# Ver logs
docker compose logs -f agent
```

## Deploy

Railway desde GitHub. Variables de entorno se configuran en Railway dashboard. Webhook URL: `https://tu-app.up.railway.app/webhook`. Para Twilio: configurar en Twilio Console -> Messaging -> WhatsApp Sandbox Settings -> "When a message comes in" = webhook URL.

## Notas de desarrollo

- El CLAUDE.md largo en `whatsapp-agentkit-main/whatsapp-agentkit-main/CLAUDE.md` es el blueprint de AgentKit (sistema de onboarding paso a paso). Este archivo (`CLAUDE.md` en la raiz) es el contexto operativo del proyecto ya construido.
- memory.py usa aiosqlite directamente (no SQLAlchemy como sugiere el blueprint). Esto es intencional para mantener simple.
- brain.py carga prompts.yaml en cada request (no cached). Esto permite editar el system prompt sin reiniciar el servidor.
- tools.py tiene funciones de calificacion de leads que no se usan activamente aun, pero estan disponibles para expansion futura.

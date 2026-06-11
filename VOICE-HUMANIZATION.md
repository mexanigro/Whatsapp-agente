# VOICE-HUMANIZATION.md — Como hacer que un voice agent suene humano

> Estudio de mercado — junio 2026. Companion de VOICE-IMPLEMENTATION.md.
> Metodologia: deep research multi-fuente con verificacion adversarial (25 claims
> verificados con 3 votos independientes cada uno; 23 confirmados, 2 refutados).
> Fuentes primarias: guias oficiales de Vapi, Retell AI, ElevenLabs y Twilio.

---

## 1. Resumen ejecutivo

Los lideres del mercado (Vapi, Retell, ElevenLabs Agents) humanizan agentes de voz
con **tres capas**, todas implementables en nuestro stack:

| Capa | Que es | Donde se implementa aca |
|---|---|---|
| **Prompting de voz** | Brevedad, disfluencias, normalizacion a palabras | `config/prompts_voice.yaml` + `BLOQUE_CANAL_VOZ` en brain.py |
| **Ingenieria de latencia** | Streaming, presupuesto por componente, endpointing | `agent/voice/relay.py` (Claude streaming + Haiku) |
| **Parametros de plataforma** | stability/speed de ElevenLabs, barge-in de CR | TwiML en `relay.py` + `agent/voice/tts.py` |

## 2. Capa 1 — Prompting de voz (la mas importante)

### 2.1 Brevedad estricta (consenso de los 3 lideres, verificado 3-0)

- **Vapi:** 1-2 frases maximo y UNA pregunta por turno. Los LLMs entrenados en
  texto son verbosos por defecto; cada token extra es "dead air" percibido.
- **Retell:** menos de 2 frases salvo temas complejos.
- **ElevenLabs:** menos de 3 frases salvo que el usuario pida detalle.
- Una respuesta de 2 frases tarda ~400ms en empezar a sonar; un parrafo, 2-3s.

**Regla nuestra:** maximo 2 frases por turno de llamada, una pregunta por turno.

### 2.2 Disfluencias deliberadas (Vapi, verificado 3-0)

La guia oficial de Vapi recomienda **2-4 fillers por turno** ("um", "uh", "like",
"well"), auto-correcciones ("I- I think we can, uh, set that up"), **calibradas a
la persona**: roles casuales mas fillers; roles clinicos/profesionales patrones
suaves tipo "let me see" / "one moment". Regla operativa de Vapi: *"si un turno
sale perfectamente pulido, agrega un filler y proba de nuevo"*.

**Regla nuestra (negocio local, tono calido-profesional):** 1-2 disfluencias suaves
por turno ("eh", "mira", "dale, dejame ver"), no en todos los turnos. CAVEAT: no
hay evidencia verificada de como renderiza Flash v2.5 los fillers escritos en
hebreo — probar con audios reales antes de subir la dosis.

### 2.3 Normalizacion total a forma hablada (consenso unanime, 4 fuentes 3-0)

NUNCA digitos, simbolos ni formato visual en el texto que va al TTS. ElevenLabs
advierte que digitos y simbolos ("@", simbolos de moneda) causan mispronunciaciones
y **alucinaciones de voz**, especialmente en modelos rapidos como Flash.

| Tipo | Mal | Bien (ejemplo oficial) |
|---|---|---|
| Plata | $42.50 | "forty-two dollars and fifty cents" / "setecientos noventa shekels" |
| Fecha | 1/15 | "January fifteenth" / "el quince de enero" |
| Telefono | 4158923245 | "four one five - eight nine two - three two four five" (grupos con guion Y espacios) |
| Email/URL | nklaundry.com | "en-kay-laundry dot com" (por telefono mejor: "te lo mando por escrito") |
| Hora | 10:30 | "a las diez y media" |

### 2.4 Estilo conversacional (Retell, verificado 3-0)

Lenguaje natural, contracciones, **acusar recibo de lo que dice el llamante**
(backchanneling saliente: "dale", "claro", "ah, perfecto") y empatia explicita.

### 2.5 Prosodia: puntuacion estandar, no SSML (Vapi, verificado 3-0)

Comas, puntos y puntos y coma se traducen consistentemente a prosodia natural en
todos los TTS. El markup SSML/break tags se comporta inconsistente entre
proveedores. **Regla nuestra:** solo puntuacion; nada de SSML en v1.

### 2.6 Estructura del system prompt (ElevenLabs, verificado 3-0)

ElevenLabs recomienda 6 bloques con headers markdown — los modelos prestan
atencion extra a ciertos headings (especialmente Guardrails):
`# Personality`, `# Environment`, `# Tone`, `# Goal`, `# Guardrails`, `# Tools`.
Aplicado en `config/prompts_voice.yaml`.

## 3. Capa 2 — Ingenieria de latencia (Twilio, nov 2025, verificado 3-0)

| Componente | Objetivo | Maximo |
|---|---|---|
| STT | 350 ms | 500 ms |
| LLM time-to-first-token | 375 ms | 750 ms |
| TTS time-to-first-byte | 100 ms | 250 ms |
| **Gap boca-a-oido total** | **~1.115 ms** | **1.400 ms** |

- **Streaming del LLM es no-negociable** (un API sin streaming queda descalificado).
  Por eso `relay.py` streamea token a token y usa **Haiku** (TTFT mas bajo).
- **Endpointing — costo de error asimetrico:** interrumpir al cliente por un falso
  positivo de fin de turno se percibe MUCHO peor que un poco de latencia extra.
  Ante la duda, esperar. (Default ~500ms de silencio; el "smart endpointing"
  ahorra 200-300ms de mediana pero mete saltos en la cola.)
- **Pausa muda durante tool use delata al bot:** mientras se consulta la agenda
  (1-2s), mandar un filler hablado ("dale, dejame chequear la agenda"). Implementado
  en `relay.py` (`fillers_herramienta`).

## 4. Capa 3 — Parametros de plataforma (verificado contra docs vivas de Twilio)

### 4.1 ConversationRelay + ElevenLabs (Public Beta)

- `ttsProvider="ElevenLabs"` (default de CR; alternativas Google y Amazon).
- **El atributo `voice` codifica todo junto:** `[VoiceID]-[Model]-[Speed]_[Stability]_[Similarity]`
  ej: `ZF6FPAbjXT4488VcRRnw-flash_v2_5-1.0_0.5_0.8`
  - speed: 0.7-1.2 (default 1.0)
  - stability: 0.0-1.0 — **baja = mas emotiva/dramatica, alta = monotona** (el knob
    de humanizacion mas directo). Ojo: un blog de Twilio dice default 1.0 dentro de
    CR (el API directo de ElevenLabs usa 0.5) — single-sourced, setearlo explicito.
  - Modelos soportados: flash_v2_5 (default), flash_v2, turbo_v2_5, turbo_v2
    (4 modelos — el blog que decia 2 fue REFUTADO 0-3).
- `elevenlabsTextNormalization="on"` — default off; activarlo como red de seguridad
  para digitos que se escapen (el prompt ya pide palabras).
- **Barge-in nativo:** `interruptible` (default any), `interruptSensitivity`
  (high/medium/low, default high).
- **`ignoreBackchannel="true"`** (default false): filtra los "yeah"/"aja"/"ok" del
  cliente para que sus acuses de recibo NO corten al agente a mitad de frase.
  CAVEAT: no esta documentado si reconoce backchannels hebreos ("כן", "אה") — probar.

### 4.2 ElevenLabs API directa (notas de voz)

`stability` baja-media (0.35-0.5) para variacion emocional, `similarity_boost`
alto (0.75-0.9) para identidad de voz estable, `style` bajo-moderado. Defaults
nuestros en `agent/voice/tts.py`: 0.4 / 0.8 / 0.2 (ajustables por env).

## 5. Que delata a un bot (sintesis operativa)

1. **La pausa larga** antes de responder (>1.4s boca-a-oido) — peor que cualquier acento.
2. **El parrafo perfecto**: respuesta larga, pulida, sin disfluencias, con estructura de lista.
3. **Digitos leidos como texto**: "setecientos noventa" suena humano, "790" suena a GPS.
4. **Interrumpir al cliente** por endpointing agresivo.
5. **El silencio muerto durante una consulta interna** (sin "dale, dejame ver").
6. **Retomar el discurso despues de ser interrumpido** como si nada (hay que responder a lo nuevo).
7. **No pedir repeticion**: un humano dice "perdon, como?" cuando no escucho bien; un bot adivina.

## 6. Caveats y preguntas abiertas (importante para Liam)

1. **Toda la evidencia que sobrevivio es de vendors** (Vapi/Retell/ElevenLabs/Twilio).
   No hay estudios independientes verificados de que estas tecnicas aumenten la
   percepcion de humanidad. Son las practicas de los lideres, no ciencia.
2. **Idiomas no-ingleses: CERO evidencia verificada.** Como se comportan fillers,
   normalizacion y stability en HEBREO es la pregunta mas critica para nuestro caso
   y quedo abierta. → Accion: A/B con frases reales en hebreo (ya estaba pendiente
   en VOICE-IMPLEMENTATION.md seccion 10).
3. Nada sobrevivio sobre Bland AI, Air AI, Sierra ni PolyAI (sus guias publicas
   no pasaron verificacion o no exponen tecnica).
4. ElevenLabs dentro de CR es **Public Beta**; el formato del atributo voice puede cambiar.
5. Verificar que `ignoreBackchannel` y el endpointing de Google STT funcionen bien
   con hebreo; y la latencia real de gpt-4o-mini-transcribe en hebreo (presupuesto 350ms).

## 7. Fuentes confirmadas

- Vapi prompting guide: docs.vapi.ai/prompting-guide
- Retell prompt engineering: docs.retellai.com/build/prompt-engineering-guide y prompt-situation-guide
- ElevenLabs agents prompting: elevenlabs.io/docs/eleven-agents/best-practices/prompting-guide
- Twilio latency guide: twilio.com/en-us/blog/developers/best-practices/guide-core-latency-ai-voice-agents
- ConversationRelay TwiML reference: twilio.com/docs/voice/twiml/connect/conversationrelay
- CR voice configuration: twilio.com/docs/voice/conversationrelay/voice-configuration
- CR + ElevenLabs: twilio.com/en-us/blog/integrate-elevenlabs-voices-with-twilios-conversationrelay

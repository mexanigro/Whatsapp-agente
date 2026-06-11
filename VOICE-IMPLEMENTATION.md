# VOICE-IMPLEMENTATION.md — Voz y audio en el agente WhatsApp

> Documento de referencia permanente. Investigado y redactado en junio 2026.
> Objetivo: agregar llamadas de voz y notas de voz al agente WhatsApp de Arzac Studio
> con voz super humanizada y costo minimo por cliente, listo para vender como servicio.

---

## 1. Resumen ejecutivo

Hay **dos features distintas** bajo el paraguas "voz", con costos y complejidad muy diferentes:

| Feature | Que es | Costo aprox | Complejidad | Prioridad |
|---|---|---|---|---|
| **Notas de voz** | El cliente manda un audio por WhatsApp, el agente lo transcribe, piensa con Claude y responde con un audio de voz natural | ~$0.05-0.06 por intercambio | Baja (se integra al webhook actual) | **Fase 1 — quick win** |
| **Llamadas en vivo** | El cliente toca "llamar" en el chat de WhatsApp y habla en tiempo real con el agente | ~$0.09-0.13 por minuto | Media (WebSocket + ConversationRelay) | **Fase 2** |

**Recomendacion final (justificacion completa en seccion 9):**

- **Fase 1 (notas de voz):** OpenAI `gpt-4o-mini-transcribe` para transcribir ($0.003/min) + Claude (cerebro actual, sin cambios) + **ElevenLabs Flash v2.5** para generar el audio de respuesta ($0.05/1K chars). Es el stack mas barato que cubre los 5 idiomas (incluido hebreo) con voz no robotica.
- **Fase 2 (llamadas):** **Twilio ConversationRelay + WhatsApp Business Calling** con TTS ElevenLabs y STT Google. Twilio maneja el audio; nuestro FastAPI solo intercambia texto por WebSocket y Claude sigue siendo el cerebro. Techo presupuestado: $0.13/min.
- **Descartados:** Amazon Polly y Deepgram Aura-2 (no tienen hebreo). OpenAI Realtime queda como plan B (caro: $0.16-0.36/min real, y saca a Claude del loop).

---

## 2. Restriccion clave: idiomas

El negocio atiende **espanol, ingles, hebreo, ruso y arabe**. El hebreo es el filtro que mata a la mitad de los proveedores:

| Proveedor TTS | Hebreo | Arabe | Ruso |
|---|---|---|---|
| ElevenLabs (Flash v2.5, Multilingual v2/v3) | SI | SI | SI |
| Cartesia Sonic 3 | SI | SI | SI |
| Google Chirp 3 HD | SI (he-IL) | SI (ar-XA) | SI |
| Azure Neural TTS | SI (voces Avri, Hila) | SI | SI |
| OpenAI TTS (todas) | Habla pero con acento anglo notorio | Igual | Igual |
| Amazon Polly | **NO** | SI | SI |
| Deepgram Aura-2 | **NO** | NO | NO |

En STT pasa lo mismo: Deepgram Nova-3 (el default de ConversationRelay) **no tiene hebreo**; OpenAI (Whisper / gpt-4o-transcribe) y Google STT si lo tienen.

---

## 3. Comparativa de proveedores TTS (precios junio 2026)

Regla practica: 1 minuto de habla ~ 750-900 caracteres.

| Proveedor / Modelo | Precio | ~$/min hablado | Calidad / Latencia | Veredicto |
|---|---|---|---|---|
| **ElevenLabs Flash v2.5** | $0.05 / 1K chars | ~$0.040-0.045 | La voz mas humana del mercado en esta gama; ~75ms de latencia de modelo | **Elegido** — mejor naturalidad en los 5 idiomas |
| ElevenLabs Multilingual v2/v3 | $0.10 / 1K chars | ~$0.085 | Maxima expresividad, mas lenta | Para audios no-realtime premium |
| **Cartesia Sonic 3** | ~$0.037-0.039 / 1K chars (planes Startup/Scale) | ~$0.030 | Ultra baja latencia (40-90ms), hebreo nativo | **Alternativa #2** — 25% mas barata, menos voces |
| Google Chirp 3 HD | $30 / 1M chars | ~$0.025 | Muy natural, hebreo GA | Alternativa solida, integra nativo con ConversationRelay |
| Azure Neural HD | $22 / 1M chars | ~$0.019 | Correcta, menos "humana" | La mas barata con hebreo, pero se nota mas sintetica |
| Google Neural2 | $16 / 1M chars | ~$0.013 | Notablemente menos humana que Chirp 3 | No cumple "super humanizada" |
| OpenAI gpt-4o-mini-tts | ~$0.015/min | ~$0.015 | Buena y controlable por instrucciones, pero acento anglo en hebreo | Descartada por acento |
| Amazon Polly Generative | $30 / 1M chars | ~$0.025 | Buena | **Descartada: sin hebreo** |
| Deepgram Aura-2 | $30 / 1M chars | ~$0.025 | Excelente latencia | **Descartada: sin hebreo/arabe/ruso** |

Nota de calidad: no hay benchmark publicado de hebreo ElevenLabs vs Cartesia. Antes de cerrar la eleccion definitiva, hacer una prueba A/B con 2-3 frases reales del negocio en hebreo y espanol (30 minutos de trabajo, decide la compra).

## 4. Comparativa de proveedores STT

| Proveedor | Precio | Hebreo | Streaming (tiempo real) | Veredicto |
|---|---|---|---|---|
| **OpenAI gpt-4o-mini-transcribe** | **$0.003/min** | SI | No (async) | **Elegido para notas de voz** — el mas barato con los 5 idiomas |
| OpenAI gpt-4o-transcribe | $0.006/min | SI | No (async) | Upgrade si mini comete errores |
| OpenAI Whisper API | $0.006/min | SI | No | Legacy, funciona bien |
| **Google STT V2** | $0.016/min streaming | SI (he-IL) | SI | **Elegido para llamadas** — unico streaming verificado con hebreo |
| Deepgram Nova-3 | ~$0.0058-0.0077/min | **NO** | SI (la mejor latencia) | Descartado por idiomas |
| AssemblyAI Universal | $0.27/hr async | Solo async | Streaming solo 6 idiomas (sin hebreo) | Descartado para llamadas |

---

## 5. Twilio: las piezas disponibles

### 5.1 WhatsApp Business Calling (GA desde julio 2025)

El cliente toca el boton "llamar" dentro del chat de WhatsApp del negocio. Es VoIP dentro de la app — no necesita numero PSTN israeli. La llamada llega al webhook de Twilio Voice como una llamada normal y es compatible con ConversationRelay.

- **Entrante (cliente llama al negocio):** disponible en todos los paises de Meta Cloud API, **Israel incluido**. Costo: $0.005/min fee Twilio + $0.00 Meta = **$0.005/min**.
- **Saliente (el negocio llama al cliente):** soportado en Israel. Costo: $0.005/min Twilio + ~$0.0127/min Meta (region "Rest of Middle East") ~ **$0.018/min**.
- Comparado con PSTN Israel ($0.0107/min entrante local, $0.0659-0.1868/min saliente), WhatsApp Calling es mucho mas barato, sobre todo en salientes.

### 5.2 ConversationRelay ($0.07/min)

Orquestador de voice AI de Twilio. Twilio se encarga del audio (STT + TTS) y nos entrega/recibe **texto** por un WebSocket persistente:

```
Cliente <--> Twilio (STT/TTS) <--> WebSocket wss:// <--> FastAPI <--> Claude API
```

- TTS integrados: Google, Amazon, **ElevenLabs**. STT: Google, Deepgram, Amazon.
- Maneja interrupciones (barge-in), DTMF, y streaming token a token (Twilio empieza a hablar antes de que Claude termine de generar).
- Requiere onboarding previo en Twilio Console (Voice > ConversationRelay) — no es instantaneo, pedirlo con anticipacion. ElevenLabs dentro de CR requiere habilitacion de cuenta (error 64101 si no esta).
- Limitaciones: sin acceso a audio crudo, voz/proveedor fijados al iniciar la llamada (solo `language` cambia mid-call), si se cae el WebSocket se corta la llamada (mitigar con `<Connect action>`).

### 5.3 Media Streams ($0.004/min)

WebSocket bidireccional de audio crudo (mu-law 8kHz). Es la alternativa DIY: nosotros conectamos STT y TTS a mano, o enchufamos OpenAI Realtime. Mas control, mucho mas codigo, y hay que manejar barge-in, buffering y latencia manualmente. Solo tiene sentido si ConversationRelay no soporta hebreo en la practica.

### 5.4 Notas de voz (media messages)

- Twilio cobra $0.005 por mensaje WhatsApp (entrante o saliente, con o sin media). Meta: $0 dentro de la ventana de servicio de 24h.
- El audio entrante llega como `MediaUrl0` en el webhook actual (form-encoded). Se descarga con auth Basic (Account SID + Auth Token).
- Para responder con audio: subir el archivo generado a un storage publico (o endpoint propio servido por FastAPI) y enviarlo como media message.
- Formato: WhatsApp acepta OGG/Opus para que se vea como nota de voz nativa (audio/ogg; codecs=opus).

---

## 6. Arquitectura recomendada

### Fase 1 — Notas de voz (extension del pipeline actual)

```
WhatsApp (cliente manda audio)
  -> Twilio webhook POST /webhook (MediaUrl0 + MediaContentType0=audio/ogg)
  -> providers/twilio.py: detecta media, descarga el OGG (auth Basic)
  -> voice/transcribe.py: gpt-4o-mini-transcribe -> texto + idioma
  -> [pipeline actual SIN CAMBIOS: memoria, lead, brain.py con Claude]
  -> decision de formato de respuesta:
       - si el cliente mando audio -> responder con audio (espejar el canal)
       - si mando texto -> responder texto (flujo actual)
  -> voice/tts.py: ElevenLabs Flash v2.5 -> OGG/Opus
  -> FastAPI sirve el archivo en /media/{id}.ogg (temporal, se borra a las 24h)
  -> providers/twilio.py: enviar_media(telefono, url_audio)
```

Puntos clave:
- El texto transcripto se guarda en el historial igual que un mensaje de texto. Claude no se entera de que fue audio (solo un flag para decidir el formato de respuesta).
- La respuesta en audio NO usa el separador `|||` ni los delays de tipeo: es UN solo audio. Hay que pedirle a Claude una version "hablada" (sin fragmentos) cuando el canal es voz — un bloque extra en el system prompt alcanza.
- Verificacion antes de enviar: si la generacion de TTS falla, hacer fallback a texto. Nunca dejar al cliente sin respuesta.

### Fase 2 — Llamadas en vivo

```
Cliente toca "llamar" en el chat de WhatsApp
  -> Twilio Voice webhook POST /voice (llamada WhatsApp entrante)
  -> FastAPI responde TwiML:
       <Connect><ConversationRelay url="wss://nuestro-app/ws/voice"
                 voice="<voice_id ElevenLabs>"
                 transcriptionProvider="google" ... />
  -> WebSocket /ws/voice:
       evento "prompt" (transcript del cliente)
         -> brain_voice.py: Claude streaming (historial compartido con el chat!)
         -> tokens -> {"type":"text","token":...,"last":false} -> Twilio habla
       evento "interrupt" -> cortar generacion de Claude
       evento "dtmf" -> opciones por teclado si hiciera falta
  -> al cortar: guardar resumen de la llamada en el historial de ese telefono
```

Puntos clave:
- **Memoria unificada:** el WebSocket identifica al cliente por su numero (viene en el webhook de la llamada). Se carga el mismo historial de `memory.py` — el agente "se acuerda" de lo chateado cuando atiende la llamada, y al reves. Esto es lo que lo hace sentir humano de verdad.
- **Streaming token a token** desde Claude para latencia minima (Twilio empieza a hablar con el primer token, no espera la respuesta completa).
- **System prompt de voz separado** (`prompts_voice.yaml`): sin `|||`, sin reglas de emojis/markdown; en su lugar: frases cortas, muletillas naturales ("dale", "mira"), numeros en palabras ("setecientos noventa" y no "790"), confirmaciones habladas.
- Deteccion de idioma: arrancar la llamada en el idioma del historial de chat si existe; si no, hebreo/ingles como saludo neutro y cambiar `language` mid-call segun lo que hable el cliente.
- Saliente (callback): mismo flujo iniciado por la Calls API de Twilio con TwiML `<WhatsApp>`. Caso de uso: "te llamo en 5 minutos" desde el chat, o recordatorios de turno hablados.

### Donde vive el codigo

```
agent/
  voice/
    __init__.py
    transcribe.py     -- STT de notas de voz (OpenAI)
    tts.py            -- generacion de audio (ElevenLabs) + cache de frases comunes
    media.py          -- descarga de media de Twilio, storage temporal, limpieza
    relay.py          -- handler del WebSocket de ConversationRelay (Fase 2)
  providers/
    twilio.py         -- agregar: parsear media entrante, enviar_media()
config/
  prompts_voice.yaml  -- system prompt para canal de voz
```

---

## 7. Flujos detallados

### 7.1 Nota de voz entrante (Fase 1)

1. Webhook recibe POST con `NumMedia=1`, `MediaUrl0`, `MediaContentType0=audio/ogg`.
2. Dedup + rate limit + pausa: identico al flujo de texto actual.
3. Descargar el audio (httpx, auth Basic, timeout 10s, max ~16MB que es el limite de WhatsApp).
4. Transcribir con `gpt-4o-mini-transcribe`. Si la transcripcion viene vacia o con confianza baja: responder en texto "no te escuche bien, me lo mandas de nuevo?" (humanizado, en el idioma del historial).
5. Pasar el texto al pipeline actual (debounce incluido — una nota de voz + un texto seguidos se responden juntos).
6. Claude responde. Si el ultimo mensaje del cliente fue audio: generar TTS con la voz del idioma detectado y enviar como nota de voz. El texto de la respuesta se guarda en el historial normal.
7. Costo del intercambio completo: ~$0.05-0.06 (STT $0.003 + Claude ~$0.01 + TTS ~$0.04 + Twilio $0.01).

### 7.2 Llamada entrante (Fase 2)

1. Cliente toca "llamar" en WhatsApp. Twilio dispara webhook a `/voice`.
2. FastAPI valida firma, busca historial + lead por telefono, decide idioma y voz.
3. Responde TwiML `<Connect><ConversationRelay>` con `welcomeGreeting` natural ("Hola, habla Liam" en el idioma correcto).
4. Twilio abre el WebSocket. Por cada `prompt`: Claude streaming con historial completo -> tokens a Twilio -> el cliente escucha con ~1s de latencia total.
5. `interrupt`: el cliente hablo encima — se cancela el stream de Claude y se escucha lo nuevo.
6. Al terminar: se guarda en `memory.py` un resumen ("[Llamada de voz] cliente pregunto por X, quedamos en Y") para que el proximo chat tenga el contexto.
7. Escalacion: si el cliente pide hablar con una persona, el agente responde "te paso con Liam, te llama en un rato", corta con `{"type":"end"}` y dispara la notificacion de escalacion existente (`escalacion.py`).

### 7.3 Llamada saliente (Fase 2)

1. Trigger: Liam manda comando admin (`#llamar +972...`) o un flujo automatico (recordatorio de turno).
2. Calls API de Twilio crea la llamada WhatsApp (TwiML `<WhatsApp>` + mismo ConversationRelay).
3. El agente abre con contexto: "Hola, te llamo de Arzac Studio por el turno de manana" — el historial ya esta cargado.
4. Restriccion Meta: salientes requieren que el usuario haya dado permiso de llamada (se solicita via mensaje interactivo de WhatsApp). Implementar el pedido de permiso como template antes de la primera llamada saliente.

---

## 8. Voz humanizada: como se logra (no es solo el proveedor)

1. **Eleccion de voz:** en ElevenLabs usar una voz clonada o de libreria con tono calido y energia media. Una por idioma (la misma "persona" no puede sonar distinta entre audios). Guardar los voice_id en config.
2. **El texto manda:** la voz suena robotica cuando el texto es de folleto. El system prompt de voz debe pedir: frases cortas, contracciones, reacciones ("dale", "perfecto", "uh, entiendo"), numeros y horas en palabras, nada de listas.
3. **Prosodia ElevenLabs:** ajustar `stability` baja-media (0.35-0.5) y `similarity_boost` alto para que la voz tenga variacion emocional sin perder identidad. `style` moderado. Probar con frases reales.
4. **Latencia = humanidad en llamadas:** streaming token a token + Flash v2.5 (75ms) + Claude Haiku para turnos simples mantiene la conversacion fluida. Una pausa de 3 segundos delata al robot mas que cualquier acento.
5. **Notas de voz:** duracion 10-30 segundos maximo. Nadie manda audios de 2 minutos para confirmar un turno. Si la respuesta es larga, mejor audio corto + texto con el detalle.
6. **Imperfeccion controlada:** en notas de voz, arrancar a veces con "Hola, mira," o "Bueno, te cuento" — igual que las reglas de humanizacion del chat.

---

## 9. Costos totales y recomendacion final

### Por stack (llamadas, por minuto)

| Stack | $/min | Idiomas | Veredicto |
|---|---|---|---|
| **A: ConversationRelay + ElevenLabs + Google STT + Claude** | **$0.09-0.13** | Los 5 | **RECOMENDADO** — mejor voz, minimo codigo, Claude sigue siendo el cerebro |
| B: Media Streams + OpenAI Realtime | $0.16-0.36 | 5 con acento | Plan B — gran latencia pero caro y saca a Claude |
| B-mini: gpt-realtime-mini | $0.06-0.16 | 5 con acento | Solo si el costo de A no cierra |
| C: Media Streams + Deepgram (Aura+Nova) | ~$0.05 | Sin hebreo | Descartado para Israel |
| Vapi / Retell / Bland (todo-en-uno) | $0.09-0.31 reales | Segun config | Mas caros que armarlo con CR y agregan un vendor mas |

### Proyeccion mensual por cliente del SaaS

Supuestos conservadores para un negocio local (peluqueria/clinica):

| Uso mensual | Notas de voz (60 intercambios) | Llamadas (100 min) | Total voz |
|---|---|---|---|
| Costo | ~$3.50 | ~$13 | **~$16.50/mes** |

Contra el precio del plan (790 ILS ~ $210/mes), el costo de voz es ~8% del revenue: **viable para incluir en el plan actual o vender como add-on** (ej: +100 ILS/mes por "atencion por voz", margen ~75%).

### Recomendacion final

1. **Implementar Fase 1 (notas de voz) ya**: costo casi nulo ($3-4/mes por cliente), cero riesgo de arquitectura, y es una demo de venta brutal ("mandale un audio y te contesta con la voz de tu negocio").
2. **Pedir el onboarding de ConversationRelay en Twilio Console ahora** (tarda) y habilitar ElevenLabs en la cuenta.
3. **POC de Fase 2** cuando (1) este en produccion: una llamada de prueba con ElevenLabs en hebreo y espanol. Si el hebreo de ElevenLabs no convence, A/B contra Cartesia Sonic 3 y Google Chirp 3 HD.
4. Presupuestar **$0.13/min como techo** de llamadas hasta confirmar el bundling de ConversationRelay.

---

## 10. Pendientes de verificacion (antes de cerrar Fase 2)

1. Si los $0.07/min de ConversationRelay **incluyen** el costo de STT/TTS de los proveedores default, y si ElevenLabs lleva recargo. Confirmar en Twilio Console o con ventas.
2. Soporte de `he-IL` (y ar/ru) en la config de idioma de ConversationRelay.
3. Fee exacto de Meta para llamadas salientes a Israel (asumido "Rest of Middle East" $0.0127/min).
4. Calidad real del hebreo: A/B ElevenLabs Flash v2.5 vs Cartesia Sonic 3 con frases del negocio.
5. Flujo de permiso de llamada saliente de Meta (template interactivo) en la practica via Twilio.

## 11. Fuentes

- Twilio WhatsApp pricing (calling + media): twilio.com/en-us/whatsapp/pricing
- WhatsApp Business Calling GA: twilio.com/en-us/blog/products/launches/generally-available-whatsapp-business-calling-twilio-voice · docs: twilio.com/docs/voice/whatsapp-business-calling
- ConversationRelay: twilio.com/en-us/products/conversational-ai/conversationrelay · pricing: twilio.com/en-us/products/conversational-ai/pricing
- Twilio Voice Israel: twilio.com/en-us/voice/pricing/il · Media Streams: twilio.com/docs/voice/media-streams
- ElevenLabs: elevenlabs.io/pricing/api · idiomas: help.elevenlabs.io (13313366263441)
- Cartesia: cartesia.ai/pricing · hebreo: cartesia.ai/languages/hebrew
- Google TTS: cloud.google.com/text-to-speech/pricing · Chirp 3 HD: docs.cloud.google.com/text-to-speech/docs/chirp3-hd
- Google STT: cloud.google.com/speech-to-text/pricing
- Azure Speech: azure.microsoft.com/en-us/pricing/details/speech/ (Neural HD $22/1M desde marzo 2026)
- Amazon Polly: aws.amazon.com/polly/pricing/ · idiomas (sin hebreo): docs.aws.amazon.com/polly/latest/dg/supported-languages.html
- Deepgram: deepgram.com/pricing · idiomas: developers.deepgram.com/docs/models-languages-overview
- OpenAI: openai.com/api/pricing/ · platform.openai.com/docs/pricing
- AssemblyAI: assemblyai.com/pricing
- Costos reales OpenAI Realtime: eesel.ai/blog/gpt-realtime-mini-pricing

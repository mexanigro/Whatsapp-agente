# TWILIO-PLAN.md — SMS + Voice para Arzac Studio

> Investigacion y plan de integracion para agregar SMS notifications y Voice AI agent
> al producto de Arzac Studio. Pricing para Israel (+972).
>
> Fecha: 2026-06-01

---

## 1. Pricing Twilio para Israel

### 1.1 SMS Israel

| Concepto | Costo |
|---|---|
| SMS saliente a Israel | **$0.2575** / segmento |
| SMS entrante desde Israel | **$0.0075** / segmento |
| Numero movil israeli | **$15.00** / mes |
| Alphanumeric Sender ID | **Gratis** (solo saliente, no recibe) |

> Fuente: https://www.twilio.com/en-us/sms/pricing/il

**Nota importante:** SMS a Israel es MUY caro. Un SMS de recordatorio de cita cuesta ~$0.26.
Con 10 recordatorios/mes = $2.58 solo en mensajes + $15 del numero = $17.58/mes. Inviable con $10/mes.

### 1.2 Voice Israel

| Concepto | Costo |
|---|---|
| Llamada saliente a fijo Israel | **$0.0659** / min |
| Llamada saliente a fijo (desde numero Israel) | **$0.0294** / min |
| Llamada saliente a movil Israel | **$0.1868** / min |
| Llamada saliente a movil (desde numero Israel) | **$0.0646** / min |
| Llamada entrante - numero local | **$0.0107** / min |
| Llamada entrante - numero movil | **$0.0350** / min |
| Llamada entrante - toll-free | **$0.1344** / min |

| Numero telefonico | Costo mensual |
|---|---|
| Local | **$5.50** / mes |
| Movil | **$15.00** / mes |
| Toll-free | **$22.00** / mes |

| Feature Voice AI | Costo |
|---|---|
| **ConversationRelay** | **$0.07** / min |
| Media Streams (WebSocket raw audio) | $0.004 / min |
| Gather Speech Recognition | $0.018-0.025 / uso |

> Fuente: https://www.twilio.com/en-us/voice/pricing/il

### 1.3 WhatsApp via Twilio

| Concepto | Meta fee (Israel) | Twilio fee | Total |
|---|---|---|---|
| Service / free-form (dentro de 24h window) | **$0.00** | $0.005 | **$0.005** |
| Utility template (dentro de 24h window) | **$0.00** | $0.005 | **$0.005** |
| Utility template (fuera de window) | $0.0053 | $0.005 | **$0.0103** |
| Authentication template | $0.0053 | $0.005 | **$0.0103** |
| Marketing template | $0.0353 | $0.005 | **$0.0403** |

> **Service window**: 24 horas despues de que el cliente envia un mensaje. Dentro de esa
> ventana, los mensajes de utilidad y free-form son gratis de parte de Meta.
> Solo se paga el fee de Twilio ($0.005/msg).
>
> **Cambio de pricing Meta (Julio 2025):** Meta cambio de pricing por conversacion a
> pricing por mensaje template. Costos subieron ~20-40% para la mayoria de negocios.
> Israel esta en tier internacional estandar sin surcharges especiales.
>
> **Alternativa:** Usar Meta Cloud API directamente (provider `meta.py` ya existe en el repo)
> evita el markup de Twilio de $0.005/msg. Con 1,000 conversaciones de servicio gratis/mes.

> Fuente: https://www.twilio.com/en-us/whatsapp/pricing

### 1.4 WhatsApp Business Calling (nuevo)

| Concepto | Costo |
|---|---|
| Twilio Channel API (in/out) | **$0.005** / min |
| Meta connectivity - Israel inbound | **$0.00** / min |
| Meta connectivity - Israel outbound | **$0.0127** / min |

---

## 2. Analisis de presupuesto: $10 USD/mes por cliente

### 2.1 Escenario SMS puro (INVIABLE)

```
Numero movil:    $15.00/mes
10 SMS salientes: $2.58
TOTAL:           $17.58/mes  ← MUY POR ENCIMA del presupuesto
```

SMS a Israel es prohibitivamente caro a $0.2575/mensaje. **No recomendado.**

### 2.2 Escenario Voice AI puro (JUSTO)

```
Numero local:    $5.50/mes
Presupuesto restante: $4.50
ConversationRelay:    $0.07/min
Voice inbound local:  $0.0107/min
Total por minuto:     $0.0807/min
Minutos disponibles:  $4.50 / $0.0807 = ~55 minutos
Llamadas de 3 min:    ~18 llamadas/mes
```

**Posible pero ajustado.** 18 llamadas de 3 minutos por mes. No incluye costo de Claude API (~$0.01-0.03/llamada).

### 2.3 Escenario WhatsApp puro (EXCELENTE)

```
Sin numero extra (usa el mismo de WhatsApp)
$0.005/mensaje Twilio
Meta fee: $0 (en service window)
Mensajes disponibles: $10 / $0.005 = 2,000 mensajes/mes
```

**Lejos el mas economico.** Ya lo tenemos implementado.

### 2.4 Escenario recomendado: WhatsApp + Voice Light

```
Numero local Israel:     $5.50/mes
WhatsApp (ya existente):  $0.00 extra en numero
SMS:                      $0.00 (NO usar SMS, usar WhatsApp)

Presupuesto restante:    $4.50/mes

Distribucion:
- Recordatorios de cita via WhatsApp: ~$0.50 (100 msgs x $0.005)
- Voice AI (ConversationRelay):       ~$4.00 (50 min = ~16 llamadas de 3 min)
                                      ──────
TOTAL:                                $10.00/mes
```

### 2.5 Conclusion de presupuesto

| Canal | Recomendacion | Por que |
|---|---|---|
| **SMS** | NO usar | $0.26/msg es inviable. Usar WhatsApp templates en su lugar |
| **WhatsApp** | CANAL PRINCIPAL | $0.005/msg, ya implementado, mas popular en Israel |
| **Voice AI** | OPCIONAL, light | $0.08/min total, viable para 15-20 llamadas cortas/mes |
| **Recordatorios** | VIA WHATSAPP | Utility templates gratis (Meta) + $0.005 (Twilio) |

---

## 3. Plan de implementacion

### 3.1 SMS Notifications → Reemplazar con WhatsApp Templates

En lugar de SMS (caro), usar **WhatsApp Utility Templates** para:
- Recordatorios de cita (24h antes)
- Confirmaciones de reserva
- Notificaciones de cambio/cancelacion

**Ventaja:** Los utility templates dentro del service window son gratis de parte de Meta.
Solo se paga $0.005 Twilio fee por mensaje.

**Implementacion:**

```
nichos-hub (CRM)
  → Evento: nueva cita / recordatorio
  → POST a whatsapp-agentkit /api/notify
  → agentkit envia WhatsApp template via Twilio
```

Archivos a crear/modificar:
1. `agent/notifications.py` — Logica de envio de templates
2. `agent/main.py` — Nuevo endpoint `/api/notify`
3. Templates en Twilio Console (requiere aprobacion de Meta)

**Templates necesarios:**
- `appointment_reminder`: "Hola {{1}}, te recordamos tu turno de {{2}} manana {{3}} a las {{4}}. Para cancelar, responde CANCELAR."
- `appointment_confirmation`: "Turno confirmado: {{1}} el {{2}} a las {{3}} con {{4}}."
- `appointment_cancelled`: "Tu turno de {{1}} del {{2}} fue cancelado."

### 3.2 Voice AI Agent con ConversationRelay

#### Que es ConversationRelay

ConversationRelay es un servicio de Twilio que maneja la infraestructura de voz para agentes IA:
- **STT** (Speech-to-Text): convierte la voz del llamante a texto
- **TTS** (Text-to-Speech): convierte la respuesta del agente a voz
- **WebSocket**: comunica en real-time con tu app
- **Session management**: maneja la sesion de la llamada

Tu solo provees la logica de IA (Claude API). Twilio hace el resto.

#### Como funciona

```
Cliente llama al numero del negocio
  → Twilio recibe la llamada
  → TwiML responde con <Connect><ConversationRelay>
  → Twilio abre WebSocket a tu server
  → Cliente habla → Twilio STT → texto al WebSocket
  → Tu app recibe texto → Claude API genera respuesta
  → Tu app envia texto al WebSocket → Twilio TTS → cliente escucha
  → Loop hasta que termine la llamada
```

#### Puede hacer todo lo que hace el WhatsApp agent?

**SI**, con las mismas herramientas:
- Consultar disponibilidad de turnos
- Reservar turnos
- Cancelar turnos
- Responder preguntas sobre el negocio
- Detectar idioma (hebreo, ingles, ruso)

El voice agent usaria el mismo `brain.py` y `appointments.py` que ya existen.
Solo cambia el canal de entrada/salida.

#### Costo por llamada tipica (3 minutos)

```
ConversationRelay:  $0.07 x 3 = $0.210
Voice inbound:      $0.0107 x 3 = $0.032
Claude API (~500 in/out tokens): ~$0.015
TOTAL por llamada:  ~$0.26
```

Con $4.50/mes de budget para voice: ~17 llamadas de 3 min.

#### Implementacion

Archivos a crear:
1. `agent/voice.py` — WebSocket handler para ConversationRelay
2. `agent/main.py` — Endpoints `/voice/incoming` (TwiML) + `/voice/ws` (WebSocket)

```python
# agent/voice.py — Voice AI agent via ConversationRelay (concepto)

# Endpoint TwiML para llamadas entrantes:
# <Response>
#   <Connect>
#     <ConversationRelay
#       url="wss://tu-app.up.railway.app/voice/ws"
#       welcomeGreeting="Hola, bienvenido a [NEGOCIO]. En que puedo ayudarte?"
#       language="he-IL"
#       ttsProvider="google"
#       transcriptionProvider="deepgram"
#       interruptible="true"
#     />
#   </Connect>
# </Response>

# WebSocket handler:
# 1. Recibe mensaje tipo "prompt" con texto transcrito
# 2. Llama a generar_respuesta() (mismo brain.py)
# 3. Envia respuesta como JSON: {"type": "text", "token": "respuesta aqui"}
# 4. Maneja eventos: setup, prompt, interrupt, dtmf, end
```

**Dependencias nuevas:**
- `websockets>=12.0` (ya podria usar Starlette WebSocket nativo de FastAPI)

**Configuracion Twilio:**
- Comprar numero local israelí ($5.50/mes)
- Configurar Voice webhook: `https://tu-app.up.railway.app/voice/incoming`

### 3.3 Twilio Agent Connect (TAC) — Alternativa avanzada

Twilio tiene un SDK llamado **Twilio Agent Connect (TAC)** que simplifica la integracion:
- SDK Python que conecta tu LLM a Voice + SMS + WhatsApp
- Maneja WebSocket, routing, y session management
- Integra con Conversation Memory (perfil del cliente persistente)
- Soporta escalacion a agente humano

**Ventaja:** Unifica todos los canales (WhatsApp, Voice, SMS) en un solo handler.
**Desventaja:** Agrega dependencia en el SDK de TAC, mas complejo que ConversationRelay puro.

Para la V1, recomiendo ConversationRelay directo. TAC para V2 si se necesita unificar canales.

> Fuente: https://www.twilio.com/docs/conversations/agent-connect

---

## 4. Roadmap de implementacion

### Fase 1 — WhatsApp Templates para notificaciones (1-2 dias)

1. Crear templates en Twilio Console (necesitan aprobacion de Meta, ~24-48h)
2. Implementar `agent/notifications.py` con envio de templates
3. Agregar endpoint `/api/notify` en `main.py`
4. nichos-hub envia eventos de citas a este endpoint

**Costo adicional por cliente:** ~$0.50/mes (100 notificaciones x $0.005)

### Fase 2 — Voice AI Agent con ConversationRelay (3-5 dias)

1. Comprar numero local israelí en Twilio ($5.50/mes por cliente)
2. Implementar `agent/voice.py` con WebSocket handler
3. Agregar endpoints `/voice/incoming` + `/voice/ws` en `main.py`
4. Reutilizar `brain.py` y `appointments.py` existentes
5. Configurar voice webhook en Twilio Console
6. Testear con llamadas reales

**Costo adicional por cliente:** $5.50/mes (numero) + ~$4/mes (usage) = ~$9.50/mes

### Fase 3 — Dashboard de costos unificado (1 dia)

1. Extender `memory.py` para trackear costos de WhatsApp templates y Voice
2. Agregar comando admin `#costos` con desglose por canal
3. Alertas cuando un cliente se acerca al limite de $10/mes

---

## 5. Resumen ejecutivo

| Canal | Estado | Costo/mes | Recomendacion |
|---|---|---|---|
| WhatsApp IA | Ya funciona | ~$1-3 (API Claude) | Mantener como canal principal |
| WhatsApp Templates | Por implementar | ~$0.50 | Reemplaza SMS para notificaciones |
| Voice AI | Por implementar | ~$9.50 | Opcional, solo para clientes que lo necesiten |
| SMS | No implementar | $17+ | Inviable en Israel, usar WhatsApp |

**Conclusion:** Con $10/mes por cliente, la estrategia optima es:
1. **WhatsApp como canal principal** (ya implementado, baratisimo)
2. **WhatsApp templates para notificaciones** (reemplaza SMS, ~$0.50/mes)
3. **Voice AI opcional** como add-on premium (consume casi todo el budget solo)

Si un cliente necesita Voice, considerar cobrar extra ($5-10/mes adicionales) porque
el numero telefonico solo cuesta $5.50/mes antes de sumar usage.

---

## 6. Dato critico: costo del numero telefonico

**El numero movil israelí para WhatsApp cuesta $15/mes**, que por si solo supera el budget
de $10/mes por cliente. Opciones:

1. **Compartir numero via sub-accounts de Twilio**: Un numero para multiples clientes.
   Costo repartido: $15 / N clientes.
2. **Usar Meta Cloud API directo**: El provider `meta.py` ya existe en el repo. Meta ofrece
   1,000 conversaciones de servicio gratis/mes y no cobra markup de Twilio.
3. **Ajustar pricing del SaaS**: Si el plan es 790 NIS/mes, el costo de Twilio (~$17-20/mes)
   es ~5-6% del revenue por cliente. Viable como costo operativo.

---

## 7. Fuentes

- SMS Israel: https://www.twilio.com/en-us/sms/pricing/il
- Voice Israel: https://www.twilio.com/en-us/voice/pricing/il
- WhatsApp: https://www.twilio.com/en-us/whatsapp/pricing
- ConversationRelay: https://www.twilio.com/docs/voice/conversationrelay
- Twilio Agent Connect: https://www.twilio.com/docs/conversations/agent-connect
- ConversationRelay TwiML: https://www.twilio.com/docs/voice/conversationrelay/conversationrelay-noun
- Claude + ConversationRelay tutorial: https://www.twilio.com/en-us/blog/integrate-anthropic-twilio-voice-using-conversationrelay
- Meta WhatsApp pricing rates: https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing
- Twilio changelog pricing update: https://www.twilio.com/en-us/changelog/meta-is-updating-whatsapp-pricing-on-july-1--2025

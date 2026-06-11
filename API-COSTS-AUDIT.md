# Audit de Costos de APIs — WhatsApp AgentKit

Fecha: 2026-06-06
Scope: todas las llamadas a APIs pagas en el agente de WhatsApp de Arzac Studio.

---

## Resumen ejecutivo

| Servicio | Costo por interaccion tipica | Costo mensual estimado (100 conv/mes) |
|----------|------------------------------|---------------------------------------|
| Claude API (Anthropic) | $0.005–$0.025 | $1.50–$5.00 |
| Twilio WhatsApp (mensajes) | $0.015–$0.060 | $4.50–$12.00 |
| Twilio Typing Indicator | $0.00 (gratis) | $0.00 |
| Nichos-hub HTTP (turnos) | $0.00 (interno) | $0.00 |
| Redis (rate limit) | $0.00 (opcional, self-hosted) | $0–$5 si Railway addon |
| Firebase/Google Calendar | $0.00 (no se consume desde aca) | $0.00 |
| **Total por interaccion** | **$0.02–$0.085** | **$6–$17** |

---

## 1. Anthropic Claude API

### Donde se consume

| Archivo | Linea | Que hace |
|---------|-------|----------|
| `agent/brain.py` | 13 | `client = AsyncAnthropic(...)` — instancia el cliente |
| `agent/brain.py` | 14 | `CLAUDE_MODEL = "claude-sonnet-4-6"` (default, overrideable via env) |
| `agent/brain.py` | 276–286 | `client.messages.create(...)` — llamada real a la API |
| `agent/brain.py` | 279 | `max_tokens=1024` — cap de output |

### Modelo y precios

Modelo default: **claude-sonnet-4-6** (configurable via `CLAUDE_MODEL` env var).

Precios registrados en `agent/memory.py:180-183`:

| Modelo | Input | Output | Cache read | Cache write |
|--------|-------|--------|------------|-------------|
| claude-sonnet-4-6 | $3.00/MTok | $15.00/MTok | $0.30/MTok | $3.75/MTok |
| claude-haiku-4-5 | $0.80/MTok | $4.00/MTok | $0.08/MTok | $1.00/MTok |

### Tokens por interaccion (estimacion)

**System prompt:** ~5,500 chars = ~1,500 tokens base. Se envia con `cache_control: ephemeral` (`brain.py:216`), asi que la primera llamada paga cache_write y las siguientes pagan cache_read (5 min TTL).

**Historial:** `memory.py:94-112` limita a 10 mensajes y ~6,000 chars (~1,500 tokens). Se acumula con la conversacion.

**Bloques extras del system prompt:**
- Lead context (`brain.py:220-224`): ~50 tokens
- Cliente recurrente (`brain.py:229-240`): ~30 tokens
- Calendar desconectado (`brain.py:246-250`): ~50 tokens

**Tool definitions:** 4 herramientas de turnos (`brain.py:62-138`): ~500 tokens. Solo se envian si el mensaje matchea keywords de turnos.

**Estimacion por mensaje (sin tools):**

| Componente | Tokens | Tipo |
|------------|--------|------|
| System prompt (cache hit) | ~1,500 | cache_read |
| Historial (promedio 5 mensajes) | ~800 | input |
| Mensaje nuevo | ~50 | input |
| Contexto lead/recurrencia | ~50 | input |
| **Total input** | **~2,400** | |
| Respuesta (1-3 fragmentos |||) | ~150–300 | output |
| **Total output** | **~200** | |

**Costo por mensaje (sin tools, con cache hit):**
- Input (no-cache): 850 tokens x $3/M = $0.0026
- Cache read: 1,500 tokens x $0.30/M = $0.00045
- Output: 200 tokens x $15/M = $0.003
- **Total: ~$0.006 por mensaje**

**Costo por mensaje (con tools, caso booking):**

El loop de tool-use puede hacer hasta 3 iteraciones (`brain.py:266`). Caso tipico de un booking completo:
1. Llamada 1: Claude decide usar `obtener_config_turnos` → respuesta del tool (~300 tokens)
2. Llamada 2: Claude decide usar `consultar_disponibilidad` → respuesta del tool (~200 tokens)
3. Llamada 3: Claude responde al usuario con horarios

Cada iteracion acumula los mensajes anteriores + tool results.

| Iteracion | Input tokens (aprox) | Output tokens |
|-----------|---------------------|---------------|
| 1 | ~2,900 (base + tools schema) | ~100 (tool_use) |
| 2 | ~3,500 (+ assistant + tool_result) | ~100 (tool_use) |
| 3 | ~4,000 (+ todo) | ~200 (respuesta final) |
| **Total** | **~10,400** | **~400** |

**Costo mensaje con tools (con cache):**
- Cache read: 1,500 x 3 = 4,500 tokens x $0.30/M = $0.00135
- Input (no-cache): ~5,900 tokens x $3/M = $0.0177
- Output: 400 tokens x $15/M = $0.006
- **Total: ~$0.025 por interaccion con booking**

### Prompt caching

**Estado: IMPLEMENTADO.** `brain.py:213-217` usa `cache_control: {"type": "ephemeral"}` en el system prompt. Esto es correcto.

**Efectividad:** El cache de Anthropic tiene TTL de 5 minutos. Si un cliente escribe varios mensajes seguidos (tipico en conversacion), los mensajes 2+ pagan solo cache_read ($0.30/M vs $3.00/M para input, y $3.75/M para cache_write). **Ahorra ~90% en el system prompt para mensajes consecutivos.**

**Problema potencial:** Solo el primer bloque del system prompt tiene cache_control. Los bloques de lead context, recurrencia y calendar status NO lo tienen. Esto esta bien porque son dinamicos (cambian por conversacion), pero el sistema si cachea el bloque grande correctamente.

### Controles de costo existentes

| Control | Archivo:Linea | Que hace |
|---------|---------------|----------|
| Cap diario | `main.py:31,256-259` | `COSTO_DIARIO_MAXIMO = $2.00` default, corta si se excede |
| Registro por request | `brain.py:356-362` | Calcula y registra costo en SQLite |
| Fuera de horario | `main.py:262-276` | Responde mensaje fijo, 0 tokens Claude |
| Rate limit | `main.py:247-254` | 5/min, 30/hora por telefono — evita spam |
| Max tokens output | `brain.py:279` | `max_tokens=1024` — limita output |
| Historial recortado | `memory.py:104-111` | Max 6,000 chars (~1,500 tokens) de historial |
| Max iteraciones tools | `brain.py:265` | Max 3 iteraciones de tool-use |

### Optimizaciones posibles

1. **Bajar a Haiku para mensajes simples** (~70% mas barato). Si el mensaje es un "hola" o "gracias", no necesita Sonnet. Ahorro estimado: 30-50% del gasto mensual si se implementa routing inteligente.

2. **El historial podria ser mas agresivo.** 6,000 chars es generoso. Para conversaciones de ventas cortas, 3,000 chars serian suficientes. Ahorro: ~400 tokens input por request.

3. **Tools schema se envia completo en cada iteracion.** Los ~500 tokens de tool definitions se repiten en cada iteracion del loop. Esto es inevitable con la API actual.

---

## 2. Twilio WhatsApp

### Donde se consume

| Archivo | Linea | Metodo | Que hace |
|---------|-------|--------|----------|
| `providers/twilio.py` | 64-80 | `_enviar_texto()` | Envia un mensaje de texto via API REST |
| `providers/twilio.py` | 82-83 | `enviar_mensaje()` | Wrapper de `_enviar_texto` |
| `providers/twilio.py` | 85-108 | `enviar_typing_indicator()` | Envia "escribiendo..." via `/v2/Indicators/Typing.json` |
| `providers/twilio.py` | 110-131 | `enviar_template()` | Envia template aprobado via ContentSid |

### Precios Twilio WhatsApp (junio 2026)

Los precios dependen del tipo de conversacion (Meta pricing pass-through):

| Tipo conversacion | Costo por conversacion (24h window) | Quien la inicia |
|-------------------|-------------------------------------|-----------------|
| Service | ~$0.005–$0.008 (depende pais) | Negocio responde a cliente |
| Marketing | ~$0.015–$0.025 | Negocio inicia (templates) |
| Utility | ~$0.005–$0.010 | Templates transaccionales |
| Authentication | ~$0.003–$0.006 | Templates de OTP |

**PLUS Twilio markup:** Twilio cobra $0.005 por mensaje enviado (outbound) ademas del costo de Meta.

### Mensajes por interaccion normal

**El splitting de humanize.py multiplica los mensajes Twilio.**

`humanize.py:11-16` divide la respuesta del LLM por el separador `|||`. El system prompt (`prompts.yaml:37-39`) instruye al agente a dividir en "2 o 3 mensajes cortos" y "nunca mas de 3 fragmentos".

**Flujo de envio por respuesta** (`main.py:117-136`):

| Accion | Mensajes Twilio | Costo Twilio |
|--------|----------------|--------------|
| Typing indicator pre-fragmento 1 | 1 API call (gratis, /v2/Indicators/) | $0.00 |
| Fragmento 1 | 1 mensaje | $0.005 |
| Typing indicator pre-fragmento 2 | 1 API call (gratis) | $0.00 |
| Fragmento 2 | 1 mensaje | $0.005 |
| Typing indicator pre-fragmento 3 (si hay) | 1 API call (gratis) | $0.00 |
| Fragmento 3 (si hay) | 1 mensaje | $0.005 |

**Promedio por respuesta: 2.2 mensajes Twilio outbound** (basado en la instruccion de 2-3 fragmentos).

**Costo Twilio por interaccion tipica:**
- 1 conversacion service (24h window): ~$0.008 (Israel)
- 2.2 mensajes outbound x $0.005 markup: $0.011
- **Total: ~$0.019 por interaccion dentro de window**
- Si es la primera interaccion del dia: +$0.008 por abrir conversacion

**Typing indicator:** `providers/twilio.py:85-108` llama a `messaging.twilio.com/v2/Indicators/Typing.json`. Esta API es **gratuita** (no crea mensajes). Se llama 1 vez por fragmento, y 2 veces si el delay > 25s (`main.py:129-131`). Promedio: ~3 calls por respuesta. **Costo: $0.00.**

### Notificaciones y templates

| Flujo | Archivo:Linea | Mensajes extra | Costo |
|-------|---------------|----------------|-------|
| Escalacion a admin | `escalacion.py:135` | 1 msg a admin | $0.005 markup |
| Escalacion aviso cliente | `main.py:232-235` | 1 msg al cliente | $0.005 markup |
| Confirmacion turno (cliente) | `notifications.py:175-197` | 1 msg o template | $0.005–$0.015 |
| Confirmacion turno (admin) | `main.py:376-386` | 1 msg o template | $0.005–$0.015 |
| Confirmacion turno (staff) | `main.py:392-400` | 1 msg | $0.005 |
| Fuera de horario | `main.py:265` | 1 msg fijo | $0.005 |
| Nuevo lead (admin) | `notifications.py:99-124` | 1 msg por admin | $0.005 |
| Template desde nichos-hub | `main.py:610-611` | 1 template | $0.005–$0.015 |

### Seguimientos automaticos (mensajes extra por booking)

| Seguimiento | Cuando | Archivo:Linea | Mensaje | Costo |
|-------------|--------|---------------|---------|-------|
| Recordatorio 24h pre-turno | 24h antes | `seguimiento.py:256-270` | 1 template/msg | $0.005–$0.015 |
| Review post-servicio | 4h despues | `seguimiento.py:273-281` | 1 template/msg | $0.005–$0.015 |
| Follow-up lead 24h | 24h sin respuesta | `seguimiento.py:235-239` | 1 template/msg | $0.005–$0.015 |

**Mensajes extra por booking exitoso: 2-3 mensajes automaticos** (confirmacion + recordatorio + review). Costo extra: $0.015–$0.045.

**Follow-up de lead:** Se programa si hay lead asociado (`main.py:170-174`). Se cancela si el cliente responde (`main.py:215-218`). Smart: no manda si hubo respuesta reciente (`seguimiento.py:179-180`). Costo: $0.005-$0.015 solo si se dispara.

### Optimizacion potencial — Splitting

**El splitting es el multiplicador principal de costo Twilio.** Cada respuesta genera 2-3 mensajes en lugar de 1.

| Escenario | Msgs Twilio | Costo markup |
|-----------|------------|--------------|
| Sin splitting (1 msg) | 1 | $0.005 |
| Con splitting (promedio 2.2) | 2.2 | $0.011 |
| **Costo extra mensual (100 conv x 4 msgs):** | +480 msgs | **+$2.40/mes** |

El splitting es una feature de UX valiosa (se siente humano). Pero si el costo importa mas que la UX, se puede desactivar o reducir a max 2 fragmentos. **Ahorro estimado: ~$1-2/mes por cada 100 conversaciones.**

---

## 3. Nichos-hub (HTTP interno)

### Donde se consume

| Archivo | Linea | Endpoint | Que hace |
|---------|-------|----------|----------|
| `appointments.py:49` | GET | `/api/appointments/config` | Lista servicios/staff |
| `appointments.py:60-61` | GET | `/api/appointments/available` | Disponibilidad |
| `appointments.py:72-79` | POST | `/api/appointments/book` | Reservar turno |
| `appointments.py:87-88` | PATCH | `/api/appointments/{id}/cancel` | Cancelar turno |
| `pausa.py:175-176` | GET | `/api/agent/config` | Sync config remota |

**Costo: $0.00.** Son llamadas HTTP a la propia infra de nichos-hub en Railway. No hay API paga involucrada. El costo es solo el compute de Railway que ya se paga.

**Google Calendar:** Las llamadas a Google Calendar API las hace **nichos-hub** (no este agente). Este agente solo recibe webhooks de nichos-hub avisando si Calendar se conecto/desconecto (`main.py:567-599`). **Costo en este repo: $0.00.**

---

## 4. Firebase / Firestore

**No se consume desde este repo.** Firebase/Firestore se usa en nichos-hub y master-template (otros repos del ecosistema). Este agente usa **SQLite local** (`aiosqlite`) para todo su storage.

Las unicas referencias a Firebase en este repo son en docs (`AUTOMATION-FLOWS.md`, `knowledge/arzac-studio-info.md`) describiendo la arquitectura general del ecosistema.

**Costo: $0.00.**

---

## 5. Redis (opcional)

### Donde se consume

| Archivo | Linea | Que hace |
|---------|-------|----------|
| `rate_limit.py:26-33` | Inicializa si `REDIS_URL` esta en env | Rate limiting |
| `rate_limit.py:75-109` | `_verificar_redis()` | Sorted sets para rate limit |

**Estado actual:** Probablemente **no activo** (fallback a in-memory). Solo se usa si `REDIS_URL` esta configurada en Railway.

**Costo si activo:** Depende del plan de Redis en Railway (~$5/mes para el plan starter). Las operaciones son minimas (2 `ZADD` + 2 `ZREMRANGEBYSCORE` + 2 `ZCARD` + 2 `EXPIRE` por mensaje) — no genera costo por operacion.

---

## 6. Analytics (local)

`agent/analytics.py` registra eventos en SQLite local. **No llama a ninguna API externa.** No hay Google Analytics, Mixpanel, ni ningun servicio de tracking externo.

**Costo: $0.00.**

---

## 7. Tabla consolidada por escenario

### Escenario A: Mensaje simple de un lead (sin tools)

| Paso | Servicio | Costo |
|------|----------|-------|
| Recibir webhook | Twilio inbound | incluido en conversacion |
| Claude API (1 call, cache hit) | Anthropic | $0.006 |
| Enviar 2 fragmentos WhatsApp | Twilio | $0.010 |
| Typing indicators x2 | Twilio | $0.000 |
| Analytics (SQLite) | local | $0.000 |
| **Total** | | **~$0.016** |

### Escenario B: Booking completo (con tools, 3 iteraciones Claude)

| Paso | Servicio | Costo |
|------|----------|-------|
| Claude API (3 calls, tools) | Anthropic | $0.025 |
| Consulta disponibilidad | nichos-hub HTTP | $0.000 |
| Reservar turno | nichos-hub HTTP | $0.000 |
| Enviar 2 fragmentos WhatsApp | Twilio | $0.010 |
| Confirmacion al cliente | Twilio | $0.005 |
| Notificacion al admin | Twilio | $0.005 |
| **Total inmediato** | | **~$0.045** |
| Recordatorio 24h (futuro) | Twilio | $0.005-$0.015 |
| Review post-turno (futuro) | Twilio | $0.005-$0.015 |
| **Total con follow-ups** | | **~$0.060-$0.075** |

### Escenario C: Fuera de horario

| Paso | Servicio | Costo |
|------|----------|-------|
| Mensaje fijo multi-idioma | Twilio | $0.005 |
| Claude API | — | $0.000 (no se llama) |
| **Total** | | **~$0.005** |

### Escenario D: Escalacion urgente

| Paso | Servicio | Costo |
|------|----------|-------|
| Detectar urgencia (local) | — | $0.000 |
| Aviso al cliente | Twilio | $0.005 |
| Notificacion al admin | Twilio | $0.005 |
| Claude API | — | $0.000 (no se llama) |
| **Total** | | **~$0.010** |

---

## 8. Desperdicios y oportunidades de ahorro

### Confirmados (ya optimizado)

| Optimizacion | Estado | Archivo |
|-------------|--------|---------|
| Prompt caching (ephemeral) | IMPLEMENTADO | `brain.py:216` |
| Fuera de horario sin LLM | IMPLEMENTADO | `main.py:262-276` |
| Rate limiting | IMPLEMENTADO | `rate_limit.py` |
| Cap diario de costo | IMPLEMENTADO | `main.py:256-259` |
| Deduplicacion de mensajes | IMPLEMENTADO | `main.py:193-197` |
| Historial recortado por chars | IMPLEMENTADO | `memory.py:104-111` |
| Follow-ups inteligentes (skip si respondio) | IMPLEMENTADO | `seguimiento.py:179` |
| Tools solo si keyword match | IMPLEMENTADO | `brain.py:152-154,264` |
| Escalacion no duplica (1h cooldown) | IMPLEMENTADO | `escalacion.py:80-91` |

### Oportunidades de ahorro (no implementado)

| Optimizacion | Ahorro estimado | Esfuerzo | Detalle |
|-------------|-----------------|----------|---------|
| **Routing a Haiku para mensajes triviales** | 30-50% en Claude | Medio | Clasificar "hola"/"gracias"/"ok" y responder con Haiku ($0.80 input vs $3.00) |
| **Reducir max fragmentos a 2** | ~$1/mes por 100 conv | Bajo | Cambiar instruccion en prompts.yaml de "2 o 3" a "maximo 2" |
| **Reducir historial a 4,000 chars** | ~200 tokens/req | Bajo | Cambiar `memory.py:109` de 6000 a 4000 |
| **Batch de notificaciones** | Marginal | Alto | Agrupar notifs admin+staff+cliente en menos API calls a Twilio |
| **Reducir max_tokens a 512** | Marginal (solo paga output real) | Bajo | Solo protege contra respuestas largas inesperadas |

### Anti-desperdicio ya cubierto (del audit previo)

El audit anterior (`project_api_cost_audit.md`) identifico reintentos Twilio por delays. Esto ya se resolvio con:
- Deduplicacion en `main.py:193-197`
- Processing en background (`main.py:279`)
- Typing indicators para mantener session alive (`main.py:127-134`)

---

## 9. Proyeccion mensual por volumen

Asumiendo mix: 70% mensajes simples, 20% con tools, 10% fuera de horario. Promedio 4 mensajes por conversacion.

| Conversaciones/mes | Costo Claude | Costo Twilio | Total |
|--------------------|-------------|-------------|-------|
| 50 | $1.50 | $3.00 | **$4.50** |
| 100 | $3.00 | $6.00 | **$9.00** |
| 200 | $6.00 | $12.00 | **$18.00** |
| 500 | $15.00 | $30.00 | **$45.00** |

**Costo por cliente (multi-tenant):** Con el cap de $2/dia, un cliente individual no puede gastar mas de ~$60/mes en Claude. Twilio no tiene cap (cada mensaje se cobra).

---

## 10. Resumen final

**El agente esta bien optimizado.** Los controles de costo (cap diario, rate limit, fuera de horario sin LLM, dedup, prompt caching, tools condicionales) son solidos.

**Principal driver de costo:** Twilio > Claude. El splitting de mensajes (2.2x promedio) es el mayor multiplicador de costo Twilio, pero es una feature de UX intencional.

**No hay Firebase ni Google Calendar consumido desde este repo.** Todo el storage es SQLite local. Las interacciones con Calendar/Firestore las maneja nichos-hub.

**La unica API paga externa directa es Anthropic + Twilio.** Todo lo demas es HTTP interno o SQLite local.

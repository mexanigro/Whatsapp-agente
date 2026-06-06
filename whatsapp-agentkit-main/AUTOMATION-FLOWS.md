# AUTOMATION-FLOWS.md — Flujos automaticos end-to-end

> Documenta TODOS los flujos automaticos que recorren el ecosistema
> (master-template, nichos-hub, whatsapp-agentkit) y como se conectan.
>
> Para cada flujo: trigger, pasos, modulos involucrados, gaps pendientes.

---

## Mapa de responsabilidades

| Sistema | Responsabilidad |
|---|---|
| **master-template** | Web del cliente — capta leads via formulario, dispara webhooks a nichos-hub. |
| **nichos-hub** | CRM con Firestore — guarda leads/turnos, Google Calendar, llama a agentkit para notificar. |
| **whatsapp-agentkit** (este) | Conversaciones WhatsApp con IA + notificaciones, follow-ups, analytics. |

---

## Flujo 1 — Lead entra por la web

```
[Web del cliente] formulario contacto/booking
        |
        v
[nichos-hub] POST /api/leads (auth API key)
        |
        +---> Firestore: crear lead
        |
        +---> POST {AGENTKIT_URL}/webhook/lead
              (auth x-agent-secret)
              body: {clientId, nombre, telefono, email, mensaje, fuente, adminPhones}
                    |
                    v
        [agentkit] /webhook/lead
                    |
                    +---> notifications.notificar_nuevo_lead_admin(adminPhones, lead)
                    |     -> WhatsApp al dueno del negocio con datos del lead
                    |
                    +---> memory.guardar_lead(telefono, nombre+email)
                    |     -> Asocia telefono con nombre para enriquecer futuras conversaciones
                    |
                    +---> analytics.registrar_evento(lead_creado)
```

**Implementado:** endpoint `/webhook/lead` + `notificar_nuevo_lead_admin`.

**Pendiente (nichos-hub):** llamar a `/webhook/lead` desde el handler de creacion de leads.

**Confirmacion automatica al cliente:** si se quiere mandar WhatsApp al cliente del lead,
nichos-hub debe llamar a `/send-template` con `TWILIO_TEMPLATE_LEAD_FOLLOWUP` o similar.

---

## Flujo 2 — Cliente agenda turno por WhatsApp

```
[Cliente] envia mensaje "quiero turno mañana"
        |
        v
[agentkit] /webhook (Twilio)
        |
        +---> webhook_handler -> procesar_mensaje
        |
        +---> brain.generar_respuesta -> Claude detecta intencion turno
        |     -> usa herramienta `reservar_turno`
        |     -> appointments.reservar_turno (HTTP a nichos-hub)
        |
        v
[nichos-hub] POST /api/appointments/book
        |
        +---> Firestore: crear turno
        +---> Google Calendar: crear evento
        +---> POST {AGENTKIT_URL}/notify
              body: {
                clientId, type="appointment_booked",
                adminPhones, message, variables,
                staffPhones,         <-- NUEVO: notifica al profesional
                customerPhone,       <-- NUEVO: confirma al cliente
                customerMessage,
                appointment: {date, time, serviceName, staffName, businessName}
              }
                    |
                    v
        [agentkit] /notify
                    |
                    +---> Admin: WhatsApp con datos del turno
                    +---> Staff: WhatsApp con turno asignado (NUEVO)
                    +---> Cliente: confirmacion (NUEVO)
                    +---> seguimiento.programar_recordatorio_turno (24h pre) (NUEVO)
                    +---> seguimiento.programar_review_post_turno (4h post) (NUEVO)
                    +---> analytics.registrar_evento(turno_agendado)
```

**Implementado:**
- `reservar_turno` tool en brain.py + cliente HTTP en appointments.py (pre-existente)
- `/notify` extendido: staffPhones, customerPhone, appointment data
- Programacion automatica de recordatorio 24h y review post-turno

**Pendiente (nichos-hub):**
- En el handler de creacion de turno, incluir `staffPhones` (lookup en Firestore por staff_id)
- Incluir `customerPhone` y `appointment.{date,time,serviceName,staffName,businessName}` en `/notify`

---

## Flujo 3 — Cliente cancela turno

```
[Cliente o staff] cancela en CRM o por WhatsApp
        |
        v
[nichos-hub] PATCH /api/appointments/:id/cancel
        |
        +---> Firestore: marcar cancelado
        +---> Google Calendar: eliminar evento
        +---> POST {AGENTKIT_URL}/notify
              body: { type="appointment_cancelled", adminPhones, staffPhones,
                      customerPhone, appointment }
                    |
                    v
        [agentkit] /notify
                    |
                    +---> Notifica a todos (admin, staff, cliente)
                    +---> seguimiento.cancelar_pendientes (recordatorio + review)
                    +---> analytics.registrar_evento(turno_cancelado)
```

**Implementado:** cancelacion automatica de los follow-ups asociados.

**Pendiente (nichos-hub):** pasar el mismo `appointment.{date, time}` para que el dispatcher pueda cancelar el seguimiento correcto.

---

## Flujo 4 — Recordatorio 24h antes del turno

```
[cron externo] cada 5 min llama POST {AGENTKIT_URL}/tasks/seguimientos
        |
        v
[agentkit] /tasks/seguimientos
        |
        +---> seguimiento.disparar_pendientes
        |     -> obtiene filas de seguimientos_programados con programado_para <= now
        |     -> para TIPO_RECORDATORIO_TURNO: notifications.notificar_recordatorio_cliente
        |     -> marca como ejecutado
        +---> analytics.registrar_evento(recordatorio_enviado)
```

**Implementado:** tabla `seguimientos_programados`, dispatcher, helpers.

**Pendiente (infra Railway):** configurar cron job que llame el endpoint cada N minutos.
Opcion economica: usar Railway Cron Trigger nativo o un servicio externo (cron-job.org).

**Importante:** los templates de WhatsApp para recordatorios DEBEN estar aprobados por Meta
porque se envian fuera del service window (>24h sin contacto del cliente). Configurar en
Twilio Console y setear el SID en `TWILIO_TEMPLATE_APPT_REMINDER`.

---

## Flujo 5 — Follow-up post-servicio (review)

Identico al recordatorio, pero el seguimiento es de tipo `review_post_turno`
y se programa para X horas DESPUES del turno (default 4h).

Disparador unico: el mismo `/tasks/seguimientos`. Notification: `notificar_review_cliente`.

Template recomendado: `TWILIO_TEMPLATE_APPT_REVIEW`.

---

## Flujo 6 — Follow-up a lead que no responde

```
[Cliente] envia 1er mensaje
        |
        v
[agentkit] procesa, responde
        |
        +---> seguimiento.programar_followup_lead(telefono, lead_data, horas=24)
        |     (solo si telefono tiene lead asociado)
        |
        ... 24h sin nuevos mensajes ...
        |
[cron] /tasks/seguimientos
        |
        +---> Si _hubo_respuesta_reciente(): skip (cliente respondio, no molestar)
        +---> Sino: notificar_followup_lead_cliente
```

**Implementado:**
- Auto-programacion al responder a un lead (en `procesar_mensaje` y `_maybe_programar_followup`)
- Cancelacion automatica si el cliente vuelve a escribir (en webhook_handler)
- Check final de "ya respondio" antes de disparar (defensa en profundidad)

---

## Flujo 7 — Escalacion por urgencia / frustracion

```
[Cliente] envia "esto es un desastre, quiero hablar con humano"
        |
        v
[agentkit] webhook_handler
        |
        +---> escalacion.detectar_urgencia(texto) -> True, razones=[palabra:urgente, ...]
        |
        +---> escalacion.escalar(telefono, texto, razones)
        |     |
        |     +---> WhatsApp a ADMIN_PHONE_NUMBER con extracto + razones + contexto lead
        |     +---> pausa.guardar_pausa(30 min) — IA no responde
        |     +---> marca telefono como "escalado" 60min para evitar spam
        |
        +---> WhatsApp al cliente: "Recibido, te paso con alguien del equipo"
        |
        +---> analytics.registrar_evento(escalacion)
```

**Implementado.** Activar/desactivar con `ESCALACION_ACTIVA` en .env.

Palabras clave configurables en `agent/escalacion.py:PALABRAS_URGENCIA`.
Multi-idioma (es/en/he/ru/ar).

---

## Flujo 8 — Auto-respuesta fuera de horario

```
[Cliente] escribe a las 23:00 (negocio cerrado)
        |
        v
[agentkit] webhook_handler
        |
        +---> horario.esta_en_horario() -> False
        +---> horario.detectar_idioma_simple(texto)
        +---> horario.mensaje_fuera_horario(idioma)
        |     "Hola, gracias por escribir. Ahora estoy fuera de horario.
        |      Te respondo cuando arranque mas tarde (en aprox 8h)."
        +---> proveedor.enviar_mensaje(...)
        +---> analytics.registrar_evento(fuera_horario)
        +---> (no se llama a Claude API — ahorra tokens)
```

**Implementado.** Lee horario del `config/business.yaml`. Activar/desactivar con
`AUTO_REPLY_FUERA_HORARIO=true|false`.

TZ del negocio configurable con `BUSINESS_TIMEZONE` (default `Asia/Jerusalem`).

---

## Flujo 9 — Cliente recurrente (no se presenta de nuevo)

Cuando el cliente ya tiene historial previo (3+ mensajes o turno previo en los ultimos 90 dias),
brain.py inyecta un bloque extra al system prompt:

> "Esta persona ya interactuo antes (15 mensajes previos, 2 turnos agendados).
> Saludala con familiaridad, no te presentes de nuevo."

Esto evita que el agente vuelva a presentarse y se sienta robotico para clientes habituales.

**Implementado** en `brain.py` usando `analytics.es_cliente_recurrente`.

---

## Flujo 10 — Comandos admin via WhatsApp

Pre-existente, ampliado con `#stats` y `#seguimientos`:

| Comando | Efecto |
|---------|--------|
| `#pausa` / `#pausa 2h` / `#pausa 45m` | Pausa IA |
| `#volver` | Reactiva IA |
| `#estado` | Muestra estado actual |
| `#lead +972XX Nombre` | Registra lead |
| `#leads` | Lista leads |
| `#costo` | Costos API hoy + semana |
| `#stats` / `#metricas` (NUEVO) | Tasas de conversion ultimos 30d |
| `#seguimientos` (NUEVO) | Lista de follow-ups programados |
| `#recargar` | Sincroniza prompt + config desde nichos-hub |

---

## Endpoints internos del agente (auth: x-agent-secret)

| Metodo | Path | Quien llama | Para que |
|---|---|---|---|
| GET | `/` | health check | Railway monitor |
| GET | `/status` | nichos-hub | Estado del agente (pausado, calendar, costo) |
| GET | `/analytics/stats?dias=30` | nichos-hub | Metricas de conversion (NUEVO) |
| POST | `/webhook` | Twilio/Meta | Mensaje WhatsApp entrante |
| POST | `/webhook/lead` | master-template, nichos-hub | Lead nuevo (NUEVO) |
| POST | `/notify` | nichos-hub | Notif de turno/lead/etc. — soporta staff+cliente (AMPLIADO) |
| POST | `/send-template` | nichos-hub | Envia template Twilio directo |
| POST | `/webhook/calendar-disconnected` | nichos-hub | Calendar OAuth desconectado |
| POST | `/webhook/calendar-connected` | nichos-hub | Calendar OAuth reconectado |
| POST | `/followup/schedule` | nichos-hub | Programa follow-up manual (NUEVO) |
| POST | `/tasks/seguimientos` | cron | Dispara seguimientos pendientes (NUEVO) |
| POST | `/tasks/limpieza` | cron | Limpia registros antiguos (NUEVO) |

---

## Pendientes / proximos pasos

### En este repo (whatsapp-agentkit)
- **Voice agent (ConversationRelay)** — diseno en TWILIO-PLAN.md. ~3-5 dias para MVP.
- **Tests automatizados** — los endpoints nuevos no tienen tests aun. Usar pytest + httpx AsyncClient.
- **Migracion a SQLAlchemy / PostgreSQL** — actualmente SQLite via aiosqlite. Para multi-tenant
  o multi-worker en serio, conviene Postgres. Cap de SQLite con WAL es razonable para 1 cliente.
- **Detección de idioma mas robusta** — actualmente heuristica simple en horario.py.
  Para auto-reply fuera de horario es suficiente, pero conviene LLM-light para casos borde.

### En nichos-hub (otro repo)
- Llamar `/webhook/lead` cuando entra un lead por la web
- En creacion de turno, incluir `staffPhones`, `customerPhone`, `appointment` en `/notify`
- En cancelacion de turno, llamar `/notify` con `type=appointment_cancelled` y `appointment`
- (opcional) consumir `/analytics/stats` para dashboard

### En master-template (otro repo)
- Formulario de contacto/booking dispara POST a nichos-hub /api/leads (que a su vez llama al agente)

### Infra
- Configurar cron job que llame `POST /tasks/seguimientos` cada 5-10 minutos en Railway
- Crear los templates en Twilio Console y configurar SIDs en .env
- Aprobacion Meta de los templates (~24-48h cada uno)

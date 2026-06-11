# agent/main.py — Servidor FastAPI + Webhook de WhatsApp

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from agent.brain import generar_respuesta, obtener_mensaje_error
from agent.memory import (
    inicializar_db, guardar_mensaje, obtener_historial, obtener_lead,
    registrar_procesado, obtener_costo_diario,
    limpiar_registros_antiguos, guardar_config, guardar_lead
)
from agent.providers import obtener_proveedor
from agent.pausa import es_admin, parsear_comando, ejecutar_comando, esta_pausado
from agent.rate_limit import verificar_rate_limit, verificar_rate_limit_global, inicializar_rate_limit
from agent.humanize import partir_respuesta, calcular_delay
from agent.horario import esta_en_horario, mensaje_fuera_horario, detectar_idioma_simple
from agent.escalacion import detectar_urgencia, escalar
from agent.security import (
    verificar_secret, enmascarar_telefono, sanitizar_para_log,
    sanitizar_mensaje_entrante, verificar_timestamp_webhook, error_seguro,
)
from agent import analytics
from agent import notifications
from agent import seguimiento

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
COSTO_DIARIO_MAXIMO = float(os.getenv("COSTO_DIARIO_MAXIMO", "2.0"))
CLIENT_ID = os.getenv("CLIENT_ID", "")
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_RESPONSES", "20"))
# Si "true", fuera de horario respondemos con mensaje fijo (ahorra tokens).
# Si "false", la IA responde igual (puede hacer la salvedad en su mensaje).
AUTO_REPLY_FUERA_HORARIO = os.getenv("AUTO_REPLY_FUERA_HORARIO", "true").lower() == "true"
# Activar deteccion de urgencia + escalacion automatica
ESCALACION_ACTIVA = os.getenv("ESCALACION_ACTIVA", "true").lower() == "true"
# Horas despues del primer mensaje sin respuesta para programar un follow-up
FOLLOWUP_LEAD_HORAS = int(os.getenv("FOLLOWUP_LEAD_HORAS", "24"))
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))

# Semaforo para limitar respuestas concurrentes y evitar saturar el proceso
_semaforo = asyncio.Semaphore(MAX_CONCURRENT)

# Locks por telefono para serializar mensajes concurrentes del mismo usuario
# (evita races en el historial: leer -> generar -> guardar)
_locks_usuario: dict[str, asyncio.Lock] = {}


def _obtener_lock_usuario(telefono: str) -> asyncio.Lock:
    # Eviction simple para que el dict no crezca sin limite
    if len(_locks_usuario) > 1000:
        for key in [k for k, lk in _locks_usuario.items() if not lk.locked()]:
            del _locks_usuario[key]
    if telefono not in _locks_usuario:
        _locks_usuario[telefono] = asyncio.Lock()
    return _locks_usuario[telefono]

# Alias para mantener compatibilidad interna
_verificar_secret = verificar_secret

# --- Debounce de mensajes consecutivos ---
# Una persona suele mandar 2-3 mensajes cortos seguidos ("hola" / "una consulta" /
# "cuanto sale?"). En vez de responder cada uno por separado, esperamos unos
# segundos desde el ultimo mensaje y respondemos al hilo completo de una vez.
DEBOUNCE_SEGUNDOS = float(os.getenv("DEBOUNCE_MENSAJES_SEGUNDOS", "8"))

# telefono -> {"textos": [str], "mensaje_id": str, "task": asyncio.Task}
_buffers_debounce: dict[str, dict] = {}


def encolar_con_debounce(telefono: str, texto: str, mensaje_id: str):
    """Acumula mensajes del mismo telefono y dispara el procesamiento
    cuando pasan DEBOUNCE_SEGUNDOS sin mensajes nuevos."""
    buf = _buffers_debounce.get(telefono)
    if buf:
        buf["textos"].append(texto)
        buf["mensaje_id"] = mensaje_id
        buf["task"].cancel()
        logger.info(
            f"Debounce: mensaje acumulado para {enmascarar_telefono(telefono)} "
            f"({len(buf['textos'])} en buffer)"
        )
    else:
        buf = {"textos": [texto], "mensaje_id": mensaje_id}
        _buffers_debounce[telefono] = buf
    buf["task"] = asyncio.create_task(_disparar_debounce(telefono))


async def _disparar_debounce(telefono: str):
    try:
        await asyncio.sleep(DEBOUNCE_SEGUNDOS)
    except asyncio.CancelledError:
        return
    buf = _buffers_debounce.pop(telefono, None)
    if not buf:
        return
    texto = "\n".join(buf["textos"])
    await procesar_mensaje(telefono, texto, buf["mensaje_id"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    await inicializar_db()
    inicializar_rate_limit()
    asyncio.create_task(limpiar_registros_antiguos())
    logger.info("Base de datos inicializada")
    logger.info(f"Servidor AgentKit corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")
    logger.info(f"Cap diario de costos API: ${COSTO_DIARIO_MAXIMO}")
    logger.info(f"Max respuestas concurrentes: {MAX_CONCURRENT}")
    if CLIENT_ID:
        logger.info(f"Client ID: {CLIENT_ID}")
    yield


app = FastAPI(
    title="AgentKit — WhatsApp AI Agent",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if ENVIRONMENT == "development" else None,
    redoc_url="/redoc" if ENVIRONMENT == "development" else None,
    openapi_url="/openapi.json" if ENVIRONMENT == "development" else None,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    if ENVIRONMENT != "development":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "agentkit"}


@app.get("/status")
async def status_detallado(request: Request):
    """Status detallado para nichos-hub. Requiere x-agent-secret."""
    _verificar_secret(request)
    from agent.memory import obtener_costo_diario, obtener_config
    from agent.brain import _obtener_estado_calendario
    costo = await obtener_costo_diario(client_id=CLIENT_ID)
    calendar = await _obtener_estado_calendario()
    pausado = await esta_pausado()
    return {
        "status": "ok",
        "clientId": CLIENT_ID,
        "provider": proveedor.__class__.__name__,
        "paused": pausado,
        "calendarConnected": calendar,
        "dailyCostUsd": round(costo, 4),
        "dailyCostCap": COSTO_DIARIO_MAXIMO,
    }


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


async def _enviar_humano(telefono: str, fragmentos: list[str], mensaje_id: str):
    """Envia los fragmentos como mensajes separados con typing indicator y delays variables.
    Reproduce el ritmo de una persona escribiendo por WhatsApp."""
    for i, fragmento in enumerate(fragmentos):
        delay = calcular_delay(fragmento, es_primer_fragmento=(i == 0))
        logger.info(
            f"Fragmento {i+1}/{len(fragmentos)} a {enmascarar_telefono(telefono)} en {delay:.1f}s "
            f"({len(fragmento)} chars)"
        )
        # Typing indicator antes de cada fragmento (se renueva, dura hasta 25s o hasta enviar)
        await proveedor.enviar_typing_indicator(mensaje_id)
        # Si delay > 25s el indicador se vencera; lo refrescamos a los 20s
        if delay > 25:
            await asyncio.sleep(20)
            await proveedor.enviar_typing_indicator(mensaje_id)
            await asyncio.sleep(delay - 20)
        else:
            await asyncio.sleep(delay)
        await proveedor.enviar_mensaje(telefono, fragmento)


async def procesar_mensaje(telefono: str, texto: str, mensaje_id: str):
    """Procesa un mensaje con backpressure via semaforo."""
    if _semaforo._value == 0:
        logger.warning(f"Semaforo lleno ({MAX_CONCURRENT}), esperando para {enmascarar_telefono(telefono)}")
    async with _semaforo, _obtener_lock_usuario(telefono):
        try:
            historial = await obtener_historial(telefono)
            lead_negocio = await obtener_lead(telefono)

            # Guardar el mensaje del usuario ANTES de generar: si la generacion
            # falla o llega otro mensaje, el historial ya lo refleja.
            # (historial se leyo antes, asi que no se duplica en el prompt)
            await guardar_mensaje(telefono, "user", texto)

            respuesta = await generar_respuesta(texto, historial, lead_negocio, telefono)

            await guardar_mensaje(telefono, "assistant", respuesta)

            fragmentos = partir_respuesta(respuesta)
            if not fragmentos:
                logger.warning(f"Respuesta vacia para {enmascarar_telefono(telefono)}, no se envia nada")
                return

            await _enviar_humano(telefono, fragmentos, mensaje_id)
            logger.info(
                f"Respuesta a {enmascarar_telefono(telefono)} en {len(fragmentos)} fragmento(s) "
                f"({len(respuesta)} chars)"
            )

            # Analytics: registrar mensaje saliente
            await analytics.registrar_evento(
                analytics.EVENTO_MENSAJE_OUTBOUND, telefono,
                {"fragmentos": len(fragmentos)}, client_id=CLIENT_ID
            )

            # Programar follow-up si hay lead y aun no esta agendado uno
            if lead_negocio:
                await seguimiento.programar_followup_lead(
                    telefono, {"negocio": lead_negocio},
                    horas=FOLLOWUP_LEAD_HORAS
                )

        except Exception as e:
            logger.error(f"Error procesando mensaje de {enmascarar_telefono(telefono)}: {e}")
            # Avisar al usuario con mensaje generico para que no quede sin respuesta
            try:
                await proveedor.enviar_mensaje(telefono, obtener_mensaje_error())
            except Exception as e2:
                logger.error(f"No se pudo enviar mensaje de error a {enmascarar_telefono(telefono)}: {e2}")


@app.post("/webhook")
async def webhook_handler(request: Request, background_tasks: BackgroundTasks):
    try:
        # Rate limiting global (anti-abuse desde multiples numeros)
        if not verificar_rate_limit_global():
            raise HTTPException(status_code=429, detail="Too many requests")

        # Anti-replay: rechazar webhooks con timestamp viejo (>5 min)
        if not verificar_timestamp_webhook(request.headers.get("X-Twilio-Timestamp")):
            logger.warning("Webhook rechazado por timestamp viejo (posible replay)")
            return {"status": "rejected", "reason": "stale_timestamp"}

        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            if msg.es_propio or not msg.texto:
                continue

            logger.info(f"Mensaje de {enmascarar_telefono(msg.telefono)} ({len(msg.texto)} chars)")

            # Sanitizar mensaje entrante (longitud, caracteres de control)
            msg.texto = sanitizar_mensaje_entrante(msg.texto)
            if not msg.texto:
                continue

            # Deduplicacion atomica: el INSERT OR IGNORE es el gate
            # (evita race TOCTOU entre chequear y registrar)
            if not await registrar_procesado(msg.mensaje_id, msg.telefono):
                logger.info(f"Mensaje duplicado ignorado: {msg.mensaje_id}")
                continue

            # Analytics: registrar mensaje entrante (silencioso ante errores)
            await analytics.registrar_evento(
                analytics.EVENTO_MENSAJE_INBOUND, msg.telefono,
                {"len": len(msg.texto)}, client_id=CLIENT_ID
            )

            # Comandos admin (siempre se procesan, ignoran pausa/rate-limit/horario)
            if es_admin(msg.telefono) and parsear_comando(msg.texto):
                respuesta_cmd = await ejecutar_comando(msg.texto)
                if respuesta_cmd:
                    await proveedor.enviar_mensaje(msg.telefono, respuesta_cmd)
                    logger.info(f"Comando admin ejecutado: {sanitizar_para_log(msg.texto, 40)}")
                continue

            # Si el cliente respondio, cancelar follow-ups pendientes para este telefono
            # (evita seguir molestando a alguien que ya contesto)
            background_tasks.add_task(
                seguimiento.cancelar_pendientes, msg.telefono,
                seguimiento.TIPO_FOLLOWUP_LEAD
            )

            # Deteccion de urgencia: si hay senal, escalar a Liam ANTES de responder
            if ESCALACION_ACTIVA:
                es_urgente, razones = detectar_urgencia(msg.texto)
                if es_urgente:
                    await analytics.registrar_evento(
                        analytics.EVENTO_ESCALACION, msg.telefono,
                        {"razones": razones}, client_id=CLIENT_ID
                    )
                    background_tasks.add_task(
                        escalar, msg.telefono, msg.texto, razones, 30
                    )
                    # Aviso liviano al cliente (en su idioma) y NO respondemos con IA
                    avisos_escalacion = {
                        "es": "Recibido, te paso con una persona del equipo ahora",
                        "en": "Got it, let me get someone from the team for you now",
                        "he": "קיבלתי, אני מעביר אותך למישהו מהצוות עכשיו",
                        "ru": "Принято, сейчас передам вас человеку из команды",
                        "ar": "وصلني، رح وصلك مع حدا من الفريق هلق",
                    }
                    idioma_esc = detectar_idioma_simple(msg.texto)
                    await proveedor.enviar_mensaje(
                        msg.telefono,
                        avisos_escalacion.get(idioma_esc, avisos_escalacion["es"])
                    )
                    continue

            # Si la IA esta pausada, no contestar
            if await esta_pausado():
                logger.info(f"IA pausada, ignorando mensaje de {enmascarar_telefono(msg.telefono)}")
                await analytics.registrar_evento(
                    analytics.EVENTO_PAUSA_ACTIVA, msg.telefono, {}, client_id=CLIENT_ID
                )
                continue

            # Rate limiting
            if not verificar_rate_limit(msg.telefono):
                logger.warning(f"Rate limit excedido para {enmascarar_telefono(msg.telefono)}")
                await analytics.registrar_evento(
                    analytics.EVENTO_RATE_LIMIT_BLOQUEO, msg.telefono, {},
                    client_id=CLIENT_ID
                )
                continue

            # Cap diario de costos (por client_id si esta configurado)
            costo_hoy = await obtener_costo_diario(client_id=CLIENT_ID)
            if costo_hoy >= COSTO_DIARIO_MAXIMO:
                logger.warning(f"Cap diario de costos alcanzado: ${costo_hoy:.4f} >= ${COSTO_DIARIO_MAXIMO}")
                break

            # Fuera de horario: si esta activo, responder con mensaje fijo (ahorra tokens)
            if AUTO_REPLY_FUERA_HORARIO and not esta_en_horario():
                idioma = detectar_idioma_simple(msg.texto)
                respuesta_fuera = mensaje_fuera_horario(idioma)
                await proveedor.enviar_mensaje(msg.telefono, respuesta_fuera)
                await guardar_mensaje(msg.telefono, "user", msg.texto)
                await guardar_mensaje(msg.telefono, "assistant", respuesta_fuera)
                await analytics.registrar_evento(
                    analytics.EVENTO_FUERA_HORARIO, msg.telefono, {"idioma": idioma},
                    client_id=CLIENT_ID
                )
                # Programar follow-up por si quedo colgado (no hay lead aun, solo telefono)
                background_tasks.add_task(
                    _maybe_programar_followup, msg.telefono
                )
                continue

            # Encolar con debounce: si llegan varios mensajes seguidos del mismo
            # telefono, se responde una sola vez al hilo completo (mas humano).
            # El webhook responde 200 rapido igual porque el task corre aparte.
            encolar_con_debounce(msg.telefono, msg.texto, msg.mensaje_id)

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=error_seguro(e))


async def _maybe_programar_followup(telefono: str):
    """Si el telefono tiene un lead asociado, programa follow-up automatico
    para FOLLOWUP_LEAD_HORAS sin respuesta. Si no hay lead, no programa nada
    (no queremos perseguir a desconocidos).
    """
    try:
        negocio = await obtener_lead(telefono)
        if not negocio:
            return
        await seguimiento.programar_followup_lead(
            telefono, {"negocio": negocio}, horas=FOLLOWUP_LEAD_HORAS
        )
        logger.info(f"Follow-up programado para {enmascarar_telefono(telefono)} en {FOLLOWUP_LEAD_HORAS}h")
    except Exception as e:
        logger.warning(f"No se pudo programar follow-up para {enmascarar_telefono(telefono)}: {e}")


# --- Modelos para endpoints internos ---

class NotificacionTurno(BaseModel):
    clientId: str
    type: str  # "appointment_booked", "appointment_cancelled", "appointment_reminder"
    adminPhones: list[str]
    message: str
    templateSid: str | None = None
    variables: dict | None = None
    # Nuevos (opcionales, compatibles hacia atras):
    staffPhones: list[str] | None = None  # Notificar tambien al staff
    staffMessage: str | None = None       # Mensaje distinto para staff
    customerPhone: str | None = None      # Si se setea, enviar confirmacion al cliente
    customerMessage: str | None = None
    appointment: dict | None = None       # Datos del turno para programar follow-ups


class CalendarDisconnected(BaseModel):
    clientId: str
    reason: str | None = None


class SendTemplate(BaseModel):
    clientId: str
    templateSid: str
    recipientPhone: str
    variables: dict | None = None


class LeadEntrante(BaseModel):
    """Lead que entra por la web (formulario de contacto) o por nichos-hub."""
    clientId: str
    nombre: str
    telefono: str | None = None
    email: str | None = None
    mensaje: str | None = None
    fuente: str | None = "web"
    adminPhones: list[str]  # A quien notificar


class FollowUpManual(BaseModel):
    clientId: str
    telefono: str
    tipo: str  # followup_lead_24h | recordatorio_turno_24h | review_post_turno
    programarPara: str  # ISO datetime
    payload: dict | None = None


# --- Endpoints internos (auth con x-agent-secret) ---

@app.post("/notify")
async def notificar_turno(payload: NotificacionTurno, request: Request):
    """Recibe notificacion (turno, lead, cancelacion) y la envia a admin/staff/cliente.

    Acepta multiples destinatarios opcionales:
    - adminPhones: dueno del negocio (Liam o dueno del cliente)
    - staffPhones: el profesional asignado al turno
    - customerPhone: el cliente (confirmacion, recordatorio)

    Si appointment trae fecha/hora, programa automaticamente recordatorio 24h
    y review post-servicio.
    """
    _verificar_secret(request)

    if payload.clientId != CLIENT_ID and CLIENT_ID:
        raise HTTPException(status_code=403, detail="clientId no coincide")

    resultados = {"admin": 0, "staff": 0, "customer": 0}

    # Admins
    enviados_admin = 0
    for telefono in payload.adminPhones:
        telefono = telefono.strip()
        if not telefono:
            continue
        if payload.templateSid:
            ok = await proveedor.enviar_template(telefono, payload.templateSid, payload.variables)
        else:
            ok = await proveedor.enviar_mensaje(telefono, payload.message)
        if ok:
            enviados_admin += 1
        else:
            logger.error(f"Error enviando notificacion admin a {enmascarar_telefono(telefono)}")
    resultados["admin"] = enviados_admin

    # Staff (nuevo)
    if payload.staffPhones:
        mensaje_staff = payload.staffMessage or payload.message
        for telefono in payload.staffPhones:
            telefono = telefono.strip()
            if not telefono:
                continue
            ok = await notifications.notificar(
                telefono, "appointment_assigned_staff", mensaje_staff, payload.variables
            )
            if ok:
                resultados["staff"] += 1

    # Cliente (nuevo - confirmacion inmediata)
    if payload.customerPhone:
        mensaje_cli = payload.customerMessage or payload.message
        tipo_cli = (
            "appointment_confirmation_client"
            if payload.type == "appointment_booked"
            else "appointment_cancelled_client"
            if payload.type == "appointment_cancelled"
            else "appointment_reminder_client"
        )
        ok = await notifications.notificar(
            payload.customerPhone, tipo_cli, mensaje_cli, payload.variables
        )
        if ok:
            resultados["customer"] = 1

    # Si es un turno agendado y trae datos, programar recordatorio + review automaticamente
    if payload.type == "appointment_booked" and payload.appointment and payload.customerPhone:
        try:
            await seguimiento.programar_recordatorio_turno(
                payload.customerPhone, payload.appointment
            )
            await seguimiento.programar_review_post_turno(
                payload.customerPhone, payload.appointment
            )
            await analytics.registrar_evento(
                analytics.EVENTO_TURNO_AGENDADO, payload.customerPhone,
                payload.appointment, client_id=CLIENT_ID
            )
        except Exception as e:
            logger.warning(f"No se pudieron programar follow-ups del turno: {e}")
    elif payload.type == "appointment_cancelled" and payload.customerPhone:
        # Cancelar follow-ups pendientes del turno
        await seguimiento.cancelar_pendientes(
            payload.customerPhone, seguimiento.TIPO_RECORDATORIO_TURNO
        )
        await seguimiento.cancelar_pendientes(
            payload.customerPhone, seguimiento.TIPO_REVIEW_POST_TURNO
        )
        await analytics.registrar_evento(
            analytics.EVENTO_TURNO_CANCELADO, payload.customerPhone,
            payload.appointment or {}, client_id=CLIENT_ID
        )

    logger.info(
        f"Notificacion '{payload.type}': admin={resultados['admin']}/{len(payload.adminPhones)} "
        f"staff={resultados['staff']} customer={resultados['customer']}"
    )
    return {"status": "ok", "sent": resultados, "total_admins": len(payload.adminPhones)}


@app.post("/webhook/lead")
async def lead_entrante(payload: LeadEntrante, request: Request):
    """Recibe leads desde la web (formulario contacto/booking) o desde nichos-hub.

    Notifica al admin con datos del lead y registra el evento para analytics.
    Si el lead trae telefono valido, lo asocia en la tabla leads local para
    enriquecer futuras conversaciones.
    """
    _verificar_secret(request)

    if payload.clientId != CLIENT_ID and CLIENT_ID:
        raise HTTPException(status_code=403, detail="clientId no coincide")

    # Guardar lead local (asocia telefono -> nombre del negocio/cliente)
    if payload.telefono:
        # Si el lead viene de un negocio cliente de Arzac, payload.nombre
        # suele ser el nombre del lead, no del negocio. Por eso usamos
        # nombre+email+fuente para tener algo util en la conversacion.
        etiqueta = payload.nombre
        if payload.email:
            etiqueta += f" ({payload.email})"
        await guardar_lead(payload.telefono, etiqueta)

    # Notificar al admin
    resultado = await notifications.notificar_nuevo_lead_admin(
        payload.adminPhones,
        {
            "nombre": payload.nombre,
            "telefono": payload.telefono or "",
            "email": payload.email or "",
            "mensaje": payload.mensaje or "",
            "fuente": payload.fuente or "web",
        }
    )

    # Registrar evento
    await analytics.registrar_evento(
        analytics.EVENTO_LEAD_CREADO,
        payload.telefono or "",
        {
            "nombre": payload.nombre,
            "email": payload.email,
            "fuente": payload.fuente,
        },
        client_id=CLIENT_ID,
    )

    logger.info(
        f"Lead recibido: {payload.nombre} ({payload.fuente}) "
        f"-> notif admin: {resultado['enviados']}/{resultado['total']}"
    )
    return {"status": "ok", "notif_admin": resultado}


@app.post("/tasks/seguimientos")
async def disparar_seguimientos(request: Request):
    """Endpoint cron-triggerable: dispara seguimientos pendientes (follow-ups,
    recordatorios 24h, reviews post-servicio).

    Llamar cada 5-15 minutos desde un cron externo (Railway scheduler, etc).
    """
    _verificar_secret(request)
    resultado = await seguimiento.disparar_pendientes()
    logger.info(f"Seguimientos disparados: {resultado}")
    return {"status": "ok", **resultado}


@app.post("/tasks/limpieza")
async def disparar_limpieza(request: Request):
    """Endpoint cron-triggerable: limpia registros antiguos (mensajes procesados,
    costos viejos, eventos analytics).
    """
    _verificar_secret(request)
    await limpiar_registros_antiguos()
    await analytics.limpiar_eventos_antiguos(dias=180)
    return {"status": "ok"}


@app.post("/followup/schedule")
async def programar_followup_manual(payload: FollowUpManual, request: Request):
    """Permite a nichos-hub programar un follow-up manualmente. Usado cuando
    nichos-hub agenda un turno y quiere programar el recordatorio/review por su cuenta
    (en lugar de delegarlo en el `/notify`).
    """
    _verificar_secret(request)
    if payload.clientId != CLIENT_ID and CLIENT_ID:
        raise HTTPException(status_code=403, detail="clientId no coincide")

    try:
        from datetime import datetime as dt
        cuando = dt.fromisoformat(payload.programarPara.replace("Z", "+00:00"))
        # Normalizar a naive UTC para consistencia con el resto del codigo
        if cuando.tzinfo is not None:
            cuando = cuando.replace(tzinfo=None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"programarPara invalido: {e}")

    sid = await seguimiento.programar(
        payload.telefono, payload.tipo, cuando, payload.payload or {}
    )
    if sid is None:
        return {"status": "skipped", "reason": "ya pendiente"}
    return {"status": "scheduled", "id": sid}


@app.get("/analytics/stats")
async def obtener_analytics(request: Request, dias: int = 30):
    """Stats de conversion y actividad para dashboard de nichos-hub."""
    _verificar_secret(request)
    stats = await analytics.obtener_stats(client_id=CLIENT_ID, dias=dias)
    return {"status": "ok", "stats": stats}


@app.post("/webhook/calendar-disconnected")
async def calendar_disconnected(payload: CalendarDisconnected, request: Request):
    """Nichos-hub avisa que se desconecto Google Calendar para este cliente."""
    _verificar_secret(request)

    if payload.clientId != CLIENT_ID and CLIENT_ID:
        raise HTTPException(status_code=403, detail="clientId no coincide")

    await guardar_config("calendar_connected", "false")

    # Invalidar cache de brain para que el prompt refleje que no hay calendario
    from agent.brain import recargar_config
    recargar_config()

    logger.info(f"Calendar desconectado para {payload.clientId}: {payload.reason or 'sin razon'}")
    return {"status": "ok", "calendar_connected": False}


@app.post("/webhook/calendar-connected")
async def calendar_connected(payload: CalendarDisconnected, request: Request):
    """Nichos-hub avisa que se reconecto Google Calendar."""
    _verificar_secret(request)

    if payload.clientId != CLIENT_ID and CLIENT_ID:
        raise HTTPException(status_code=403, detail="clientId no coincide")

    await guardar_config("calendar_connected", "true")

    from agent.brain import recargar_config
    recargar_config()

    logger.info(f"Calendar reconectado para {payload.clientId}")
    return {"status": "ok", "calendar_connected": True}


@app.post("/send-template")
async def enviar_template_endpoint(payload: SendTemplate, request: Request):
    """Envia un template de WhatsApp cuando nichos-hub lo solicita."""
    _verificar_secret(request)

    if payload.clientId != CLIENT_ID and CLIENT_ID:
        raise HTTPException(status_code=403, detail="clientId no coincide")

    ok = await proveedor.enviar_template(
        payload.recipientPhone, payload.templateSid, payload.variables
    )

    if not ok:
        raise HTTPException(status_code=502, detail="Error enviando template via proveedor")

    logger.info(f"Template {payload.templateSid} enviado a {payload.recipientPhone}")
    return {"status": "ok", "templateSid": payload.templateSid, "recipientPhone": payload.recipientPhone}

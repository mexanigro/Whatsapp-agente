# agent/seguimiento.py — Sistema de seguimientos programados (follow-ups)
#
# Encolamos seguimientos futuros (24h sin respuesta, 24h pre-turno, 24h post-turno)
# en una tabla SQLite, y un endpoint cron-triggerable los dispara cuando toca.
#
# Por que no usar APScheduler/Celery: queremos simplicidad. Railway puede pingear
# el endpoint /tasks/seguimientos cada 5-15 min via cron job externo.
# Si en el futuro hace falta latencia menor, se cambia.

import os
import json
import logging
import aiosqlite
from datetime import datetime, timedelta

logger = logging.getLogger("agentkit")

DB_PATH = os.getenv("DB_PATH", "agentkit.db")

# Tipos de seguimiento
TIPO_FOLLOWUP_LEAD = "followup_lead_24h"
TIPO_RECORDATORIO_TURNO = "recordatorio_turno_24h"
TIPO_REVIEW_POST_TURNO = "review_post_turno"


async def inicializar_tabla_seguimientos(db: aiosqlite.Connection):
    """Crea la tabla. Se llama desde memory.inicializar_db()."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS seguimientos_programados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telefono TEXT NOT NULL,
            tipo TEXT NOT NULL,
            programado_para TEXT NOT NULL,
            payload TEXT,
            estado TEXT DEFAULT 'pendiente',
            ejecutado_en TEXT,
            resultado TEXT,
            created_at TEXT NOT NULL
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_seguimientos_estado_fecha "
        "ON seguimientos_programados(estado, programado_para)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_seguimientos_telefono_tipo "
        "ON seguimientos_programados(telefono, tipo)"
    )


async def programar(telefono: str, tipo: str, programado_para: datetime,
                    payload: dict | None = None,
                    deduplicar: bool = True) -> int | None:
    """Programa un seguimiento. Si deduplicar=True, no encola si ya hay uno pendiente
    para el mismo telefono+tipo.

    Returns el id del seguimiento creado, o None si se dedupeo.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if deduplicar:
            cursor = await db.execute(
                "SELECT id FROM seguimientos_programados "
                "WHERE telefono = ? AND tipo = ? AND estado = 'pendiente'",
                (telefono, tipo)
            )
            existente = await cursor.fetchone()
            if existente:
                logger.info(f"Seguimiento {tipo} ya pendiente para {telefono}, no se duplica")
                return None

        cursor = await db.execute(
            "INSERT INTO seguimientos_programados "
            "(telefono, tipo, programado_para, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (telefono, tipo, programado_para.isoformat(),
             json.dumps(payload or {}, ensure_ascii=False),
             datetime.utcnow().isoformat())
        )
        await db.commit()
        return cursor.lastrowid


async def cancelar_pendientes(telefono: str, tipo: str | None = None):
    """Cancela seguimientos pendientes de un telefono. Si tipo es None, cancela todos."""
    async with aiosqlite.connect(DB_PATH) as db:
        if tipo:
            cursor = await db.execute(
                "UPDATE seguimientos_programados SET estado = 'cancelado' "
                "WHERE telefono = ? AND tipo = ? AND estado = 'pendiente'",
                (telefono, tipo)
            )
        else:
            cursor = await db.execute(
                "UPDATE seguimientos_programados SET estado = 'cancelado' "
                "WHERE telefono = ? AND estado = 'pendiente'",
                (telefono,)
            )
        await db.commit()
        return cursor.rowcount


async def obtener_pendientes_a_disparar(limite: int = 50) -> list[dict]:
    """Devuelve seguimientos pendientes cuya programacion ya paso."""
    ahora = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, telefono, tipo, programado_para, payload "
            "FROM seguimientos_programados "
            "WHERE estado = 'pendiente' AND programado_para <= ? "
            "ORDER BY programado_para ASC LIMIT ?",
            (ahora, limite)
        )
        filas = await cursor.fetchall()

    return [
        {
            "id": f[0],
            "telefono": f[1],
            "tipo": f[2],
            "programado_para": f[3],
            "payload": json.loads(f[4]) if f[4] else {},
        }
        for f in filas
    ]


async def marcar_ejecutado(seguimiento_id: int, resultado: str):
    """Marca un seguimiento como ejecutado con el resultado del envio."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE seguimientos_programados SET estado = ?, ejecutado_en = ?, resultado = ? "
            "WHERE id = ?",
            ("ejecutado", datetime.utcnow().isoformat(), resultado, seguimiento_id)
        )
        await db.commit()


async def marcar_fallido(seguimiento_id: int, error: str):
    """Marca un seguimiento como fallido (no se reintenta por defecto)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE seguimientos_programados SET estado = ?, ejecutado_en = ?, resultado = ? "
            "WHERE id = ?",
            ("fallido", datetime.utcnow().isoformat(), error[:500], seguimiento_id)
        )
        await db.commit()


async def disparar_pendientes() -> dict:
    """Ejecuta los seguimientos pendientes que ya vencieron.

    Usado por el endpoint /tasks/seguimientos llamado por cron externo.
    """
    from agent.notifications import (
        notificar_followup_lead_cliente,
        notificar_recordatorio_cliente,
        notificar_review_cliente,
    )
    from agent.analytics import (
        registrar_evento,
        EVENTO_RECORDATORIO_ENVIADO,
        EVENTO_FOLLOWUP_ENVIADO,
        EVENTO_REVIEW_REQUEST,
    )

    pendientes = await obtener_pendientes_a_disparar()
    ejecutados = 0
    fallidos = 0

    for seg in pendientes:
        try:
            telefono = seg["telefono"]
            tipo = seg["tipo"]
            payload = seg["payload"]

            ok = False
            if tipo == TIPO_FOLLOWUP_LEAD:
                # No mandar si el cliente ya respondio despues de programar el follow-up
                if await _hubo_respuesta_reciente(telefono, seg["programado_para"]):
                    logger.info(f"Skip follow-up: {telefono} ya respondio")
                    await marcar_ejecutado(seg["id"], "skip_cliente_respondio")
                    continue
                ok = await notificar_followup_lead_cliente(telefono, payload)
                if ok:
                    await registrar_evento(EVENTO_FOLLOWUP_ENVIADO, telefono,
                                           {"tipo": tipo})

            elif tipo == TIPO_RECORDATORIO_TURNO:
                ok = await notificar_recordatorio_cliente(telefono, payload)
                if ok:
                    await registrar_evento(EVENTO_RECORDATORIO_ENVIADO, telefono,
                                           {"turno_id": payload.get("appointmentId")})

            elif tipo == TIPO_REVIEW_POST_TURNO:
                ok = await notificar_review_cliente(telefono, payload)
                if ok:
                    await registrar_evento(EVENTO_REVIEW_REQUEST, telefono,
                                           {"turno_id": payload.get("appointmentId")})
            else:
                logger.warning(f"Tipo de seguimiento desconocido: {tipo}")
                await marcar_fallido(seg["id"], f"tipo_desconocido:{tipo}")
                fallidos += 1
                continue

            if ok:
                await marcar_ejecutado(seg["id"], "ok")
                ejecutados += 1
            else:
                await marcar_fallido(seg["id"], "envio_fallo")
                fallidos += 1

        except Exception as e:
            logger.error(f"Error ejecutando seguimiento {seg['id']}: {e}")
            await marcar_fallido(seg["id"], str(e))
            fallidos += 1

    return {"ejecutados": ejecutados, "fallidos": fallidos, "total": len(pendientes)}


async def _hubo_respuesta_reciente(telefono: str, desde_iso: str) -> bool:
    """Verifica si el cliente envio algun mensaje desde la fecha indicada.
    Se usa para cancelar follow-ups si el cliente ya respondio.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM mensajes WHERE telefono = ? AND role = 'user' AND timestamp >= ? LIMIT 1",
            (telefono, desde_iso)
        )
        return await cursor.fetchone() is not None


# --- Helpers de programacion por tipo ---


async def programar_followup_lead(telefono: str, lead_data: dict,
                                  horas: int = 24) -> int | None:
    """Programa un follow-up automatico para un lead que no respondio."""
    cuando = datetime.utcnow() + timedelta(hours=horas)
    return await programar(telefono, TIPO_FOLLOWUP_LEAD, cuando, lead_data)


def _turno_a_utc(turno: dict) -> datetime | None:
    """Convierte date+time del turno (en TZ del negocio) a datetime UTC naive."""
    from agent.horario import TIMEZONE
    try:
        fecha_local = datetime.fromisoformat(f"{turno['date']}T{turno['time']}:00")
        fecha_local = fecha_local.replace(tzinfo=TIMEZONE)
        # Convertir a UTC y dropear tzinfo para almacenar/comparar con utcnow
        from datetime import timezone as _tz
        return fecha_local.astimezone(_tz.utc).replace(tzinfo=None)
    except (KeyError, ValueError) as e:
        logger.error(f"Fecha de turno invalida: {e}")
        return None


async def programar_recordatorio_turno(telefono_cliente: str, turno: dict) -> int | None:
    """Programa el recordatorio 24h antes del turno.

    turno debe tener: date (YYYY-MM-DD), time (HH:mm) en TZ del negocio.
    """
    fecha_turno_utc = _turno_a_utc(turno)
    if fecha_turno_utc is None:
        return None

    cuando = fecha_turno_utc - timedelta(hours=24)
    if cuando <= datetime.utcnow():
        # Turno es en menos de 24h, no programamos recordatorio
        return None

    return await programar(telefono_cliente, TIPO_RECORDATORIO_TURNO, cuando, turno)


async def programar_review_post_turno(telefono_cliente: str, turno: dict,
                                       horas_despues: int = 4) -> int | None:
    """Programa el pedido de review X horas despues del turno."""
    fecha_turno_utc = _turno_a_utc(turno)
    if fecha_turno_utc is None:
        return None

    cuando = fecha_turno_utc + timedelta(hours=horas_despues)
    return await programar(telefono_cliente, TIPO_REVIEW_POST_TURNO, cuando, turno)

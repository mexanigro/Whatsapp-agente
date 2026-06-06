# agent/analytics.py — Tracking de eventos de conversacion para metricas de negocio
#
# Registra eventos clave (mensaje_inbound, lead_creado, turno_agendado, escalacion, etc.)
# para poder calcular tasas de conversion y exportar para dashboard de nichos-hub.
#
# La tabla `eventos` es liviana: cada evento es una fila con telefono + tipo + metadata.

import os
import json
import logging
import aiosqlite
from datetime import datetime, timedelta

logger = logging.getLogger("agentkit")

DB_PATH = os.getenv("DB_PATH", "agentkit.db")

# Tipos de evento estandarizados
EVENTO_MENSAJE_INBOUND = "mensaje_inbound"
EVENTO_MENSAJE_OUTBOUND = "mensaje_outbound"
EVENTO_FUERA_HORARIO = "fuera_horario"
EVENTO_LEAD_CREADO = "lead_creado"
EVENTO_TURNO_AGENDADO = "turno_agendado"
EVENTO_TURNO_CANCELADO = "turno_cancelado"
EVENTO_ESCALACION = "escalacion"
EVENTO_TEMPLATE_ENVIADO = "template_enviado"
EVENTO_RECORDATORIO_ENVIADO = "recordatorio_enviado"
EVENTO_FOLLOWUP_ENVIADO = "followup_enviado"
EVENTO_REVIEW_REQUEST = "review_request"
EVENTO_RATE_LIMIT_BLOQUEO = "rate_limit_bloqueo"
EVENTO_PAUSA_ACTIVA = "pausa_activa"


async def inicializar_tablas_analytics(db: aiosqlite.Connection):
    """Crea las tablas. Se llama desde memory.inicializar_db()."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telefono TEXT,
            tipo TEXT NOT NULL,
            metadata TEXT,
            timestamp TEXT NOT NULL,
            client_id TEXT DEFAULT ''
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_eventos_tipo_ts ON eventos(tipo, timestamp)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_eventos_telefono ON eventos(telefono)"
    )


async def registrar_evento(tipo: str, telefono: str = "",
                           metadata: dict | None = None,
                           client_id: str = "") -> None:
    """Registra un evento. metadata se serializa a JSON. Silencioso ante errores."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO eventos (telefono, tipo, metadata, timestamp, client_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (telefono, tipo, json.dumps(metadata or {}, ensure_ascii=False),
                 datetime.utcnow().isoformat(), client_id)
            )
            await db.commit()
    except Exception as e:
        # No queremos que un fallo de analytics rompa el flujo principal
        logger.warning(f"No se pudo registrar evento {tipo}: {e}")


async def obtener_stats(client_id: str = "", dias: int = 30) -> dict:
    """Calcula tasas de conversion y metricas clave para los ultimos N dias.

    Returns:
        {
            "rango_dias": 30,
            "leads_creados": int,
            "turnos_agendados": int,
            "turnos_cancelados": int,
            "escalaciones": int,
            "conversaciones_unicas": int (telefonos distintos con mensaje_inbound),
            "conv_a_lead": float (leads/conversaciones),
            "conv_a_turno": float (turnos/conversaciones),
            "fuera_horario": int,
            "templates_enviados": int,
        }
    """
    desde = (datetime.utcnow() - timedelta(days=dias)).isoformat()

    def _where_client():
        return "AND client_id = ?" if client_id else ""

    params_base = [desde]
    if client_id:
        params_base.append(client_id)

    async with aiosqlite.connect(DB_PATH) as db:
        async def contar(tipo: str) -> int:
            sql = f"SELECT COUNT(*) FROM eventos WHERE tipo = ? AND timestamp >= ? {_where_client()}"
            params = [tipo, desde]
            if client_id:
                params.append(client_id)
            cursor = await db.execute(sql, params)
            fila = await cursor.fetchone()
            return fila[0] if fila else 0

        leads_creados = await contar(EVENTO_LEAD_CREADO)
        turnos_agendados = await contar(EVENTO_TURNO_AGENDADO)
        turnos_cancelados = await contar(EVENTO_TURNO_CANCELADO)
        escalaciones = await contar(EVENTO_ESCALACION)
        fuera_horario = await contar(EVENTO_FUERA_HORARIO)
        templates_enviados = await contar(EVENTO_TEMPLATE_ENVIADO)

        # Telefonos unicos con mensaje_inbound
        sql_unicos = (
            f"SELECT COUNT(DISTINCT telefono) FROM eventos "
            f"WHERE tipo = ? AND timestamp >= ? AND telefono != '' {_where_client()}"
        )
        params_unicos = [EVENTO_MENSAJE_INBOUND, desde]
        if client_id:
            params_unicos.append(client_id)
        cursor = await db.execute(sql_unicos, params_unicos)
        fila = await cursor.fetchone()
        conversaciones_unicas = fila[0] if fila else 0

    def _ratio(a, b):
        return round(a / b, 4) if b > 0 else 0.0

    return {
        "rango_dias": dias,
        "leads_creados": leads_creados,
        "turnos_agendados": turnos_agendados,
        "turnos_cancelados": turnos_cancelados,
        "escalaciones": escalaciones,
        "conversaciones_unicas": conversaciones_unicas,
        "conv_a_lead": _ratio(leads_creados, conversaciones_unicas),
        "conv_a_turno": _ratio(turnos_agendados, conversaciones_unicas),
        "fuera_horario": fuera_horario,
        "templates_enviados": templates_enviados,
    }


async def es_cliente_recurrente(telefono: str, dias: int = 90) -> dict:
    """Detecta si un telefono ya interactuo antes (recurrente vs. primera vez).

    Returns:
        {
            "recurrente": bool,
            "primera_interaccion": str | None (ISO datetime),
            "total_mensajes": int,
            "turnos_previos": int,
        }
    """
    desde = (datetime.utcnow() - timedelta(days=dias)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        # Primera interaccion en la ventana
        cursor = await db.execute(
            "SELECT MIN(timestamp) FROM eventos "
            "WHERE telefono = ? AND tipo = ? AND timestamp >= ?",
            (telefono, EVENTO_MENSAJE_INBOUND, desde)
        )
        fila = await cursor.fetchone()
        primera = fila[0] if fila and fila[0] else None

        # Total de mensajes inbound
        cursor = await db.execute(
            "SELECT COUNT(*) FROM eventos "
            "WHERE telefono = ? AND tipo = ? AND timestamp >= ?",
            (telefono, EVENTO_MENSAJE_INBOUND, desde)
        )
        fila = await cursor.fetchone()
        total = fila[0] if fila else 0

        # Turnos previos
        cursor = await db.execute(
            "SELECT COUNT(*) FROM eventos "
            "WHERE telefono = ? AND tipo = ? AND timestamp >= ?",
            (telefono, EVENTO_TURNO_AGENDADO, desde)
        )
        fila = await cursor.fetchone()
        turnos = fila[0] if fila else 0

    # Recurrente si tiene >=3 mensajes previos o algun turno previo
    recurrente = total >= 3 or turnos > 0

    return {
        "recurrente": recurrente,
        "primera_interaccion": primera,
        "total_mensajes": total,
        "turnos_previos": turnos,
    }


async def limpiar_eventos_antiguos(dias: int = 180):
    """Borra eventos mas viejos que N dias. Llamar periodicamente."""
    desde = (datetime.utcnow() - timedelta(days=dias)).isoformat()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "DELETE FROM eventos WHERE timestamp < ?", (desde,)
            )
            await db.commit()
            logger.info(f"Limpieza: {cursor.rowcount} eventos antiguos eliminados")
    except Exception as e:
        logger.error(f"Error limpiando eventos: {e}")

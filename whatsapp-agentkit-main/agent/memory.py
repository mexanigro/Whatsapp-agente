# agent/memory.py — Memoria de conversaciones con SQLite
#
# TODO SEGURIDAD: los datos de conversacion (mensajes, leads, telefonos) se almacenan
# en texto plano en SQLite. En produccion (Railway) el disco es efimero y no persiste
# entre deploys. Para despliegues con disco persistente, evaluar encripcion at-rest
# (SQLCipher o encripcion a nivel filesystem).

import os
import aiosqlite
import logging
from datetime import datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

DB_PATH = os.getenv("DB_PATH", "agentkit.db")

# TZ del negocio para el corte del cap diario de costos (misma que horario.py)
BUSINESS_TIMEZONE = ZoneInfo(os.getenv("BUSINESS_TIMEZONE", "Asia/Jerusalem"))


async def inicializar_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # Optimizar SQLite para concurrencia
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout = 3000")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS mensajes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telefono TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_telefono ON mensajes(telefono)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                telefono TEXT PRIMARY KEY,
                negocio TEXT NOT NULL,
                fecha_registro TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS mensajes_procesados (
                mensaje_id TEXT PRIMARY KEY,
                telefono TEXT NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS costos_api (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telefono TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                costo_usd REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        # Indice para consultas de costo diario (WHERE timestamp >= ?)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_costos_timestamp ON costos_api(timestamp)
        """)
        # Tabla de configuracion persistente (pausa, etc.)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS configuracion (
                clave TEXT PRIMARY KEY,
                valor TEXT
            )
        """)
        # Agregar columna client_id a costos_api si no existe (migracion segura)
        try:
            await db.execute("ALTER TABLE costos_api ADD COLUMN client_id TEXT DEFAULT ''")
        except Exception:
            pass  # Columna ya existe

        # Tablas de modulos auxiliares (analytics, seguimiento)
        from agent.analytics import inicializar_tablas_analytics
        from agent.seguimiento import inicializar_tabla_seguimientos
        await inicializar_tablas_analytics(db)
        await inicializar_tabla_seguimientos(db)

        await db.commit()


async def guardar_mensaje(telefono: str, role: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO mensajes (telefono, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (telefono, role, content, datetime.utcnow().isoformat())
        )
        await db.commit()


async def obtener_historial(telefono: str, limite: int = 24) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT role, content FROM mensajes WHERE telefono = ? ORDER BY timestamp DESC LIMIT ?",
            (telefono, limite)
        )
        filas = await cursor.fetchall()

    # filas viene en DESC (mas reciente primero)
    # Recortar por tokens: iterar desde el mas reciente, mantener lo que quepa en ~1500 tokens
    total_chars = 0
    historial_recortado = []
    for fila in filas:
        total_chars += len(fila[1])
        if total_chars > 6000:
            break
        historial_recortado.insert(0, {"role": fila[0], "content": fila[1]})

    return historial_recortado


async def limpiar_historial(telefono: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM mensajes WHERE telefono = ?", (telefono,))
        await db.commit()


# --- Deduplicacion ---

async def ya_procesado(mensaje_id: str) -> bool:
    if not mensaje_id:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT 1 FROM mensajes_procesados WHERE mensaje_id = ?",
            (mensaje_id,)
        )
        return await cursor.fetchone() is not None


async def registrar_procesado(mensaje_id: str, telefono: str) -> bool:
    """Registra el mensaje como procesado de forma atomica (gate anti-duplicados).
    Retorna True solo si es el primer procesamiento (INSERT efectivo);
    False si ya estaba registrado (duplicado concurrente o reintento)."""
    if not mensaje_id:
        return True
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO mensajes_procesados (mensaje_id, telefono, timestamp) VALUES (?, ?, ?)",
            (mensaje_id, telefono, datetime.utcnow().isoformat())
        )
        await db.commit()
        return cursor.rowcount == 1


# --- Leads ---

async def guardar_lead(telefono: str, negocio: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO leads (telefono, negocio, fecha_registro)
               VALUES (?, ?, ?)
               ON CONFLICT(telefono) DO UPDATE SET negocio = ?, fecha_registro = ?""",
            (telefono, negocio, datetime.utcnow().isoformat(),
             negocio, datetime.utcnow().isoformat())
        )
        await db.commit()


async def obtener_lead(telefono: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT negocio FROM leads WHERE telefono = ?",
            (telefono,)
        )
        fila = await cursor.fetchone()
    return fila[0] if fila else None


async def listar_leads() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT telefono, negocio, fecha_registro FROM leads ORDER BY fecha_registro DESC LIMIT 50"
        )
        filas = await cursor.fetchall()
    return [{"telefono": f[0], "negocio": f[1], "fecha": f[2]} for f in filas]


# --- Cost tracking ---

PRECIOS_POR_MODELO = {
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
}


async def registrar_costo(telefono: str, input_tokens: int, output_tokens: int,
                          cache_read: int = 0, cache_creation: int = 0,
                          client_id: str = "", modelo: str = ""):
    if not modelo:
        modelo = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
    precios = PRECIOS_POR_MODELO.get(modelo, PRECIOS_POR_MODELO["claude-sonnet-4-6"])
    costo = (
        (input_tokens * precios["input"] / 1_000_000) +
        (output_tokens * precios["output"] / 1_000_000) +
        (cache_read * precios["cache_read"] / 1_000_000) +
        (cache_creation * precios["cache_write"] / 1_000_000)
    )
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO costos_api
               (telefono, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, costo_usd, timestamp, client_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (telefono, input_tokens, output_tokens, cache_read, cache_creation,
             costo, datetime.utcnow().isoformat(), client_id)
        )
        await db.commit()
    return costo


async def obtener_costo_diario(client_id: str = "") -> float:
    # Inicio del dia en la TZ del negocio, convertido a UTC naive
    # (los timestamps se guardan con datetime.utcnow().isoformat()).
    # Asi el cap diario se resetea a medianoche local, no a medianoche UTC.
    hoy_local = datetime.now(BUSINESS_TIMEZONE).date()
    inicio_local = datetime.combine(hoy_local, time.min, tzinfo=BUSINESS_TIMEZONE)
    hoy = inicio_local.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        if client_id:
            cursor = await db.execute(
                "SELECT COALESCE(SUM(costo_usd), 0) FROM costos_api WHERE timestamp >= ? AND client_id = ?",
                (hoy, client_id)
            )
        else:
            cursor = await db.execute(
                "SELECT COALESCE(SUM(costo_usd), 0) FROM costos_api WHERE timestamp >= ?",
                (hoy,)
            )
        fila = await cursor.fetchone()
    return fila[0] if fila else 0.0


async def obtener_costo_semanal() -> float:
    hace_7_dias = (datetime.utcnow() - timedelta(days=7)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(costo_usd), 0) FROM costos_api WHERE timestamp >= ?",
            (hace_7_dias,)
        )
        fila = await cursor.fetchone()
    return fila[0] if fila else 0.0


async def obtener_stats_costos() -> dict:
    hoy = datetime.utcnow().date().isoformat()
    hace_7_dias = (datetime.utcnow() - timedelta(days=7)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(costo_usd), 0), COUNT(*), COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) FROM costos_api WHERE timestamp >= ?",
            (hoy,)
        )
        diario = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT COALESCE(SUM(costo_usd), 0), COUNT(*) FROM costos_api WHERE timestamp >= ?",
            (hace_7_dias,)
        )
        semanal = await cursor.fetchone()
    return {
        "costo_hoy": diario[0],
        "llamadas_hoy": diario[1],
        "tokens_input_hoy": diario[2],
        "tokens_output_hoy": diario[3],
        "costo_semana": semanal[0],
        "llamadas_semana": semanal[1],
    }


# --- Configuracion persistente ---

async def guardar_config(clave: str, valor: str | None):
    """Guarda o borra un valor de configuracion en la tabla configuracion."""
    async with aiosqlite.connect(DB_PATH) as db:
        if valor is None:
            await db.execute("DELETE FROM configuracion WHERE clave = ?", (clave,))
        else:
            await db.execute(
                "INSERT OR REPLACE INTO configuracion (clave, valor) VALUES (?, ?)",
                (clave, valor)
            )
        await db.commit()


async def obtener_config(clave: str) -> str | None:
    """Lee un valor de configuracion de la tabla configuracion."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT valor FROM configuracion WHERE clave = ?", (clave,)
        )
        fila = await cursor.fetchone()
    return fila[0] if fila else None


# --- Limpieza de registros antiguos ---

async def limpiar_registros_antiguos():
    """Borra registros viejos: mensajes_procesados >24h, costos_api >30 dias."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            hace_24h = (datetime.utcnow() - timedelta(hours=24)).isoformat()
            hace_30d = (datetime.utcnow() - timedelta(days=30)).isoformat()
            cursor1 = await db.execute(
                "DELETE FROM mensajes_procesados WHERE timestamp < ?", (hace_24h,)
            )
            cursor2 = await db.execute(
                "DELETE FROM costos_api WHERE timestamp < ?", (hace_30d,)
            )
            await db.commit()
            logger.info(
                f"Limpieza: {cursor1.rowcount} mensajes_procesados, "
                f"{cursor2.rowcount} costos_api eliminados"
            )
    except Exception as e:
        logger.error(f"Error en limpieza de registros: {e}")

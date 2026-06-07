# agent/pausa.py — Sistema de pausa para que Liam (humano) tome el control

import os
import re
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

from agent.security import enmascarar_telefono

load_dotenv()
logger = logging.getLogger("agentkit")

NUMERO_ADMIN = os.getenv("ADMIN_PHONE_NUMBER", "")

# Cache in-memory con fallback a DB para persistir entre deploys
_pausa_hasta: datetime | None = None
_pausa_cargada: bool = False


async def _cargar_pausa():
    """Carga pausa desde DB (lazy, una sola vez al arrancar)."""
    global _pausa_hasta, _pausa_cargada
    if _pausa_cargada:
        return
    from agent.memory import obtener_config
    valor = await obtener_config("pausa_hasta")
    if valor:
        try:
            _pausa_hasta = datetime.fromisoformat(valor)
        except ValueError:
            _pausa_hasta = None
    _pausa_cargada = True


async def _guardar_pausa(hasta: datetime | None):
    """Guarda pausa en cache + DB para persistir entre restarts."""
    global _pausa_hasta, _pausa_cargada
    _pausa_hasta = hasta
    _pausa_cargada = True
    from agent.memory import guardar_config
    await guardar_config("pausa_hasta", hasta.isoformat() if hasta else None)


def es_admin(telefono: str) -> bool:
    if not NUMERO_ADMIN:
        return False
    limpio = telefono.replace(" ", "").replace("-", "")
    admin_limpio = NUMERO_ADMIN.replace(" ", "").replace("-", "")
    return limpio == admin_limpio or limpio.endswith(admin_limpio[-10:])


def parsear_comando(texto: str) -> bool:
    return texto.strip().lower().startswith("#")


async def ejecutar_comando(texto: str) -> str | None:
    from agent.memory import guardar_lead, listar_leads, obtener_stats_costos

    texto_lower = texto.strip().lower()

    if texto_lower == "#recargar":
        from agent.brain import recargar_config
        resultado = await _sincronizar_config_remota()
        recargar_config()
        if resultado:
            return f"Config sincronizada desde servidor y recargada. {resultado}"
        return "Config local recargada. El system prompt se actualizo."

    if texto_lower == "#volver":
        await _guardar_pausa(None)
        logger.info("IA reactivada por admin")
        return "IA reactivada."

    if texto_lower == "#estado":
        await _cargar_pausa()
        if _pausa_hasta and _pausa_hasta > datetime.utcnow():
            restante = int((_pausa_hasta - datetime.utcnow()).total_seconds() / 60)
            return f"IA pausada. Quedan {restante} minutos. Manda #volver para reactivar."
        return "IA activa, contestando todo."

    if texto_lower == "#costo":
        stats = await obtener_stats_costos()
        return (
            f"Costos API:\n"
            f"Hoy: ${stats['costo_hoy']:.4f} ({stats['llamadas_hoy']} llamadas, "
            f"{stats['tokens_input_hoy']} in / {stats['tokens_output_hoy']} out)\n"
            f"Semana: ${stats['costo_semana']:.4f} ({stats['llamadas_semana']} llamadas)"
        )

    if texto_lower in ("#stats", "#metricas"):
        from agent.analytics import obtener_stats
        client_id = os.getenv("CLIENT_ID", "")
        s = await obtener_stats(client_id=client_id, dias=30)
        return (
            f"Stats 30d:\n"
            f"Conversaciones: {s['conversaciones_unicas']}\n"
            f"Leads: {s['leads_creados']} (conv {s['conv_a_lead']*100:.1f}%)\n"
            f"Turnos: {s['turnos_agendados']} (conv {s['conv_a_turno']*100:.1f}%)\n"
            f"Cancelados: {s['turnos_cancelados']}\n"
            f"Escalaciones: {s['escalaciones']}\n"
            f"Fuera horario: {s['fuera_horario']}\n"
            f"Templates: {s['templates_enviados']}"
        )

    if texto_lower == "#seguimientos":
        from agent.seguimiento import obtener_pendientes_a_disparar
        from datetime import datetime as dt
        # Truco: pedir 100 con un horizonte largo. Para una vista de admin alcanza.
        async def _todos():
            import aiosqlite
            db_path = os.getenv("DB_PATH", "agentkit.db")
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute(
                    "SELECT telefono, tipo, programado_para FROM seguimientos_programados "
                    "WHERE estado = 'pendiente' ORDER BY programado_para ASC LIMIT 20"
                )
                return await cursor.fetchall()
        filas = await _todos()
        if not filas:
            return "No hay seguimientos pendientes."
        lineas = [f"{f[1]} -> {f[0]} ({f[2][:16]})" for f in filas]
        return "Seguimientos pendientes:\n" + "\n".join(lineas)

    # #lead +972XXXXXXXXX Nombre del Negocio
    match = re.match(r"^#lead\s+(\+?\d[\d\s-]{7,})\s+(.+)$", texto.strip(), re.IGNORECASE)
    if match:
        telefono = re.sub(r"[\s-]", "", match.group(1))
        negocio = match.group(2).strip()
        await guardar_lead(telefono, negocio)
        logger.info(f"Lead registrado: {enmascarar_telefono(telefono)} -> {negocio}")
        return f"Lead registrado: {negocio} ({telefono})"

    if texto_lower == "#leads":
        leads = await listar_leads()
        if not leads:
            return "No hay leads registrados."
        lineas = [f"{l['negocio']} ({l['telefono']})" for l in leads]
        return "Leads registrados:\n" + "\n".join(lineas)

    match = re.match(r"^#pausa\s+(\d+)\s*h$", texto_lower)
    if match:
        minutos = int(match.group(1)) * 60
        hasta = datetime.utcnow() + timedelta(minutes=minutos)
        await _guardar_pausa(hasta)
        logger.info(f"Pausa global: {minutos} min")
        return f"IA pausada por {match.group(1)} horas. Manda #volver para reactivar."

    match = re.match(r"^#pausa\s+(\d+)\s*m?$", texto_lower)
    if match:
        minutos = int(match.group(1))
        hasta = datetime.utcnow() + timedelta(minutes=minutos)
        await _guardar_pausa(hasta)
        logger.info(f"Pausa global: {minutos} min")
        return f"IA pausada por {minutos} minutos. Manda #volver para reactivar."

    if re.match(r"^#pausa\s*$", texto_lower):
        hasta = datetime.utcnow() + timedelta(minutes=30)
        await _guardar_pausa(hasta)
        logger.info("Pausa global: 30 min")
        return "IA pausada por 30 minutos. Manda #volver para reactivar."

    return None


async def _sincronizar_config_remota() -> str | None:
    """Intenta sincronizar system prompt desde nichos-hub si esta configurado."""
    nichos_url = os.getenv("NICHOS_HUB_URL", "").rstrip("/")
    agent_secret = os.getenv("AGENT_API_SECRET", "")
    client_id = os.getenv("CLIENT_ID", "")
    if not nichos_url or not agent_secret or not client_id:
        return None
    try:
        import httpx
        import yaml
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(
                f"{nichos_url}/api/agent/config",
                params={"clientId": client_id},
                headers={"x-agent-secret": agent_secret}
            )
            if r.status_code != 200:
                logger.warning(f"No se pudo sincronizar config remota: {r.status_code}")
                return None
            data = r.json()
            # Actualizar prompts.yaml si viene system_prompt
            if data.get("systemPrompt"):
                config_path = "config/prompts.yaml"
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                config["system_prompt"] = data["systemPrompt"]
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
            # Actualizar estado del calendario si viene
            if "calendarConnected" in data:
                from agent.memory import guardar_config
                await guardar_config(
                    "calendar_connected",
                    "true" if data["calendarConnected"] else "false"
                )
            return "Prompt y config sincronizados."
    except Exception as e:
        logger.error(f"Error sincronizando config remota: {e}")
        return None


async def esta_pausado() -> bool:
    """Verifica si la IA esta pausada. Lee de cache o DB."""
    await _cargar_pausa()
    if _pausa_hasta and _pausa_hasta > datetime.utcnow():
        return True
    return False

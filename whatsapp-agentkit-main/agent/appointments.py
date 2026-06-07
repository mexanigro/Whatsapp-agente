# agent/appointments.py — Cliente HTTP para endpoints de turnos en nichos-hub

import os
import time
import asyncio
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

NICHOS_HUB_URL = os.getenv("NICHOS_HUB_URL", "").rstrip("/")
AGENT_API_SECRET = os.getenv("AGENT_API_SECRET", "")
CLIENT_ID = os.getenv("CLIENT_ID", "")

_HEADERS = {"x-agent-secret": AGENT_API_SECRET}
_TIMEOUT = 10.0

# --- Cache de config de turnos (TTL 1 hora) ---
_config_cache: dict | None = None
_config_cache_ts: float = 0
_CONFIG_TTL = 3600

# --- Locks por slot para prevenir doble reserva concurrente ---
_slot_locks: dict[str, asyncio.Lock] = {}


def _obtener_slot_lock(key: str) -> asyncio.Lock:
    if key not in _slot_locks:
        _slot_locks[key] = asyncio.Lock()
    return _slot_locks[key]


def invalidar_cache_config():
    global _config_cache, _config_cache_ts
    _config_cache = None
    _config_cache_ts = 0


def obtener_config_cacheada() -> dict | None:
    """Retorna la config de turnos cacheada sin hacer request. None si no hay cache valido."""
    if _config_cache is not None and (time.monotonic() - _config_cache_ts) < _CONFIG_TTL:
        return _config_cache
    return None


async def _get(path: str, params: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.get(f"{NICHOS_HUB_URL}{path}", params=params, headers=_HEADERS)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.error(f"Error GET {path}: {e}")
        return {"error": str(e)}


async def _request(method: str, path: str, json_body: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.request(method, f"{NICHOS_HUB_URL}{path}", json=json_body, headers=_HEADERS)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        try:
            return e.response.json()
        except Exception:
            return {"error": str(e)}
    except Exception as e:
        logger.error(f"Error {method} {path}: {e}")
        return {"error": str(e)}


async def obtener_config_turnos(client_id: str | None = None) -> dict:
    """Obtiene servicios, staff y reglas del negocio desde nichos-hub.
    Cacheado en memoria con TTL de 1 hora."""
    global _config_cache, _config_cache_ts
    ahora = time.monotonic()
    if _config_cache is not None and (ahora - _config_cache_ts) < _CONFIG_TTL:
        logger.debug("Config turnos servida desde cache")
        return _config_cache
    cid = client_id or CLIENT_ID
    resultado = await _get("/api/appointments/config", {"clientId": cid})
    if "error" not in resultado:
        _config_cache = resultado
        _config_cache_ts = ahora
        logger.info("Config turnos cacheada desde nichos-hub")
    return resultado


async def consultar_disponibilidad(
    fecha: str, service_id: str, staff_id: str | None = None,
    client_id: str | None = None
) -> dict:
    """Consulta horarios disponibles para una fecha y servicio."""
    cid = client_id or CLIENT_ID
    params = {"clientId": cid, "date": fecha, "serviceId": service_id}
    if staff_id:
        params["staffId"] = staff_id
    return await _get("/api/appointments/available", params)


async def _notificar_booking_increment(client_id: str):
    """Fire-and-forget: incrementa contador de bookings en nichos-hub para tiers."""
    if not NICHOS_HUB_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            await c.post(
                f"{NICHOS_HUB_URL}/api/bookings/increment",
                json={"clientId": client_id, "source": "whatsapp"},
                headers=_HEADERS,
            )
    except Exception:
        logger.debug(f"No se pudo incrementar booking counter para {client_id}")


_CONFLICTO_KEYWORDS = [
    "conflict", "already", "ocupado", "taken", "unavailable",
    "no disponible", "booked", "overlap", "duplicat",
]


async def reservar_turno(
    customer_name: str, customer_phone: str,
    service_id: str, staff_id: str,
    fecha: str, hora: str,
    client_id: str | None = None
) -> dict:
    """Reserva un turno con proteccion de concurrencia.
    Si el slot fue tomado, retorna alternativas automaticamente."""
    cid = client_id or CLIENT_ID
    slot_key = f"{cid}:{fecha}:{hora}:{staff_id}"
    lock = _obtener_slot_lock(slot_key)

    async with lock:
        resultado = await _request("POST", "/api/appointments/book", {
            "clientId": cid,
            "customerName": customer_name,
            "customerPhone": customer_phone,
            "serviceId": service_id,
            "staffId": staff_id,
            "date": fecha,
            "time": hora,
        })

        error_str = str(resultado.get("error", "")).lower()
        if resultado.get("error") and any(kw in error_str for kw in _CONFLICTO_KEYWORDS):
            logger.warning(f"Conflicto de slot: {slot_key}")
            alternativas = await consultar_disponibilidad(fecha, service_id, staff_id, cid)
            return {
                "error": "slot_ocupado",
                "mensaje": "Ese horario acaba de ser reservado por otra persona.",
                "alternativas_disponibles": alternativas.get("slots", alternativas.get("available", []))
            }

        if "error" not in resultado:
            asyncio.create_task(_notificar_booking_increment(cid))

        return resultado


async def cancelar_turno(appointment_id: str, client_id: str | None = None) -> dict:
    """Cancela un turno existente."""
    cid = client_id or CLIENT_ID
    return await _request("PATCH", f"/api/appointments/{appointment_id}/cancel", {
        "clientId": cid,
    })

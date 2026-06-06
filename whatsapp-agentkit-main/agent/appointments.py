# agent/appointments.py — Cliente HTTP para endpoints de turnos en nichos-hub

import os
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
    """Obtiene servicios, staff y reglas del negocio desde nichos-hub."""
    cid = client_id or CLIENT_ID
    return await _get("/api/appointments/config", {"clientId": cid})


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


async def reservar_turno(
    customer_name: str, customer_phone: str,
    service_id: str, staff_id: str,
    fecha: str, hora: str,
    client_id: str | None = None
) -> dict:
    """Reserva un turno. customerEmail se genera automaticamente en el server."""
    cid = client_id or CLIENT_ID
    return await _request("POST", "/api/appointments/book", {
        "clientId": cid,
        "customerName": customer_name,
        "customerPhone": customer_phone,
        "serviceId": service_id,
        "staffId": staff_id,
        "date": fecha,
        "time": hora,
    })


async def cancelar_turno(appointment_id: str, client_id: str | None = None) -> dict:
    """Cancela un turno existente."""
    cid = client_id or CLIENT_ID
    return await _request("PATCH", f"/api/appointments/{appointment_id}/cancel", {
        "clientId": cid,
    })

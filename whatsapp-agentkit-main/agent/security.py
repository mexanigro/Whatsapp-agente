# agent/security.py — Funciones de seguridad centralizadas

import os
import re
import hmac
import logging
from fastapi import Request, HTTPException

logger = logging.getLogger("agentkit")

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# Limite de caracteres por mensaje entrante (evita payloads gigantes al LLM)
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "4000"))


def verificar_secret(request: Request):
    """Valida x-agent-secret con comparacion constant-time (anti timing-attack)."""
    agent_secret = os.getenv("AGENT_API_SECRET", "")
    if not agent_secret:
        raise HTTPException(status_code=500, detail="Servicio no configurado")
    secret = request.headers.get("x-agent-secret", "")
    if not hmac.compare_digest(secret, agent_secret):
        raise HTTPException(status_code=401, detail="No autorizado")


def enmascarar_telefono(telefono: str) -> str:
    """Enmascara un telefono para logs: +972XXXXXX1234 -> ***1234"""
    if not telefono:
        return "???"
    limpio = telefono.replace(" ", "").replace("-", "")
    if len(limpio) > 4:
        return f"***{limpio[-4:]}"
    return "***"


def sanitizar_para_log(texto: str, max_chars: int = 80) -> str:
    """Trunca texto para logging seguro, sin datos sensibles completos."""
    if not texto:
        return ""
    truncado = texto[:max_chars].replace("\n", " ").replace("\r", "")
    if len(texto) > max_chars:
        truncado += "..."
    return truncado


def sanitizar_mensaje_entrante(texto: str) -> str:
    """Limita longitud y limpia caracteres de control de mensajes entrantes."""
    if not texto:
        return ""
    # Remover caracteres de control excepto newlines
    limpio = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', texto)
    if len(limpio) > MAX_MESSAGE_LENGTH:
        limpio = limpio[:MAX_MESSAGE_LENGTH]
    return limpio.strip()


def error_seguro(e: Exception) -> str:
    """Retorna mensaje de error generico en produccion, detallado en development."""
    if ENVIRONMENT == "development":
        return str(e)
    return "Error interno del servidor"

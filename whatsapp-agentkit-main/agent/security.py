# agent/security.py — Funciones de seguridad centralizadas

import os
import re
import hmac
import time
import logging
from fastapi import Request, HTTPException

logger = logging.getLogger("agentkit")

ENVIRONMENT = os.getenv("ENVIRONMENT", "production")

# Limite de caracteres por mensaje entrante (evita payloads gigantes al LLM)
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "4000"))

# Ventana maxima para aceptar webhooks (anti-replay)
WEBHOOK_MAX_AGE_SECONDS = 300

# Patrones de prompt injection a neutralizar (no bloquear, solo sanitizar)
_PROMPT_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?prior\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?previous", re.IGNORECASE),
    re.compile(r"forget\s+(all\s+)?previous", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"act\s+as\s+(if\s+you\s+are|a)\s+", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"system\s*prompt\s*:", re.IGNORECASE),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"</\s*system\s*>", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
    re.compile(r"\[/INST\]", re.IGNORECASE),
    re.compile(r"<<\s*SYS\s*>>", re.IGNORECASE),
    re.compile(r"<<\s*/SYS\s*>>", re.IGNORECASE),
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"<\|im_end\|>", re.IGNORECASE),
    re.compile(r"Human\s*:\s*$", re.MULTILINE),
    re.compile(r"Assistant\s*:\s*$", re.MULTILINE),
]

# Encoding tricks: unicode homoglyphs y zero-width characters usados para evadir filtros
_UNICODE_SMUGGLING = re.compile(r'[​‌‍⁠﻿­]')


def verificar_secret(request: Request):
    """Valida x-agent-secret con comparacion constant-time (anti timing-attack)."""
    agent_secret = os.getenv("AGENT_API_SECRET", "")
    if not agent_secret:
        logger.error("AGENT_API_SECRET no configurado — endpoint inoperante")
        raise HTTPException(
            status_code=503,
            detail="AGENT_API_SECRET no configurado. Configurar en variables de entorno.",
        )
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


def _neutralizar_prompt_injection(texto: str) -> tuple[str, bool]:
    """Detecta y neutraliza patrones de prompt injection.
    Reemplaza patrones peligrosos con version inerte (entre corchetes).
    Returns (texto_limpio, fue_detectado)."""
    detectado = False
    resultado = texto

    resultado = _UNICODE_SMUGGLING.sub('', resultado)
    if resultado != texto:
        detectado = True

    for patron in _PROMPT_INJECTION_PATTERNS:
        if patron.search(resultado):
            detectado = True
            resultado = patron.sub(lambda m: f"[filtrado:{m.group(0)[:20]}]", resultado)

    return resultado, detectado


def sanitizar_mensaje_entrante(texto: str) -> str:
    """Limita longitud, limpia caracteres de control y neutraliza prompt injection."""
    if not texto:
        return ""
    limpio = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', texto)
    if len(limpio) > MAX_MESSAGE_LENGTH:
        limpio = limpio[:MAX_MESSAGE_LENGTH]
    limpio = limpio.strip()
    if not limpio:
        return ""

    limpio, inyeccion_detectada = _neutralizar_prompt_injection(limpio)
    if inyeccion_detectada:
        logger.warning(f"Prompt injection detectado y neutralizado (len={len(texto)})")

    return limpio


def verificar_timestamp_webhook(timestamp_header: str | None) -> bool:
    """Verifica que el timestamp del webhook no sea mayor a WEBHOOK_MAX_AGE_SECONDS.
    Si no hay header de timestamp, retorna True (compatibilidad)."""
    if not timestamp_header:
        return True
    try:
        ts = int(timestamp_header)
        age = abs(time.time() - ts)
        if age > WEBHOOK_MAX_AGE_SECONDS:
            logger.warning(f"Webhook replay detectado: age={age:.0f}s > {WEBHOOK_MAX_AGE_SECONDS}s")
            return False
        return True
    except (ValueError, TypeError):
        return True


def error_seguro(e: Exception) -> str:
    """Retorna mensaje de error generico en produccion, detallado en development."""
    if ENVIRONMENT == "development":
        return str(e)
    return "Error interno del servidor"

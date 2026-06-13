# agent/env_validator.py — Validacion de variables de entorno al arrancar

import os
import sys
import logging

logger = logging.getLogger("agentkit")


def validar_entorno() -> None:
    """Verifica variables criticas al arrancar.
    Si falta alguna marcada como critica, loguea el error y termina con exit(1).
    Diseñado para fallar rapido (fail-fast) antes de aceptar trafico."""

    environment = os.getenv("ENVIRONMENT", "production")
    provider = os.getenv("WHATSAPP_PROVIDER", "twilio").lower()
    skip_signature = os.getenv("TWILIO_SKIP_SIGNATURE", "false").lower() == "true"

    errores: list[str] = []
    advertencias: list[str] = []

    # ── Variables criticas siempre ─────────────────────────────────────────
    if not os.getenv("ANTHROPIC_API_KEY"):
        errores.append(
            "ANTHROPIC_API_KEY — requerida para llamar a Claude API; sin ella el agente no puede responder"
        )

    if not os.getenv("ADMIN_PHONE_NUMBER"):
        errores.append(
            "ADMIN_PHONE_NUMBER — sin esto nadie puede ejecutar #pausa/#volver/comandos admin"
        )

    # ── Variables criticas de Twilio ───────────────────────────────────────
    if provider == "twilio":
        if not os.getenv("TWILIO_ACCOUNT_SID"):
            errores.append(
                "TWILIO_ACCOUNT_SID — requerida para enviar mensajes via Twilio"
            )
        if not os.getenv("TWILIO_PHONE_NUMBER"):
            errores.append(
                "TWILIO_PHONE_NUMBER — numero de WhatsApp de la cuenta Twilio"
            )

        # TWILIO_AUTH_TOKEN es critico excepto en dev con TWILIO_SKIP_SIGNATURE=true
        if not os.getenv("TWILIO_AUTH_TOKEN"):
            if environment == "production" or not skip_signature:
                errores.append(
                    "TWILIO_AUTH_TOKEN — CRITICO: sin este valor el webhook no puede "
                    "validar la firma de Twilio, aceptando requests de cualquier origen"
                )
            else:
                advertencias.append(
                    "TWILIO_AUTH_TOKEN no configurado (desarrollo con TWILIO_SKIP_SIGNATURE=true — OK solo en dev)"
                )

    # ── Advertencias: importantes pero con fallback ────────────────────────
    if not os.getenv("AGENT_API_SECRET"):
        advertencias.append(
            "AGENT_API_SECRET no configurado — /notify, /status y /tasks/* devolveran 503; "
            "WebSocket de llamadas rechazado. Generar con: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    if not os.getenv("WEBHOOK_BASE_URL"):
        advertencias.append(
            "WEBHOOK_BASE_URL no configurado — se usaran headers x-forwarded-proto/host "
            "(puede causar errores de firma Twilio en Railway si el proxy no los envia correctamente)"
        )

    # ── Voz: advertencias si las APIs de voz no estan configuradas ─────────
    if not os.getenv("OPENAI_API_KEY"):
        advertencias.append(
            "OPENAI_API_KEY no configurado — notas de voz entrantes no podran ser transcritas (STT desactivado)"
        )

    if not os.getenv("ELEVENLABS_API_KEY") or not os.getenv("ELEVENLABS_VOICE_ID"):
        advertencias.append(
            "ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID no configurados — "
            "respuestas de voz (TTS) desactivadas; las notas de voz se responderan en texto"
        )

    # ── Emitir resultados ──────────────────────────────────────────────────
    for adv in advertencias:
        logger.warning("CONFIG: %s", adv)

    if errores:
        logger.critical("=" * 70)
        logger.critical("AGENTE NO PUEDE ARRANCAR — Variables de entorno criticas faltantes:")
        for err in errores:
            logger.critical("  ✗ %s", err)
        logger.critical("")
        logger.critical(
            "Configura las variables en Railway > Variables "
            "(o en tu archivo .env para desarrollo local)."
        )
        logger.critical("=" * 70)
        sys.exit(1)

    logger.info("Validacion de entorno OK (provider=%s, env=%s)", provider, environment)

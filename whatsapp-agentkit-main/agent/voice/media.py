# agent/voice/media.py — Descarga de media de Twilio + storage temporal de audios
#
# Los audios de respuesta se guardan en MEDIA_DIR y se sirven via GET /media/{id}.ogg
# (Twilio los descarga de ahi para enviarlos a WhatsApp). Se borran a las 24h.

import os
import re
import time
import secrets
import logging
from pathlib import Path
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "media_temp"))
# Limite de WhatsApp para media entrante: 16MB
MAX_MEDIA_BYTES = 16 * 1024 * 1024
MEDIA_TTL_HORAS = float(os.getenv("MEDIA_TTL_HORAS", "24"))

# Solo nombres generados por nosotros: token urlsafe + extension conocida
_PATRON_NOMBRE = re.compile(r"^[A-Za-z0-9_-]{16,64}\.(ogg|mp3)$")


def url_base_publica() -> str | None:
    """URL publica del servidor (para que Twilio descargue los audios)."""
    base = os.getenv("WEBHOOK_BASE_URL") or os.getenv("PUBLIC_BASE_URL")
    if base:
        return base.rstrip("/")
    dominio = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if dominio:
        return f"https://{dominio}"
    return None


async def descargar_media_twilio(media_url: str) -> tuple[bytes, str] | None:
    """Descarga el audio entrante de Twilio. Retorna (bytes, content_type) o None.

    Twilio responde 307 hacia un link firmado de S3. El primer request lleva
    auth Basic; el redirect se sigue SIN el header Authorization (S3 rechaza
    requests con doble mecanismo de auth).
    """
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    if not sid or not token:
        logger.error("Credenciales Twilio no configuradas, no se puede descargar media")
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            r = await client.get(media_url, auth=(sid, token))
            # Seguir redirects manualmente, sin reenviar el Authorization
            saltos = 0
            while r.status_code in (301, 302, 307, 308) and saltos < 3:
                destino = r.headers.get("location")
                if not destino:
                    break
                r = await client.get(destino)
                saltos += 1
        if r.status_code != 200:
            logger.error(f"Error descargando media Twilio: {r.status_code}")
            return None
        if len(r.content) > MAX_MEDIA_BYTES:
            logger.warning(f"Media demasiado grande ({len(r.content)} bytes), ignorando")
            return None
        content_type = r.headers.get("content-type", "audio/ogg")
        return r.content, content_type
    except Exception as e:
        logger.error(f"Error descargando media: {e}")
        return None


def guardar_audio_temporal(audio: bytes, extension: str = "ogg") -> str:
    """Guarda el audio generado y retorna el nombre de archivo publico."""
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    nombre = f"{secrets.token_urlsafe(24)}.{extension}"
    (MEDIA_DIR / nombre).write_bytes(audio)
    return nombre


def ruta_media(nombre: str) -> Path | None:
    """Valida el nombre (anti path-traversal) y retorna la ruta si existe."""
    if not _PATRON_NOMBRE.match(nombre):
        return None
    ruta = MEDIA_DIR / nombre
    return ruta if ruta.is_file() else None


def url_publica_media(nombre: str) -> str | None:
    base = url_base_publica()
    if not base:
        return None
    return f"{base}/media/{nombre}"


def limpiar_media_antigua():
    """Borra audios mas viejos que MEDIA_TTL_HORAS. Llamar desde el cron de limpieza."""
    if not MEDIA_DIR.is_dir():
        return 0
    limite = time.time() - MEDIA_TTL_HORAS * 3600
    borrados = 0
    for archivo in MEDIA_DIR.iterdir():
        try:
            if archivo.is_file() and archivo.stat().st_mtime < limite:
                archivo.unlink()
                borrados += 1
        except OSError:
            continue
    if borrados:
        logger.info(f"Limpieza media: {borrados} audio(s) borrado(s)")
    return borrados

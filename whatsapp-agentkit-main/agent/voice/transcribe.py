# agent/voice/transcribe.py — STT de notas de voz con OpenAI gpt-4o-mini-transcribe

import os
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# gpt-4o-mini-transcribe: $0.003/min, soporta los 5 idiomas (incluido hebreo)
MODELO_STT = os.getenv("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
COSTO_STT_POR_MINUTO = 0.003

# Extension de archivo segun content-type (OpenAI infiere el formato del nombre)
_EXTENSIONES = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "mp4",
    "audio/aac": "m4a",
    "audio/amr": "amr",
    "audio/wav": "wav",
    "audio/webm": "webm",
}


def stt_configurado() -> bool:
    return bool(OPENAI_API_KEY)


async def transcribir(audio: bytes, content_type: str = "audio/ogg") -> str | None:
    """Transcribe una nota de voz. Retorna el texto o None si falla.

    El modelo detecta el idioma solo (hebreo, espanol, ingles, ruso, arabe).
    """
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY no configurada, no se puede transcribir")
        return None
    if not audio:
        return None

    ext = _EXTENSIONES.get(content_type.split(";")[0].strip().lower(), "ogg")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                data={"model": MODELO_STT},
                files={"file": (f"nota.{ext}", audio, content_type)},
            )
        if r.status_code != 200:
            logger.error(f"Error OpenAI STT: {r.status_code} — {r.text[:200]}")
            return None
        texto = (r.json().get("text") or "").strip()
        if not texto:
            logger.warning("Transcripcion vacia (audio sin habla?)")
            return None
        logger.info(f"Transcripcion OK ({len(audio)} bytes -> {len(texto)} chars)")
        return texto
    except Exception as e:
        logger.error(f"Error transcribiendo audio: {e}")
        return None


def estimar_costo_stt(bytes_audio: int) -> float:
    """Estima el costo de transcripcion. OGG/Opus de WhatsApp ~ 2KB/seg."""
    minutos = bytes_audio / (2048 * 60)
    return round(minutos * COSTO_STT_POR_MINUTO, 6)

# agent/voice/tts.py — Generacion de audio con ElevenLabs Flash v2.5
#
# Genera OGG/Opus para que WhatsApp lo muestre como nota de voz nativa.
# Una voz por idioma (la misma "persona" debe sonar igual en todos los audios
# de un mismo idioma). Los voice_id se configuran por env.

import os
import re
import logging
import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
# Flash v2.5: $0.05/1K chars, ~75ms latencia, hebreo/arabe/ruso incluidos
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
# Opus 48kHz = contenedor OGG que WhatsApp renderiza como nota de voz
ELEVENLABS_OUTPUT_FORMAT = os.getenv("ELEVENLABS_OUTPUT_FORMAT", "opus_48000_32")
COSTO_TTS_POR_1K_CHARS = 0.05

# Voz default + override por idioma (mismo timbre, distinto voice_id si hace falta)
_VOICE_DEFAULT = os.getenv("ELEVENLABS_VOICE_ID", "")
_VOICES_POR_IDIOMA = {
    "es": os.getenv("ELEVENLABS_VOICE_ID_ES", ""),
    "en": os.getenv("ELEVENLABS_VOICE_ID_EN", ""),
    "he": os.getenv("ELEVENLABS_VOICE_ID_HE", ""),
    "ru": os.getenv("ELEVENLABS_VOICE_ID_RU", ""),
    "ar": os.getenv("ELEVENLABS_VOICE_ID_AR", ""),
}

# Parametros de prosodia (ver VOICE-HUMANIZATION.md):
# - stability baja-media: variacion emocional sin perder identidad
# - similarity_boost alto: mantiene el timbre de la voz elegida
# - style moderado: expresividad sin sobreactuar
VOICE_SETTINGS = {
    "stability": float(os.getenv("ELEVENLABS_STABILITY", "0.4")),
    "similarity_boost": float(os.getenv("ELEVENLABS_SIMILARITY", "0.8")),
    "style": float(os.getenv("ELEVENLABS_STYLE", "0.2")),
    "use_speaker_boost": True,
}


def tts_configurado() -> bool:
    return bool(ELEVENLABS_API_KEY and _VOICE_DEFAULT)


def obtener_voice_id(idioma: str = "") -> str:
    return _VOICES_POR_IDIOMA.get(idioma, "") or _VOICE_DEFAULT


def limpiar_texto_para_voz(texto: str) -> str:
    """Prepara el texto del LLM para TTS: quita separadores |||, emojis y
    markdown residual. El audio es UN solo bloque hablado."""
    texto = texto.replace("|||", ". ")
    # Quitar emojis y simbolos no hablables
    texto = re.sub(
        "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF✀-➿️]",
        "", texto)
    # Markdown residual (negritas, listas)
    texto = re.sub(r"[*_#`]+", "", texto)
    texto = re.sub(r"\n+", ". ", texto)
    texto = re.sub(r"\s{2,}", " ", texto)
    return texto.strip()


async def generar_audio(texto: str, idioma: str = "") -> bytes | None:
    """Genera audio OGG/Opus con ElevenLabs. Retorna bytes o None si falla
    (el caller hace fallback a texto — nunca dejar al cliente sin respuesta)."""
    if not ELEVENLABS_API_KEY:
        logger.error("ELEVENLABS_API_KEY no configurada, no se puede generar audio")
        return None
    voice_id = obtener_voice_id(idioma)
    if not voice_id:
        logger.error("ELEVENLABS_VOICE_ID no configurado, no se puede generar audio")
        return None

    texto = limpiar_texto_para_voz(texto)
    if not texto:
        return None

    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        f"?output_format={ELEVENLABS_OUTPUT_FORMAT}"
    )
    payload = {
        "text": texto,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": VOICE_SETTINGS,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                url,
                json=payload,
                headers={"xi-api-key": ELEVENLABS_API_KEY},
            )
        if r.status_code != 200:
            logger.error(f"Error ElevenLabs TTS: {r.status_code} — {r.text[:200]}")
            return None
        logger.info(
            f"TTS OK ({len(texto)} chars -> {len(r.content)} bytes, "
            f"~${estimar_costo_tts(texto):.4f})"
        )
        return r.content
    except Exception as e:
        logger.error(f"Error generando TTS: {e}")
        return None


def estimar_costo_tts(texto: str) -> float:
    return round(len(texto) / 1000 * COSTO_TTS_POR_1K_CHARS, 6)

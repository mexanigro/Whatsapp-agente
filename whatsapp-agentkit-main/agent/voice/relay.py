# agent/voice/relay.py — Llamadas de voz en vivo via Twilio ConversationRelay
#
# Flujo: cliente toca "llamar" en WhatsApp -> Twilio Voice webhook POST /voice
# -> TwiML <Connect><ConversationRelay> -> Twilio abre WebSocket /ws/voice
# -> por cada "prompt" (transcript del cliente) streameamos tokens de Claude
# -> Twilio los convierte a voz (ElevenLabs) y el cliente escucha en ~1s.
#
# Twilio maneja el audio (STT Google + TTS ElevenLabs). Nosotros solo texto.

import os
import json
import yaml
import hashlib
import logging
import asyncio
import httpx
from fastapi import WebSocket, WebSocketDisconnect
from dotenv import load_dotenv

from agent.brain import (
    client as claude_client, HERRAMIENTAS_TURNOS, _ejecutar_herramienta,
    _tiene_intencion_tools, MODELO_HAIKU,
)
from agent.memory import obtener_historial, obtener_lead, guardar_mensaje, registrar_costo
from agent.security import enmascarar_telefono, sanitizar_mensaje_entrante
from agent.horario import detectar_idioma_simple
from agent.voice.tts import obtener_voice_id, VOICE_SETTINGS

load_dotenv()
logger = logging.getLogger("agentkit")

# Claude Haiku para llamadas: latencia minima (la pausa larga delata al robot)
VOICE_CLAUDE_MODEL = os.getenv("VOICE_CLAUDE_MODEL", MODELO_HAIKU)
MAX_TOKENS_VOZ = int(os.getenv("VOICE_MAX_TOKENS", "300"))
CLIENT_ID = os.getenv("CLIENT_ID", "")

# Idioma corto -> codigo de ConversationRelay (STT Google / TTS)
# ar-IL: arabe israeli en Google STT (el negocio esta en Israel)
CODIGOS_IDIOMA = {
    "es": "es-ES",
    "en": "en-US",
    "he": "he-IL",
    "ru": "ru-RU",
    "ar": "ar-IL",
}
IDIOMA_DEFAULT = os.getenv("VOICE_IDIOMA_DEFAULT", "he")

# Cache del YAML de prompts de voz
_config_voz_cache: dict | None = None


def cargar_config_voz() -> dict:
    global _config_voz_cache
    if _config_voz_cache is not None:
        return _config_voz_cache
    try:
        with open("config/prompts_voice.yaml", "r", encoding="utf-8") as f:
            _config_voz_cache = yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.error("config/prompts_voice.yaml no encontrado")
        _config_voz_cache = {}
    return _config_voz_cache


def recargar_config_voz():
    global _config_voz_cache
    _config_voz_cache = None


def _token_ws_esperado() -> str:
    """Token para autenticar el WebSocket (va como query param en la URL TwiML).
    Derivado de AGENT_API_SECRET para no agregar otro secret."""
    explicito = os.getenv("VOICE_WS_TOKEN", "")
    if explicito:
        return explicito
    secret = os.getenv("AGENT_API_SECRET", "")
    if not secret:
        return ""
    return hashlib.sha256(f"{secret}:voice-ws".encode()).hexdigest()[:32]


def _voz_elevenlabs_cr(idioma: str) -> str:
    """Codifica la voz para ConversationRelay: [VoiceID]-[Model]-[Speed]_[Stability]_[Similarity]
    (formato verificado contra docs de Twilio, ver VOICE-HUMANIZATION.md 4.1).
    stability baja = voz mas emotiva; el blog de Twilio sugiere que el default
    interno de CR es 1.0 (monotona), por eso la fijamos explicita."""
    voice_id = obtener_voice_id(idioma)
    if not voice_id:
        return ""
    modelo = os.getenv("VOICE_CR_TTS_MODEL", "flash_v2_5")
    speed = float(os.getenv("ELEVENLABS_SPEED", "1.0"))
    stability = VOICE_SETTINGS["stability"]
    similarity = VOICE_SETTINGS["similarity_boost"]
    return f"{voice_id}-{modelo}-{speed}_{stability}_{similarity}"


def twiml_llamada(telefono: str, idioma: str, base_url: str) -> str:
    """Genera el TwiML <Connect><ConversationRelay> para una llamada entrante.
    La voz y el proveedor quedan fijados al iniciar (limitacion de CR);
    solo language puede cambiar mid-call.

    Atributos de humanizacion (ver VOICE-HUMANIZATION.md):
    - elevenlabsTextNormalization=on: red de seguridad para digitos que se escapen
      (los digitos causan alucinaciones de voz en Flash)
    - ignoreBackchannel=true: los "aja"/"ok" del cliente no cortan al agente
    - interruptSensitivity: barge-in real si corta, sin falsos positivos por ruido
    """
    config = cargar_config_voz()
    saludos = config.get("saludos", {})
    saludo = saludos.get(idioma) or saludos.get("he") or "Hello"
    codigo = CODIGOS_IDIOMA.get(idioma, "he-IL")
    voz = _voz_elevenlabs_cr(idioma)

    ws_url = base_url.replace("https://", "wss://").rstrip("/") + "/ws/voice"
    token = _token_ws_esperado()
    if token:
        ws_url += f"?token={token}"

    from twilio.twiml.voice_response import VoiceResponse, Connect

    response = VoiceResponse()
    connect = Connect()
    atributos = {
        "url": ws_url,
        "welcome_greeting": saludo,
        "language": codigo,
        "transcription_provider": "Google",
        "interruptible": "any",
        "interrupt_sensitivity": os.getenv("VOICE_INTERRUPT_SENSITIVITY", "medium"),
        "ignore_backchannel": True,
    }
    if voz:
        atributos["voice"] = voz
        atributos["tts_provider"] = "ElevenLabs"
        atributos["elevenlabs_text_normalization"] = "on"
    cr = connect.conversation_relay(**atributos)
    # El telefono viaja como custom parameter (llega en el mensaje "setup")
    cr.parameter(name="telefono", value=telefono)
    response.append(connect)
    return str(response)


def twiml_rechazar(razon: str = "busy") -> str:
    from twilio.twiml.voice_response import VoiceResponse
    response = VoiceResponse()
    response.reject(reason=razon)
    return str(response)


# --- Cerebro de voz: Claude streaming con historial compartido ---

async def _construir_system_voz(telefono: str, lead_negocio: str | None) -> list[dict]:
    config = cargar_config_voz()
    base = config.get("system_prompt_voz", "Sos Liam de Arzac Studio. Responde corto y hablado.")
    bloques = [{
        "type": "text",
        "text": base,
        "cache_control": {"type": "ephemeral"},
    }]

    # Servicios actuales (cacheados, mismo mecanismo que el chat)
    from agent.appointments import obtener_config_cacheada
    config_turnos = obtener_config_cacheada()
    if config_turnos and "error" not in config_turnos:
        servicios = config_turnos.get("services", [])
        if servicios:
            lineas = "\n".join(
                f"- {s.get('name', '')}: {s.get('price', '')} ({s.get('duration', '')} min)"
                for s in servicios
            )
            bloques.append({
                "type": "text",
                "text": f"\n\n## Servicios actuales\n{lineas}\n(Recorda: precios y duraciones EN PALABRAS al hablar)"
            })

    if lead_negocio:
        bloques.append({
            "type": "text",
            "text": f"\n\n## Contexto\nEsta persona es del negocio: {lead_negocio}. Ya la conoces, no preguntes quien es."
        })
    return bloques


class SesionLlamada:
    """Estado de una llamada en curso sobre el WebSocket."""

    def __init__(self):
        self.telefono: str = ""
        self.call_sid: str = ""
        self.idioma: str = IDIOMA_DEFAULT
        self.historial: list[dict] = []      # historial de chat previo (memoria unificada)
        self.turnos: list[dict] = []         # turnos de ESTA llamada
        self.lead_negocio: str | None = None
        self.tarea_actual: asyncio.Task | None = None
        self.escalada: bool = False


async def _enviar_token(ws: WebSocket, token: str, last: bool):
    await ws.send_text(json.dumps({"type": "text", "token": token, "last": last}))


async def _responder_prompt(ws: WebSocket, sesion: SesionLlamada, texto_usuario: str):
    """Genera la respuesta a un turno del cliente, streaming token a token.
    Soporta tool use (turnos) con filler hablado mientras ejecuta."""
    config = cargar_config_voz()
    system = await _construir_system_voz(sesion.telefono, sesion.lead_negocio)

    mensajes = list(sesion.historial) + list(sesion.turnos)
    mensajes.append({"role": "user", "content": texto_usuario})

    usar_tools = _tiene_intencion_tools(texto_usuario)
    respuesta_completa = ""
    total_in = total_out = cache_read = cache_creation = 0

    try:
        for iteracion in range(3):
            kwargs = {
                "model": VOICE_CLAUDE_MODEL,
                "max_tokens": MAX_TOKENS_VOZ,
                "system": system,
                "messages": mensajes,
            }
            if usar_tools:
                kwargs["tools"] = HERRAMIENTAS_TURNOS

            async with claude_client.messages.stream(**kwargs) as stream:
                async for token in stream.text_stream:
                    respuesta_completa += token
                    await _enviar_token(ws, token, last=False)
                final = await stream.get_final_message()

            usage = final.usage
            total_in += usage.input_tokens
            total_out += usage.output_tokens
            cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_creation += getattr(usage, "cache_creation_input_tokens", 0) or 0

            tool_uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                break

            # Filler hablado mientras la herramienta consulta (la pausa muda delata al bot)
            if not respuesta_completa.strip():
                filler = config.get("fillers_herramienta", {}).get(sesion.idioma, "")
                if filler:
                    respuesta_completa += filler + " "
                    await _enviar_token(ws, filler + " ", last=False)

            assistant_content = []
            for block in final.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use", "id": block.id,
                        "name": block.name, "input": block.input,
                    })
            mensajes.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in tool_uses:
                logger.info(f"[voz] Tool use: {block.name}")
                resultado = await _ejecutar_herramienta(block.name, block.input, sesion.telefono)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": resultado,
                })
            mensajes.append({"role": "user", "content": tool_results})

        # Fin del turno: last=True hace que Twilio cierre el buffer de TTS
        await _enviar_token(ws, "", last=True)

        sesion.turnos.append({"role": "user", "content": texto_usuario})
        sesion.turnos.append({"role": "assistant", "content": respuesta_completa or "(sin respuesta)"})

        if sesion.telefono and (total_in or total_out):
            costo = await registrar_costo(
                sesion.telefono, total_in, total_out, cache_read, cache_creation,
                client_id=CLIENT_ID, modelo=VOICE_CLAUDE_MODEL,
            )
            logger.info(f"[voz] Turno: {total_in} in / {total_out} out (${costo:.6f})")

        # Escalacion pedida: avisar y cortar elegante
        if sesion.escalada:
            await ws.send_text(json.dumps({"type": "end", "reason": "escalada a humano"}))

    except asyncio.CancelledError:
        # Cliente interrumpio (barge-in): registrar lo dicho hasta aca y salir
        if respuesta_completa.strip():
            sesion.turnos.append({"role": "user", "content": texto_usuario})
            sesion.turnos.append({
                "role": "assistant",
                "content": respuesta_completa + " [interrumpido por el cliente]"
            })
        raise
    except Exception as e:
        logger.error(f"[voz] Error generando respuesta: {e}")
        errores = {
            "es": "Perdon, se me corto un segundo, que me decias?",
            "en": "Sorry, I lost you for a second, what were you saying?",
            "he": "סליחה, נקטעתי לשנייה, מה אמרת?",
            "ru": "Извините, прервалось на секунду, что вы говорили?",
            "ar": "معلش، قطع معي لحظة، شو كنت عم تحكي؟",
        }
        await _enviar_token(ws, errores.get(sesion.idioma, errores["es"]), last=True)


async def _guardar_resumen_llamada(sesion: SesionLlamada):
    """Al cortar: guarda un resumen en el historial del telefono para que el
    proximo chat (o llamada) tenga el contexto. Memoria unificada chat<->voz."""
    if not sesion.telefono or not sesion.turnos:
        return
    try:
        transcript = "\n".join(
            f"{'Cliente' if t['role'] == 'user' else 'Liam'}: {t['content']}"
            for t in sesion.turnos if isinstance(t.get("content"), str)
        )[:4000]
        r = await claude_client.messages.create(
            model=MODELO_HAIKU,
            max_tokens=150,
            system="Resumi esta llamada telefonica en 1-2 frases, en el idioma de la conversacion: que pregunto el cliente y en que quedaron. Solo el resumen, nada mas.",
            messages=[{"role": "user", "content": transcript}],
        )
        resumen = r.content[0].text.strip() if r.content else ""
        if resumen:
            await guardar_mensaje(
                sesion.telefono, "assistant", f"[Llamada de voz] {resumen}"
            )
            logger.info(f"[voz] Resumen guardado para {enmascarar_telefono(sesion.telefono)}")
    except Exception as e:
        logger.error(f"[voz] No se pudo guardar resumen: {e}")


def _detectar_pedido_humano(texto: str) -> bool:
    """Heuristica simple: el cliente pide hablar con una persona."""
    señales = [
        "con una persona", "con un humano", "con liam", "persona real",
        "talk to a person", "real person", "speak to someone",
        "בן אדם", "נציג אנושי", "לדבר עם מישהו",
        "с человеком", "живой человек",
        "بني آدم", "انسان حقيقي",
    ]
    texto_lower = texto.lower()
    return any(s in texto_lower for s in señales)


async def manejar_websocket_voz(ws: WebSocket):
    """Handler principal del WebSocket de ConversationRelay."""
    # Auth: token por query param (la URL la generamos nosotros en el TwiML)
    esperado = _token_ws_esperado()
    recibido = ws.query_params.get("token", "")
    if esperado and recibido != esperado:
        logger.warning("[voz] WebSocket rechazado: token invalido")
        await ws.close(code=1008)
        return
    if not esperado and os.getenv("ENVIRONMENT", "production") == "production":
        logger.error("[voz] AGENT_API_SECRET/VOICE_WS_TOKEN no configurado — WebSocket rechazado")
        await ws.close(code=1008)
        return

    await ws.accept()
    sesion = SesionLlamada()
    logger.info("[voz] WebSocket de llamada conectado")

    try:
        while True:
            data = await ws.receive_json()
            tipo = data.get("type", "")

            if tipo == "setup":
                sesion.call_sid = data.get("callSid", "")
                # El telefono viene en customParameters (lo pusimos en el TwiML)
                # y tambien en "from" (whatsapp:+972...)
                params = data.get("customParameters", {}) or {}
                crudo = params.get("telefono") or data.get("from", "")
                sesion.telefono = crudo.replace("whatsapp:", "").strip()
                if sesion.telefono:
                    sesion.historial = await obtener_historial(sesion.telefono)
                    sesion.lead_negocio = await obtener_lead(sesion.telefono)
                    # Idioma: del ultimo mensaje de chat si existe
                    for m in reversed(sesion.historial):
                        if m.get("role") == "user" and m.get("content"):
                            sesion.idioma = detectar_idioma_simple(m["content"])
                            break
                logger.info(
                    f"[voz] Llamada de {enmascarar_telefono(sesion.telefono)} "
                    f"(idioma={sesion.idioma}, historial={len(sesion.historial)} msgs)"
                )

            elif tipo == "prompt":
                texto = sanitizar_mensaje_entrante(data.get("voicePrompt", "") or "")
                if not texto:
                    continue
                # Actualizar idioma con lo que realmente habla el cliente
                sesion.idioma = detectar_idioma_simple(texto)
                if _detectar_pedido_humano(texto):
                    sesion.escalada = True
                # Si habia una generacion en curso (raro, CR espera el turno), cancelarla
                if sesion.tarea_actual and not sesion.tarea_actual.done():
                    sesion.tarea_actual.cancel()
                sesion.tarea_actual = asyncio.create_task(
                    _responder_prompt(ws, sesion, texto)
                )

            elif tipo == "interrupt":
                # Barge-in: el cliente hablo encima del TTS. Cortar la generacion.
                if sesion.tarea_actual and not sesion.tarea_actual.done():
                    sesion.tarea_actual.cancel()
                logger.debug("[voz] Cliente interrumpio el TTS")

            elif tipo == "error":
                logger.error(f"[voz] Error de ConversationRelay: {data.get('description', '')}")

            # dtmf / info: ignorados por ahora

    except WebSocketDisconnect:
        logger.info(f"[voz] Llamada terminada ({enmascarar_telefono(sesion.telefono)})")
    except Exception as e:
        logger.error(f"[voz] Error en WebSocket: {e}")
    finally:
        if sesion.tarea_actual and not sesion.tarea_actual.done():
            sesion.tarea_actual.cancel()
        await _guardar_resumen_llamada(sesion)
        if sesion.escalada:
            try:
                from agent.escalacion import escalar
                await escalar(
                    sesion.telefono,
                    "[Llamada de voz] El cliente pidio hablar con una persona",
                    ["pedido_humano_en_llamada"], 30
                )
            except Exception as e:
                logger.error(f"[voz] No se pudo escalar: {e}")
        try:
            from agent import analytics
            await analytics.registrar_evento(
                "llamada_voz", sesion.telefono,
                {"turnos": len(sesion.turnos) // 2, "escalada": sesion.escalada},
                client_id=CLIENT_ID
            )
        except Exception:
            pass


# --- Llamadas salientes (comando admin #llamar o recordatorios) ---

async def iniciar_llamada_saliente(telefono: str) -> bool:
    """Inicia una llamada WhatsApp saliente. El webhook /voice atiende cuando
    el cliente acepta (mismo ConversationRelay que las entrantes).

    Restriccion Meta: el cliente tiene que haber dado permiso de llamada
    previamente (se pide via mensaje interactivo de WhatsApp)."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    numero = os.getenv("TWILIO_PHONE_NUMBER", "")
    from agent.voice.media import url_base_publica
    base = url_base_publica()
    if not all([sid, token, numero, base]):
        logger.error("[voz] Faltan credenciales o WEBHOOK_BASE_URL para llamada saliente")
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
                auth=(sid, token),
                data={
                    "To": f"whatsapp:{telefono}",
                    "From": f"whatsapp:{numero}",
                    "Url": f"{base}/voice",
                    "Method": "POST",
                },
            )
        if r.status_code != 201:
            logger.error(f"[voz] Error iniciando llamada: {r.status_code} — {r.text[:200]}")
            return False
        logger.info(f"[voz] Llamada saliente iniciada a {enmascarar_telefono(telefono)}")
        return True
    except Exception as e:
        logger.error(f"[voz] Error iniciando llamada saliente: {e}")
        return False

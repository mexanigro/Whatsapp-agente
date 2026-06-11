# agent/brain.py — Cerebro del agente: conexion con Claude API + tool use

import os
import re
import json
import yaml
import logging
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=30.0, max_retries=2)
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
MODELO_HAIKU = "claude-haiku-4-5-20251001"

# Cache del system prompt a nivel modulo (no releer YAML en cada request)
_system_prompt_cache: str | None = None
_config_cache: dict | None = None


def cargar_config_prompts() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    try:
        with open("config/prompts.yaml", "r", encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f) or {}
            return _config_cache
    except FileNotFoundError:
        logger.error("config/prompts.yaml no encontrado")
        return {}


def cargar_system_prompt() -> str:
    global _system_prompt_cache
    if _system_prompt_cache is not None:
        return _system_prompt_cache
    config = cargar_config_prompts()
    _system_prompt_cache = config.get("system_prompt", "Eres un asistente util. Responde en espanol.")
    return _system_prompt_cache


def recargar_config():
    global _system_prompt_cache, _config_cache, _calendar_status
    _system_prompt_cache = None
    _config_cache = None
    _calendar_status = None


def obtener_mensaje_error() -> str:
    config = cargar_config_prompts()
    return config.get("error_message", "Lo siento, estoy teniendo problemas tecnicos. Por favor intenta de nuevo en unos minutos.")


def obtener_mensaje_fallback() -> str:
    config = cargar_config_prompts()
    return config.get("fallback_message", "Disculpa, no entendi tu mensaje. Podrias reformularlo?")


# --- Definiciones de herramientas para turnos ---

HERRAMIENTAS_TURNOS = [
    {
        "name": "obtener_config_turnos",
        "description": "Obtiene la lista de servicios disponibles con precios y duracion, los profesionales del negocio y las reglas de reserva. Usar al inicio de una consulta sobre servicios, precios o turnos.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "consultar_disponibilidad",
        "description": "Consulta los horarios disponibles para un servicio en una fecha. Necesita el service_id (obtenerlo primero con obtener_config_turnos).",
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "Fecha en formato YYYY-MM-DD"
                },
                "service_id": {
                    "type": "string",
                    "description": "ID del servicio"
                },
                "staff_id": {
                    "type": "string",
                    "description": "ID del profesional (opcional, si no se especifica muestra todos)"
                }
            },
            "required": ["fecha", "service_id"]
        }
    },
    {
        "name": "reservar_turno",
        "description": "Reserva un turno para el cliente. Solo usar cuando el cliente confirme fecha, hora, servicio y profesional.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Nombre del cliente"
                },
                "service_id": {
                    "type": "string",
                    "description": "ID del servicio a reservar"
                },
                "staff_id": {
                    "type": "string",
                    "description": "ID del profesional"
                },
                "fecha": {
                    "type": "string",
                    "description": "Fecha en formato YYYY-MM-DD"
                },
                "hora": {
                    "type": "string",
                    "description": "Hora en formato HH:mm"
                }
            },
            "required": ["customer_name", "service_id", "staff_id", "fecha", "hora"]
        }
    },
    {
        "name": "cancelar_turno",
        "description": "Cancela un turno existente por su ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "string",
                    "description": "ID del turno a cancelar"
                }
            },
            "required": ["appointment_id"]
        }
    }
]

# Palabras clave para activar herramientas (turno + servicios/precios)
_PALABRAS_TOOLS = {
    "turno", "turnos", "cita", "reserva", "reservar", "agendar", "cancelar",
    "disponibilidad", "horario", "horarios",
    "servicio", "servicios", "precio", "precios",
    "cuanto cuesta", "cuanto sale", "que ofrecen",
    "appointment", "book", "cancel", "available",
    "תור", "תורים", "לקבוע", "ביטול", "שירות", "שירותים", "מחיר", "מחירים",
    "записаться", "запись", "отмена", "услуг", "цена",
}


def _tiene_intencion_tools(texto: str) -> bool:
    texto_lower = texto.lower()
    return any(p in texto_lower for p in _PALABRAS_TOOLS)


# --- Clasificador de modelo Sonnet/Haiku ---

_PALABRAS_BOOKING = {
    "reservar", "agendar", "cancelar", "cancela", "cambiar turno", "mover turno",
    "reagendar", "modificar turno", "anular",
    "book", "cancel", "reschedule", "appointment",
    "לקבוע", "ביטול", "לבטל",
    "записаться", "отменить", "отмена", "перенести",
}

_INDICADORES_FLUJO_BOOKING = [
    "turno", "reserv", "confirm", "disponib",
    "horario disponible", "agendar", "appointment", "booking",
]


def clasificar_modelo(mensaje: str, historial: list[dict]) -> str:
    """Decide si usar Haiku (barato) o Sonnet (calidad).
    Sonnet: booking, cancelaciones, flujo de herramientas activo.
    Haiku: todo lo demas."""
    texto = mensaje.strip().lower()

    if any(p in texto for p in _PALABRAS_BOOKING):
        return CLAUDE_MODEL

    if historial:
        ultimos = historial[-3:]
        for msg in ultimos:
            contenido = msg.get("content", "").lower()
            if any(kw in contenido for kw in _INDICADORES_FLUJO_BOOKING):
                return CLAUDE_MODEL

    return MODELO_HAIKU


# Cache del estado del calendario (se invalida con recargar_config)
_calendar_status: bool | None = None


async def _obtener_estado_calendario() -> bool:
    """Retorna True si el calendario esta conectado."""
    global _calendar_status
    if _calendar_status is not None:
        return _calendar_status
    from agent.memory import obtener_config
    valor = await obtener_config("calendar_connected")
    # Default: conectado (true) si no hay registro
    _calendar_status = valor != "false"
    return _calendar_status


async def _ejecutar_herramienta(nombre: str, argumentos: dict, telefono: str) -> str:
    from agent.appointments import (
        consultar_disponibilidad, reservar_turno,
        cancelar_turno, obtener_config_turnos
    )

    if nombre == "obtener_config_turnos":
        resultado = await obtener_config_turnos()
    elif nombre == "consultar_disponibilidad":
        resultado = await consultar_disponibilidad(
            argumentos["fecha"],
            argumentos["service_id"],
            argumentos.get("staff_id")
        )
    elif nombre == "reservar_turno":
        resultado = await reservar_turno(
            argumentos["customer_name"],
            telefono,
            argumentos["service_id"],
            argumentos["staff_id"],
            argumentos["fecha"],
            argumentos["hora"]
        )
    elif nombre == "cancelar_turno":
        resultado = await cancelar_turno(argumentos["appointment_id"])
    else:
        resultado = {"error": f"Herramienta desconocida: {nombre}"}

    return json.dumps(resultado, ensure_ascii=False)


# Bloque extra de system prompt cuando la respuesta sera convertida a audio.
# Tecnicas de VOICE-HUMANIZATION.md: hablado, corto, numeros en palabras.
BLOQUE_CANAL_VOZ = """

## Canal de esta respuesta: NOTA DE VOZ (critico)
El cliente mando un audio y tu respuesta va a ser convertida a voz y enviada como nota de voz. Reglas que REEMPLAZAN al formato WhatsApp:
- UN solo bloque de texto hablado. NUNCA uses el separador ||| ni dividas en mensajes.
- Escribi como se habla, no como se escribe: frases cortas, contracciones, arranque natural ("Hola, mira", "Bueno, te cuento", "Dale, si").
- Numeros, horas y precios en palabras: "setecientos noventa shekels", "a las diez y media", nunca "790" ni "10:30".
- Nada de emojis, listas, markdown ni URLs. Si tenes que pasar un link o dato exacto, decilo en el audio ("te lo paso por escrito ahora") y corta ahi.
- Largo maximo: lo que se dice en quince o veinte segundos (2 a 4 frases). Nadie manda audios largos para confirmar un turno.
- Esta permitido (y suma) alguna muletilla natural del idioma: "eh", "mira", "viste" en espanol; equivalentes en hebreo/ingles/ruso/arabe. Maximo una por audio."""


async def generar_respuesta(mensaje: str, historial: list[dict],
                            lead_negocio: str | None = None,
                            telefono: str | None = None,
                            canal: str = "texto") -> str:
    # Solo fallback si viene realmente vacio. Un "ok", "k" o un emoji solo
    # son mensajes validos: responder "no te entendi" a un pulgar arriba
    # delata al bot. El LLM sabe reaccionar natural a eso.
    if not mensaje or not mensaje.strip():
        return obtener_mensaje_fallback()

    # --- Routing inteligente Sonnet/Haiku ---
    modelo_usar = clasificar_modelo(mensaje, historial)

    base_prompt = cargar_system_prompt()

    system_blocks = [
        {
            "type": "text",
            "text": base_prompt,
            "cache_control": {"type": "ephemeral"}
        }
    ]

    # Inyectar config de turnos cacheada (evita tool call para preguntas de info)
    from agent.appointments import obtener_config_cacheada
    config_turnos = obtener_config_cacheada()
    if config_turnos and "error" not in config_turnos:
        servicios = config_turnos.get("services", [])
        staff = config_turnos.get("staff", [])
        if servicios:
            lineas_servicios = "\n".join(
                f"- {s.get('name', '')}: {s.get('price', '')} ({s.get('duration', '')} min)"
                for s in servicios
            )
            lineas_staff = "\n".join(
                f"- {s.get('name', '')}" for s in staff
            ) if staff else "No configurado"
            system_blocks.append({
                "type": "text",
                "text": (
                    f"\n\n## Servicios y profesionales actuales\n{lineas_servicios}"
                    f"\n\nProfesionales:\n{lineas_staff}"
                )
            })

    if lead_negocio:
        system_blocks.append({
            "type": "text",
            "text": f"\n\n## Contexto de esta conversacion\nEsta persona es del negocio: {lead_negocio}. Ya le construimos una web demo y se la mandamos. Sabes exactamente quien es y de que negocio se trata, no necesitas preguntarle. Usa el nombre del negocio de forma natural en la conversacion."
        })

    if telefono:
        from agent.pausa import es_admin
        if es_admin(telefono):
            system_blocks.append({
                "type": "text",
                "text": (
                    "\n\n## Info interna — tiers de pricing (solo para el admin)\n"
                    "Planes de Arzac Studio segun bookings mensuales:\n"
                    "- Base 770 ₪/mes: hasta 100 bookings\n"
                    "- Pro 960 ₪/mes: hasta 300 bookings\n"
                    "- Enterprise 1,270 ₪/mes: ilimitado\n"
                    "Esta info es interna del SaaS. NUNCA mencionarla a clientes finales."
                )
            })

    if telefono:
        try:
            from agent.analytics import es_cliente_recurrente
            recurrencia = await es_cliente_recurrente(telefono, dias=90)
            if recurrencia["recurrente"]:
                turnos = recurrencia["turnos_previos"]
                bloque = (
                    "\n\n## Cliente recurrente\nEsta persona ya interactuo antes "
                    f"({recurrencia['total_mensajes']} mensajes previos"
                )
                if turnos > 0:
                    bloque += f", {turnos} turnos agendados"
                bloque += "). Saludala con familiaridad, no te presentes de nuevo."
                system_blocks.append({"type": "text", "text": bloque})
        except Exception as e:
            logger.debug(f"No se pudo evaluar recurrencia para {telefono}: {e}")

    if canal == "voz":
        system_blocks.append({"type": "text", "text": BLOQUE_CANAL_VOZ})

    calendar_status = await _obtener_estado_calendario()
    if not calendar_status:
        system_blocks.append({
            "type": "text",
            "text": "\n\n## Estado del sistema de turnos\nEl calendario esta temporalmente desconectado. NO ofrezcas agendar turnos por ahora. Si alguien pregunta por turnos, decile que el sistema de reservas esta en mantenimiento y que nos escriba mas tarde o nos llame."
        })

    mensajes = []
    for msg in historial:
        mensajes.append({
            "role": msg["role"],
            "content": msg["content"]
        })
    mensajes.append({
        "role": "user",
        "content": mensaje
    })

    # Tools solo con Sonnet y calendario conectado
    usar_tools = (
        _tiene_intencion_tools(mensaje)
        and calendar_status
        and modelo_usar == CLAUDE_MODEL
    )
    max_iteraciones = 3

    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_creation = 0

    logger.info(f"Modelo seleccionado: {modelo_usar} | tools={usar_tools}")

    try:
        response = None

        for iteracion in range(max_iteraciones):
            kwargs = {
                "model": modelo_usar,
                "max_tokens": 1024,
                "system": system_blocks,
                "messages": mensajes,
            }
            if usar_tools:
                kwargs["tools"] = HERRAMIENTAS_TURNOS

            response = await client.messages.create(**kwargs)

            usage = response.usage
            total_input += usage.input_tokens
            total_output += usage.output_tokens
            total_cache_read += getattr(usage, 'cache_read_input_tokens', 0) or 0
            total_cache_creation += getattr(usage, 'cache_creation_input_tokens', 0) or 0

            has_tool_use = any(
                getattr(block, 'type', None) == "tool_use"
                for block in response.content
            )

            if response.stop_reason == "end_turn" or not has_tool_use:
                break

            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            mensajes.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"Tool use: {block.name}({json.dumps(block.input, ensure_ascii=False)[:200]})")
                    resultado = await _ejecutar_herramienta(
                        block.name, block.input, telefono or ""
                    )
                    logger.info(f"Tool result: {resultado[:200]}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": resultado,
                    })

            mensajes.append({"role": "user", "content": tool_results})

        texto_final = ""
        if response:
            for block in response.content:
                if getattr(block, 'type', None) == "text":
                    texto_final = block.text
                    break

        if not texto_final:
            texto_final = obtener_mensaje_fallback()

        logger.info(
            f"Tokens totales ({modelo_usar}): {total_input} in / {total_output} out | "
            f"Cache: {total_cache_read} read / {total_cache_creation} creation"
        )

        if telefono:
            from agent.memory import registrar_costo
            costo = await registrar_costo(
                telefono, total_input, total_output,
                total_cache_read, total_cache_creation,
                client_id=os.getenv("CLIENT_ID", ""),
                modelo=modelo_usar
            )
            logger.info(f"Costo request ({modelo_usar}): ${costo:.6f}")

        return texto_final

    except Exception as e:
        logger.error(f"Error Claude API: {e}")
        return obtener_mensaje_error()

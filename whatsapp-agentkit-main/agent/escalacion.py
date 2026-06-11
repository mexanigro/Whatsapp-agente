# agent/escalacion.py — Deteccion de urgencia y escalacion al humano (Liam)
#
# Cuando un cliente expresa frustracion, urgencia, queja o pide hablar con humano,
# notificamos a Liam por WhatsApp para que tome el control.
# Se pausa la IA automaticamente 30 min para evitar que siga respondiendo
# mientras Liam atiende.

import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from agent.security import enmascarar_telefono

logger = logging.getLogger("agentkit")

# Palabras clave por idioma (lowercase). Multi-idioma porque el negocio atiende
# espanol/ingles/hebreo/ruso/arabe.
PALABRAS_URGENCIA = {
    # Espanol
    "urgente", "urgencia", "emergencia", "ya mismo", "ahora mismo",
    "inmediato", "inmediatamente", "rapido por favor",
    # Frustracion espanol
    "estafa", "fraude", "denunciar", "demanda", "abogado",
    "queja formal", "queja", "reclamo", "molesto", "indignado",
    "harto", "no funciona", "esto es un desastre", "pesimo", "horrible",
    "nunca mas", "cancelar todo", "devolver el dinero", "reembolso",
    # Pedir humano espanol
    "hablar con humano", "hablar con persona", "persona real", "no eres humano",
    "no sos humano", "sos un bot", "eres un bot", "habla con liam",
    "quiero hablar con", "necesito hablar con",
    # Ingles
    "urgent", "emergency", "asap", "right now", "immediately",
    "scam", "fraud", "report you", "lawyer", "lawsuit", "complaint",
    "refund", "cancel everything", "this is terrible", "terrible service",
    "speak to human", "talk to human", "real person", "are you a bot",
    "you're a bot", "talk to liam",
    # Hebreo
    "דחוף", "מיידי", "עכשיו", "הונאה", "תרמית", "תלונה",
    "החזר כספי", "לבטל הכל", "בן אדם אמיתי", "אתה בוט",
    "לדבר עם", "אני רוצה לדבר",
    # Ruso
    "срочно", "немедленно", "мошенник", "жалоба", "вернуть деньги",
    "поговорить с человеком", "ты бот",
    # Arabe
    "عاجل", "فورا", "احتيال", "شكوى", "استرداد",
    "اريد التحدث مع", "هل انت روبوت",
}

# Patrones regex adicionales (caps lock excesivo, signos de !!! repetidos)
_PATRON_GRITO = re.compile(r"[A-Z֐-׿Ѐ-ӿ]{10,}")
_PATRON_EXCLAMACION = re.compile(r"!{3,}|\?{3,}")


def detectar_urgencia(texto: str) -> tuple[bool, list[str]]:
    """Detecta si un mensaje requiere escalacion.

    Returns:
        (es_urgente, razones) — razones es una lista de tags como ['palabra:urgente', 'caps']
    """
    if not texto:
        return False, []

    razones = []
    texto_lower = texto.lower()

    for palabra in PALABRAS_URGENCIA:
        if palabra in texto_lower:
            razones.append(f"palabra:{palabra}")
            break  # Una palabra alcanza

    if _PATRON_GRITO.search(texto):
        razones.append("caps_excesivo")

    if _PATRON_EXCLAMACION.search(texto):
        razones.append("exclamacion_repetida")

    es_urgente = len(razones) > 0
    return es_urgente, razones


async def ya_escalado_recientemente(telefono: str, ventana_minutos: int = 60) -> bool:
    """Evita spam de notificaciones: si ya escalamos en la ultima hora, no duplicar."""
    from agent.memory import obtener_config
    clave = f"escalacion:{telefono}"
    valor = await obtener_config(clave)
    if not valor:
        return False
    try:
        ts = datetime.fromisoformat(valor)
        return datetime.utcnow() - ts < timedelta(minutes=ventana_minutos)
    except (ValueError, TypeError):
        return False


async def marcar_escalado(telefono: str):
    """Marca un telefono como escalado recientemente."""
    from agent.memory import guardar_config
    await guardar_config(f"escalacion:{telefono}", datetime.utcnow().isoformat())


async def escalar(telefono: str, texto_original: str, razones: list[str],
                  pausar_minutos: int = 30) -> Optional[str]:
    """Notifica a Liam (admin) que hay una conversacion urgente y pausa la IA.

    Returns el mensaje enviado al admin (para logs), o None si no se pudo escalar
    (ej: ya escalado recientemente, admin no configurado).
    """
    import os
    numero_admin = os.getenv("ADMIN_PHONE_NUMBER", "")
    if not numero_admin:
        logger.warning("ADMIN_PHONE_NUMBER no configurado, no se puede escalar")
        return None

    if await ya_escalado_recientemente(telefono):
        logger.info(f"Telefono {enmascarar_telefono(telefono)} ya escalado recientemente, saltando notificacion")
        return None

    from agent.providers import obtener_proveedor
    from agent.pausa import _guardar_pausa
    from agent.memory import obtener_lead

    lead_negocio = await obtener_lead(telefono)
    contexto_lead = f"\nNegocio: {lead_negocio}" if lead_negocio else ""
    razones_str = ", ".join(razones)
    extracto = texto_original[:200].replace("\n", " ")

    # Telefono completo intencional: admin necesita el numero para contactar al cliente
    mensaje = (
        f"ESCALACION URGENTE\n"
        f"De: {telefono}{contexto_lead}\n"
        f"Razon: {razones_str}\n"
        f"Mensaje: {extracto}\n\n"
        f"IA pausada 30 min para que tomes el control."
    )

    proveedor = obtener_proveedor()
    ok = await proveedor.enviar_mensaje(numero_admin, mensaje)
    if ok:
        # Pausar IA para que el admin tome el control sin interferencia
        hasta = datetime.utcnow() + timedelta(minutes=pausar_minutos)
        await _guardar_pausa(hasta)
        await marcar_escalado(telefono)
        logger.warning(f"Escalacion exitosa: {enmascarar_telefono(telefono)} -> admin. IA pausada {pausar_minutos}min")
        return mensaje
    else:
        logger.error(f"Error escalando a admin: provider no pudo enviar mensaje")
        return None

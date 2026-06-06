# agent/notifications.py — Centraliza notificaciones a admin, staff y clientes
#
# Tipos de notificaciones que maneja:
# 1. Admin (Liam): nuevo lead, turno, escalacion, calendar desconectado, etc.
# 2. Staff: turno asignado, cambio de horario
# 3. Cliente: confirmacion turno, recordatorio 24h, follow-up post-servicio
#
# Cada tipo soporta envio como texto libre (dentro de service window de 24h)
# o como template aprobado (fuera de window).

import os
import logging
from typing import Optional

logger = logging.getLogger("agentkit")

# Mapeo de tipos de notificacion a ContentSid de templates Twilio.
# Estos se configuran en Twilio Console y se guardan en env vars.
# Si no esta configurado, se envia como texto libre.
TEMPLATES_ENV_MAP = {
    "appointment_confirmation_client": "TWILIO_TEMPLATE_APPT_CONFIRMATION",
    "appointment_reminder_client": "TWILIO_TEMPLATE_APPT_REMINDER",
    "appointment_review_request_client": "TWILIO_TEMPLATE_APPT_REVIEW",
    "appointment_cancelled_client": "TWILIO_TEMPLATE_APPT_CANCELLED",
    "appointment_assigned_staff": "TWILIO_TEMPLATE_APPT_STAFF",
    "new_lead_admin": "TWILIO_TEMPLATE_NEW_LEAD",
    "lead_follow_up_client": "TWILIO_TEMPLATE_LEAD_FOLLOWUP",
}


def obtener_template_sid(tipo: str) -> Optional[str]:
    """Retorna el ContentSid del template para este tipo de notificacion, o None."""
    env_var = TEMPLATES_ENV_MAP.get(tipo)
    if not env_var:
        return None
    sid = os.getenv(env_var, "").strip()
    return sid if sid else None


async def notificar(telefono: str, tipo: str, mensaje_libre: str,
                    variables: Optional[dict] = None) -> bool:
    """Envia una notificacion. Usa template si esta configurado, sino texto libre.

    Args:
        telefono: destinatario (en formato E.164 o como acepta el provider)
        tipo: clave en TEMPLATES_ENV_MAP — define que template usar
        mensaje_libre: texto a enviar si no hay template configurado
        variables: variables para el template (si aplica)

    Returns:
        True si se envio, False si fallo.
    """
    from agent.providers import obtener_proveedor
    proveedor = obtener_proveedor()

    template_sid = obtener_template_sid(tipo)
    if template_sid:
        ok = await proveedor.enviar_template(telefono, template_sid, variables)
        if ok:
            logger.info(f"Notif {tipo} via template a {telefono}")
        else:
            logger.warning(f"Template {tipo} fallo, intentando texto libre a {telefono}")
            ok = await proveedor.enviar_mensaje(telefono, mensaje_libre)
        return ok

    # Sin template configurado: enviar texto libre
    ok = await proveedor.enviar_mensaje(telefono, mensaje_libre)
    if ok:
        logger.info(f"Notif {tipo} via texto a {telefono}")
    else:
        logger.error(f"Error enviando notif {tipo} a {telefono}")
    return ok


async def notificar_lista(telefonos: list[str], tipo: str, mensaje_libre: str,
                          variables: Optional[dict] = None) -> dict:
    """Envia la misma notificacion a multiples telefonos. Retorna stats."""
    enviados = 0
    fallidos = []
    for telefono in telefonos:
        telefono = telefono.strip()
        if not telefono:
            continue
        ok = await notificar(telefono, tipo, mensaje_libre, variables)
        if ok:
            enviados += 1
        else:
            fallidos.append(telefono)
    return {
        "enviados": enviados,
        "total": len([t for t in telefonos if t.strip()]),
        "fallidos": fallidos,
    }


# --- Helpers de alto nivel para cada flujo de negocio ---


async def notificar_nuevo_lead_admin(admin_phones: list[str], lead: dict) -> dict:
    """Lead nuevo entro por la web. Avisar al admin con datos clave.

    lead dict esperado: {nombre, telefono, email?, mensaje?, fuente?}
    """
    nombre = lead.get("nombre", "Sin nombre")
    tel = lead.get("telefono", "")
    email = lead.get("email", "")
    mensaje = (lead.get("mensaje") or "")[:300]
    fuente = lead.get("fuente", "web")

    texto = f"Nuevo lead ({fuente})\n"
    texto += f"Nombre: {nombre}\n"
    if tel:
        texto += f"Tel: {tel}\n"
    if email:
        texto += f"Email: {email}\n"
    if mensaje:
        texto += f"Mensaje: {mensaje}"

    variables = {
        "1": nombre,
        "2": tel or email or "sin contacto",
        "3": mensaje or fuente,
    }
    return await notificar_lista(admin_phones, "new_lead_admin", texto, variables)


async def notificar_turno_staff(staff_phones: list[str], turno: dict) -> dict:
    """Cuando se reserva un turno con staff asignado, avisar al staff."""
    cliente = turno.get("customerName", "Cliente")
    servicio = turno.get("serviceName", "servicio")
    fecha = turno.get("date", "")
    hora = turno.get("time", "")
    tel_cliente = turno.get("customerPhone", "")

    texto = f"Turno asignado\n"
    texto += f"Cliente: {cliente} ({tel_cliente})\n"
    texto += f"Servicio: {servicio}\n"
    texto += f"Cuando: {fecha} {hora}"

    variables = {
        "1": cliente,
        "2": servicio,
        "3": f"{fecha} {hora}",
        "4": tel_cliente,
    }
    return await notificar_lista(staff_phones, "appointment_assigned_staff", texto, variables)


async def notificar_recordatorio_cliente(telefono_cliente: str, turno: dict) -> bool:
    """Recordatorio 24h antes del turno al cliente.

    NOTA: Este mensaje SI o SI requiere template aprobado por Meta porque
    se envia fuera del service window (>24h sin que el cliente escriba).
    Si no hay template, intentara texto libre pero Meta puede rechazarlo.
    """
    servicio = turno.get("serviceName", "tu turno")
    fecha = turno.get("date", "")
    hora = turno.get("time", "")
    negocio = turno.get("businessName", os.getenv("BUSINESS_NAME", ""))

    texto = (
        f"Hola, te recordamos tu turno{f' en {negocio}' if negocio else ''} "
        f"de {servicio} manana {fecha} a las {hora}. "
        f"Si necesitas cancelar o reprogramar, avisanos."
    )
    variables = {
        "1": negocio or "tu turno",
        "2": servicio,
        "3": fecha,
        "4": hora,
    }
    return await notificar(telefono_cliente, "appointment_reminder_client", texto, variables)


async def notificar_confirmacion_cliente(telefono_cliente: str, turno: dict) -> bool:
    """Confirmacion inmediata cuando se reserva un turno."""
    servicio = turno.get("serviceName", "tu turno")
    fecha = turno.get("date", "")
    hora = turno.get("time", "")
    staff = turno.get("staffName", "")
    negocio = turno.get("businessName", os.getenv("BUSINESS_NAME", ""))

    texto = f"Turno confirmado"
    if negocio:
        texto += f" en {negocio}"
    texto += f": {servicio} el {fecha} a las {hora}"
    if staff:
        texto += f" con {staff}"
    texto += "."

    variables = {
        "1": servicio,
        "2": fecha,
        "3": hora,
        "4": staff or negocio or "",
    }
    return await notificar(telefono_cliente, "appointment_confirmation_client", texto, variables)


async def notificar_review_cliente(telefono_cliente: str, turno: dict) -> bool:
    """Follow-up post-servicio (X horas despues) pidiendo review."""
    servicio = turno.get("serviceName", "el servicio")
    negocio = turno.get("businessName", os.getenv("BUSINESS_NAME", ""))
    link_review = turno.get("reviewLink", "")

    texto = f"Hola, gracias por venir{f' a {negocio}' if negocio else ''}. "
    texto += f"Como te fue con {servicio}? Si nos podes dejar una resena nos ayudas un monton."
    if link_review:
        texto += f" {link_review}"

    variables = {
        "1": negocio or servicio,
        "2": link_review or "",
    }
    return await notificar(telefono_cliente, "appointment_review_request_client", texto, variables)


async def notificar_followup_lead_cliente(telefono_cliente: str, lead: dict) -> bool:
    """Follow-up automatico a lead que no respondio en X tiempo."""
    negocio = lead.get("negocio", "tu negocio")

    texto = (
        f"Hola, te paso por aca. Quedo pendiente lo de la web para {negocio}. "
        f"Cualquier cosa te quedo, decime y te explico."
    )
    variables = {"1": negocio}
    return await notificar(telefono_cliente, "lead_follow_up_client", texto, variables)

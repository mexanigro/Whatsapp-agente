# agent/providers/twilio.py — Adaptador para Twilio WhatsApp

import os
import json
import logging
import base64
import httpx
from fastapi import Request
from twilio.request_validator import RequestValidator
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante
from agent.security import enmascarar_telefono

logger = logging.getLogger("agentkit")


class ProveedorTwilio(ProveedorWhatsApp):

    def __init__(self):
        self.account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        self.phone_number = os.getenv("TWILIO_PHONE_NUMBER")
        # Validador de firma Twilio (protege contra webhooks falsos)
        self.validator = RequestValidator(self.auth_token) if self.auth_token else None

    def _construir_url_publica(self, request: Request) -> str:
        """Reconstruye la URL publica que Twilio uso para firmar el request.
        Detras de un reverse proxy (Railway), request.url tiene el esquema interno (http),
        pero Twilio firma contra la URL publica (https).
        WEBHOOK_BASE_URL es el override definitivo si esta configurado."""
        base = os.getenv("WEBHOOK_BASE_URL")
        if base:
            return base.rstrip("/") + str(request.url.path)
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        host = request.headers.get("x-forwarded-host") or request.headers.get("host", request.url.hostname)
        # Railway a veces incluye el port en host — para HTTPS standard (443) lo removemos
        if proto == "https" and host and ":443" in host:
            host = host.replace(":443", "")
        return f"{proto}://{host}{request.url.path}"

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        form = await request.form()

        # Validar firma X-Twilio-Signature en produccion
        if os.getenv("ENVIRONMENT") != "development" and self.validator:
            url = self._construir_url_publica(request)
            signature = request.headers.get("X-Twilio-Signature", "")
            # Twilio valida contra strings — asegurar que no haya UploadFile u otros tipos
            form_dict = {k: str(v) for k, v in form.items()}
            if not self.validator.validate(url, form_dict, signature):
                logger.warning(f"Firma Twilio invalida, rechazando webhook (url={url})")
                return []

        texto = form.get("Body", "")
        telefono = form.get("From", "").replace("whatsapp:", "")
        mensaje_id = form.get("MessageSid", "")
        if not texto:
            return []
        return [MensajeEntrante(
            telefono=telefono,
            texto=texto,
            mensaje_id=mensaje_id,
            es_propio=False,
        )]

    async def _enviar_texto(self, telefono: str, texto: str) -> bool:
        if not all([self.account_sid, self.auth_token, self.phone_number]):
            logger.warning("Variables de Twilio no configuradas")
            return False
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        auth = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}
        data = {
            "From": f"whatsapp:{self.phone_number}",
            "To": f"whatsapp:{telefono}",
            "Body": texto,
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(url, data=data, headers=headers)
            if r.status_code != 201:
                logger.error(f"Error Twilio: {r.status_code} — {r.text[:200]}")
            return r.status_code == 201

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        return await self._enviar_texto(telefono, mensaje)

    async def enviar_typing_indicator(self, mensaje_id: str) -> bool:
        """Envia el typing indicator 'escribiendo...' a WhatsApp via Twilio API beta.
        Referencia el SID del mensaje entrante que se esta respondiendo.
        El indicador dura hasta que se envie respuesta o 25 segundos."""
        if not all([self.account_sid, self.auth_token]):
            return False
        if not mensaje_id or not mensaje_id.startswith(("SM", "MM")):
            return False
        url = "https://messaging.twilio.com/v2/Indicators/Typing.json"
        auth = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}
        data = {
            "messageId": mensaje_id,
            "channel": "whatsapp",
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.post(url, data=data, headers=headers)
                if r.status_code not in (200, 201):
                    logger.debug(f"Typing indicator fallo: {r.status_code} — {r.text[:200]}")
                return r.status_code in (200, 201)
        except Exception as e:
            logger.debug(f"Typing indicator error: {e}")
            return False

    async def enviar_template(self, telefono: str, content_sid: str,
                              variables: dict | None = None) -> bool:
        """Envia un template de WhatsApp via Twilio Content API (ContentSid)."""
        if not all([self.account_sid, self.auth_token, self.phone_number]):
            logger.warning("Variables de Twilio no configuradas")
            return False
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        auth = base64.b64encode(f"{self.account_sid}:{self.auth_token}".encode()).decode()
        headers = {"Authorization": f"Basic {auth}"}
        data = {
            "From": f"whatsapp:{self.phone_number}",
            "To": f"whatsapp:{telefono}",
            "ContentSid": content_sid,
        }
        if variables:
            data["ContentVariables"] = json.dumps(variables)
        async with httpx.AsyncClient() as client:
            r = await client.post(url, data=data, headers=headers)
            if r.status_code != 201:
                logger.error(f"Error Twilio template: {r.status_code} — {r.text[:200]}")
            return r.status_code == 201

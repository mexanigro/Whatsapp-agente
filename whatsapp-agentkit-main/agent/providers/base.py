# agent/providers/base.py — Clase base para proveedores de WhatsApp
# Generado por AgentKit

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fastapi import Request


@dataclass
class MensajeEntrante:
    telefono: str
    texto: str
    mensaje_id: str
    es_propio: bool
    # Media entrante (nota de voz, imagen, etc.) — None si es solo texto
    media_url: str | None = None
    media_content_type: str | None = None

    @property
    def es_audio(self) -> bool:
        return bool(
            self.media_url
            and (self.media_content_type or "").startswith("audio")
        )


class ProveedorWhatsApp(ABC):

    @abstractmethod
    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        ...

    @abstractmethod
    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        ...

    async def enviar_template(self, telefono: str, content_sid: str,
                              variables: dict | None = None) -> bool:
        """Envia un template de WhatsApp. Override en cada provider."""
        return False

    async def enviar_typing_indicator(self, mensaje_id: str) -> bool:
        """Envia indicador 'escribiendo...' al usuario. Override en cada provider."""
        return False

    async def enviar_audio(self, telefono: str, url_audio: str) -> bool:
        """Envia un audio (nota de voz) desde una URL publica. Override en cada provider."""
        return False

    async def validar_webhook(self, request: Request) -> dict | int | None:
        return None

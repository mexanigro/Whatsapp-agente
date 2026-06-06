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

    async def validar_webhook(self, request: Request) -> dict | int | None:
        return None

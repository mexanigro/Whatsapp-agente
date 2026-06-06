# agent/humanize.py — Helpers para hacer que el envio suene humano
# Divide la respuesta del LLM en fragmentos y calcula delays variables.

import random

SEPARADOR_FRAGMENTO = "|||"
DELAY_MIN = 5.0
DELAY_MAX = 60.0


def partir_respuesta(respuesta: str) -> list[str]:
    """Divide la respuesta del LLM en fragmentos por el separador |||."""
    if not respuesta:
        return []
    partes = [p.strip() for p in respuesta.split(SEPARADOR_FRAGMENTO)]
    return [p for p in partes if p]


def calcular_delay(texto: str) -> float:
    """Calcula delay humano variable antes de enviar un fragmento.
    Base aleatoria + factor por longitud + spike ocasional (como si estuviera ocupado).
    Resultado entre DELAY_MIN y DELAY_MAX."""
    base = random.uniform(5.0, 15.0)
    factor_longitud = len(texto) * random.uniform(0.03, 0.08)
    # 4 de 9 veces sin spike, el resto con spike de 5/10/15/20/30 segundos
    spike = random.choice([0, 0, 0, 0, 5, 10, 15, 20, 30])
    total = base + factor_longitud + spike
    return max(DELAY_MIN, min(DELAY_MAX, total))

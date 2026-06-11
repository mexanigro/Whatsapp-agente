# agent/humanize.py — Helpers para hacer que el envio suene humano
# Divide la respuesta del LLM en fragmentos y calcula delays variables.

import random

SEPARADOR_FRAGMENTO = "|||"
DELAY_MIN = 3.0
DELAY_MAX = 45.0
DELAY_MAX_SIGUIENTE = 18.0
# Velocidad de tipeo de alguien fluido en el celular (que ademas abrevia)
CHARS_POR_SEGUNDO_MIN = 7.0
CHARS_POR_SEGUNDO_MAX = 12.0


def partir_respuesta(respuesta: str) -> list[str]:
    """Divide la respuesta del LLM en fragmentos por el separador |||."""
    if not respuesta:
        return []
    partes = [p.strip() for p in respuesta.split(SEPARADOR_FRAGMENTO)]
    return [p for p in partes if p]


def calcular_delay(texto: str, es_primer_fragmento: bool = True) -> float:
    """Calcula delay humano variable antes de enviar un fragmento.

    Primer fragmento: tiempo de leer el mensaje + pensar + tipear,
    con spike ocasional (como si estuviera ocupado con otra cosa).
    Fragmentos siguientes: la persona ya esta escribiendo, solo tipea.
    Resultado entre DELAY_MIN y DELAY_MAX."""
    tipeo = len(texto) / random.uniform(CHARS_POR_SEGUNDO_MIN, CHARS_POR_SEGUNDO_MAX)

    if es_primer_fragmento:
        base = random.uniform(3.0, 8.0)
        # 5 de 8 veces sin spike, el resto con spike (estaba con otra cosa)
        spike = random.choice([0, 0, 0, 0, 0, 8, 15, 25])
        total = base + tipeo + spike
    else:
        # Ya venia escribiendo: pausa corta entre mensaje y mensaje
        base = random.uniform(1.0, 2.5)
        return max(DELAY_MIN, min(DELAY_MAX_SIGUIENTE, base + tipeo))

    return max(DELAY_MIN, min(DELAY_MAX, total))

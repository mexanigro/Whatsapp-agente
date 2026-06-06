# agent/horario.py — Helper para chequear si el negocio esta en horario de atencion
#
# Se usa para:
# 1. Decidir si responder con LLM o con mensaje fijo fuera de horario (ahorra tokens)
# 2. Permitir que tools verifiquen disponibilidad real
# 3. Loguear conversaciones fuera de horario para metricas

import os
import yaml
import logging
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger("agentkit")

# Israel timezone por default (negocios de Arzac Studio)
TIMEZONE = ZoneInfo(os.getenv("BUSINESS_TIMEZONE", "Asia/Jerusalem"))

# Mapa de dias en hebreo/espanol/ingles para parsear el YAML
_DIAS_MAP = {
    "domingo": 6, "lunes": 0, "martes": 1, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sabado": 5,
    "sunday": 6, "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5,
    "dom": 6, "lun": 0, "mar": 1, "mie": 2, "jue": 3, "vie": 4, "sab": 5,
}

# Cache del horario parseado
_horario_cache: dict | None = None


def _parsear_hora(s: str) -> time | None:
    """Convierte '7:30' o '19:00' a time(). Retorna None si no es valido."""
    s = s.strip()
    match = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if not match:
        return None
    h, m = int(match.group(1)), int(match.group(2))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return time(h, m)


def _parsear_rango(rango: str) -> tuple[time, time] | None:
    """Parsea '7:30-19:00' o '7:30 a 19:00'."""
    rango = rango.replace(" a ", "-").replace("a", "-").replace("—", "-")
    partes = [p.strip() for p in rango.split("-") if p.strip()]
    if len(partes) != 2:
        return None
    inicio = _parsear_hora(partes[0])
    fin = _parsear_hora(partes[1])
    if not inicio or not fin:
        return None
    return (inicio, fin)


def _parsear_horario_yaml(horario_str: str) -> dict[int, list[tuple[time, time]]]:
    """Parsea el string de horario del business.yaml a {dia_semana: [(inicio, fin)]}.

    Soporta formatos como:
    - 'Domingos a Jueves 7:30-19:00, Viernes 7:30-17:00, Sabado cerrado'
    - 'Lun-Vie 9:00-18:00'
    """
    resultado: dict[int, list[tuple[time, time]]] = {}
    if not horario_str:
        return resultado

    # Separar por comas
    bloques = [b.strip() for b in horario_str.split(",")]
    for bloque in bloques:
        b_lower = bloque.lower()
        # Detectar "cerrado"
        if "cerrado" in b_lower or "closed" in b_lower or "סגור" in b_lower:
            for dia_nombre, dia_num in _DIAS_MAP.items():
                if dia_nombre in b_lower:
                    resultado.setdefault(dia_num, [])
            continue

        # Buscar dias mencionados
        dias_en_bloque: list[int] = []
        # Rango "Domingo a Jueves"
        match_rango = re.search(
            r"(domingo|lunes|martes|miercoles|jueves|viernes|sabado|"
            r"sunday|monday|tuesday|wednesday|thursday|friday|saturday|"
            r"dom|lun|mar|mie|jue|vie|sab)s?\s*(?:a|-|al|to)\s*"
            r"(domingo|lunes|martes|miercoles|jueves|viernes|sabado|"
            r"sunday|monday|tuesday|wednesday|thursday|friday|saturday|"
            r"dom|lun|mar|mie|jue|vie|sab)",
            b_lower,
        )
        if match_rango:
            d1 = _DIAS_MAP[match_rango.group(1)]
            d2 = _DIAS_MAP[match_rango.group(2)]
            # Generar rango circular (dom=6, lun=0... lo manejamos como secuencia natural)
            # Si d1 > d2, wrap around
            actual = d1
            while True:
                dias_en_bloque.append(actual)
                if actual == d2:
                    break
                actual = (actual + 1) % 7
        else:
            # Dia individual
            for dia_nombre, dia_num in _DIAS_MAP.items():
                if re.search(rf"\b{dia_nombre}s?\b", b_lower):
                    if dia_num not in dias_en_bloque:
                        dias_en_bloque.append(dia_num)

        # Extraer rango horario
        match_horas = re.search(r"(\d{1,2}:\d{2})\s*[-aá]\s*(\d{1,2}:\d{2})", bloque)
        if match_horas:
            rango = _parsear_rango(f"{match_horas.group(1)}-{match_horas.group(2)}")
            if rango:
                for dia in dias_en_bloque:
                    resultado.setdefault(dia, []).append(rango)

    return resultado


def cargar_horario() -> dict[int, list[tuple[time, time]]]:
    """Carga el horario del business.yaml y lo cachea."""
    global _horario_cache
    if _horario_cache is not None:
        return _horario_cache
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        horario_str = data.get("negocio", {}).get("horario", "")
        _horario_cache = _parsear_horario_yaml(horario_str)
        logger.info(f"Horario cargado: {len(_horario_cache)} dias con franjas")
        return _horario_cache
    except FileNotFoundError:
        logger.warning("config/business.yaml no encontrado, horario vacio")
        _horario_cache = {}
        return _horario_cache


def recargar_horario():
    """Invalida el cache del horario para que se re-lea del YAML."""
    global _horario_cache
    _horario_cache = None


def esta_en_horario(ahora: datetime | None = None) -> bool:
    """Devuelve True si el negocio esta abierto en este momento."""
    if ahora is None:
        ahora = datetime.now(TIMEZONE)
    elif ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=TIMEZONE)

    horario = cargar_horario()
    dia = ahora.weekday()
    hora_actual = ahora.time()

    franjas = horario.get(dia, [])
    for inicio, fin in franjas:
        if inicio <= hora_actual <= fin:
            return True
    return False


def proximo_horario_apertura(ahora: datetime | None = None) -> datetime | None:
    """Devuelve el datetime de la proxima apertura. None si no hay horario configurado."""
    if ahora is None:
        ahora = datetime.now(TIMEZONE)
    elif ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=TIMEZONE)

    horario = cargar_horario()
    if not horario:
        return None

    # Buscar hasta 7 dias hacia adelante
    for i in range(8):
        dia_check = (ahora + timedelta(days=i)).date()
        dia_semana = dia_check.weekday()
        franjas = horario.get(dia_semana, [])
        for inicio, _ in franjas:
            dt = datetime.combine(dia_check, inicio, tzinfo=TIMEZONE)
            if dt > ahora:
                return dt
    return None


def mensaje_fuera_horario(idioma: str = "es") -> str:
    """Mensaje fijo fuera de horario, multi-idioma.

    Se usa para responder sin gastar tokens cuando el negocio esta cerrado.
    Si la deteccion de idioma falla, default espanol.
    """
    proximo = proximo_horario_apertura()
    horario_str = ""
    if proximo:
        ahora = datetime.now(TIMEZONE)
        delta = proximo - ahora
        horas = int(delta.total_seconds() / 3600)
        if horas < 24:
            horario_str_es = f" Te respondo cuando arranque mas tarde (en aprox {horas}h)."
            horario_str_en = f" I'll get back to you when I'm back (in about {horas}h)."
            horario_str_he = f" אחזור אליך מאוחר יותר (בערך {horas}ש)."
        else:
            horario_str_es = " Te respondo cuando vuelva a estar disponible."
            horario_str_en = " I'll get back to you when I'm available again."
            horario_str_he = " אחזור אליך כשאהיה זמין שוב."
    else:
        horario_str_es = horario_str_en = horario_str_he = ""

    msgs = {
        "es": f"Hola, gracias por escribir. Ahora estoy fuera de horario.{horario_str_es if proximo else ''}",
        "en": f"Hi, thanks for writing. I'm currently outside business hours.{horario_str_en if proximo else ''}",
        "he": f"היי, תודה על הפנייה. כרגע אני מחוץ לשעות פעילות.{horario_str_he if proximo else ''}",
    }
    return msgs.get(idioma, msgs["es"])


def detectar_idioma_simple(texto: str) -> str:
    """Detecta idioma de forma simple basado en alfabeto. Para auto-reply fuera de horario."""
    if re.search(r"[֐-׿]", texto):
        return "he"
    if re.search(r"[؀-ۿ]", texto):
        return "ar"
    if re.search(r"[Ѐ-ӿ]", texto):
        return "ru"
    # Heuristica espanol vs ingles
    palabras_es = {"hola", "que", "como", "para", "donde", "cuanto", "gracias", "sí", "si",
                   "buenos", "buenas", "necesito", "quiero", "puedo"}
    palabras_lower = set(texto.lower().split())
    if palabras_lower & palabras_es:
        return "es"
    return "en"

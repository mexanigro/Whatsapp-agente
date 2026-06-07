# agent/rate_limit.py — Rate limiting por telefono
#
# NOTA: El rate limiter in-memory se resetea con cada deploy/restart de Railway.
# Para single-worker (1 replica) esto es aceptable — los limites se re-aplican
# desde cero despues de cada restart.
# Para multi-worker o persistencia entre deploys, configurar REDIS_URL en .env.

import os
import time
import logging
from collections import defaultdict

from agent.security import enmascarar_telefono

logger = logging.getLogger("agentkit")

LIMITE_POR_MINUTO = 5
LIMITE_POR_HORA = 30

# Backend de rate limiting (Redis o in-memory)
_redis_client = None
_usando_redis = False


def inicializar_rate_limit():
    """Inicializar backend de rate limiting. Llamar en startup (lifespan)."""
    global _redis_client, _usando_redis
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis
            _redis_client = redis.from_url(redis_url)
            _redis_client.ping()
            _usando_redis = True
            logger.info("Rate limiter: usando Redis")
        except Exception as e:
            logger.warning(f"Redis no disponible, usando rate limiter in-memory: {e}")
    else:
        logger.info("Rate limiter: in-memory (REDIS_URL no configurada)")


# In-memory fallback: {telefono: [timestamps]}
_registros: dict[str, list[float]] = defaultdict(list)


def verificar_rate_limit(telefono: str) -> bool:
    """Verifica si el telefono puede enviar un mensaje mas. Usa Redis si esta disponible."""
    if _usando_redis:
        return _verificar_redis(telefono)
    return _verificar_inmemory(telefono)


def _verificar_inmemory(telefono: str) -> bool:
    """Rate limiting con dict in-memory (default, single-worker)."""
    ahora = time.time()
    timestamps = _registros[telefono]

    # Limpiar timestamps viejos (>1 hora)
    _registros[telefono] = [t for t in timestamps if ahora - t < 3600]
    timestamps = _registros[telefono]

    msgs_ultimo_minuto = sum(1 for t in timestamps if ahora - t < 60)
    msgs_ultima_hora = len(timestamps)

    if msgs_ultimo_minuto >= LIMITE_POR_MINUTO:
        logger.warning(f"Rate limit minuto excedido: {enmascarar_telefono(telefono)} ({msgs_ultimo_minuto}/{LIMITE_POR_MINUTO})")
        return False

    if msgs_ultima_hora >= LIMITE_POR_HORA:
        logger.warning(f"Rate limit hora excedido: {enmascarar_telefono(telefono)} ({msgs_ultima_hora}/{LIMITE_POR_HORA})")
        return False

    _registros[telefono].append(ahora)
    return True


def _verificar_redis(telefono: str) -> bool:
    """Rate limiting con Redis usando sorted sets (multi-worker safe)."""
    try:
        ahora = time.time()
        key_min = f"rl:{telefono}:min"
        key_hora = f"rl:{telefono}:hora"

        pipe = _redis_client.pipeline()
        # Limpiar entradas expiradas + contar
        pipe.zremrangebyscore(key_min, 0, ahora - 60)
        pipe.zremrangebyscore(key_hora, 0, ahora - 3600)
        pipe.zcard(key_min)
        pipe.zcard(key_hora)
        _, _, count_min, count_hora = pipe.execute()

        if count_min >= LIMITE_POR_MINUTO:
            logger.warning(f"Rate limit minuto excedido (Redis): {enmascarar_telefono(telefono)} ({count_min}/{LIMITE_POR_MINUTO})")
            return False

        if count_hora >= LIMITE_POR_HORA:
            logger.warning(f"Rate limit hora excedido (Redis): {enmascarar_telefono(telefono)} ({count_hora}/{LIMITE_POR_HORA})")
            return False

        # Registrar nuevo timestamp
        pipe = _redis_client.pipeline()
        pipe.zadd(key_min, {str(ahora): ahora})
        pipe.zadd(key_hora, {str(ahora): ahora})
        pipe.expire(key_min, 60)
        pipe.expire(key_hora, 3600)
        pipe.execute()
        return True

    except Exception as e:
        logger.error(f"Error Redis rate limit, fallback a in-memory: {e}")
        return _verificar_inmemory(telefono)

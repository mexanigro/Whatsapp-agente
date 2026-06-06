# tests/test_local.py — Simulador de chat en terminal
# Generado por AgentKit

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, limpiar_historial
from agent.humanize import partir_respuesta, calcular_delay

TELEFONO_TEST = "test-local-001"


async def main():
    await inicializar_db()

    print()
    print("=" * 55)
    print("   AgentKit — Test Local")
    print("   Agente: Liam | Negocio: Arzac Studio")
    print("=" * 55)
    print()
    print("  Escribe mensajes como si fueras un cliente.")
    print("  Comandos especiales:")
    print("    'limpiar'  — borra el historial")
    print("    'salir'    — termina el test")
    print()
    print("-" * 55)
    print()

    while True:
        try:
            mensaje = input("Tu: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nTest finalizado.")
            break

        if not mensaje:
            continue

        if mensaje.lower() == "salir":
            print("\nTest finalizado.")
            break

        if mensaje.lower() == "limpiar":
            await limpiar_historial(TELEFONO_TEST)
            print("[Historial borrado]\n")
            continue

        historial = await obtener_historial(TELEFONO_TEST)

        respuesta = await generar_respuesta(mensaje, historial, telefono=TELEFONO_TEST)

        fragmentos = partir_respuesta(respuesta)
        if not fragmentos:
            print("\n[respuesta vacia]\n")
            continue

        for i, fragmento in enumerate(fragmentos):
            delay = calcular_delay(fragmento)
            print(f"\n[escribiendo... {delay:.1f}s]", flush=True)
            await asyncio.sleep(delay)
            print(f"Liam ({i+1}/{len(fragmentos)}): {fragmento}")
        print()

        await guardar_mensaje(TELEFONO_TEST, "user", mensaje)
        await guardar_mensaje(TELEFONO_TEST, "assistant", respuesta)


if __name__ == "__main__":
    asyncio.run(main())

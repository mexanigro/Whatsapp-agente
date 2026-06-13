# TODO: Persistencia de datos — SQLite en Railway es efímero

## El problema

El agente usa SQLite (`agentkit.db`) como base de datos para:
- Historial de conversaciones por número de teléfono
- Leads registrados
- Costos API acumulados del día
- Seguimientos programados
- Eventos de analytics
- Estado de pausa/config del agente

**En Railway, el filesystem es efímero**: cada redeploy (push a main, restart manual,
o crash + restart automático) destruye el `.db` y el agente pierde toda esa información.

### Consecuencias prácticas
- El agente "olvida" a todos los clientes y empieza desde cero
- El cap de costos diario se resetea (peligroso si la razón del restart fue un bug)
- Los seguimientos programados se pierden (leads sin follow-up)
- Los leads quedan sin referencia de nombre/negocio
- El historial de conversación desaparece (el agente no recuerda el contexto)

---

## Opciones de solución

### Opción A — Railway Volumes (recomendada para corto plazo)
- Agregar un Volume en Railway al servicio: Settings → Volumes → Add Volume
- Montar en `/app` (o donde viva el `.db`)
- Costo: ~$0.25/GB/mes
- Pro: sin cambios de código, funciona inmediatamente
- Con: el Volume es local a la instancia (no escala a múltiples workers)
- Pendiente Liam: crear el Volume y configurar el mount path en Railway

### Opción B — PostgreSQL compartido con nichos-hub
- nichos-hub ya usa Firestore, pero podría exponer un servicio PostgreSQL en Railway
- memory.py soporta `DATABASE_URL` para PostgreSQL (`postgresql+asyncpg://...`)
- Solo hay que cambiar la variable de entorno `DB_PATH` → `DATABASE_URL`
- Pro: persistencia real, escala a múltiples workers
- Con: requiere aprovisionar PostgreSQL en Railway (plan pago) y coordinar con nichos-hub

### Opción C — Migrar a Firestore
- nichos-hub ya tiene Firestore con leads y config por cliente
- Migrar memory.py para usar Firestore en vez de SQLite
- Pro: consistencia con el resto del ecosistema (nichos-hub ya lo usa)
- Con: reescritura significativa de memory.py + analytics.py + seguimiento.py
- Requiere: credencial de Firebase, cambios en todos los módulos que usan SQLite directo

---

## Estado actual

**No implementado.** SQLite sigue siendo efímero en Railway.

Para el MVP y demos es aceptable, pero para clientes en producción con historial
real de conversaciones es un problema que hay que resolver antes de escalar.

## Próximos pasos (cuando sea el momento)

1. Evaluar si Railway Volumes (Opción A) es suficiente para la escala esperada
2. Si se necesita escalar a múltiples instancias → ir directo a Opción B o C
3. Crear una migración para exportar el `.db` local antes de cambiar el motor

---
*Documentado: 2026-06-13. No implementar sin revisar las opciones arriba.*

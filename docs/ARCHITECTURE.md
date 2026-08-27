# Arquitectura de staging local

La API enlazada a `127.0.0.1` autentica, limita e inicia un workflow con un ID
derivado de `Idempotency-Key`. Temporal conserva historial y permite que otro
worker recupere la ejecución. El workflow publica snapshots y eventos.

Estados: `queued`, `planning`, `generating`, `testing`, `correcting`,
`awaiting_approval`, `passed`, `failed`, `cancelled`, `model_error` e
`infrastructure_error`.

`PythonCodeWorkflow` genera fuente y pruebas por separado, ejecuta código solo
en el sandbox y permite al corrector reemplazar exclusivamente la fuente. Las
actividades envían heartbeats y no tienen reintentos automáticos porque invocan
modelos, crean workspaces o ejecutan código.

Límites de confianza:

- La API nunca entrega rutas; resuelve artefactos por ID y nombre permitido.
- Ollama es el único proveedor activo y dispone de circuit breaker.
- Codex implementa el contrato, pero no hace llamadas y queda desactivado.
- El sandbox usa imagen existente, red nula, raíz y montaje de solo lectura, UID
  no root, límites de CPU/RAM/PIDs/tiempo/salida y rechazo de symlinks.
- PostgreSQL almacena memoria/checkpoints. Temporal es la fuente de verdad de
  trabajos, eventos, cancelación y recuperación.
- Obsidian es un adaptador opcional de conocimiento humano. Lee Markdown solo
  desde `Knowledge`, `Projects` y `Skills`, y publica reportes exclusivamente
  en `Runs`. Nunca actúa como cola, base transaccional ni estado canónico.

Al reiniciar un worker, Temporal reproduce decisiones hasta el último comando
registrado. Una actividad en curso debe mantener heartbeats; si desaparece,
expira y queda como fallo visible, sin repetirse automáticamente.

El adaptador Obsidian está fuera del workflow principal: una caída o permiso
incorrecto se refleja como `degraded`, pero no detiene Temporal, API, Ollama ni
el sandbox. La exportación de reportes requiere una petición API autenticada y
explícita; no existe exportación automática.

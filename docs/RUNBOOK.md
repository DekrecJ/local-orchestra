# Runbook de staging local

## Preparación y diagnóstico

1. Copiar `.env.example` a `.env` y ajustar solo valores no secretos.
2. Crear manualmente `.secrets/postgres_password` y `.secrets/api_token` con
   permisos `0600`, sin imprimirlos.
3. Confirmar Temporal, PostgreSQL, Ollama y Docker, y que
   `python:3.13-slim` ya exista localmente.
4. Ejecutar `ops/bin/orchestra-diagnose`.

## Arranque y parada

`ops/bin/orchestra-start` inicia solo API/worker y escribe PID files bajo
`run/`. `ops/bin/orchestra-stop` valida PID y command line antes de `SIGTERM`.
Temporal, PostgreSQL, Ollama y Docker siguen bajo gestión manual.

Las unidades en `deploy/systemd/` no están instaladas. Instalarlas requiere
autorización. El worker necesita el launcher exacto permitido por sudoers; por
eso la unidad del worker no puede usar `NoNewPrivileges=true`. El contenedor sí.

Para activar el launcher versionado, revisar primero el diff y ejecutar de forma
interactiva:

```text
sudo install -o root -g root -m 0755 ops/sandbox/orchestra-python-test /usr/local/sbin/orchestra-python-test
```

## Recuperación, backup e incidentes

Tras caída del worker, iniciarlo con la misma task queue y consultar estado,
eventos e historial. No sustituir el job usando otra clave. Respaldar por
separado PostgreSQL, volumen de Temporal y, si aplican, workspaces. Probar la
restauración en otra instancia; el repositorio no automatiza restauración.

- Ollama degraded: revisar circuito/proceso; nunca escalar a Codex solo.
- Temporal degraded: no crear trabajos.
- Sandbox degraded: no ejecutar código en host.
- Token ausente: la API devuelve 503 y permanece cerrada.

`python -m app.retention` es dry-run. Tras revisar la lista, ejecutar manualmente
`python -m app.retention --apply`. El timer incluido solo ejecuta dry-run.

## Obsidian

Está desactivado por defecto. Para activarlo, verificar primero que se trata de
un vault dedicado —nunca uno personal— con las siete carpetas esperadas y añadir
a `.env`:

```text
ORCHESTRA_OBSIDIAN_ENABLED=true
ORCHESTRA_OBSIDIAN_VAULT_PATH=/home/derex/Capsulas/Obsidian/Orchestra-Vault
```

Reiniciar únicamente API/worker mediante los scripts y consultar
`/health/ready` y `/v1/knowledge/status`. Para desactivarlo, restaurar
`ORCHESTRA_OBSIDIAN_ENABLED=false` y reiniciar API/worker. Un estado Obsidian
`degraded` no vuelve no-ready al backend principal.

Diagnóstico: confirmar que la raíz y las siete carpetas son directorios reales,
sin symlinks; las tres carpetas de lectura requieren lectura/traversal y `Runs`
además escritura. Corregir permisos fuera del backend y reiniciar. El adaptador
no repara, elimina ni mueve notas. Un temporal oculto tras un fallo de proceso
puede revisarse manualmente; nunca se publica como reporte y no se sobrescribe.

La incorporación futura de RAG debe indexar copias acotadas en PostgreSQL,
mantener IDs lógicos y conservar Obsidian como fuente humana no canónica. Una
CLI o integración MCP/REST posterior debe reutilizar el mismo adaptador,
autenticación y límites, enlazarse solo a localhost y requerir autorización
antes de instalar plugins o modificar `.obsidian`.

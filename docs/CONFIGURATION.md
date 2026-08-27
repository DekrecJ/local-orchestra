# Configuración

La configuración usa variables con prefijo `ORCHESTRA_`. `.env.example` solo
contiene valores seguros; cada instalación crea su propio `.env`. No copie uno
ajeno ni incorpore `.env` o `.secrets` a Git.

“Obligatoria” significa que debe existir para operar esa capacidad. Las demás
son opcionales porque tienen un valor predeterminado validado.

| Variable | Obligatoria | Tipo | Predeterminado | Efecto |
|---|---:|---|---|---|
| `ORCHESTRA_ENVIRONMENT` | No | texto | `local-staging` | Etiqueta del entorno |
| `ORCHESTRA_LOG_LEVEL` | No | enum | `INFO` | Nivel DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `ORCHESTRA_LOG_JSON` | No | booleano | `true` | Logs JSON estructurados |
| `ORCHESTRA_OLLAMA_BASE_URL` | No | URL local | `http://127.0.0.1:11434` | Endpoint Ollama; se rechazan hosts no locales |
| `ORCHESTRA_OLLAMA_MODEL` | No | texto | `qwen3.5:4b` | Modelo de generación local |
| `ORCHESTRA_EMBEDDING_MODEL` | No | texto | `qwen3-embedding:0.6b` | Modelo de embeddings |
| `ORCHESTRA_EMBEDDING_DIMENSION` | No | entero | `1024` | Dimensión esperada del vector |
| `ORCHESTRA_PROVIDER_TIMEOUT_SECONDS` | No | segundos | `180` | Timeout por invocación de proveedor |
| `ORCHESTRA_PROVIDER_FAILURE_THRESHOLD` | No | entero | `3` | Fallos antes de abrir el circuito |
| `ORCHESTRA_PROVIDER_RECOVERY_SECONDS` | No | segundos | `60` | Espera antes de half-open |
| `ORCHESTRA_CODEX_ENABLED` | No | booleano | `false` | Reserva el adaptador Codex; no configura autenticación |
| `ORCHESTRA_TEMPORAL_ADDRESS` | No | host:puerto | `127.0.0.1:7233` | Temporal local |
| `ORCHESTRA_TEMPORAL_NAMESPACE` | No | texto | `local-orchestra` | Namespace Temporal |
| `ORCHESTRA_TEMPORAL_TASK_QUEUE` | No | texto | `local-ai` | Cola del worker |
| `ORCHESTRA_WORKFLOW_EXECUTION_TIMEOUT_SECONDS` | No | segundos | `2400` | Límite total de ejecución |
| `ORCHESTRA_WORKFLOW_RUN_TIMEOUT_SECONDS` | No | segundos | `2400` | Límite de un run; no puede superar ejecución |
| `ORCHESTRA_POSTGRES_HOST` | No | host | `127.0.0.1` | PostgreSQL local |
| `ORCHESTRA_POSTGRES_PORT` | No | puerto | `5432` | Puerto PostgreSQL |
| `ORCHESTRA_POSTGRES_DATABASE` | No | texto | `local_orchestra` | Base de datos |
| `ORCHESTRA_POSTGRES_USER` | No | texto | `orchestra_app` | Rol de aplicación |
| `ORCHESTRA_POSTGRES_PASSWORD_FILE` | Sí | ruta | `.secrets/postgres_password` | Archivo de contraseña; nunca valor en claro |
| `ORCHESTRA_API_AUTH_ENABLED` | Sí | booleano | `true` | Debe mantenerse activo en staging |
| `ORCHESTRA_API_TOKEN_FILE` | Sí | ruta | `.secrets/api_token` | Bearer token local |
| `ORCHESTRA_API_RATE_LIMIT_REQUESTS` | No | entero | `60` | Solicitudes por ventana/proceso |
| `ORCHESTRA_API_RATE_LIMIT_WINDOW_SECONDS` | No | segundos | `60` | Ventana de rate limiting |
| `ORCHESTRA_API_MAX_RESPONSE_BYTES` | No | bytes | `262144` | Tamaño máximo de respuesta |
| `ORCHESTRA_API_MAX_LIST_LIMIT` | No | entero | `100` | Máximo de elementos por listado |
| `ORCHESTRA_HEALTH_CACHE_SECONDS` | No | segundos | `15` | Caché del readiness |
| `ORCHESTRA_WORKSPACE_ROOT` | Sí | ruta absoluta | `/srv/local-orchestra/workspaces` | Workspaces aislados por job |
| `ORCHESTRA_WORKSPACE_RETENTION_HOURS` | No | horas | `168` | Antigüedad para candidatos de retención |
| `ORCHESTRA_SANDBOX_LAUNCHER` | Sí | ruta absoluta | `/usr/local/sbin/orchestra-python-test` | Launcher privilegiado exacto |
| `ORCHESTRA_SANDBOX_OUTER_TIMEOUT_SECONDS` | No | segundos | `45` | Timeout externo del launcher |
| `ORCHESTRA_SANDBOX_MAX_OUTPUT_BYTES` | No | bytes | `131072` | Salida máxima del sandbox |
| `ORCHESTRA_OBSIDIAN_ENABLED` | No | booleano | `false` | Activa el adaptador filesystem |
| `ORCHESTRA_OBSIDIAN_VAULT_PATH` | Si se activa | ruta absoluta | `/home/USUARIO/Orchestra-Vault` | Vault dedicado, nunca personal |
| `ORCHESTRA_OBSIDIAN_MAX_NOTE_BYTES` | No | bytes | `262144` | Tamaño máximo de nota |
| `ORCHESTRA_OBSIDIAN_MAX_REPORT_BYTES` | No | bytes | `262144` | Tamaño máximo de reporte |
| `ORCHESTRA_OBSIDIAN_MAX_SEARCH_NOTES` | No | entero | `500` | Notas máximas examinadas |
| `ORCHESTRA_OBSIDIAN_MAX_SEARCH_RESULTS` | No | entero | `20` | Resultados máximos |
| `ORCHESTRA_OBSIDIAN_CONTEXT_MAX_BYTES` | No | bytes | `32768` | Contexto no confiable máximo |

Los rangos exactos están validados por Pydantic en `app/settings.py`; campos
desconocidos provocan error en vez de ignorarse.

## Ollama y Codex

Ollama es el proveedor activo y debe escuchar solo en loopback. Registra modelo,
duración, intento y resultado, y dispone de circuit breaker.

Codex es un adaptador separado y desactivado. Cambiar solo
`ORCHESTRA_CODEX_ENABLED=true` no proporciona credenciales, presupuesto ni
permisos y no debe hacerse hasta definirlos explícitamente. El backend nunca
escala automáticamente desde Ollama hacia Codex.

## Timeouts, sandbox y retención

El timeout de run debe ser menor o igual al de ejecución. El launcher aplica
además 30 segundos internos, 1 CPU, 512 MiB RAM, 64 PIDs, salida acotada, red
nula y filesystem de solo lectura. No incremente límites para ocultar fallos.

La retención es dry-run por defecto:

```bash
.venv/bin/python -m app.retention
```

`--apply` elimina workspaces candidatos y siempre requiere revisión manual.

## Crear secretos sin mostrarlos

```bash
install -d -m 0700 .secrets
umask 077
openssl rand -base64 48 > .secrets/postgres_password
openssl rand -hex 32 > .secrets/api_token
chmod 0600 .secrets/postgres_password .secrets/api_token
```

No use los secretos de otra instancia. No los imprima, copie al portapapeles,
incluya en logs, tickets, capturas, URLs o Git. Para rotar el token, detenga la
API, reemplace el archivo mediante una creación local segura y reinicie.

## Obsidian

Use una bóveda dedicada:

```text
ORCHESTRA_OBSIDIAN_ENABLED=true
ORCHESTRA_OBSIDIAN_VAULT_PATH=/home/USUARIO/Orchestra-Vault
```

Debe contener `Knowledge`, `Projects`, `Skills`, `Templates`, `Runs`,
`Approvals` y `Archive`. El backend lee solo las tres primeras y crea reportes
solo en `Runs`. No copie un vault personal ni instale plugins para esta fase.
Las unidades systemd incluidas usan `ProtectHome=true`; con ellas, Obsidian debe
seguir desactivado hasta preparar una política explícita de usuario y ACLs.

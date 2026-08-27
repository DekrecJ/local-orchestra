# Integración Obsidian por filesystem

Obsidian aporta memoria documental y bitácora para personas. Temporal y
PostgreSQL continúan siendo las fuentes de verdad. No se usan plugins, MCP,
Local REST API ni la carpeta `.obsidian`.

## Permisos

| Carpeta lógica | Operación del backend |
|---|---|
| `Knowledge` | Lectura |
| `Projects` | Lectura |
| `Skills` | Lectura |
| `Runs` | Creación exclusiva de reportes Markdown |
| `Templates` | Ninguna |
| `Approvals` | Ninguna |
| `Archive` | Ninguna |

No se aceptan rutas absolutas, `..`, componentes ocultos, archivos distintos de
`.md`, symlinks ni identificadores Unicode no normalizados o con caracteres de
formato. Todos los contenidos, listados, búsquedas y reportes están acotados.

## Configuración

```text
ORCHESTRA_OBSIDIAN_ENABLED=false
ORCHESTRA_OBSIDIAN_VAULT_PATH=/home/USUARIO/Orchestra-Vault
ORCHESTRA_OBSIDIAN_MAX_NOTE_BYTES=262144
ORCHESTRA_OBSIDIAN_MAX_REPORT_BYTES=262144
ORCHESTRA_OBSIDIAN_MAX_SEARCH_NOTES=500
ORCHESTRA_OBSIDIAN_MAX_SEARCH_RESULTS=20
ORCHESTRA_OBSIDIAN_CONTEXT_MAX_BYTES=32768
```

Al habilitarse se validan raíz, carpetas, tipo, symlinks y permisos. Health
informa `disabled`, `ready` o `degraded`. Obsidian no forma parte del conjunto de
dependencias requeridas para readiness general.

## Reportes y recuperación

La exportación se solicita manualmente para un job terminado. El resultado se
consulta desde Temporal y se renderiza con job, UTC, solicitud opcional, estado,
proveedor/modelo, duración, eventos, artefactos lógicos, pruebas, resultado,
errores, riesgos y aprobación cuando estén disponibles. El nombre incluye job,
UTC y UUID. Un temporal se escribe con `O_EXCL`, se sincroniza y se publica
atómicamente solo si el destino no existe.

Un error del vault se devuelve estructurado y deja el adaptador `degraded`; no
detiene workflows. Recuperar permisos o montaje y repetir la operación manual.
Nunca eliminar ni renombrar automáticamente un reporte o temporal.

## Contexto no confiable y evolución

Las notas pueden contener prompt injection. El selector las etiqueta como
`UNTRUSTED_OBSIDIAN_CONTEXT`, serializa los fragmentos como JSON y declara que
no pueden cambiar políticas, permisos, prompts de sistema, herramientas o
sandbox. Los consumidores futuros deben conservar esa separación.

Para RAG local, indexar fragmentos limitados en PostgreSQL con su ID lógico y
hash, sin convertir Obsidian en estado transaccional. Una CLI o MCP/REST futuro
debe enlazarse únicamente a localhost, reutilizar autenticación/rate limiting y
requiere autorización separada antes de instalar plugins. Usar siempre un vault
dedicado; no apuntar este adaptador a un vault personal.

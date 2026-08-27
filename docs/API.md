# Contrato API v1

OpenAPI está en `/openapi.json` y Swagger en `/docs`. Excepto health, los
endpoints requieren `Authorization: Bearer <token>`. Crear trabajos también
requiere `Idempotency-Key` de 8–128 caracteres seguros.

| Método | Ruta | Uso |
|---|---|---|
| GET | `/health/live` | Vida del proceso |
| GET | `/health/ready` | Dependencias y estado degraded |
| GET | `/metrics` | Contadores locales Prometheus |
| POST | `/v1/jobs` | Crear/recuperar un trabajo idempotente |
| GET | `/v1/jobs` | Listar trabajos |
| GET | `/v1/jobs/{job_id}` | Estado y progreso |
| GET | `/v1/jobs/{job_id}/events` | Eventos semánticos para UI |
| GET | `/v1/jobs/{job_id}/history` | Historial Temporal sanitizado |
| POST | `/v1/jobs/{job_id}/cancel` | Cancelación idempotente |
| POST | `/v1/jobs/{job_id}/approval` | Aprobar o rechazar |
| GET | `/v1/jobs/{job_id}/result` | Resultado estructurado |
| GET | `/v1/jobs/{job_id}/artifacts` | Metadatos y SHA-256 |
| GET | `/v1/jobs/{job_id}/artifacts/{name}` | Contenido acotado |
| POST | `/v1/memories` | Almacenar memoria idempotente por contenido |
| POST | `/v1/memories/search` | Buscar memoria semántica |
| GET | `/v1/knowledge/status` | Estado y permisos lógicos de Obsidian |
| GET | `/v1/knowledge/notes` | Listar notas autorizadas y acotadas |
| POST | `/v1/knowledge/search` | Buscar texto y frontmatter seguro |
| GET | `/v1/knowledge/notes/{note_id}` | Leer una nota como contexto no confiable |
| GET | `/v1/knowledge/reports` | Listar reportes de `Runs` |
| GET | `/v1/knowledge/reports/{report_id}` | Consultar un reporte acotado |
| POST | `/v1/jobs/{job_id}/reports` | Exportar manualmente un resultado terminado |

Los errores tienen `error.code`, `message`, `category`, `retryable` y `details`.
Los requests rechazan campos desconocidos. Tipos disponibles: `python_code` y
`conversation`. `requires_approval=true` impide invocar el proveedor antes de
una decisión humana.

Los identificadores de notas son relativos y deben comenzar por `Knowledge/`,
`Projects/` o `Skills/`; los reportes usan solo su nombre lógico. Ninguna
respuesta contiene la raíz física del vault. Las búsquedas y contenidos tienen
límites configurables. El contenido de notas se devuelve con `trust=untrusted`.

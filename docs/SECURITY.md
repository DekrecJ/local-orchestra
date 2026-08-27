# Seguridad y superficie de ataque

- Autenticación cerrada por defecto; el token está en `.secrets/api_token`, se
  compara en tiempo constante y nunca se registra.
- Rate limiting por ventana deslizante local al proceso.
- IDs idempotentes mediante SHA-256 de sujeto y clave.
- Sandbox sin red ni pulls, con límites y permisos mínimos.
- El runner valida ruta y rechaza symlinks antes de invocar el launcher; la
  política versionada repite el control dentro de la frontera privilegiada.
- Ningún endpoint despliega, publica, instala o elimina datos.
- Retención en dry-run por defecto; `--apply` siempre es manual.

El lock usa versiones exactas. La superficie principal es FastAPI/httpx,
Temporal, LangChain/LangGraph/Ollama y psycopg/pgvector. No se consultó una base
remota de vulnerabilidades. Antes de exponer fuera de localhost se requiere un
escaneo autorizado, TLS y rate limit compartido.

`requeriments.lock` queda como nombre legado. `requirements.lock` es canónico e
incluye `pgvector`, dependencia importada por el código.

Decisiones no activadas: Codex (auth/presupuesto/permisos), instalación del
launcher/sudoers versionado, unidades systemd y cualquier exposición de red.

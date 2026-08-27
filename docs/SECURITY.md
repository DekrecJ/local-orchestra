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
- Obsidian está desactivado por defecto. Rechaza rutas absolutas, traversal,
  componentes ocultos, Unicode de formato, extensiones distintas de `.md` y
  symlinks. Las lecturas y la publicación exclusiva usan `O_NOFOLLOW` y
  descriptores de directorio.
- El adaptador nunca escribe en `Knowledge`, `Projects`, `Skills`, `Templates`,
  `Approvals` ni `Archive`. `Runs` es append-only: publica mediante enlace
  atómico exclusivo y no sustituye archivos existentes.
- El Markdown recuperado es dato no confiable. El selector de contexto lo
  serializa dentro de límites explícitos y prohíbe interpretarlo como políticas,
  permisos, prompts de sistema, llamadas a herramientas o configuración del
  sandbox.

El lock usa versiones exactas. La superficie principal es FastAPI/httpx,
Temporal, LangChain/LangGraph/Ollama y psycopg/pgvector. No se consultó una base
remota de vulnerabilidades. Antes de exponer fuera de localhost se requiere un
escaneo autorizado, TLS y rate limit compartido.

`requeriments.lock` queda como nombre legado. `requirements.lock` es canónico e
incluye `pgvector`, dependencia importada por el código.

Decisiones no activadas: Codex (auth/presupuesto/permisos), instalación del
launcher/sudoers versionado, unidades systemd y cualquier exposición de red.

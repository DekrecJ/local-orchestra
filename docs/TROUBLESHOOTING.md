# Resolución de problemas

Todos los comandos siguientes son locales y evitan mostrar `.env`, tokens o
contraseñas. Empiece con:

```bash
ops/bin/orchestra-requirements --check
ops/bin/orchestra-diagnose
curl --silent --show-error http://127.0.0.1:8000/health/ready
```

No adjunte `.env`, `.secrets` ni logs sin revisarlos.

## Docker no disponible

```bash
systemctl is-active docker
docker --version
docker info
docker compose version
```

Si `docker info` devuelve permiso denegado, vuelva a iniciar sesión después de
añadir el usuario al grupo `docker`, o solicite al administrador la política
aprobada. No haga el socket Docker público. Confirme también que el launcher
instalado coincide:

```bash
sha256sum ops/sandbox/orchestra-python-test /usr/local/sbin/orchestra-python-test
```

## Ollama no responde o falta un modelo

```bash
systemctl status ollama --no-pager
curl --fail --silent http://127.0.0.1:11434/api/tags >/dev/null
ollama list
```

Los nombres esperados son `qwen3.5:4b` y `qwen3-embedding:0.6b`. `ollama pull`
requiere red y debe ejecutarse explícitamente. No cambie el endpoint a una IP
externa.

## Temporal no disponible

```bash
docker compose -f deploy/compose/compose.yaml ps temporal
timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/7233'
```

Revise logs acotados sin incluir datos de jobs sensibles:

```bash
docker compose -f deploy/compose/compose.yaml logs --tail=100 temporal
```

No sustituya IDs ni borre el volumen para corregir un workflow.

## PostgreSQL no disponible

```bash
docker compose -f deploy/compose/compose.yaml ps postgresql
timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/5432'
```

No incluya la contraseña en `psql`, URLs o historial. Si es una base nueva,
ejecute los inicializadores sin imprimir el DSN:

```bash
.venv/bin/python -m app.setup_memory
.venv/bin/python -m app.setup_vector_memory
```

## La API devuelve 401 o 503 de autenticación

`401 authentication_required` significa que falta el header Bearer.
`503 authentication_unconfigured` significa que el archivo local no existe o
está vacío. Compruebe solo metadatos:

```bash
stat -c '%n mode=%a bytes=%s' .secrets/api_token
```

Debe ser archivo regular, no symlink, modo `600` y tamaño mayor que cero. Nunca
publique ni imprima su contenido.

## Readiness degraded

```bash
curl --silent --show-error http://127.0.0.1:8000/health/live
curl --silent --show-error http://127.0.0.1:8000/health/ready
ops/bin/orchestra-diagnose
```

Revise el componente marcado: Temporal, PostgreSQL, Ollama, Docker, sandbox o
autenticación son obligatorios. Codex y Obsidian pueden estar `disabled` sin
fallar readiness.

## Obsidian disabled, degraded o con permisos incorrectos

`disabled` es el estado normal si `ORCHESTRA_OBSIDIAN_ENABLED=false`.
Para `degraded`, compruebe únicamente estructura y permisos del vault dedicado:

```bash
VAULT=/home/USUARIO/Orchestra-Vault
stat -c '%n type=%F mode=%a' "$VAULT" \
  "$VAULT/Knowledge" "$VAULT/Projects" "$VAULT/Skills" \
  "$VAULT/Templates" "$VAULT/Runs" "$VAULT/Approvals" "$VAULT/Archive"
find -P "$VAULT" -maxdepth 2 -type l -print
```

Sustituya `USUARIO` antes de ejecutar. No apunte a un vault personal. FUSE solo
es relevante si el vault está sobre un montaje FUSE: este debe soportar
`O_NOFOLLOW`, enlaces duros, `fsync`, permisos Unix y creación exclusiva. Si no
los soporta, use un filesystem local normal.

## Puerto ocupado

```bash
ss -ltn | grep -E ':(5432|7233|8000|8233|11434)[[:space:]]'
```

No mate procesos desconocidos. Identifique el servicio propietario y cambie la
configuración de forma coordinada; mantenga los bindings en `127.0.0.1`.

## Worker detenido

```bash
test -s run/worker.pid && stat -c '%n bytes=%s' run/worker.pid
ps -eo pid,user,stat,cmd | grep '[a]pp.worker'
tail -n 100 logs/worker.log
```

Temporal conserva el historial. Una vez corregida la infraestructura, arranque
el worker con la misma task queue mediante `ops/bin/orchestra-start`.

## PID obsoleto

`orchestra-start` se niega a sobrescribir PID files. Ejecute primero:

```bash
ops/bin/orchestra-stop
```

El script valida `/proc/PID/cmdline` antes de enviar `SIGTERM`. Si informa que el
PID pertenece a otro proceso, no elimine ni señale nada hasta revisarlo. Solo un
administrador que haya confirmado que el proceso ya no existe debe retirar el
PID file afectado.

## Después de reiniciar el sistema

```bash
systemctl is-active docker
systemctl is-active ollama
docker compose -f deploy/compose/compose.yaml ps
ops/bin/orchestra-requirements --check
ops/bin/orchestra-start
ops/bin/orchestra-diagnose
```

Si se instalaron unidades systemd opcionales, use `systemctl status` sobre sus
nombres en vez de iniciar una segunda copia manual.

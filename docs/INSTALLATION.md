# Instalación en Ubuntu

Esta guía crea una instancia independiente. No copie `.env`, `.secrets`, bases
de datos, workspaces ni vaults de otra persona. Los comandos marcados como
`sudo` modifican el sistema; los demás deben ejecutarse como el usuario normal.

## Plataforma compatible

La combinación realmente validada es:

- Ubuntu 26.04 LTS.
- Python 3.14.4; las dependencias declaran Python 3.10 o posterior.
- Git 2.53.0.
- Docker Engine 29.7.2.
- Ollama CLI 0.32.15.
- Sandbox `python:3.13-slim`.

Ubuntu 24.04 y 22.04 no se probaron con este lock y no se declaran compatibles.
En otra versión, ejecute toda la suite y los workflows reales antes de confiar en
la instalación.

## 1. Revisar acciones privilegiadas

Una instalación nueva puede necesitar `sudo` para:

1. instalar Git, Python, Docker, curl y OpenSSL;
2. crear `/srv/local-orchestra/apps` y `/srv/local-orchestra/workspaces`;
3. instalar el launcher restringido en `/usr/local/sbin`;
4. conceder acceso administrativo a Docker;
5. editar una regla sudoers limitada para el launcher;
6. opcionalmente instalar unidades systemd.

El repositorio no automatiza sudoers, firewall ni systemd. No use `curl | bash`.

## 2. Paquetes base

Comandos con sudo:

```bash
sudo apt update
sudo apt install git python3 python3-venv python3-pip docker.io docker-compose-v2 curl openssl
```

Verifique:

```bash
git --version
python3 --version
docker --version
docker compose version
openssl version
```

Python debe ser 3.10 o posterior. Si Docker no permite acceso al usuario normal,
puede añadirlo al grupo `docker`; este grupo equivale prácticamente a acceso
root y debe concederse solo a administradores autorizados:

```bash
sudo usermod -aG docker "$USER"
```

Cierre completamente la sesión y vuelva a entrar antes de probar `docker info`.
No desactive firewall ni publique el socket Docker.

## 3. Clonar el repositorio privado

Prepare la disposición canónica. Comando con sudo:

```bash
sudo install -d -o "$USER" -g "$(id -gn)" -m 0750 /srv/local-orchestra/apps
sudo install -d -o "$USER" -g "$(id -gn)" -m 0750 /srv/local-orchestra/workspaces
```

Comandos normales:

```bash
git clone URL_PRIVADA_DEL_REPOSITORIO /srv/local-orchestra/apps/orchestrator
cd /srv/local-orchestra/apps/orchestrator
git status --short --branch
```

Use el método de autenticación privado aprobado por el propietario. No incluya
tokens en la URL, historial shell o archivos del repositorio.

## 4. Python y entorno virtual

Comandos normales:

```bash
cd /srv/local-orchestra/apps/orchestrator
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip check
```

`requirements.lock` es el lock canónico. `requeriments.lock` se conserva solo
como nombre legado y no debe usarse en instalaciones nuevas.

## 5. Credenciales locales y configuración

No copie el `.env` del desarrollador. Comandos normales:

```bash
cd /srv/local-orchestra/apps/orchestrator
cp -n .env.example .env
install -d -m 0700 .secrets
umask 077
openssl rand -base64 48 > .secrets/postgres_password
openssl rand -hex 32 > .secrets/api_token
chmod 0600 .secrets/postgres_password .secrets/api_token
```

Estos comandos guardan los secretos sin imprimirlos. No ejecute `cat`, `echo` o
depuración sobre esos archivos. Revise `.env` con un editor local y sustituya
solo placeholders como `USUARIO`; consulte [CONFIGURATION.md](CONFIGURATION.md).
`cp -n` nunca sobrescribe un `.env` existente.

## 6. PostgreSQL y Temporal

La configuración Compose incluida usa volúmenes persistentes, PostgreSQL 17 con
pgvector y Temporal dev server. Todos los puertos se publican solo en loopback.
Las etiquetas están fijadas en `deploy/compose/compose.yaml`; las imágenes no se
descargaron durante la preparación documental y deben verificarse en el registro
autorizado antes de actualizar sus tags.

Comandos normales con acceso Docker; `pull` necesita red:

```bash
cd /srv/local-orchestra/apps/orchestrator
docker compose -f deploy/compose/compose.yaml config --quiet
docker compose -f deploy/compose/compose.yaml pull
docker compose -f deploy/compose/compose.yaml up -d
docker compose -f deploy/compose/compose.yaml ps
```

PostgreSQL queda en `127.0.0.1:5432`; Temporal en `127.0.0.1:7233` y su UI de
desarrollo en `127.0.0.1:8233`. No cambie estos bindings a `0.0.0.0`.

Inicialice los esquemas de aplicación después del healthcheck:

```bash
.venv/bin/python -m app.setup_memory
.venv/bin/python -m app.setup_vector_memory
```

Los comandos son idempotentes. No ejecute `docker compose down --volumes` salvo
que haya decidido eliminar los datos de esa instancia.

## 7. Ollama y modelos

Instale Ollama desde un paquete oficial para Ubuntu descargado y verificado por
separado. No canalice un script remoto directamente al shell. Si dispone de un
archivo `.deb` verificado, la instalación es una acción con sudo:

```bash
sudo apt install ./ollama_VERSION_amd64.deb
```

Inicie el servicio según el paquete y confirme que escucha solo localmente:

```bash
systemctl status ollama --no-pager
curl --fail --silent http://127.0.0.1:11434/api/tags >/dev/null
```

Descargue explícitamente los modelos; estos comandos requieren red y espacio en
disco:

```bash
ollama pull qwen3.5:4b
ollama pull qwen3-embedding:0.6b
ollama list
```

No configure Ollama en una dirección externa. Codex permanece desactivado.

## 8. Sandbox

Descargue de forma explícita la imagen usada para ejecutar código no confiable:

```bash
docker pull python:3.13-slim
docker image inspect python:3.13-slim >/dev/null
```

Revise primero el launcher versionado:

```bash
sed -n '1,240p' ops/sandbox/orchestra-python-test
sha256sum ops/sandbox/orchestra-python-test
```

Instálelo con sudo:

```bash
sudo install -o root -g root -m 0755 \
  ops/sandbox/orchestra-python-test \
  /usr/local/sbin/orchestra-python-test
```

El worker necesita ejecutar únicamente ese launcher como root. Edite con
`sudo visudo -f /etc/sudoers.d/local-orchestra-sandbox` y sustituya `USUARIO`
por el usuario operativo real:

```text
USUARIO ALL=(root) NOPASSWD: /usr/local/sbin/orchestra-python-test
```

Valide la sintaxis antes de cerrar el editor. No conceda `NOPASSWD` a Python,
Docker, Bash ni comodines. El launcher acepta solo IDs de job por stdin, rechaza
symlinks y ejecuta el contenedor sin red, capacidades ni escritura sobre el
workspace.

## 9. Comprobar y probar

Comandos normales:

```bash
ops/bin/orchestra-requirements --help
ops/bin/orchestra-requirements --check
.venv/bin/python -m compileall -q app integration
.venv/bin/python -m unittest discover -v
.venv/bin/python -m pip check
```

El comprobador no lee `.env` ni secretos y no modifica el sistema. Para pruebas
de integración Temporal, con la infraestructura activa:

```bash
timeout 60s .venv/bin/python -m integration.temporal_backend_checks
```

## 10. Inicio, diagnóstico y parada

```bash
ops/bin/orchestra-start
ops/bin/orchestra-diagnose
curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
ops/bin/orchestra-stop
```

Los scripts resuelven el checkout por su propia ubicación. Solo API y worker son
gestionados por ellos; PostgreSQL, Temporal, Docker y Ollama permanecen aparte.

## 11. Reiniciar la computadora

Compose usa `restart: unless-stopped`, pero compruebe siempre la recuperación:

```bash
systemctl is-active docker
systemctl is-active ollama
cd /srv/local-orchestra/apps/orchestrator
docker compose -f deploy/compose/compose.yaml ps
ops/bin/orchestra-requirements --check
ops/bin/orchestra-start
ops/bin/orchestra-diagnose
```

API/worker no arrancan automáticamente salvo que un administrador instale de
forma explícita las plantillas de `deploy/systemd`. Esas unidades asumen usuario
`orchestra` y la disposición canónica; deben revisarse y adaptarse antes de
copiarlas a `/etc/systemd/system`. Si se usan sin cambiar ese usuario, la regla
sudoers limitada para el launcher también debe autorizar a `orchestra`, y el
repositorio, secretos y workspace deben tener propietarios/permisos coherentes.
Esta guía no crea el usuario, instala las unidades ni las habilita.
Las plantillas actuales usan `ProtectHome=true` y, por tanto, asumen Obsidian
desactivado. Habilitar un vault bajo `/home` con systemd requiere una revisión
separada de usuario, ACLs y rutas permitidas; no debilite `ProtectHome` sin un
modelo de amenazas aprobado.

## 12. Desinstalación conservadora

Detenga procesos sin borrar datos:

```bash
cd /srv/local-orchestra/apps/orchestrator
ops/bin/orchestra-stop
docker compose -f deploy/compose/compose.yaml stop
```

Después, un administrador puede deshabilitar unidades opcionales o retirar el
launcher/sudoers, pero debe confirmar cada eliminación. No use `rm -rf`,
`docker compose down --volumes`, `docker volume prune` ni elimine el vault.
Conserve o respalde por separado:

- el repositorio y su `.env` local;
- `.secrets`;
- volúmenes `local-orchestra_postgres_data` y `local-orchestra_temporal_data`;
- `/srv/local-orchestra/workspaces` si su política lo exige;
- el vault Obsidian dedicado, si está habilitado.

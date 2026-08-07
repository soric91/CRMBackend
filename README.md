# CRM Backend — Device Management Platform (EMS)

Backend independiente para administrar clientes, sedes, gateways y equipos
Modbus de la plataforma EMS. **No** es un CRM de ventas: es la fuente de verdad
administrativa del parque de dispositivos.

Responsabilidades:

1. Administrar clientes, sedes, gateways, equipos Modbus y sus variables.
2. Gestionar credenciales y roles (`admin`, `tecnico`, `cliente`,
   `solo_lectura`).
3. Habilitar o deshabilitar, por cliente, el acceso a su página de consumo
   energético (`clients.puede_ver_consumo`).

Fuera del alcance de v1, para agregar más adelante: servir la configuración
del firmware (`GET /gateway/config`), actualización remota de firmware,
tarifas consumidas por `ApiEMS` y configuración de alertas.

Los repositorios `gatewayEMS` y `ApiEMS` no se modifican desde aquí.

## Stack

Python 3.13 · uv · FastAPI · SQLAlchemy 2.x async · Alembic · PostgreSQL
(Supabase) · Pydantic v2 · pytest.

## Arquitectura

```
app/
  api/v1/        routers REST — sin lógica de negocio
  core/          config, seguridad, logging, excepciones, sesión de BD
  domain/        entidades y reglas puras (sin FastAPI ni SQLAlchemy)
  services/      casos de uso; orquestan repositorios
  repositories/  acceso a datos vía SQLAlchemy
  models/        tablas SQLAlchemy
  schemas/       modelos Pydantic de request/response
  main.py        application factory
alembic/         migraciones versionadas
tests/           unit/ e integration/
```

Dirección de dependencias: `api` → `services` → `repositories` → `models`.
El dominio no depende de ninguna capa externa.

## Puesta en marcha

```bash
uv sync                     # instala dependencias
cp .env.example .env        # completar con credenciales propias
uv run uvicorn app.main:create_app --factory --reload
```

Docs interactivas en `http://localhost:8000/docs` (deshabilitadas cuando
`ENVIRONMENT=production`).

### Variables de entorno

Todas están documentadas en `.env.example`. Las obligatorias:

| Variable               | Descripción                                        |
| ---------------------- | -------------------------------------------------- |
| `SUPABASE_DB_URL`      | DSN PostgreSQL de Supabase, sin la password        |
| `SUPABASE_DB_PASSWORD` | Password de la base, en su propia variable         |
| `JWT_SECRET_KEY`       | Clave de firma JWT, mínimo 32 caracteres           |
| `DB_SSL_MODE`          | TLS de la conexión; default `require`              |

El `.env` nunca se commitea y no contiene valores de ejemplo reales.

La password va aparte del DSN a propósito: la app la mergea y la escapa al
armar la URL, así que caracteres como `@ / # % :` no rompen la conexión. Si de
todos modos la ponés inline en `SUPABASE_DB_URL`, también funciona;
`SUPABASE_DB_PASSWORD` tiene prioridad si están las dos.

Usá el **Session pooler** de Supabase (Dashboard → Connect → Connection
String), no la Direct connection: esta última solo publica registro AAAA y es
inalcanzable desde una red sin IPv6.

## Migraciones

Las tablas **no** se crean a mano en el SQL Editor de Supabase. El flujo es:

```bash
uv run alembic revision --autogenerate -m "descripcion"   # genera migración
uv run alembic upgrade head                               # aplica a la BD
uv run alembic downgrade -1                               # revierte una
```

La URL de la base la inyecta `alembic/env.py` desde `SUPABASE_DB_URL`;
`alembic.ini` no contiene credenciales.

## Docker

La base es Supabase, así que **no hay servicio de PostgreSQL en el compose**:
un solo contenedor con la API, que lee todo de `.env` en tiempo de ejecución.

```bash
docker compose up -d --build
curl localhost:8000/api/v1/health
docker compose logs -f api
docker compose down
```

La imagen es multi-stage: `uv` y el toolchain de compilación se quedan en la
etapa de build y nunca llegan a producción. Corre como usuario sin privilegios
(`app`, uid 1001), con el sistema de archivos en solo lectura y
`no-new-privileges`. El `.dockerignore` excluye `.env`, así que ningún secreto
queda en una capa de la imagen.

El `HEALTHCHECK` apunta a `/api/v1/health` y no a `/ready`: el primero no toca
servicios externos, así que una intermitencia de la base no hace que Docker
reinicie un proceso que está sano.

**Las migraciones no corren solas al arrancar el contenedor.** Aplicarlas es un
paso deliberado, para que un despliegue con varias réplicas no dispare varios
`upgrade head` a la vez:

```bash
docker compose run --rm api alembic upgrade head
```

### Cuidado con las comillas en `.env`

`python-dotenv` quita las comillas que rodean un valor; el `--env-file` de
Docker y el `env_file` de Compose **no**. Un `SUPABASE_DB_PASSWORD="abc"`
funciona con `uv run` y falla dentro del contenedor con un error de
autenticación que no menciona las comillas. No entrecomilles nada en `.env`.

## Calidad

```bash
uv run pytest          # tests + cobertura
uv run ruff check .    # lint
uv run ruff format .   # formato
uv run pyright         # tipos
```

Los tests no tocan la base real: usan settings desechables y SQLite en memoria.
Son 4xx tests entre unitarios e integración, con la cobertura por encima del
95%.

Dos detalles que hacen que la cobertura sea confiable:

- `concurrency = ["greenlet", "thread"]` en `pyproject.toml`. SQLAlchemy async
  reanuda las corrutinas dentro de un greenlet; sin esa opción, coverage deja
  de seguir el rastro después del primer `await` sobre la sesión y reporta como
  no cubierto código que demostrablemente corre.
- Las FK se activan explícitamente en SQLite (`PRAGMA foreign_keys=ON`), que por
  defecto las ignora. Sin eso, los tests de cascada pasarían sin probar nada.

Aun así, SQLite no es PostgreSQL: las reglas que dependen del motor —CHECK
constraints, cascadas, tipos— se verificaron además contra la base real dentro
de transacciones con rollback.

## Endpoints actuales

| Método | Ruta                    | Descripción                                 |
| ------ | ----------------------- | ------------------------------------------- |
| GET    | `/api/v1/health`        | Liveness — no toca servicios externos       |
| GET    | `/api/v1/ready`         | Readiness — verifica la base (503 si falla) |
| POST   | `/api/v1/auth/login`    | Credenciales → par de tokens                |
| POST   | `/api/v1/auth/refresh`  | Refresh token → par nuevo                   |
| GET    | `/api/v1/auth/me`       | Cuenta del que llama (requiere bearer)      |
| POST   | `/api/v1/auth/password` | Cambiar la contraseña propia                |

La web de monitoreo tiene su propia superficie de autenticación, con audiencia
`monitor`. Solo entra el rol `cliente`:

| Método | Ruta                            | Descripción                              |
| ------ | ------------------------------- | ---------------------------------------- |
| POST   | `/api/v1/auth-monitor/login`    | Credenciales → tokens + `client_id`      |
| POST   | `/api/v1/auth-monitor/refresh`  | Refresh token → par nuevo                |
| GET    | `/api/v1/auth-monitor/me`       | Identidad del cliente que llama          |
| POST   | `/api/v1/auth-monitor/password` | Cambiar la contraseña propia             |

CRUD administrativo (todo requiere bearer):

| Método         | Ruta                                    |
| -------------- | --------------------------------------- |
| GET, POST      | `/api/v1/clients`                       |
| GET            | `/api/v1/gateways` (flota completa)     |
| GET            | `/api/v1/sites` (flota completa)        |
| GET            | `/api/v1/equipment` (flota completa)    |
| GET, PATCH     | `/api/v1/clients/{id}`                  |
| GET, POST      | `/api/v1/clients/{id}/sites`            |
| GET, POST, DELETE | `/api/v1/clients/{id}/monitor-access` |
| POST           | `/api/v1/clients/{id}/monitor-access/reset` |
| GET, PATCH, DELETE | `/api/v1/sites/{id}`                |
| GET, POST      | `/api/v1/sites/{id}/gateways`           |
| GET, PATCH, DELETE | `/api/v1/gateways/{id}`             |
| GET, POST      | `/api/v1/gateways/{id}/equipment`       |
| GET, PATCH, DELETE | `/api/v1/equipment/{id}`            |
| GET, POST      | `/api/v1/equipment/{id}/variables`      |
| GET, PATCH, DELETE | `/api/v1/variables/{id}`            |
| GET, POST      | `/api/v1/users`                         |
| GET, PATCH, DELETE | `/api/v1/users/{id}`                |
| POST           | `/api/v1/users/{id}/password`           |
| GET, POST      | `/api/v1/tariffs`                       |
| GET, PATCH, DELETE | `/api/v1/tariffs/{id}`              |
| GET, POST, DELETE | `/api/v1/gateways/{id}/credential`   |
| GET            | `/api/v1/fleet` (árbol completo)        |
| GET, POST      | `/api/v1/service-accounts` (solo admin) |
| GET, PATCH, DELETE | `/api/v1/service-accounts/{id}`     |
| POST           | `/api/v1/service-accounts/{id}/secret` (rotar) |

El firmware tiene su propia superficie, con audiencia `gateway`:

| Método | Ruta                                | Quién |
| ------ | ----------------------------------- | ----- |
| POST   | `/api/v1/gateway/token`             | el gateway, con su credencial |
| GET    | `/api/v1/gateway/{uuid}/config`     | el gateway, con su token |
| POST   | `/api/v1/gateway/{uuid}/heartbeat`  | el gateway, para reportar vida |
| POST   | `/api/v1/gateway/{uuid}/config/ack` | el gateway, al aplicarla |
| GET    | `/api/v1/gateways/{id}/config-status` | el CRM |

Las listas están paginadas (`?limit=&offset=`, máximo 200) y devuelven
`{items, total, limit, offset}`. Los listados de flota aceptan además
`?search=` y, en gateways, `?estado=`, `?client_id=` y `?site_id=`.

### `GET /api/v1/fleet` — el árbol en una sola petición

Recorrer la jerarquía con los listados por padre cuesta una petición por nodo:
cliente, luego sitios, luego gateways, luego equipos, luego variables. Este
endpoint devuelve lo mismo anidado, de una vez.

```
GET /api/v1/fleet?client_id=<uuid>&nivel=variables&search=&limit=50&offset=0
```

- **`nivel`** — `sitios` | `gateways` | `equipos` | `variables` (por defecto
  `variables`). Una colección por debajo del nivel pedido llega como `null`, no
  como `[]`: "no lo pediste" y "no hay ninguno" son respuestas distintas.
- **Paginación sobre los clientes**, la raíz del árbol.
- **`client_id`** acota; nunca amplía. Un token de `cliente` ya está fijado a su
  empresa, así que para él `/fleet` es "todo lo mío" — no hay ruta aparte.
- **`ETag` + `If-None-Match`** → **304**. La huella cubre la página entera,
  `total` incluido.

No incluye credenciales ni el interruptor de descarga: eso vive en
`/api/v1/gateways/{id}/config-status`.

Cada gateway trae su `uuid`, que es la identidad con la que aparece en los
tópicos MQTT y con la que el firmware llama a su propia superficie. Eso es lo
que permite resolver una lectura hasta el registro que la produjo.

### Credenciales de servicio — cómo entra otro sistema

`ApiEMS` consume esta API. Antes lo hacía con el login de una persona: una
contraseña que abre el panel, con un rol que además puede escribir, de alguien
que en algún momento se va de la empresa. Ahora tiene identidad propia.

| Método | Ruta                       | Quién |
| ------ | -------------------------- | ----- |
| POST   | `/api/v1/service/token`    | el sistema consumidor, con su credencial |

La credencial tiene dos mitades. `client_id` (prefijo `svc_`) es pública y va
en cada pedido; `client_secret` (prefijo `svcsec_`) se muestra **una sola vez**
y se guarda solo hasheado.

```bash
curl -X POST http://localhost:8000/api/v1/service/token \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"svc_...","client_secret":"svcsec_..."}'
```

Devuelve un token de **1 hora**, audiencia `service`, que llega exactamente a
dos rutas:

| Permiso        | Qué abre                     |
| -------------- | ---------------------------- |
| `tariffs:read` | `GET /api/v1/tariffs`        |
| `fleet:read`   | `GET /api/v1/fleet`          |

Las reglas, en orden de importancia:

- **Nunca escribe.** No hay permiso que lo habilite y no se puede crear uno.
- **Default-deny.** Un endpoint acepta tokens de servicio solo si pide esa
  dependencia por nombre. Cualquier otra ruta responde 401, incluida
  `/api/v1/tariffs/{id}` — se abrió el listado, no el detalle.
- **Los permisos se releen de la base en cada pedido**, no se confían del
  token: desactivar o recortar una credencial se siente en el pedido siguiente.
- **Se puede fijar a un cliente.** Con `client_id`, el consumidor queda
  confinado a esa empresa igual que un login de rol `cliente`.
- **Solo un administrador las emite**, las rota y las revoca. Un `tecnico` no.
- Todas las fallas del intercambio responden el mismo 401 — identificador
  desconocido, secreto errado, desactivada, vencida.

La ventana en la que una revocación todavía no se siente es exactamente
`SERVICE_TOKEN_EXPIRE_MINUTES`, porque un token ya emitido no se puede alcanzar.

Para escribir el firmware hay una guía paso a paso en
`GATEWAY_INTEGRATION.html` — se abre en el navegador y cubre las cuatro rutas
del gateway, los tópicos MQTT y el bucle completo. No se versiona: es un
documento de trabajo.

## Autenticación

JWT con dos tokens. El **access token** (30 min por defecto) lleva `sub`, `role`
y `client_id`. El **refresh token** (7 días) no lleva rol a propósito: al
canjearlo, los privilegios se releen de la base, así que desactivar o degradar
una cuenta surte efecto de inmediato en vez de esperar a que expire.

```bash
curl -X POST localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","password":"..."}'

curl localhost:8000/api/v1/auth/me -H "Authorization: Bearer <access_token>"
```

Roles: `admin`, `tecnico`, `cliente`, `solo_lectura`. Se protege una ruta con

```python
Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.TECNICO))]
```

La lista enumera quién **puede** pasar, no quién no: un rol agregado más
adelante queda denegado por defecto hasta que se lo liste explícitamente.

### Qué ve y qué puede cambiar cada rol

| Rol            | Lee                    | Escribe |
| -------------- | ---------------------- | ------- |
| `admin`        | toda la plataforma     | sí      |
| `tecnico`      | toda la plataforma     | sí      |
| `solo_lectura` | toda la plataforma     | no      |
| `cliente`      | solo su propia empresa | no      |

Las tarifas tienen su propia regla, más estricta: las leen `admin`, `tecnico` y
`solo_lectura`, pero **solo `admin` las modifica** — son precios que multiplican
consumo para producir dinero, y mantener dispositivos no es lo mismo que fijar
lo que cuesta la energía. Un `cliente` recibe **403**, no 404: las tarifas son
de la plataforma, no datos de otra empresa que haya que ocultar.

El mes se normaliza al día 1 al crear, así que mandar `2026-05-17` guarda mayo
2026. El período **no se puede editar**: mover una tarifa reescribiría costos ya
calculados. Si quedó en el mes equivocado, se borra y se carga de nuevo.

Las reglas viven en `app/domain/access.py` como `AccessScope`, sin dependencias
de FastAPI ni SQLAlchemy, y los services las consultan. Ningún router decide
permisos por su cuenta: así no puede aparecer un endpoint que se olvide de
preguntar.

Un `cliente` que pide algo de otra empresa recibe **404, no 403**. Un 403
confirmaría que el recurso existe, que es justo lo que no debe poder averiguar.

### Cuentas

`/users` es solo para `admin` — más estricto que el resto de la escritura a
propósito: un `tecnico` que pudiera crear usuarios podría fabricarse un admin y
promoverse.

Reglas que el service garantiza, sin importar por qué endpoint se entre:

- el rol `cliente` siempre lleva `client_id`; los demás roles nunca lo llevan.
  Promover un `cliente` a staff le quita el cliente automáticamente;
- la plataforma nunca se queda sin administradores activos;
- nadie se desactiva, se degrada ni se borra a sí mismo;
- la contraseña nunca vuelve en ninguna respuesta.

Para desactivar una cuenta se usa `is_active`, no `DELETE`: conserva el rastro
de quién existió. `DELETE` está igual disponible cuando hace falta borrar de
verdad.

Cualquier rol puede cambiar su propia contraseña con `POST /auth/password`,
que exige la contraseña actual — un token robado por sí solo no alcanza para
apoderarse de la cuenta.

### Dar acceso a un cliente

```bash
# 1. crear el cliente y habilitarle la página de consumo
POST  /api/v1/clients            {"nombre_empresa": "..."}
PATCH /api/v1/clients/{id}       {"puede_ver_consumo": true}

# 2. crear su cuenta
POST  /api/v1/users              {"email": "...", "password": "...",
                                  "role": "cliente", "client_id": "..."}
```

El usuario entra por `/auth/login`, y el frontend decide si muestra la página
leyendo `puede_ver_consumo` de `GET /clients/{client_id}`.

### Primer administrador

No hay endpoint público de registro — sería una vía para que cualquiera se cree
un admin. El primero se crea con:

```bash
uv run python -m app.scripts.create_admin
```

Pide email y contraseña por terminal (entrada oculta con `getpass`), usa el
mismo `hash_password` que verifica el login e inserta directo en la base de
`.env`. La contraseña no viaja por HTTP ni queda en logs ni archivos.

Detalles que importan:

- Hashing con **bcrypt directo**, no `passlib`: su última release es de 2020 y
  rompe contra bcrypt 4.1+ leyendo un atributo privado que ya no existe.
- Contraseñas de más de 72 bytes se **rechazan** en vez de truncarse — bcrypt
  ignora el resto en silencio, y dos contraseñas distintas quedarían
  equivalentes.
- Login con email desconocido gasta igual un `bcrypt` de descarte, para que el
  tiempo de respuesta no revele qué direcciones existen. Los tres modos de
  fallo (email inexistente, contraseña mala, cuenta inactiva) devuelven el
  mismo mensaje.
- `decode_token` fija la lista de algoritmos permitidos: sin eso, un token
  puede declarar `alg: none` y saltarse la verificación de firma.

## Modelo de datos

```
Client ──< Site ──< Gateway ──< Equipment ──< Variable
   │                   │
   └──< User           └──< AlertConfig

Tariff        (independiente: una fila por mes, para toda la plataforma)
```

Ocho tablas: `clients`, `sites`, `gateways`, `equipment`, `variables`,
`tariffs`, `users`, `alerts_config`. En v1 se usan las primeras seis;
`tariffs` y `alerts_config` ya están creadas pero todavía sin endpoints.

### Configuración del firmware

El gateway guarda una **credencial larga** que no vence, y con ella pide un
**token de 24 h** que renueva solo. En el CRM se ve y se regenera la
credencial; el token no aparece nunca en la interfaz. Un token de 24 h que
hubiera que copiar a mano a cada equipo todos los días no es operable.

`GET /gateway/{uuid}/config` exige tres cosas a la vez: token válido, que el
uuid de la ruta sea el del token, y `config_habilitada` en `true`. Pedir la
configuración de otro gateway devuelve **404**, no 403 — la ruta es alcanzable
por cualquiera con una credencial válida de otro equipo, y confirmar que un
uuid existe permitiría enumerar el parque.

Revocar la credencial invalida también los tokens ya emitidos: el token por sí
solo no prueba que el gateway siga siendo de confianza.

El documento sale en el vocabulario del firmware — tipos como carácter de
`struct` (`f`, `h`, `H`, `i`, `I`), `gain` en vez de `escala` — pero como JSON,
no como INI: el CRM no conoce el formato de archivos del gateway.

### `estado` se observa, no se guarda

`online`/`offline` se deriva de `ultima_conexion` contra un umbral de 5 minutos
(`app/domain/gateway_status.py`). Antes era una columna que un operador tipeaba
y nada actualizaba, así que el panel mostraba lo que se hubiera puesto en la
instalación, para siempre. Una bandera guardada que nadie refresca es peor que
no tener bandera.

El gateway refresca ese timestamp en cada `POST /gateway/{uuid}/heartbeat`, que
funciona **con la descarga deshabilitada** — si no, un equipo ya configurado
dejaría de reportar vida y aparecería caído. La respuesta del latido le dice
además si tiene una configuración esperando, así que un equipo que solo late se
entera igual de que hay trabajo.

### MQTT: avisos hacia el gateway, presencia desde él

Opcional y apagado por defecto (`MQTT_ENABLED`). El puente vive en
`app/core/mqtt.py` y arranca y para con la aplicación.

**Hacia afuera**: cuando se habilita la descarga de configuración, el CRM
publica un aviso mínimo en `crm/gateways/{uuid}/config`. **El aviso no lleva la
configuración** — el gateway la sigue bajando por HTTP con su credencial. Así
el contrato vive en un solo lugar y el broker nunca es algo en lo que haya que
confiar para entregarlo. Va con `retain`, de modo que un equipo apagado lo
recibe al reconectar.

**Hacia adentro**: los gateways publican en `crm/gateways/{uuid}/status` y eso
refresca `ultima_conexion` sin esperar a la próxima llamada HTTP.

Perder un mensaje no cuesta nada: el gateway consulta igual, así que el puente
hace el sistema más rápido, nunca más correcto. Si el broker está caído, todo
funciona un poco más tarde.

**Confianza**: hoy todos los gateways comparten una credencial de broker, así
que nada impide que uno publique en el tópico de otro. El daño está acotado —un
equipo podría figurar en línea sin estarlo— y la señal autenticada sigue siendo
`POST /gateway/{uuid}/heartbeat`. Credenciales por dispositivo con ACLs por
tópico cierran esa brecha.

El árbol de tópicos es propio, separado de aquel en el que los gateways ya
publican sus lecturas.

### Listados de flota

La navegación por padre —cliente, sede, gateway, equipo— responde *qué tiene
este cliente*. No responde *qué está roto*, que es con lo que se abre el panel.
Para eso están `GET /gateways`, `GET /sites` y `GET /equipment`, con filtros y
búsqueda.

`?estado=offline` se resuelve comparando `ultima_conexion` contra el corte, así
que la respuesta es cierta en el momento de preguntar. Y los filtros **solo
estrechan**: un `cliente` que pide `?client_id=` de otra empresa recibe una
página vacía, nunca los suyos.

### El ciclo de configuración, y por qué no entra en bucle

La respuesta lleva `config_version`, un hash del contenido, y un `ETag`. El
gateway lo guarda y consulta mandando `If-None-Match`: si nada cambió recibe
**304** y no reaplica. El hash excluye `generated_at`, que cambia en cada
petición — incluirlo haría que el equipo reaplicara siempre.

Al escribir el archivo, el gateway avisa con `POST /gateway/{uuid}/config/ack`.
Eso registra la versión aplicada y **apaga `config_habilitada`**, de modo que el
mismo documento no se entrega de nuevo. Un acuse con una versión que ya no es
la vigente se rechaza con 400: dar por aplicado un documento viejo dejaría al
CRM creyendo que el equipo corre algo que no corre.

La contrapartida de que el interruptor se apague solo es que **una edición
posterior no llega al gateway hasta que alguien lo vuelve a encender**. Por eso
existe `GET /gateways/{id}/config-status`, que compara la versión aplicada
contra la que se entregaría ahora y expone `desactualizada`. El panel muestra
ese desfase con la acción para resolverlo; sin eso, un cambio quedaría sin
entregar en silencio.

`ultima_conexion` se actualiza en cada consulta, incluidas las que responden
304: es la señal de vida más fiel que tiene el CRM.

### La base del registro se guarda junto con el número

Las hojas de datos imprimen direcciones en decimal o en hexadecimal, y `2006`
significa dos registros distintos según cuál sea. `variables.notacion_registro`
lo registra: el valor se guarda siempre como entero canónico, y la notación
decide cómo se interpreta lo que el operador escribe y en qué base se entrega.

Cargar `2006` como hex guarda `8198` y sale `"0x2006"`. Cargar `2000` como
decimal guarda `2000` y sale `"2000"`. Mandar `0x2006` declarando decimal es un
422: las dos cosas se contradicen, y adivinar cuál vale pierde datos.

### Acceso a la página de consumo

`clients.puede_ver_consumo` decide si los usuarios de ese cliente pueden abrir
su página de consumo energético. Arranca en `false`: un cliente a medio
configurar no debe ver lecturas parciales. Solo `admin` y `tecnico` lo cambian,
vía `PATCH /clients/{id}`.

El frontend lo resuelve en dos pasos: `GET /auth/me` devuelve el `client_id`
del usuario, y `GET /clients/{client_id}` devuelve `puede_ver_consumo`.

`tariffs` no cuelga de ningún cliente: es el precio de la energía de un mes,
igual para todos. Tres campos de negocio — `mes`, `valor_importado` y
`valor_excedente` (lo que se paga por el excedente que queda después de restar
la energía importada). El mes se guarda como `DATE` en el día 1, con un CHECK
que lo obliga, y la API lo muestra como nombre en español: `junio 2026`. Se
guarda con año porque el precio de enero 2026 no es el de enero 2027, y
`ApiEMS` tiene que poder recalcular un costo pasado.

### Equipos Modbus

Un equipo habla **RTU** (puerto serie) o **TCP** (red), y `transporte` decide
qué campos aplican: los de serie (`puerto`, `baudrate`, `paridad`, `bits`,
`stop_bits`) o los de red (`host`, `puerto_tcp`). Mandar los del otro
transporte es un 422, no un descarte silencioso. `modbus_id` sirve para ambos:
en TCP es el unit id detrás del gateway Modbus, y no siempre es 1.

La unicidad usa **dos índices parciales**, uno por transporte, porque la tupla
que identifica al equipo cambia: `(gateway, puerto, modbus_id)` para RTU y
`(gateway, host, puerto_tcp, modbus_id)` para TCP. Una sola restricción sobre
un `puerto` nullable no protegería nada en TCP — para el motor los NULL son
distintos entre sí. Un equipo RTU y uno TCP pueden compartir `modbus_id` en el
mismo gateway: viven en buses distintos.

`frecuencia_lectura_segundos` vive en el **equipo**, no en cada variable: el
firmware abre el puerto, recorre los registros del esclavo y cierra, así que
una sola cadencia es lo que el hardware puede cumplir. `tipo_registro` vive en
la **variable**, porque un mismo analizador expone las medidas eléctricas como
holding o input y los estados de relé como coils.

Decisiones que aplican a todas:

- **PK UUID**, no enteros secuenciales: es una plataforma multi-cliente y los
  IDs viajan en la URL. Con enteros, un cliente puede enumerar los recursos de
  otro.
- **Enums como VARCHAR + CHECK**, no como tipo `ENUM` nativo de PostgreSQL.
  Agregar un valor a un enum nativo es una migración que Alembic no puede
  autogenerar, y el tipo sobrevive a la tabla que lo usaba. Los valores viven
  en `app/domain/enums.py` y se comparten con la capa API.
- **`Numeric`, nunca `float`**, para tarifas y escalas: son valores que
  multiplican consumo para producir dinero.
- **Borrado en cascada** de `Client` hacia abajo, salvo `users`, que usa
  `RESTRICT` — borrar un cliente no debe destruir cuentas de acceso en
  silencio.
- **`lazy="raise"`** en todas las relaciones: bajo asyncio un lazy load
  implícito falla como `MissingGreenlet` lejos de su causa. Los repositorios
  cargan lo que necesitan con `selectinload`.
- **Los gateways no guardan credencial en v1.** El token de acceso existía solo
  para que el firmware se autenticara al pedir su configuración; sin ese
  endpoint, era peso muerto. Cuando se agregue, vuelve como columna hasheada.

## Estado

**v1 completa** — fundaciones, modelos y migraciones, auth y roles, CRUD
administrativo, acceso del cliente al monitoreo, gestión de usuarios, Docker.

Agregado después de v1, a pedido:

- tarifas mensuales con su API;
- acceso del cliente a la web de monitoreo (`auth-monitor`), con contraseña
  inicial de un solo uso y cambio obligatorio en el primer ingreso;
- transporte Modbus RTU o TCP, con unicidad por transporte;
- notación decimal o hexadecimal por registro;
- credencial del gateway, token de 24 h y entrega de configuración con `ETag`,
  acuse y detección de desfase;
- `estado` derivado del último contacto, con latido propio;
- listados de flota con filtros y búsqueda;
- puente MQTT para avisar al gateway y recibir su presencia;
- `GET /fleet` — el árbol completo en una petición, con `ETag`;
- credenciales de servicio: audiencia `service`, permisos de solo lectura,
  emisión y rotación desde el panel.

Pendiente, en orden de utilidad:

- `DELETE /clients/{id}` — el único de la jerarquía que no se puede borrar;
- `?search=` en `/clients` y un `GET /dashboard/summary` con los conteos de la
  pantalla inicial;
- un script de verificación de extremo a extremo, que recorra los cinco flujos
  con los cuatro roles contra la base real;
- configuración de alertas: la tabla existe, sin endpoints;
- **el primer commit** — el repositorio todavía no tiene historia.

Fuera de alcance por diseño: las lecturas de consumo, que son de `ApiEMS`.

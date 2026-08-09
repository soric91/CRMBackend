# Mudar la base a otro Postgres

Sirve para cualquier destino —tu servidor, Neon, Railway, un contenedor local—
porque el proyecto solo usa Postgres como Postgres: la autenticación es propia
(RS256 con claves en `secrets/`), no hay funciones de Supabase, ni RLS, ni
Storage. Lo único que hay que mudar son las tablas y sus filas.

## Antes de empezar

| | |
|---|---|
| Postgres | 15 o superior |
| Extensiones | ninguna. Los UUID los genera la aplicación, no `uuid-ossp` |
| Acceso | el usuario tiene que poder crear tablas en el esquema `public` |
| `pg_dump` / `pg_restore` | de una versión **igual o mayor** que el Postgres de origen |

Verificá las herramientas antes de tocar nada:

```bash
pg_dump --version
psql --version
```

Si `pg_dump` es más viejo que el servidor de origen, el volcado falla a mitad
de camino y no siempre lo dice claro.

---

## 1 · Anotar de dónde salís

La URL actual está en `.env`, en `DATABASE_URL`. No la copies a mano a ningún
lado: guardala en una variable de la terminal, así no queda en el historial ni
en un archivo suelto.

```bash
cd ~/Documentos/projects/CRMBackend
read -s ORIGEN        # pegás la URL y Enter; no se ve al escribir
export ORIGEN
```

La URL del `.env` empieza con `postgresql+asyncpg://`. Ese prefijo es de
SQLAlchemy: **`pg_dump` no lo entiende.** Usá `postgresql://` a secas.

---

## 2 · Levantar el destino

Si es tu propio servidor, con Docker:

```bash
docker run -d --name ems-postgres \
  -e POSTGRES_PASSWORD='<una contraseña larga>' \
  -e POSTGRES_USER=ems \
  -e POSTGRES_DB=ems \
  -p 5432:5432 \
  -v ems_pgdata:/var/lib/postgresql/data \
  postgres:17
```

El `-v` es lo que hace que los datos sobrevivan a recrear el contenedor. Sin
volumen, `docker rm` borra la base entera.

Si es un servicio administrado, creá la base desde su panel y copiá la URL de
conexión que te dé.

```bash
read -s DESTINO
export DESTINO
```

Comprobá que responde antes de seguir:

```bash
psql "$DESTINO" -c 'SELECT version();'
```

---

## 3 · Volcar el origen

> Esto copia **la estructura y todas las filas**. Si lo que querés es una base
> vacía —un entorno de pruebas, una instalación nueva— saltá al
> [apéndice](#apéndice--una-base-vacía-sin-datos) y volvé al paso 6.

```bash
pg_dump "$ORIGEN" \
  --no-owner --no-privileges --no-acl \
  --format=custom \
  --file=ems_$(date +%Y%m%d).dump
```

Qué hace cada bandera, porque importan:

- `--no-owner`, `--no-privileges`, `--no-acl` — el destino tiene otros usuarios
  y otros roles. Sin esto el restore falla intentando asignar permisos a un rol
  que allá no existe.
- `--format=custom` — comprimido y restaurable en paralelo. También permite
  restaurar una sola tabla si algo sale mal.

Mirá el tamaño: si el archivo pesa unos pocos KB y esperabas más, el volcado no
trajo los datos y no tiene sentido continuar.

```bash
ls -lh ems_*.dump
```

---

## 4 · Restaurar

```bash
pg_restore --no-owner --no-privileges \
  --dbname="$DESTINO" \
  ems_$(date +%Y%m%d).dump
```

Es normal que imprima avisos sobre extensiones o comentarios. Lo que **no** es
normal es un error sobre una tabla o una restricción: eso sí hay que leerlo.

---

## 5 · Comprobar que llegó todo

Contá las filas en los dos lados y compará. Si un número no coincide, no sigas.

```bash
for base in "$ORIGEN" "$DESTINO"; do
  echo "--- $base" | cut -c1-30
  psql "$base" -t -c "
    SELECT 'clients', count(*) FROM clients
    UNION ALL SELECT 'sites', count(*) FROM sites
    UNION ALL SELECT 'gateways', count(*) FROM gateways
    UNION ALL SELECT 'equipment', count(*) FROM equipment
    UNION ALL SELECT 'variables', count(*) FROM variables
    UNION ALL SELECT 'users', count(*) FROM users
    UNION ALL SELECT 'platform_settings', count(*) FROM platform_settings
    UNION ALL SELECT 'service_accounts', count(*) FROM service_accounts
    ORDER BY 1;"
done
```

Y que la versión de migraciones sea la misma:

```bash
psql "$DESTINO" -c 'SELECT version_num FROM alembic_version;'
```

Ese número tiene que coincidir con el del origen. Si difiere, el volcado se
hizo antes de aplicar una migración.

---

## 6 · Apuntar la aplicación al destino

En `.env`, cambiar `DATABASE_URL`. Acá **sí** va el prefijo de SQLAlchemy:

```
DATABASE_URL=postgresql+asyncpg://ems:<contraseña>@<host>:5432/ems
```

Si el destino exige TLS —los servicios administrados suelen hacerlo— agregá al
final `?ssl=require`. Con `asyncpg` el parámetro es `ssl`, no `sslmode`: es la
diferencia que hace que la conexión falle con un mensaje que no menciona TLS.

Después:

```bash
uv run alembic upgrade head        # debería decir que ya está al día
uv run pytest -q                   # la suite corre contra la base configurada
uv run uvicorn app.main:create_app --factory --port 8000
```

`alembic upgrade head` sobre una base recién restaurada no debería aplicar
nada. Si aplica algo, es que el volcado salió de una base más vieja.

---

## 7 · Lo que **no** viaja en el volcado

Esto es lo que se olvida y rompe después, no durante:

**Las claves de firma.** Viven en `secrets/` como archivos, no en la base. Si
cambiás de servidor hay que copiarlas: sin ellas el CRM firma con una clave
nueva y **todas las sesiones abiertas dejan de valer**, incluidas las de los
gateways enrolados.

**`SETTINGS_ENCRYPTION_KEY`.** Está en `.env`. Los secretos de
`platform_settings` —la contraseña del broker, el token de InfluxDB— están
cifrados con esa clave. Si la perdés, las filas viajan pero no se pueden
descifrar, y hay que volver a cargar cada secreto a mano.

**Las demás variables del `.env`.** No están en la base por diseño.

---

## 8 · Volver atrás

No borres el origen hasta haber usado el destino unos días. Volver es cambiar
`DATABASE_URL` de nuevo y reiniciar — mientras el origen siga existiendo.

Lo que sí conviene: apagar las escrituras al origen en cuanto el destino esté
sirviendo. Con las dos bases vivas y la aplicación escribiendo en una, la otra
se queda atrás en silencio y "volver atrás" pasa a significar perder lo nuevo.

---

## Sobre la latencia

La base actual está en `aws-0-us-east-2` y cada consulta cuesta ~190 ms de ida
y vuelta. Eso no lo arregla ningún índice: es distancia. Un endpoint que hace
seis consultas en serie paga ~1,1 s antes de ejecutar una sola línea de lógica.

Poner Postgres en el mismo servidor que la API lleva ese viaje a ~0,2 ms. El
costo real de hacerlo son los respaldos, que pasan a ser tuyos:

```bash
# Diario, con rotación a 14 días.
pg_dump "$DESTINO" --format=custom --file=/respaldos/ems_$(date +\%F).dump
find /respaldos -name 'ems_*.dump' -mtime +14 -delete
```

Un respaldo que nunca se restauró no es un respaldo. Probá el restore en una
base vacía al menos una vez.

---

# Apéndice · Una base vacía, sin datos

Para un entorno de pruebas o una instalación nueva. Hay dos formas y **no dan
lo mismo**; la diferencia importa.

## Opción A · Dejar que Alembic la construya (recomendada)

Es la forma propia del proyecto: las migraciones son la definición del esquema,
así que aplicarlas sobre una base vacía produce exactamente la estructura que el
código espera.

```bash
psql "$DESTINO" -c 'SELECT 1;'          # que responda antes de seguir
# En .env: DATABASE_URL apuntando al destino
uv run alembic upgrade head
```

**No queda del todo vacía, y está bien que así sea.** Tres migraciones insertan
filas que no son datos de nadie sino la configuración que comparte la flota:

```bash
uv run python -c "
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import get_settings
from app.core.database import build_connect_args
async def main():
    s = get_settings()
    m = create_async_engine(s.database_url, connect_args=build_connect_args(s))
    async with m.connect() as c:
        n = await c.scalar(text('SELECT count(*) FROM platform_settings'))
        print(f'platform_settings: {n} filas')
    await m.dispose()
asyncio.run(main())"
```

Tienen que ser 32. Los valores vienen vacíos —el broker, los tokens— y se
cargan desde el panel de Configuración; lo que las migraciones traen son las
claves y sus descripciones.

Después hace falta el primer administrador, porque la API no tiene registro
público:

```bash
uv run python -m app.scripts.create_admin
```

## Opción B · Copiar la estructura del origen

Solo si necesitás una copia exacta de cómo está **hoy** la base de origen,
incluidas las diferencias que haya acumulado por fuera de las migraciones.

```bash
pg_dump "$ORIGEN" --schema-only \
  --no-owner --no-privileges --no-acl \
  --file=ems_estructura.sql

psql "$DESTINO" --file=ems_estructura.sql
```

### El detalle que rompe esta opción

`--schema-only` copia la tabla `alembic_version` **vacía**. Alembic la lee para
saber qué se aplicó, así que en el destino cree que no se aplicó nada y el
siguiente `upgrade head` intenta crear tablas que ya existen:

```
DuplicateTableError: relation "clients" already exists
```

Hay que decirle en qué punto está, sin ejecutar nada:

```bash
uv run alembic stamp head
```

`stamp` solo escribe la fila de versión. Y después queda la otra diferencia:
las migraciones que insertan filas **no corrieron**, así que
`platform_settings` está vacía y el gateway no va a recibir configuración
ninguna. Si no la vas a cargar a mano, la Opción A es el camino.

---

## Cuál elegir

| | Opción A · Alembic | Opción B · `--schema-only` |
|---|---|---|
| Estructura | la que definen las migraciones | la que tiene el origen hoy |
| `platform_settings` | 32 filas, listas para completar | vacía |
| `alembic_version` | correcto solo | hay que hacer `stamp head` |
| Diferencias no migradas | se pierden, que suele ser lo deseable | se copian, incluidas las accidentales |

Para una base nueva, **A**. **B** sirve para reproducir un problema que solo
aparece con la estructura exacta de producción.

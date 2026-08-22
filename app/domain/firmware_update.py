"""Las reglas de una actualización de firmware, sin base de datos de por medio.

Tres cosas viven acá porque son las que, si están mal, rompen equipos en
sedes a las que hay que viajar:

* **Cómo se lee una versión.** El paquete se publica como `v1.2.0` y el
  equipo reporta `1.2.0`. Compararlas como texto haría que el gateway se
  actualizara a la versión que ya tiene, para siempre.
* **Cuándo se puede aplicar.** La hora es local a la sede, así que el cálculo
  pasa por su zona horaria y tiene que sobrevivir a los cambios de hora.
* **Qué estado puede seguir a cuál.** Los acuses llegan desde el campo, por
  una red que duplica y reordena. Sin una tabla de transiciones, un acuse
  viejo puede pisar uno nuevo y dejar como "descargando" a un equipo que ya
  terminó.

Todo son funciones puras: se prueban sin servidor, sin broker y sin equipos.
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.enums import FirmwareUpdateState

# `v1.2.0` o `1.2.0`. Se aceptan las dos formas porque el tag de git lleva la
# `v` y el `pyproject.toml` del firmware no, y ambas nombran lo mismo.
VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

# Un sha256 en hexadecimal, en minúsculas. Se valida la forma acá para que una
# versión con el checksum mal pegado no llegue a publicarse: el equipo lo
# descubriría después de bajar el paquete entero, por 4G, en una sede remota.
SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Cuántas veces se reintenta antes de dejar de insistir. Un equipo que falla
# tres noches seguidas tiene un problema que otro reintento no arregla, y
# seguir intentando lo reinicia todas las noches.
MAX_INTENTOS = 3

# Una ventana más larga que esto ya no es una ventana: es "cuando sea". Se
# corta para que nadie deje sin querer un `1440` que hace que un equipo se
# reinicie a media mañana.
VENTANA_MINUTOS_MAXIMA = 12 * 60


class VersionInvalidaError(ValueError):
    """El texto no nombra una versión que se pueda comparar."""


def normalizar_version(texto: str) -> str:
    """Devuelve la versión sin la `v` y sin espacios: `v1.2.0` → `1.2.0`.

    Es la forma en la que se guarda y se compara. La `v` se conserva solo
    donde arma el nombre del archivo del paquete.
    """
    numeros = parse_version(texto)
    return ".".join(str(numero) for numero in numeros)


def parse_version(texto: str) -> tuple[int, int, int]:
    """Los tres números de una versión."""
    coincidencia = VERSION.match(texto.strip())
    if coincidencia is None:
        raise VersionInvalidaError(
            f"'{texto}' no es una versión con la forma 1.2.3 o v1.2.3"
        )
    mayor, menor, parche = coincidencia.groups()
    return int(mayor), int(menor), int(parche)


def comparar_versiones(una: str, otra: str) -> int:
    """-1, 0 o 1, comparando número a número.

    Número y no texto: `1.10.0` es posterior a `1.9.0`, y alfabéticamente
    sería al revés.
    """
    izquierda, derecha = parse_version(una), parse_version(otra)
    if izquierda < derecha:
        return -1
    return 0 if izquierda == derecha else 1


def misma_version(una: str | None, otra: str | None) -> bool:
    """Si las dos nombran la misma versión, aunque estén escritas distinto.

    Un `None` no es igual a nada, ni siquiera a otro `None`: un equipo que
    todavía no reportó su versión no está al día — no se sabe.
    """
    if una is None or otra is None:
        return False
    try:
        return parse_version(una) == parse_version(otra)
    except VersionInvalidaError:
        # Un equipo viejo puede reportar cualquier cosa como versión. Que no
        # se pueda leer significa "no sé si está al día", que es un no.
        return False


def es_descenso(desde: str | None, hacia: str) -> bool:
    """Si instalar `hacia` sería volver atrás.

    No lo prohíbe: volver atrás es justamente cómo se sale de una versión
    mala. Está para que la decisión se tome a la vista y quede advertida.
    """
    if desde is None:
        return False
    try:
        return comparar_versiones(hacia, desde) < 0
    except VersionInvalidaError:
        return False


def parse_bandera(texto: str) -> bool:
    """Un `true`/`false` escrito por una persona en una fila de un `.env`.

    Cualquier otra cosa —vacío, `tal vez`, un espacio— es **false**. La
    variable que enciende las actualizaciones de toda la flota no puede
    quedar encendida por un valor que nadie entiende.
    """
    return texto.strip().lower() in {"true", "1", "si", "sí", "yes", "on"}


def paquete_url(base: str, version: str) -> str:
    """De dónde se baja el `.tar.gz` de una versión.

    Se arma acá y no en cada llamador porque el enrolamiento y la
    actualización remota bajan **el mismo archivo**: si un día cambia el
    nombre, tiene que cambiar en un solo lugar o la mitad de la flota
    quedaría pidiendo una dirección que ya no existe.
    """
    return f"{base.rstrip('/')}/gatewayEMS-{version}.tar.gz"


def parse_hora(texto: str) -> time:
    """La hora de la ventana, escrita `HH:MM`.

    Se guarda como texto en `platform_settings` —es una fila de un `.env`—
    así que alguien puede teclear `3:00`, `25:00` o dejarla vacía. Se rechaza
    acá, al guardar, y no de madrugada en el equipo.
    """
    limpio = texto.strip()
    if not re.match(r"^\d{2}:\d{2}$", limpio):
        raise ValueError(f"'{texto}' no es una hora con la forma HH:MM")
    horas, minutos = (int(parte) for parte in limpio.split(":"))
    if horas > 23 or minutos > 59:
        raise ValueError(f"'{texto}' no es una hora del día")
    return time(hour=horas, minute=minutos)


def parse_ventana_minutos(texto: str) -> int:
    """Cuántos minutos dura la ventana, con su tope."""
    limpio = texto.strip()
    if not limpio.isdigit():
        raise ValueError(f"'{texto}' no es una cantidad de minutos")
    minutos = int(limpio)
    if minutos < 1:
        raise ValueError("La ventana tiene que durar al menos un minuto")
    if minutos > VENTANA_MINUTOS_MAXIMA:
        raise ValueError(
            f"Una ventana de más de {VENTANA_MINUTOS_MAXIMA} minutos deja al "
            "equipo reiniciándose a cualquier hora"
        )
    return minutos


def zona(timezone: str) -> ZoneInfo:
    """La zona horaria de la sede, o un error que la nombra.

    La sede ya guarda su `timezone`; si tiene un valor que el sistema no
    conoce, la actualización no se programa. Caer en UTC sería aplicarla a una
    hora que nadie eligió.
    """
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"Zona horaria desconocida: '{timezone}'") from error


def _instante(dia: date, hora: time, tz: ZoneInfo) -> datetime:
    """El momento UTC en que en esa zona son ese día y esa hora.

    En el adelanto de reloj hay horas locales que no existen: el 29 de marzo
    en Madrid no hay 02:30. Convertir esa hora local da el instante en que el
    reloj ya saltó —o sea, un poco más tarde—, que es exactamente lo que se
    quiere: la ventana empieza apenas se puede, nunca se saltea un día.

    En el atraso de reloj la hora local ocurre dos veces; se toma la primera
    (`fold=0`), que es la que llega antes.
    """
    local = datetime.combine(dia, hora, tzinfo=tz)
    return local.astimezone(UTC)


def proxima_ventana(
    ahora: datetime, hora: time, ventana_minutos: int, timezone: str
) -> datetime:
    """A partir de cuándo puede aplicarse una actualización pedida ahora.

    Si en la sede ya estamos dentro de la ventana, es ahora mismo: quien
    apretó el botón a las 03:30 con la ventana abierta hasta las 05:00 no
    quiso esperar hasta mañana. Si no, es el próximo comienzo de ventana.

    Se miran tres días locales porque una ventana puede cruzar la medianoche
    (23:30 + 2h): a las 00:30 el equipo sigue dentro de la de ayer.
    """
    tz = zona(timezone)
    duracion = timedelta(minutes=ventana_minutos)
    hoy = ahora.astimezone(tz).date()

    for desplazamiento in (-1, 0, 1):
        inicio = _instante(hoy + timedelta(days=desplazamiento), hora, tz)
        if ahora < inicio:
            return inicio
        if ahora < inicio + duracion:
            # Dentro de una ventana abierta: se aplica ya.
            return ahora

    # Inalcanzable con tres días consecutivos, pero devolver algo válido es
    # mejor que dejar la función sin salida si alguien cambia el rango.
    return _instante(hoy + timedelta(days=2), hora, tz)


def como_utc(momento: datetime) -> datetime:
    """El mismo instante, siempre con zona.

    Las filas que vuelven de SQLite no traen zona horaria —el motor no la
    guarda— y compararlas con un `datetime` con zona explota. Se tratan como
    UTC, que es lo que se escribió, en vez de romper la consulta del equipo.
    """
    if momento.tzinfo is None:
        return momento.replace(tzinfo=UTC)
    return momento


def dentro_de_ventana(
    ahora: datetime, aplicar_desde: datetime, ventana_minutos: int
) -> bool:
    """Si este momento sigue estando dentro de la ventana concedida.

    Lo pregunta el equipo antes de tocar nada. Pasada la ventana no se
    actualiza: se espera a la siguiente. Un gateway que se despierta a las
    09:00 y aplica ahí reinicia una planta en producción.
    """
    desde = como_utc(aplicar_desde)
    if ahora < desde:
        return False
    return ahora < desde + timedelta(minutes=ventana_minutos)


# Qué estado puede seguir a cuál. Todo lo que no esté acá se rechaza.
#
# `aplicando` no se puede cancelar a propósito: para entonces el equipo ya
# está reiniciándose con el paquete nuevo, y decir "cancelada" sería escribir
# en la pantalla algo que no va a pasar.
TRANSICIONES: dict[FirmwareUpdateState, frozenset[FirmwareUpdateState]] = {
    FirmwareUpdateState.SIN_PENDIENTE: frozenset({FirmwareUpdateState.PROGRAMADA}),
    FirmwareUpdateState.PROGRAMADA: frozenset(
        {
            FirmwareUpdateState.DESCARGANDO,
            FirmwareUpdateState.FALLIDA,
            FirmwareUpdateState.SIN_PENDIENTE,
        }
    ),
    FirmwareUpdateState.DESCARGANDO: frozenset(
        {
            FirmwareUpdateState.APLICANDO,
            FirmwareUpdateState.FALLIDA,
            FirmwareUpdateState.SIN_PENDIENTE,
        }
    ),
    FirmwareUpdateState.APLICANDO: frozenset(
        {FirmwareUpdateState.APLICADA, FirmwareUpdateState.FALLIDA}
    ),
    FirmwareUpdateState.APLICADA: frozenset(
        {FirmwareUpdateState.PROGRAMADA, FirmwareUpdateState.SIN_PENDIENTE}
    ),
    FirmwareUpdateState.FALLIDA: frozenset(
        {FirmwareUpdateState.PROGRAMADA, FirmwareUpdateState.SIN_PENDIENTE}
    ),
}


def transicion_valida(actual: FirmwareUpdateState, nuevo: FirmwareUpdateState) -> bool:
    """Si el equipo puede pasar de un estado al otro.

    Repetir el estado actual siempre vale: el acuse se manda por una red que
    se corta, el equipo reintenta, y el segundo acuse dice lo mismo que el
    primero. Tratar eso como error llenaría de fallos falsos una flota que
    está funcionando.
    """
    if actual is nuevo:
        return True
    return nuevo in TRANSICIONES[actual]


def validar_transicion(
    actual: FirmwareUpdateState, nuevo: FirmwareUpdateState
) -> None:
    """Igual que :func:`transicion_valida`, pero explota con el motivo."""
    if not transicion_valida(actual, nuevo):
        raise ValueError(
            f"Una actualización '{actual.value}' no puede pasar a '{nuevo.value}'"
        )


def puede_reintentar(intentos: int) -> bool:
    """Si todavía queda margen para volver a intentarlo esta noche."""
    return intentos < MAX_INTENTOS

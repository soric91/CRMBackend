"""PlatformSetting: la configuración que comparte toda la flota."""

from sqlalchemy import Boolean, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domain.enums import SettingOrigin
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import enum_column


class PlatformSetting(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Un valor del `.env` de los gateways que es igual para todos.

    El host del broker, los tópicos, los intervalos, la URL del CRM. Hoy se
    escriben a mano en cada equipo, así que cambiar el broker significa
    visitar cada sede — y un valor tecleado mal no falla al arrancar, falla
    más tarde y en silencio.

    Guardarlos acá los vuelve una fila que se edita una vez. Lo que **no**
    entra son los valores propios de cada equipo: su uuid, su credencial, los
    secretos de su InfluxDB local. Esos son de un gateway y viven con él.
    """

    __tablename__ = "platform_settings"

    # El nombre tal como aparece en el `.env`: `MQTT_HOST`, no `mqtt_host`.
    # Se guarda en su forma final para que no haya una traducción entre lo que
    # alguien ve en el panel y lo que termina en el archivo.
    clave: Mapped[str] = mapped_column(String(100), unique=True, index=True)

    # Cifrado cuando `es_secreto`, en claro cuando no. Cifrar `MQTT_PORT=8883`
    # no protege nada y vuelve ilegible una base que a veces hay que mirar.
    valor: Mapped[str] = mapped_column(Text, default="")

    # Decide dos cosas: si se cifra al guardar, y si el panel lo muestra
    # tapado. Es una propiedad del valor, no de quién lo mira.
    es_secreto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )

    # De dónde sale el valor. Las que no son `plataforma` existen acá solo
    # para que su nombre viaje en la configuración: el valor lo pone el equipo
    # o la ficha del gateway, y por eso no se editan desde el panel.
    origen: Mapped[SettingOrigin] = mapped_column(
        enum_column(SettingOrigin, "setting_origin"),
        nullable=False,
        default=SettingOrigin.PLATAFORMA,
        server_default=SettingOrigin.PLATAFORMA.value,
    )

    # Para qué sirve, en una línea. Sin esto, una clave que alguien agregó
    # hace seis meses es un nombre en mayúsculas que nadie se anima a tocar.
    descripcion: Mapped[str] = mapped_column(String(300), default="")

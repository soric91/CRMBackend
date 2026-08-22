"""FirmwareRelease: una versión del software del gateway, publicada."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domain.enums import FirmwareChannel
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.models.types import enum_column


class FirmwareRelease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una versión que los equipos pueden instalar.

    Publicar no es desplegar. Esta tabla es el catálogo de lo que existe; a
    qué gateway se le pide cada versión lo dice la ficha del gateway. Separar
    las dos cosas es lo que permite subir una versión, probarla en un equipo y
    recién después llevarla a la flota — y volver atrás sin republicar nada.

    El checksum vive acá y no solo junto al `.tar.gz`: son dos servidores
    distintos, así que comprometer el de paquetes no alcanza para que un
    equipo instale algo alterado. Compara contra un valor que ese servidor no
    controla.

    Dónde vive el archivo no se guarda: sale de `GATEWAY_RELEASE_BASE_URL` más
    la versión, igual que en el enrolamiento. Un segundo lugar diciendo de
    dónde se baja el software sería un segundo lugar que puede quedar viejo.
    """

    __tablename__ = "firmware_releases"
    __table_args__ = (
        # 64 caracteres es lo que mide un sha256 en hexadecimal. Que además
        # sean hexadecimales lo valida el esquema de entrada: la expresión
        # regular no es igual en PostgreSQL y en SQLite, y una restricción que
        # solo corre en producción no protege a las pruebas.
        CheckConstraint("length(sha256) = 64", name="sha256_length"),
        CheckConstraint(
            "tamano_bytes IS NULL OR tamano_bytes > 0", name="tamano_positive"
        ),
    )

    # `v1.2.0`, tal como se publicó el tag. La comparación se hace por número
    # (ver `app.domain.firmware_update`), no por este texto.
    version: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)

    canal: Mapped[FirmwareChannel] = mapped_column(
        enum_column(FirmwareChannel, "firmware_channel"),
        nullable=False,
        default=FirmwareChannel.BETA,
        server_default=FirmwareChannel.BETA.value,
    )

    # Lo que el equipo verifica antes de descomprimir nada.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # Para que el panel pueda avisar antes de mandar una descarga de 80 MB a
    # una sede que sale por un módem 4G con plan medido.
    tamano_bytes: Mapped[int | None] = mapped_column(Integer, default=None)

    # Qué cambia. Se lee en la pantalla de actualización, justo antes de
    # decidir; sin esto, elegir versión es elegir un número.
    notas: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Cuándo se dejó de ofrecer. Una versión mala se retira, no se borra: los
    # equipos que la instalaron siguen apuntando acá, y su historia es lo
    # único que explica por qué una sede quedó como quedó.
    retirado_en: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    # Quién la publicó. Sin clave foránea, por lo mismo que en
    # `enrollment_tokens.emitido_por`: es auditoría, y tiene que sobrevivir a
    # que esa cuenta se dé de baja.
    publicado_por: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)

    @property
    def disponible(self) -> bool:
        """Si todavía se puede pedir esta versión."""
        return self.retirado_en is None

    def __repr__(self) -> str:
        estado = "disponible" if self.disponible else "retirada"
        return f"<FirmwareRelease {self.version!r} ({self.canal}, {estado})>"

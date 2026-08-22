"""Lo que la base tiene que impedir por su cuenta.

Un servicio se puede saltear con un `INSERT` a mano, con una migración de
datos o con el próximo endpoint que alguien escriba apurado. Estas reglas
viven en el esquema porque el costo de que fallen es un equipo bajando un
paquete que no se puede verificar, o una versión borrada de abajo de los
gateways que están yendo hacia ella.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import FirmwareChannel, FirmwareUpdateState
from app.models import FirmwareRelease, Gateway, Site
from tests.factories import (
    make_client,
    make_firmware_release,
    make_gateway,
    make_site,
)


async def _un_gateway(session: AsyncSession) -> Gateway:
    client = make_client()
    session.add(client)
    await session.flush()
    site = make_site(client)
    session.add(site)
    await session.flush()
    gateway = make_gateway(site)
    session.add(gateway)
    await session.flush()
    return gateway


async def _una_release(session: AsyncSession, **overrides: object) -> FirmwareRelease:
    release = make_firmware_release(**overrides)
    session.add(release)
    await session.flush()
    return release


class TestElCatalogoDeVersiones:
    async def test_una_version_publicada_arranca_disponible(
        self, db_session: AsyncSession
    ) -> None:
        release = await _una_release(db_session)
        assert release.disponible
        assert release.notas == ""

    async def test_el_canal_por_defecto_es_beta(
        self, db_session: AsyncSession
    ) -> None:
        """Publicar no es desplegar: una versión recién subida no puede
        quedar ofrecida a la flota entera por omisión."""
        release = make_firmware_release()
        del release.canal
        db_session.add(release)
        await db_session.flush()
        await db_session.refresh(release)
        assert release.canal is FirmwareChannel.BETA

    async def test_la_misma_version_no_se_publica_dos_veces(
        self, db_session: AsyncSession
    ) -> None:
        """Dos filas con `v1.3.0` serían dos checksums distintos para el mismo
        nombre, y ninguna forma de saber cuál baja el equipo."""
        await _una_release(db_session, version="v1.3.0")
        with pytest.raises(IntegrityError, match="version"):
            await _una_release(db_session, version="v1.3.0")

    @pytest.mark.parametrize("sha256", ["a" * 63, "a" * 65, ""])
    async def test_un_checksum_que_no_mide_un_sha256_no_entra(
        self, db_session: AsyncSession, sha256: str
    ) -> None:
        with pytest.raises(IntegrityError, match="sha256_length"):
            await _una_release(db_session, sha256=sha256)

    @pytest.mark.parametrize("tamano", [0, -1])
    async def test_un_tamano_imposible_no_entra(
        self, db_session: AsyncSession, tamano: int
    ) -> None:
        with pytest.raises(IntegrityError, match="tamano_positive"):
            await _una_release(db_session, tamano_bytes=tamano)

    async def test_el_tamano_puede_no_saberse(
        self, db_session: AsyncSession
    ) -> None:
        release = await _una_release(db_session, tamano_bytes=None)
        assert release.tamano_bytes is None

    async def test_un_canal_inventado_no_entra(
        self, db_session: AsyncSession
    ) -> None:
        with pytest.raises(IntegrityError, match="firmware_channel"):
            await db_session.execute(
                text(
                    "INSERT INTO firmware_releases "
                    "(id, version, canal, sha256, notas, created_at, updated_at) "
                    "VALUES (:id, 'v9.9.9', 'produccion', :sha, '', :ahora, :ahora)"
                ),
                {
                    "id": uuid.uuid4().bytes,
                    "sha": "b" * 64,
                    "ahora": datetime.now(UTC).isoformat(),
                },
            )

    async def test_retirar_una_version_no_la_borra(
        self, db_session: AsyncSession
    ) -> None:
        """Los equipos que la instalaron siguen apuntando a esta fila: su
        historia es lo único que explica por qué una sede quedó como quedó."""
        release = await _una_release(db_session)
        release.retirado_en = datetime.now(UTC)
        await db_session.flush()
        assert not release.disponible

    async def test_quien_publico_sobrevive_a_que_se_borre_su_cuenta(
        self, db_session: AsyncSession
    ) -> None:
        """`publicado_por` no es clave foránea a propósito: es auditoría, y
        tiene que seguir ahí cuando esa cuenta se dé de baja."""
        claves = {
            fk.column.table.name
            for fk in FirmwareRelease.__table__.foreign_keys
        }
        assert "users" not in claves


class TestLaActualizacionQueUnGatewayTienePedida:
    async def test_un_gateway_nuevo_no_tiene_nada_pendiente(
        self, db_session: AsyncSession
    ) -> None:
        gateway = await _un_gateway(db_session)
        await db_session.refresh(gateway)
        assert gateway.firmware_estado is FirmwareUpdateState.SIN_PENDIENTE
        assert gateway.firmware_objetivo_id is None
        assert gateway.firmware_intentos == 0
        assert gateway.firmware_aplicar_desde is None
        assert gateway.firmware_error is None

    async def test_programar_una_actualizacion_guarda_a_donde_va(
        self, db_session: AsyncSession
    ) -> None:
        gateway = await _un_gateway(db_session)
        release = await _una_release(db_session)

        gateway.firmware_objetivo_id = release.id
        gateway.firmware_estado = FirmwareUpdateState.PROGRAMADA
        gateway.firmware_aplicar_desde = datetime.now(UTC)
        await db_session.flush()

        assert gateway.firmware_objetivo_id == release.id

    async def test_un_estado_en_curso_sin_objetivo_no_entra(
        self, db_session: AsyncSession
    ) -> None:
        """Una actualización en marcha sin versión a la que ir es un equipo
        descargando nada."""
        gateway = await _un_gateway(db_session)
        gateway.firmware_estado = FirmwareUpdateState.DESCARGANDO
        with pytest.raises(IntegrityError, match="firmware_target_present"):
            await db_session.flush()

    async def test_los_intentos_no_pueden_ser_negativos(
        self, db_session: AsyncSession
    ) -> None:
        gateway = await _un_gateway(db_session)
        gateway.firmware_intentos = -1
        with pytest.raises(IntegrityError, match="firmware_intentos_no_negative"):
            await db_session.flush()

    async def test_no_se_borra_una_version_a_la_que_un_equipo_va_en_camino(
        self, db_session: AsyncSession
    ) -> None:
        """`RESTRICT`: borrarla dejaría al gateway bajando un paquete del que
        ya nadie sabe el checksum."""
        gateway = await _un_gateway(db_session)
        release = await _una_release(db_session)
        gateway.firmware_objetivo_id = release.id
        gateway.firmware_estado = FirmwareUpdateState.PROGRAMADA
        await db_session.flush()

        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            await db_session.execute(
                delete(FirmwareRelease).where(FirmwareRelease.id == release.id)
            )
            await db_session.flush()

    async def test_dar_de_baja_una_sede_sigue_borrando_sus_gateways(
        self, db_session: AsyncSession
    ) -> None:
        """La versión apuntada no puede convertirse en un ancla que impida
        dar de baja una instalación."""
        gateway = await _un_gateway(db_session)
        release = await _una_release(db_session)
        gateway.firmware_objetivo_id = release.id
        gateway.firmware_estado = FirmwareUpdateState.PROGRAMADA
        await db_session.flush()
        site_id = gateway.site_id

        await db_session.execute(delete(Site).where(Site.id == site_id))
        await db_session.flush()

        restantes = await db_session.execute(
            text("SELECT count(*) FROM gateways WHERE site_id = :site"),
            {"site": str(site_id)},
        )
        assert restantes.scalar_one() == 0

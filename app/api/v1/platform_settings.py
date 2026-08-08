"""La configuración que comparte toda la flota de gateways.

Los valores del `.env` que son iguales en todos los equipos: el host del
broker, los tópicos, los intervalos, la URL de este CRM. Hoy se escriben a
mano en cada instalación, así que cambiar el broker significa visitar cada
sede — y un valor tecleado mal no falla al arrancar, falla más tarde.

Lo que **no** entra acá es lo propio de cada equipo: su uuid, su credencial,
los secretos de su InfluxDB local. Esos viven con el gateway.
"""

from typing import Annotated

from fastapi import APIRouter, Path, status

from app.api.deps import PlatformSettingServiceDep, ScopeDep
from app.schemas.platform_setting import (
    PlatformSettingCreate,
    PlatformSettingRead,
    PlatformSettingRevealed,
    PlatformSettingUpdate,
)

router = APIRouter(prefix="/platform-settings", tags=["platform-settings"])

ClaveParam = Annotated[str, Path(max_length=100)]


@router.get("", response_model=list[PlatformSettingRead])
async def list_platform_settings(
    scope: ScopeDep, service: PlatformSettingServiceDep
) -> list[PlatformSettingRead]:
    """Todas las variables. Los valores secretos vienen en `null`.

    Sin paginar: son las variables de un archivo `.env`, del orden de treinta.
    """
    return await service.list_all(scope)


@router.get("/{clave}/reveal", response_model=PlatformSettingRevealed)
async def reveal_platform_setting(
    clave: ClaveParam, scope: ScopeDep, service: PlatformSettingServiceDep
) -> PlatformSettingRevealed:
    """El valor en claro de una variable.

    Es una petición aparte y no un campo del listado a propósito: así ver un
    secreto es un acto deliberado, y queda registrado quién lo hizo.
    """
    return PlatformSettingRevealed(
        clave=clave, valor=await service.reveal(scope, clave)
    )


@router.post(
    "", response_model=PlatformSettingRead, status_code=status.HTTP_201_CREATED
)
async def create_platform_setting(
    payload: PlatformSettingCreate,
    scope: ScopeDep,
    service: PlatformSettingServiceDep,
) -> PlatformSettingRead:
    """Agregar una variable que todavía no existe."""
    return await service.create(scope, payload)


@router.patch("/{clave}", response_model=PlatformSettingRead)
async def update_platform_setting(
    clave: ClaveParam,
    payload: PlatformSettingUpdate,
    scope: ScopeDep,
    service: PlatformSettingServiceDep,
) -> PlatformSettingRead:
    """Cambiar el valor, la descripción o si es secreto.

    El nombre no se puede cambiar: los gateways leen por nombre, y renombrar
    en un paso silencioso dejaría equipos buscando una variable que dejó de
    existir. Para eso, borrar y crear.
    """
    return await service.update(scope, clave, payload)


@router.delete("/{clave}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_platform_setting(
    clave: ClaveParam, scope: ScopeDep, service: PlatformSettingServiceDep
) -> None:
    await service.delete(scope, clave)

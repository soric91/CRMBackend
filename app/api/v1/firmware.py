"""El catálogo de versiones del firmware y los despliegues.

Publicar una versión y pedírsela a los equipos son dos rutas distintas a
propósito: se publica, se prueba en un equipo, y recién entonces se despliega.
Ninguna de las dos reinicia nada por su cuenta — dejan escrito qué tiene que
instalar cada gateway, y el gateway lo busca solo.
"""

import uuid

from fastapi import APIRouter, status

from app.api.deps import FirmwareAdminServiceDep, ScopeDep
from app.schemas.firmware import (
    FirmwareReleaseCreate,
    FirmwareReleaseRead,
    RolloutCreate,
    RolloutResult,
)

router = APIRouter(prefix="/firmware", tags=["firmware"])


@router.get("/releases", response_model=list[FirmwareReleaseRead])
async def list_firmware_releases(
    scope: ScopeDep, service: FirmwareAdminServiceDep
) -> list[FirmwareReleaseRead]:
    """Las versiones publicadas, la más nueva primero.

    Cada una dice cuántos equipos la tienen pedida ahora mismo: retirar una a
    la que van tres los deja sin nada que instalar, y eso hay que verlo antes.
    """
    return await service.list_releases(scope)


@router.post(
    "/releases",
    response_model=FirmwareReleaseRead,
    status_code=status.HTTP_201_CREATED,
)
async def publish_firmware_release(
    payload: FirmwareReleaseCreate,
    scope: ScopeDep,
    service: FirmwareAdminServiceDep,
) -> FirmwareReleaseRead:
    """Agregar una versión al catálogo.

    Publicar no es desplegar: ningún equipo la instala por esto. El checksum
    se valida acá porque un valor mal pegado se descubriría recién después de
    bajar el paquete entero, por 4G, en una sede remota.
    """
    return await service.publish(scope, payload)


@router.post("/releases/{release_id}/retire", response_model=FirmwareReleaseRead)
async def retire_firmware_release(
    release_id: uuid.UUID, scope: ScopeDep, service: FirmwareAdminServiceDep
) -> FirmwareReleaseRead:
    """Dejar de ofrecer una versión, sin borrarla.

    Los equipos que iban hacia ella dejan de recibirla en su próxima
    consulta. La fila queda: es la única explicación de por qué una sede
    quedó corriendo lo que corre.
    """
    return await service.retire(scope, release_id)


@router.post(
    "/rollouts", response_model=RolloutResult, status_code=status.HTTP_201_CREATED
)
async def schedule_firmware_rollout(
    payload: RolloutCreate, scope: ScopeDep, service: FirmwareAdminServiceDep
) -> RolloutResult:
    """Pedirle una versión a un equipo, a una sede o a una empresa entera.

    La hora sale de la configuración de la plataforma y se calcula en la zona
    horaria de cada sede: las 03:00 son las 03:00 de cada planta.

    La respuesta dice a quién **no** se le pidió y por qué —ya la tiene, no
    tiene credencial, está reiniciándose— en vez de contestar "listo" y
    dejar que se descubra después.
    """
    return await service.rollout(scope, payload)

"""El catálogo de mediciones, para que el panel arme su desplegable.

Existe para que la lista viva en un solo lado. Duplicarla en TypeScript sería
garantizar que en algún momento las dos versiones difieran, y el síntoma
—una variable que el panel ofrece y la API rechaza— aparecería recién al
guardar.
"""

from fastapi import APIRouter

from app.api.deps import ScopeDep
from app.domain.measurements import CATALOGO
from app.schemas.variable import MedicionRead

router = APIRouter(prefix="/variable-catalog", tags=["variable-catalog"])


@router.get("", response_model=list[MedicionRead])
async def list_variable_catalog(_scope: ScopeDep) -> list[MedicionRead]:
    """Las mediciones que un equipo puede reportar.

    Fija: no depende del cliente ni del equipo. Requiere sesión igual que
    todo lo demás, pero no filtra por ella — es vocabulario, no datos.

    El orden es el del catálogo, agrupado por magnitud, para que el
    desplegable lo respete sin tener que reordenar.
    """
    return [MedicionRead.model_validate(medicion) for medicion in CATALOGO]

"""Request and response models for enrolling a gateway."""

from datetime import datetime

from pydantic import BaseModel

from app.domain.enums import SettingOrigin


class EnrollmentTokenIssued(BaseModel):
    """El permiso recién emitido. Se ve una sola vez."""

    token: str
    expira_en: datetime
    # El comando completo, listo para copiar. Va armado desde el servidor y no
    # desde el panel porque la URL del instalador vive en `platform_settings`:
    # el día que cambie el dominio, el comando cambia solo. Y pegarlo entero
    # evita que alguien lo rearme a mano en un chat con un espacio de más.
    comando: str


class EnvEntry(BaseModel):
    """Una línea del `.env` del equipo."""

    clave: str
    # Vacío cuando `origen` es `equipo`: el valor lo genera el propio gateway.
    # Mandarle uno haría que escribiera un secreto que otro conoce.
    valor: str
    # Es la instrucción para el instalador: escribí este valor, o generá el
    # tuyo. Sin esto, el firmware necesitaría su propia lista de qué variables
    # llena él — y esa lista se desactualiza sola.
    origen: SettingOrigin


class ReleaseRef(BaseModel):
    """Qué software instalar, y con qué verificarlo."""

    version: str
    url: str
    # Viene del CRM y no solo junto al paquete a propósito: son dos servidores
    # distintos, así que comprometer el de releases no alcanza para que un
    # equipo instale algo alterado.
    sha256: str


class EnrollmentResponse(BaseModel):
    """Todo lo que el instalador necesita, en una sola respuesta."""

    gateway_uuid: str
    env: list[EnvEntry]
    release: ReleaseRef

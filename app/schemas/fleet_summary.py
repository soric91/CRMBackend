"""Cuánto tiene instalado cada cliente, sin traer el inventario.

Existe para las tarjetas de la pantalla de proyectos. Lo alternativo era pedir
`/fleet?nivel=variables` para todos los clientes y contar en el navegador, que
transfiere el árbol completo de cada empresa —sedes, gateways, equipos y cada
registro Modbus— para dibujar "3 gateways". Funciona con tres clientes y deja
de funcionar bastante antes de lo que uno cree.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.domain.enums import ClientStatus


class ClientSummary(BaseModel):
    """Un cliente y sus conteos."""

    id: UUID
    nombre_empresa: str
    estado: ClientStatus
    puede_ver_consumo: bool

    sedes: int
    gateways: int
    # Cuántos de esos gateways están reportando ahora. La diferencia contra
    # `gateways` es lo que hay que ir a arreglar.
    gateways_en_linea: int
    equipos: int
    # Registros Modbus cargados. Un equipo sin variables está dado de alta
    # pero no mide nada, que es una forma silenciosa de estar roto.
    variables: int

    # La conexión más reciente de cualquiera de sus gateways. `None` si ninguno
    # reportó nunca. Es la señal más barata de "dejó de llegar información":
    # vive en el CRM y no obliga a consultar la base de series temporales.
    ultima_conexion: datetime | None


class GatewayCaido(BaseModel):
    """Un gateway que dejó de reportar, y dónde está.

    Lleva los nombres de la sede y de la empresa porque la pregunta que
    contesta esta vista es "a quién llamo". Con solo el número de serie hay
    que resolver a mano de quién es cada uno, que es el trabajo que la vista
    existe para evitar.
    """

    id: UUID
    numero_serie: str
    # Ojo: este campo se llama igual que el módulo `uuid`. Por eso el tipo se
    # importa como `UUID` y no se anota `uuid.UUID` — a partir de esta línea
    # `uuid` es el campo, y cualquier anotación posterior fallaría.
    uuid: UUID
    # `None` si nunca se conectó. No es lo mismo que "hace mucho": la
    # instalación puede no haber arrancado todavía.
    ultima_conexion: datetime | None

    site_id: UUID
    sitio: str
    client_id: UUID
    empresa: str

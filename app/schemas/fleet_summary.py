"""Cuánto tiene instalado cada cliente, sin traer el inventario.

Existe para las tarjetas de la pantalla de proyectos. Lo alternativo era pedir
`/fleet?nivel=variables` para todos los clientes y contar en el navegador, que
transfiere el árbol completo de cada empresa —sedes, gateways, equipos y cada
registro Modbus— para dibujar "3 gateways". Funciona con tres clientes y deja
de funcionar bastante antes de lo que uno cree.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.domain.enums import ClientStatus


class ClientSummary(BaseModel):
    """Un cliente y sus conteos."""

    id: uuid.UUID
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

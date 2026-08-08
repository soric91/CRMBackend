"""La configuración compartida de los gateways."""

from app.core.exceptions import (
    AlreadyExistsError,
    AuthorizationError,
    BusinessRuleError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.core.secret_box import SecretBox
from app.domain.access import AccessScope
from app.domain.enums import SettingOrigin
from app.models import PlatformSetting
from app.repositories.platform_setting import PlatformSettingRepository
from app.schemas.platform_setting import (
    PlatformSettingCreate,
    PlatformSettingRead,
    PlatformSettingUpdate,
)

logger = get_logger(__name__)


def _require_editable(setting: PlatformSetting) -> None:
    """Rechaza tocar una variable cuyo valor no vive acá.

    `GATEWAY_UUID` sale de la ficha del gateway y el token de InfluxDB local
    lo genera el propio equipo. Dejarlas editar produciría un valor que se ve
    guardado y que nadie lee — la peor clase de configuración, la que parece
    aplicada.
    """
    if setting.origen is SettingOrigin.PLATAFORMA:
        return
    quien = (
        "el equipo"
        if setting.origen is SettingOrigin.EQUIPO
        else "la ficha del gateway"
    )
    raise BusinessRuleError(
        f"'{setting.clave}' no se edita desde el panel: su valor lo pone {quien}"
    )


def _require_admin(scope: AccessScope) -> None:
    """Mismo permiso que las credenciales de servicio, y por la misma razón.

    Estos valores llegan al `.env` de cada gateway de la flota. Quien pueda
    editarlos puede apuntar todos los equipos a otro broker.
    """
    if not scope.can_manage_services:
        raise AuthorizationError(
            f"Role '{scope.principal}' cannot manage platform settings"
        )


class PlatformSettingService:
    """Lee y escribe los valores que comparte toda la flota.

    El cifrado vive acá y no en el modelo: así la fila que se guarda es el
    texto cifrado y no hay forma de que un `select` olvidado devuelva un
    secreto en claro.
    """

    def __init__(self, repo: PlatformSettingRepository, box: SecretBox) -> None:
        self._repo = repo
        self._box = box

    def _as_read(self, setting: PlatformSetting) -> PlatformSettingRead:
        return PlatformSettingRead(
            id=setting.id,
            clave=setting.clave,
            # Un secreto nunca sale en un listado. Verlo es otra petición.
            valor=None if setting.es_secreto else setting.valor,
            es_secreto=setting.es_secreto,
            origen=setting.origen,
            tiene_valor=bool(setting.valor),
            descripcion=setting.descripcion,
            updated_at=setting.updated_at,
        )

    async def list_all(self, scope: AccessScope) -> list[PlatformSettingRead]:
        """Todas las claves, con los secretos tapados."""
        _require_admin(scope)
        settings = await self._repo.list_ordered()
        return [self._as_read(item) for item in settings]

    async def reveal(self, scope: AccessScope, clave: str) -> str:
        """El valor en claro de una clave.

        Queda registrado quién lo pidió. Un secreto que se puede leer sin
        dejar rastro es un secreto del que nadie puede decir quién lo tiene.
        """
        _require_admin(scope)
        setting = await self._require(clave)
        logger.warning(
            "platform setting revealed",
            extra={"clave": clave, "principal": scope.principal},
        )
        if not setting.es_secreto:
            return setting.valor
        return self._box.decrypt(setting.valor) if setting.valor else ""

    async def valor_publico(self, clave: str) -> str:
        """El valor de una variable no secreta, para uso interno del servidor.

        Sin `AccessScope` a propósito: lo llaman otros servicios que ya
        verificaron su propio permiso, no una petición HTTP. Por eso **se
        niega a devolver un secreto** — si algún día hace falta uno, que sea
        un método aparte y visible, no este ensanchado en silencio.
        """
        setting = await self._require(clave)
        if setting.es_secreto:
            raise BusinessRuleError(
                f"'{clave}' es secreta y no se lee por esta vía"
            )
        return setting.valor

    async def para_un_gateway(self) -> list[tuple[str, str, SettingOrigin]]:
        """Toda la configuración, con los secretos descifrados.

        **Devuelve secretos en claro.** Sin `AccessScope` porque quien llama no
        es una persona: es el enrolamiento, que ya validó un token de un solo
        uso. Está separado de :meth:`valor_publico` a propósito — un método
        que devuelve secretos tiene que ser visible en el código que lo usa,
        no un parámetro opcional del que ya existía.

        Las de origen `equipo` salen con el valor vacío aunque tuvieran algo
        guardado: las genera el propio equipo, y mandarle un valor haría que
        escribiera un secreto que otro conoce.
        """
        return [
            (
                setting.clave,
                self._valor_en_claro(setting),
                setting.origen,
            )
            for setting in await self._repo.list_ordered()
        ]

    def _valor_en_claro(self, setting: PlatformSetting) -> str:
        if setting.origen is SettingOrigin.EQUIPO:
            return ""
        if setting.es_secreto and setting.valor:
            return self._box.decrypt(setting.valor)
        return setting.valor

    async def create(
        self, scope: AccessScope, payload: PlatformSettingCreate
    ) -> PlatformSettingRead:
        _require_admin(scope)
        if await self._repo.get_by_clave(payload.clave) is not None:
            raise AlreadyExistsError(f"La variable '{payload.clave}' ya existe")

        setting = PlatformSetting(
            clave=payload.clave,
            valor=self._store(payload.valor, payload.es_secreto),
            es_secreto=payload.es_secreto,
            descripcion=payload.descripcion,
        )
        return self._as_read(await self._repo.add(setting))

    async def update(
        self, scope: AccessScope, clave: str, payload: PlatformSettingUpdate
    ) -> PlatformSettingRead:
        _require_admin(scope)
        setting = await self._require(clave)
        _require_editable(setting)

        # Si cambia el carácter de secreto hay que reescribir el valor con el
        # tratamiento nuevo, aunque el valor no se haya tocado: marcar como
        # secreto una fila que quedó en claro no la protege de nada.
        es_secreto = (
            payload.es_secreto if payload.es_secreto is not None else setting.es_secreto
        )
        cambios: dict[str, object] = {}

        if payload.valor is not None:
            cambios["valor"] = self._store(payload.valor, es_secreto)
        elif es_secreto != setting.es_secreto and setting.valor:
            actual = (
                self._box.decrypt(setting.valor)
                if setting.es_secreto
                else setting.valor
            )
            cambios["valor"] = self._store(actual, es_secreto)

        if payload.es_secreto is not None:
            cambios["es_secreto"] = es_secreto
        if payload.descripcion is not None:
            cambios["descripcion"] = payload.descripcion

        return self._as_read(await self._repo.update(setting, cambios))

    async def delete(self, scope: AccessScope, clave: str) -> None:
        _require_admin(scope)
        setting = await self._require(clave)
        _require_editable(setting)
        await self._repo.delete(setting)

    def _store(self, valor: str, es_secreto: bool) -> str:
        """El texto tal como va a la base."""
        if not es_secreto or not valor:
            return valor
        return self._box.encrypt(valor)

    async def _require(self, clave: str) -> PlatformSetting:
        setting = await self._repo.get_by_clave(clave)
        if setting is None:
            raise NotFoundError(f"No existe la variable '{clave}'")
        return setting

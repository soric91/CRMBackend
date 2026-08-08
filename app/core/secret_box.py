"""Cifrado de los valores que sí hay que poder leer de vuelta.

Todo lo demás que este sistema guarda como secreto —la credencial de un
gateway, el secreto de una cuenta de servicio— se guarda **hasheado**: se
muestra una vez al emitirlo y nunca más, y un volcado de la base no alcanza
para suplantar a nadie.

La configuración de plataforma no puede funcionar así. La contraseña de MQTT
y el token de InfluxDB tienen que poder mostrarse y servirse a un gateway, y
un hash no se puede deshacer. Entonces son recuperables, y eso es una postura
distinta que conviene tener a la vista.

Lo que se hace al respecto: cifrarlos con una clave que vive en el entorno y
no en la base. Un volcado de la base, solo, no sirve — hace falta también la
variable de entorno del proceso. No protege contra alguien que ya entró al
servidor, y no pretende hacerlo.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings
from app.core.exceptions import BusinessRuleError


class SecretBox:
    """Cifra y descifra valores de configuración."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def configured(self) -> bool:
        return self._settings.settings_encryption_key is not None

    def _fernet(self) -> Fernet:
        key = self._settings.settings_encryption_key
        if key is None:
            # Falla ruidosa y a propósito. La alternativa —guardar en claro
            # cuando falta la clave— deja secretos legibles en la base sin
            # que nadie se entere, que es peor que no poder guardarlos.
            raise BusinessRuleError(
                "No se puede guardar un valor secreto: falta "
                "SETTINGS_ENCRYPTION_KEY en el entorno del servidor"
            )
        return Fernet(key.get_secret_value().encode())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet().encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """El valor original.

        Un texto que no descifra no se trata como vacío: significa que la
        clave del entorno cambió, o que la fila se escribió con otra. Devolver
        cadena vacía dejaría que un gateway se configure con una contraseña en
        blanco y falle mucho más lejos de la causa.
        """
        try:
            return self._fernet().decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise BusinessRuleError(
                "El valor guardado no se puede descifrar con la clave actual. "
                "Probablemente SETTINGS_ENCRYPTION_KEY cambió: hay que volver "
                "a cargar los valores secretos."
            ) from exc


def generate_key() -> str:
    """Una clave nueva, para poner en el entorno.

    Se expone para documentarla: la forma de obtener una es
    `python -c "from app.core.secret_box import generate_key; print(generate_key())"`.
    """
    return Fernet.generate_key().decode()

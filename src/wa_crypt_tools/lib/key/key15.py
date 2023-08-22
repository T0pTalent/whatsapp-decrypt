import hmac
from hashlib import sha256
from os import urandom
from pathlib import Path

from javaobj import JavaObjectMarshaller

from wa_crypt_tools.lib.utils import create_jba

from wa_crypt_tools.lib.key.key import Key
import logging
l = logging.getLogger(__name__)
class Key15(Key):
    # This constant is only used with crypt15 keys.
    BACKUP_ENCRYPTION = b'backup encryption\x01'

    def __init__(self, keyarray: bytes=None, key: bytes=None):
        """Extracts the key from a loaded crypt15 key file."""
        # encrypted_backup.key file format and encoding explanation:
        # The E2E key file is actually a serialized byte[] object.

        # After deserialization, we will have the root key (32 bytes).
        # The root key is further encoded with three different strings, depending on what you want to do.
        # These three ways are "backup encryption";
        # "metadata encryption" and "metadata authentication", for Google Drive E2E encrypted metadata.
        # We are only interested in the local backup encryption.

        # Why the \x01 at the end of the BACKUP_ENCRYPTION constant?
        # Whatsapp uses a nested encryption function to encrypt many times the same data.
        # The iteration counter is appended to the end of the encrypted data. However,
        # since the loop is actually executed only one time, we will only have one interaction,
        # and thus a \x01 at the end.
        # Take a look at utils/wa_hmacsha256_loop.java that is the original code.

        if keyarray is None:
            # Randomly generated key or with supplied parameters
            if key is None:
                self.__key = urandom(32)
            else:
                if len(key) != 32:
                    l.error("Invalid key length: {}".format(key.hex()))
                self.__key = key
            return

        if len(keyarray) != 32:
            l.critical("Crypt15 loader trying to load a crypt14 key")
        l.debug("Root key: {}".format(keyarray.hex()))
        # Save the root key in the class
        self.__key = keyarray

        l.info("Crypt15 / Raw key loaded")

    def get(self) -> bytes:
        """
        Returns the key used for encryption, that is not the root key.
        """
        # First do the HMACSHA256 hash of the file with an empty private key
        key: bytes = hmac.new(b'\x00' * 32, self.__key, sha256).digest()
        # Then do the HMACSHA256 using the previous result as key and ("backup encryption" + iteration count) as data
        key = hmac.new(key, self.BACKUP_ENCRYPTION, sha256).digest()
        return key

    def dump(self) -> bytes:
        """Dumps the key"""
        return JavaObjectMarshaller().dump(create_jba(self.__key))

    def file_dump(self, file: Path):
        with open(file, 'wb') as f:
            f.write(self.dump())

    def __str__(self) -> str:
        """Returns a string representation of the key"""
        try:
            string: str = "Key15("
            if self.__key is not None:
                string += "key: {}".format(self.__key.hex())
            return string + ")"
        except Exception as e:
            return "Exception printing key: {}".format(e)

    def __repr__(self) -> str:
        # TODO
        return self.__str__()

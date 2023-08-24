import logging
from hashlib import md5
from os import urandom
from re import findall

from Cryptodome.Cipher import AES
from google.protobuf.message import DecodeError

l = logging.getLogger(__name__)

from wa_crypt_tools.lib.db.db import Database
from wa_crypt_tools.lib.key.key15 import Key15

class Database15(Database):
    def __str__(self):
        return "Database15"
        # todo

    def __init__(self, *, key: Key15 = None, encrypted=None, iv: bytes = None):
        self.file_hash = md5()
        if encrypted:
            try:
                from wa_crypt_tools.proto import prefix_pb2 as prefix
                from wa_crypt_tools.proto import key_type_pb2 as key_type
            except ImportError as e:
                l.error("Could not import the proto classes: {}".format(e))
                if str(e).startswith("cannot import name 'builder' from 'google.protobuf.internal'"):
                    l.error("You need to upgrade the protobuf library to at least 3.20.0.\n"
                            "    python -m pip install --upgrade protobuf")
                elif str(e).startswith("no module named"):
                    l.error("Please download them and put them in the \"proto\" sub folder.")
                raise e
            except AttributeError as e:
                l.error("Could not import the proto classes: {}\n    ".format(e) +
                        "Your protobuf library is probably too old.\n    "
                        "Please upgrade to at least version 3.20.0 , by running:\n    "
                        "python -m pip install --upgrade protobuf")
                raise e

            self.header = prefix.prefix()

            l.debug("Parsing database header...")

            try:

                # The first byte is the size of the upcoming protobuf message
                protobuf_size = encrypted.read(1)
                self.file_hash.update(protobuf_size)
                protobuf_size = int.from_bytes(protobuf_size, byteorder='big')

                # A 0x01 as a second byte indicates the presence of the feature table in the protobuf.
                # It is optional and present only in msgstore database, although
                # I found some old msgstore backups without it, so it is optional.
                msgstore_features_flag = encrypted.peek(1)[0]
                if msgstore_features_flag != 1:
                    msgstore_features_flag = 0
                else:
                    self.file_hash.update(encrypted.read(1))
                if not msgstore_features_flag:
                    l.debug("No feature table found (not a msgstore DB or very old)")

                try:

                    protobuf_raw = encrypted.read(protobuf_size)
                    self.file_hash.update(protobuf_raw)

                    if self.header.ParseFromString(protobuf_raw) != protobuf_size:
                        l.error("Protobuf message not fully read. Please report a bug.")
                    else:

                        # Checking and printing WA version and phone number
                        version = findall(r"\d(?:\.\d{1,3}){3}", self.header.info.whatsapp_version)
                        if len(version) != 1:
                            l.error('WhatsApp version not found')
                        else:
                            l.debug("WhatsApp version: {}".format(version[0]))
                        if len(self.header.info.substringedUserJid) != 2:
                            l.error("The phone number end is not 2 characters long")
                        l.debug("Your phone number ends with {}".format(self.header.info.substringedUserJid))

                        if len(self.header.c15_iv.IV) != 0:
                            # DB Header is crypt15
                            # if type(key) is not Key15:
                            #    l.error("You are using a crypt14 key file with a crypt15 backup.")
                            if len(self.header.c15_iv.IV) != 16:
                                l.error("IV is not 16 bytes long but is {} bytes long".format(len(self.header.c15_iv.IV)))
                            iv = self.header.c15_iv.IV

                        elif len(self.header.c14_cipher.IV) != 0:
                            raise ValueError("Crypt14 file in crypt15 constructor!")
                        else:
                            l.error("Could not parse the IV from the protobuf message. Please report a bug.")
                            raise ValueError



                except DecodeError as e:

                    l.error("Could not parse the protobuf message: {}".format(e))
                    raise e

            except OSError as e:
                l.fatal("Reading database header failed: {}".format(e))
                raise e
        else:
            if iv:
                if len(iv) != 16:
                    l.error("IV is not 16 bytes long but is {} bytes long".format(len(iv)))
                self.iv = iv
            else:
                self.iv = urandom(16)

    def decrypt(self, key: Key15, encrypted: bytes) -> bytes:
        """Decrypts the database using the provided key"""
        checksum = encrypted[-16:]
        authentication_tag = encrypted[-32:-16]
        encrypted_data = encrypted[:-32]
        is_multifile_backup = False

        self.file_hash.update(encrypted_data)
        self.file_hash.update(authentication_tag)

        if self.file_hash.digest() != checksum:
            # We are probably in a multifile backup, which does not have a checksum.
            is_multifile_backup = True
        else:
            l.debug("Checksum OK ({}). Decrypting...".format(self.file_hash.hexdigest()))

        cipher = AES.new(key.get(), AES.MODE_GCM, self.iv)
        try:
            output_decrypted: bytes = cipher.decrypt(encrypted_data)
        except ValueError as e:
            l.fatal("Decryption failed: {}."
                    "\n    This probably means your backup is corrupted.".format(e))
            raise e

        # Verify the authentication tag
        try:
            if is_multifile_backup:
                # In multifile backups, there is no checksum.
                # This means, the last 16 bytes of the files are not the checksum,
                # despite being called "checksum", but are the authentication tag.
                # Same way, "authentication tag" is not the tag, but the last
                # 16 bytes of the encrypted file.
                output_decrypted += cipher.decrypt(authentication_tag)
                cipher.verify(checksum)
            else:
                cipher.verify(authentication_tag)
        except ValueError as e:
            l.error("Authentication tag mismatch: {}."
                    "\n    This probably means your backup is corrupted.".format(e))

        return output_decrypted

    def encrypt(self, key: Key15, decrypted: bytes) -> bytes:
        """Encrypts the database using the provided key"""
        from wa_crypt_tools.proto import C15_IV_pb2 as C15_IV
        cipher = C15_IV.C15_IV()
        cipher.IV = self.iv
        from wa_crypt_tools.proto import prefix_pb2 as prefix
        from wa_crypt_tools.proto import key_type_pb2 as key_type
        prefix = prefix.prefix()
        prefix.key_type = key_type.Key_Type.HSM_CONTROLLED
        prefix.c15_iv.CopyFrom(cipher)

        from wa_crypt_tools.proto import version_features_pb2 as version_features
        version_features = version_features.Version_Features()
        version_features.whatsapp_version = "2.22.5.13"
        version_features.substringedUserJid = "67"

        def populate_info(info, is_crypt15=True):
            """
            For know there is no way to know the correct values for these fields.
            So it is strongly advised to have another encrypted msgstore,
            and to copy the values from there. This feature is not implemented yet.
            """
            if is_crypt15:
                # Iterate over all the features and set them to true
                for feature in info.DESCRIPTOR.fields:
                    value = getattr(info, feature.name)
                    if feature.type == feature.TYPE_BOOL:
                        setattr(info, feature.name, True)
                info.idk = False
                info.message_main_verification = False
                info.feature_39 = True

        def populate_info(info):
            # Iterate over all the features and set them to true
            for feature in info.DESCRIPTOR.fields:
                value = getattr(info, feature.name)
                if feature.type == feature.TYPE_BOOL:
                    setattr(info, feature.name, False)
                info.call_log = True
                info.message_fts = True
                info.blank_me_jid = True
                info.receipt_user = True
                info.message_media = True
                info.receipt_device = True
                info.broadcast_me_jid = True
                info.participant_user = True
                info.migration_jid_store = True
                info.migration_chat_store = True
                info.quoted_order_message = True
                info.media_migration_fixer = True
                info.alter_message_ephemeral_to_message_ephemeral_remove_column = True
                info.alter_message_ephemeral_setting_to_message_ephemeral_setting_remove_column = True
                info.ClearField("feature_39")

        populate_info(version_features)

        prefix.info.CopyFrom(version_features)
        prefix = prefix.SerializeToString()
        out = b''
        file_hash = md5()
        out += len(prefix).to_bytes(1, byteorder='big')
        file_hash.update(out)
        # FIXME support feature table
        out += b'\x01'
        file_hash.update(b'\x01')
        out += prefix
        file_hash.update(prefix)
        cipher = AES.new(key.get(), AES.MODE_GCM, self.iv)
        encrypted_data, authentication_tag = cipher.encrypt_and_digest(decrypted)
        out += encrypted_data
        file_hash.update(encrypted_data)
        out += authentication_tag
        file_hash.update(authentication_tag)
        out += file_hash.digest()
        return out
    def get_iv(self) -> bytes:
        return self.iv
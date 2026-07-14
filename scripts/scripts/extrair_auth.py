import base64
import urllib.parse
import os
from pathlib import Path
from google.protobuf import descriptor_pb2, descriptor_pool
from google.protobuf.message_factory import GetMessageClass

# ─── Ajusta cwd para raiz do projeto ───
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)

# Pool global para evitar duplicação de descriptors
_POOL = descriptor_pool.Default()


def create_migration_payload_class():
    """
    Registra (uma única vez) e retorna a classe MigrationPayload protobuf,
    com nested type OtpParameters.
    """
    # Se já existe, retorna diretamente
    try:
        return GetMessageClass(_POOL.FindMessageTypeByName("authenticator.MigrationPayload"))
    except KeyError:
        pass

    # Define FileDescriptorProto
    file_desc = descriptor_pb2.FileDescriptorProto()
    file_desc.name = "migration.proto"
    file_desc.package = "authenticator"
    file_desc.syntax = "proto3"

    # Definição da mensagem MigrationPayload
    msg = file_desc.message_type.add()
    msg.name = "MigrationPayload"

    # Campo repeated OtpParameters
    field = msg.field.add()
    field.name = "otp_parameters"
    field.number = 1
    field.label = descriptor_pb2.FieldDescriptorProto.LABEL_REPEATED
    field.type = descriptor_pb2.FieldDescriptorProto.TYPE_MESSAGE
    field.type_name = "authenticator.MigrationPayload.OtpParameters"

    # Nested type OtpParameters
    nested = msg.nested_type.add()
    nested.name = "OtpParameters"

    # Campo secret (bytes)
    f1 = nested.field.add()
    f1.name = "secret"
    f1.number = 1
    f1.label = descriptor_pb2.FieldDescriptorProto.LABEL_OPTIONAL
    f1.type = descriptor_pb2.FieldDescriptorProto.TYPE_BYTES

    # Registra descriptor no pool (silencia duplicado)
    try:
        _POOL.Add(file_desc)
    except Exception:
        pass

    # Retorna a classe Python da mensagem
    return GetMessageClass(_POOL.FindMessageTypeByName("authenticator.MigrationPayload"))


def extrair_secret_de_uri(uri: str) -> str:
    """
    Extrai o parâmetro 'data' de um URI OTP, decodifica via protobuf
    e retorna o secret em base32 (sem padding).
    """
    parsed = urllib.parse.urlparse(uri)
    qs = urllib.parse.parse_qs(parsed.query)

    if "data" not in qs:
        raise KeyError("Parâmetro 'data' não encontrado na URI OTP")

    # Decodifica payload base64 URL-safe
    data_encoded = qs["data"][0]
    padding_needed = (-len(data_encoded)) % 4
    payload_bytes = base64.urlsafe_b64decode(data_encoded + "=" * padding_needed)

    # Parse via protobuf
    MigrationPayload = create_migration_payload_class()
    msg = MigrationPayload()
    msg.ParseFromString(payload_bytes)

    # Itera até achar secret
    for param in getattr(msg, 'otp_parameters', []):
        secret_b32 = base64.b32encode(param.secret).decode('utf-8').rstrip('=')
        return secret_b32

    raise ValueError("Nenhum 'otp_parameters.secret' encontrado no payload.")

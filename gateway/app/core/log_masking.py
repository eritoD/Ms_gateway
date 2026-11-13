"""Enmascara en el registro de accesos los valores que no deben quedar en los logs.

El enlace firmado de /webpay/checkout (?ticket=...) y el token de Webpay (token_ws, TBK_TOKEN) permiten abrir o
confirmar un pago mientras viven; la línea de acceso de uvicorn los escribiría tal cual junto al resto de la URL.
"""
from __future__ import annotations

import logging
import re

SENSITIVE_QUERY_PARAMS = frozenset({"ticket", "token_ws", "tbk_token"})

_PARAM = re.compile(r"(?i)([?&](?:%s)=)[^&#\s]*" % "|".join(sorted(SENSITIVE_QUERY_PARAMS)))


def mask_query(path: str) -> str:
    """The same path with the value of each sensitive query parameter replaced by ***."""
    return _PARAM.sub(r"\1***", path) if "?" in path else path


class AccessLogMasker(logging.Filter):
    """uvicorn.access records carry (client, method, full_path, http_version, status) in args."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(mask_query(a) if isinstance(a, str) else a for a in args)
        if isinstance(record.msg, str):
            record.msg = mask_query(record.msg)
        return True


def install_access_log_masking() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogMasker) for f in logger.filters):
        logger.addFilter(AccessLogMasker())

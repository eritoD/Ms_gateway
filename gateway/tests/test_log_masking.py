"""El registro de accesos no debe guardar el enlace firmado ni el token de Webpay."""
import logging

from app.core.log_masking import SENSITIVE_QUERY_PARAMS, mask_query, install_access_log_masking

TOKEN = "01ab" * 16
TICKET = "eyJhbGciOiJIUzI1NiJ9.eyJ0b2tlbl93cyI6IngifQ.firma"


def test_mask_query_hides_only_the_sensitive_values():
    assert mask_query(f"/api/v1/payments/webpay/checkout?ticket={TICKET}") == "/api/v1/payments/webpay/checkout?ticket=***"
    assert mask_query(f"/webpay/return?token_ws={TOKEN}&client=web&TBK_TOKEN={TOKEN}&TBK_ORDEN_COMPRA=SM1") == \
        "/webpay/return?token_ws=***&client=web&TBK_TOKEN=***&TBK_ORDEN_COMPRA=SM1"
    assert mask_query("/health/ready") == "/health/ready"
    assert mask_query("/x?plan=1") == "/x?plan=1"
    assert {"ticket", "token_ws", "tbk_token"} <= SENSITIVE_QUERY_PARAMS


def test_the_uvicorn_access_record_is_masked_before_it_is_written(caplog):
    install_access_log_masking()
    install_access_log_masking()  # repetir no duplica el filtro
    logger = logging.getLogger("uvicorn.access")
    assert sum(type(f).__name__ == "AccessLogMasker" for f in logger.filters) == 1
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logger.info('%s - "%s %s HTTP/%s" %d', "172.18.0.1:5000", "GET",
                    f"/api/v1/payments/webpay/checkout?ticket={TICKET}", "1.1", 200)
    assert caplog.records, "el registro debe seguir existiendo"
    assert TICKET not in caplog.text and "ticket=***" in caplog.text

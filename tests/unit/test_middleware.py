import pytest

from app.api.middleware import _resolve_request_id


def _scope(request_id: str | None) -> dict[str, object]:
    headers = [(b"x-request-id", request_id.encode())] if request_id is not None else []
    return {"type": "http", "headers": headers}


def test_wellformed_inbound_request_id_is_reused():
    assert _resolve_request_id(_scope("trace-01:abc.def")) == "trace-01:abc.def"


@pytest.mark.parametrize(
    "value",
    ["has space", "inject\nnewline", "semi;colon", "", "x" * 65],
)
def test_unsafe_inbound_request_id_is_replaced(value: str):
    resolved = _resolve_request_id(_scope(value))

    assert resolved != value
    assert len(resolved) == 32


def test_request_id_is_generated_when_header_absent():
    assert len(_resolve_request_id(_scope(None))) == 32

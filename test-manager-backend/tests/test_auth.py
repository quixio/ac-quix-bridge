from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.auth import (
    auth,
    bearer_from_request,
    extract_token,
    read_permission,
    update_permission,
    validate_token,
)
from api.settings import Settings


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("rawtoken", "rawtoken"),  # auth.py also accepts a bare token
        (None, None),
        ("", None),
    ],
)
def test_extract_token(header: str | None, expected: str | None) -> None:
    assert extract_token(header) == expected


def _request_with_auth(value: str | None) -> Request:
    headers = [(b"authorization", value.encode())] if value is not None else []
    return Request({"type": "http", "headers": headers})


def test_bearer_from_request_present() -> None:
    assert bearer_from_request(_request_with_auth("Bearer xyz")) == "xyz"


def test_bearer_from_request_absent() -> None:
    assert bearer_from_request(_request_with_auth(None)) is None


def _make_request(path: str = "/api/v1/tests") -> Mock:
    """Create a mock Request with a url.path attribute."""
    request = Mock()
    request.url.path = path
    return request


def test_auth_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that auth() returns the same instance (lru_cache behavior)."""
    monkeypatch.setenv("Quix__Portal__Api", "http://localhost:8080")
    auth.cache_clear()
    auth1 = auth()
    auth2 = auth()
    assert auth1 is auth2


def test_validate_token_auth_disabled() -> None:
    """Test that validation passes when auth is disabled."""
    mock_auth = Mock()
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = False

    validator = validate_token("Read")
    result = validator(
        request=_make_request(),
        auth_instance=mock_auth,
        settings=mock_settings,
        authorization=None,
    )

    assert result is None
    mock_auth.validate_permissions.assert_not_called()


def test_validate_token_missing_header_auth_enabled() -> None:
    """Test that missing auth header raises 403 when auth is enabled."""
    mock_auth = Mock()
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = True

    validator = validate_token("Read")

    with pytest.raises(HTTPException) as exc_info:
        validator(
            request=_make_request(),
            auth_instance=mock_auth,
            settings=mock_settings,
            authorization=None,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not Allowed"


def test_validate_token_bearer_prefix_lowercase() -> None:
    """Test that bearer prefix (lowercase) is properly stripped."""
    mock_auth = Mock()
    mock_auth.validate_permissions.return_value = True
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = True
    mock_settings.workspace_id = "test-workspace"

    validator = validate_token("Update")
    validator(
        request=_make_request(),
        auth_instance=mock_auth,
        settings=mock_settings,
        authorization="bearer test-token",
    )

    mock_auth.validate_permissions.assert_called_once_with(
        "test-token", "Workspace", "test-workspace", "Update"
    )


def test_validate_token_bearer_prefix_uppercase() -> None:
    """Test that Bearer prefix (uppercase) is properly stripped."""
    mock_auth = Mock()
    mock_auth.validate_permissions.return_value = True
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = True
    mock_settings.workspace_id = "test-workspace"

    validator = validate_token("Read")
    validator(
        request=_make_request(),
        auth_instance=mock_auth,
        settings=mock_settings,
        authorization="Bearer test-token",
    )

    mock_auth.validate_permissions.assert_called_once_with(
        "test-token", "Workspace", "test-workspace", "Read"
    )


def test_validate_token_no_bearer_prefix() -> None:
    """Test that token without bearer prefix is used directly."""
    mock_auth = Mock()
    mock_auth.validate_permissions.return_value = True
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = True
    mock_settings.workspace_id = "test-workspace"

    validator = validate_token("Read")
    validator(
        request=_make_request(),
        auth_instance=mock_auth,
        settings=mock_settings,
        authorization="test-token-direct",
    )

    mock_auth.validate_permissions.assert_called_once_with(
        "test-token-direct", "Workspace", "test-workspace", "Read"
    )


def test_validate_token_invalid_permissions() -> None:
    """Test that invalid permissions raise 403."""
    mock_auth = Mock()
    mock_auth.validate_permissions.return_value = False
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = True
    mock_settings.workspace_id = "test-workspace"

    validator = validate_token("Update")

    with pytest.raises(HTTPException) as exc_info:
        validator(
            request=_make_request(),
            auth_instance=mock_auth,
            settings=mock_settings,
            authorization="invalid-token",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not Allowed"


def test_update_permission_function() -> None:
    """Test that update_permission is correctly configured."""
    mock_auth = Mock()
    mock_auth.validate_permissions.return_value = True
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = True
    mock_settings.workspace_id = "test-workspace"

    update_permission(
        request=_make_request(),
        auth_instance=mock_auth,
        settings=mock_settings,
        authorization="test-token",
    )

    mock_auth.validate_permissions.assert_called_once_with(
        "test-token", "Workspace", "test-workspace", "Update"
    )


def test_read_permission_function() -> None:
    """Test that read_permission is correctly configured."""
    mock_auth = Mock()
    mock_auth.validate_permissions.return_value = True
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = True
    mock_settings.workspace_id = "test-workspace"

    read_permission(
        request=_make_request(),
        auth_instance=mock_auth,
        settings=mock_settings,
        authorization="test-token",
    )

    mock_auth.validate_permissions.assert_called_once_with(
        "test-token", "Workspace", "test-workspace", "Read"
    )

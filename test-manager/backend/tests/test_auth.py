from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from api.auth import auth, validate_token, update_permission, read_permission
from api.settings import Settings


def test_auth_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that auth() returns the same instance (lru_cache behavior)."""
    monkeypatch.setenv("Quix__Portal__Api", "http://localhost:8080")
    auth1 = auth()
    auth2 = auth()
    assert auth1 is auth2


def test_validate_token_auth_disabled() -> None:
    """Test that validation passes when auth is disabled."""
    mock_auth = Mock()
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = False

    validator = validate_token("Read")
    result = validator(mock_auth, mock_settings, None)

    assert result is None
    mock_auth.validate_permissions.assert_not_called()


def test_validate_token_missing_header_auth_enabled() -> None:
    """Test that missing auth header raises 403 when auth is enabled."""
    mock_auth = Mock()
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = True

    validator = validate_token("Read")

    with pytest.raises(HTTPException) as exc_info:
        validator(mock_auth, mock_settings, None)

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
    result = validator(mock_auth, mock_settings, "bearer test-token")

    assert result is None
    mock_auth.validate_permissions.assert_called_once_with(
        "test-token", "Workspace", "test-workspace", "Update"
    )


def test_validate_token_bearer_prefix_uppercase() -> None:
    """Test that bearer prefix (uppercase) is properly stripped."""
    mock_auth = Mock()
    mock_auth.validate_permissions.return_value = True
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = True
    mock_settings.workspace_id = "test-workspace"

    validator = validate_token("Read")
    result = validator(mock_auth, mock_settings, "Bearer test-token")

    assert result is None
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
    result = validator(mock_auth, mock_settings, "test-token-direct")

    assert result is None
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
        validator(mock_auth, mock_settings, "invalid-token")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Not Allowed"
    mock_auth.validate_permissions.assert_called_once_with(
        "invalid-token", "Workspace", "test-workspace", "Update"
    )


def test_update_permission_function() -> None:
    """Test that update_permission is correctly configured."""
    mock_auth = Mock()
    mock_auth.validate_permissions.return_value = True
    mock_settings = Mock(spec=Settings)
    mock_settings.api_auth_active = True
    mock_settings.workspace_id = "test-workspace"

    result = update_permission(mock_auth, mock_settings, "test-token")

    assert result is None
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

    result = read_permission(mock_auth, mock_settings, "test-token")

    assert result is None
    mock_auth.validate_permissions.assert_called_once_with(
        "test-token", "Workspace", "test-workspace", "Read"
    )

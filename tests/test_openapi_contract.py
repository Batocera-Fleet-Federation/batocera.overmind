"""Verify the OpenAPI/Swagger schema reflects the Phase 1 typed request/response models.

These assertions guard against regressing back to untyped (`{}`) request/response schemas
in `/docs` and `/openapi.json`.
"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from overmind.main import app


def _spec():
    return TestClient(app).get("/openapi.json").json()


def _ref_name(schema):
    """Return the component name a schema $refs, following a top-level allOf if present."""
    if not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    for combinator in ("allOf", "anyOf", "oneOf"):
        for part in schema.get(combinator, []):
            name = _ref_name(part)
            if name:
                return name
    return None


def _response_ref(spec, path, method, code="200"):
    op = spec["paths"][path][method]
    schema = op["responses"][code]["content"]["application/json"]["schema"]
    return _ref_name(schema)


def _request_ref(spec, path, method):
    op = spec["paths"][path][method]
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    return _ref_name(schema)


def test_openapi_is_served_and_valid():
    spec = _spec()
    assert spec["openapi"].startswith("3.")
    assert "paths" in spec and "components" in spec


def test_new_response_models_registered_in_components():
    schemas = _spec()["components"]["schemas"]
    for name in (
        "LoginResponse",
        "SwarmListResponse",
        "ProfileResponse",
        "NotificationListResponse",
        "AuthProvidersResponse",
        "SwarmAccessResponse",
        "HiveResponse",
    ):
        assert name in schemas, f"{name} missing from OpenAPI components"


def test_representative_response_models_wired():
    spec = _spec()
    assert _response_ref(spec, "/api/auth/login", "post") == "LoginResponse"
    assert _response_ref(spec, "/api/auth/refresh", "post") == "LoginResponse"
    assert _response_ref(spec, "/api/swarms", "get") == "SwarmListResponse"
    assert _response_ref(spec, "/api/profile", "get") == "ProfileResponse"
    assert _response_ref(spec, "/api/notifications", "get") == "NotificationListResponse"
    assert _response_ref(spec, "/api/hive", "get") == "HiveResponse"


def test_retyped_request_bodies_have_schemas():
    spec = _spec()
    # Previously `payload: dict` / untyped — now documented request models.
    assert _request_ref(spec, "/api/profile", "patch") == "ProfileUpdateRequest"
    assert _request_ref(spec, "/api/invitations/accept", "post") == "InvitationAcceptRequest"
    assert _request_ref(spec, "/api/swarms/{swarm_id}", "patch") == "SwarmRenameRequest"
    assert _request_ref(spec, "/api/notifications/read", "post") == "NotificationIdsRequest"


def test_phase2_4_response_models_registered():
    schemas = _spec()["components"]["schemas"]
    for name in (
        "DeviceModel",
        "DevicesListResponse",
        "HeartbeatResponse",
        "AdminOverviewResponse",
        "MasterRomsResponse",
        "DeviceSavesResponse",
        "ActionsResponse",
    ):
        assert name in schemas, f"{name} missing from OpenAPI components"


def test_phase2_4_endpoints_wired():
    spec = _spec()
    # responses
    assert _response_ref(spec, "/api/devices", "get") == "DevicesListResponse"
    assert _response_ref(spec, "/api/devices/{device_id}/heartbeat", "post") == "HeartbeatResponse"
    assert _response_ref(spec, "/api/admin/overview", "get") == "AdminOverviewResponse"
    assert _response_ref(spec, "/api/master-roms", "get") == "MasterRomsResponse"
    # retyped request bodies (were `payload: dict`)
    assert _request_ref(spec, "/api/bulk-sync", "post") == "BulkSyncRequest"
    assert _request_ref(spec, "/api/devices/{device_id}/sync-rom", "post") == "SyncRomRequest"
    assert _request_ref(spec, "/api/devices/{device_id}/auto-sync", "patch") == "AutoSyncUpdateRequest"


def test_redirect_endpoints_not_forced_through_json_model():
    """OAuth/verify redirects should not advertise a JSON response model."""
    spec = _spec()
    for path, method in (
        ("/api/auth/{provider}/start", "get"),
        ("/api/auth/{provider}/callback", "get"),
    ):
        responses = spec["paths"][path][method]["responses"]
        json_200 = responses.get("200", {}).get("content", {}).get("application/json")
        # No JSON body model (it's a redirect); a $ref here would be wrong.
        assert not (json_200 and _ref_name(json_200.get("schema", {})))

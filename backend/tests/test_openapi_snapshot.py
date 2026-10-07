"""TASK-006-B 全量 OpenAPI、统一信封和接口总览一致性测试。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.openapi_contract import (
    ENVELOPE_REF,
    GENERATION_OPERATION_IDS,
    HTTP_METHODS,
    SSE_OPERATION_IDS,
    build_openapi_snapshot,
)
from app.main import app

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
SNAPSHOT = BACKEND_ROOT / "contracts" / "openapi-v1.snapshot.json"
API_DOC = REPO_ROOT / "docs" / "后端接口文档.md"


def _http_operations(schema: dict) -> list[tuple[str, str, dict]]:
    return [
        (path, method, path_item[method])
        for path, path_item in sorted(schema["paths"].items())
        for method in HTTP_METHODS
        if method in path_item
    ]


def _normalized_path(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path.removeprefix("/api/v1"))


def _documented_operations() -> set[tuple[str, str]]:
    text = API_DOC.read_text(encoding="utf-8")
    section = text.split("## 13.", 1)[1].split("\n## 14.", 1)[0]
    documented: set[tuple[str, str]] = set()
    for line in section.splitlines():
        if not line.startswith("|") or "`/" not in line:
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        path = _normalized_path(columns[3].strip("`"))
        for method in columns[2].split("/"):
            if method.lower() in HTTP_METHODS:
                documented.add((method.lower(), path))
    return documented


def test_openapi_snapshot_is_current_and_complete():
    actual = build_openapi_snapshot(app.openapi())
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert actual == expected
    assert actual["operationCount"] == 83
    assert actual["schemaCount"] == 56


def test_every_http_operation_documents_envelope_and_real_validation_status():
    schema = app.openapi()
    seen_sse: set[str] = set()
    for _, _, operation in _http_operations(schema):
        assert operation["x-zhixue-envelope"] is True
        responses = operation["responses"]
        assert responses["200"]["content"]["application/json"]["schema"] == {
            "$ref": ENVELOPE_REF
        }
        assert "422" not in responses
        if "requestBody" in operation or operation.get("parameters"):
            assert responses["400"]["content"]["application/json"]["schema"] == {
                "$ref": ENVELOPE_REF
            }
        if operation["operationId"] in SSE_OPERATION_IDS:
            assert "text/event-stream" in responses["200"]["content"]
            seen_sse.add(operation["operationId"])
    assert seen_sse == SSE_OPERATION_IDS


def test_generation_operations_are_explicitly_marked():
    marked = {
        operation["operationId"]
        for _, _, operation in _http_operations(app.openapi())
        if operation.get("x-zhixue-generation-meta") is True
    }
    assert marked == GENERATION_OPERATION_IDS


def test_interface_overview_covers_every_openapi_operation_without_stale_entries():
    actual = {
        (method, _normalized_path(path))
        for path, method, _ in _http_operations(app.openapi())
    }
    assert _documented_operations() == actual


def test_runtime_success_and_validation_error_keep_exact_envelope():
    with TestClient(app) as client:
        success = client.get("/api/v1/health")
        invalid = client.post("/api/v1/auth/login", json={})
    assert success.status_code == 200
    assert set(success.json()) == {"code", "message", "data", "traceId"}
    assert invalid.status_code == 400
    assert invalid.json()["code"] == 1001
    assert set(invalid.json()) == {"code", "message", "data", "traceId"}

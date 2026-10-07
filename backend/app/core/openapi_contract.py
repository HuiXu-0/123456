"""OpenAPI 契约规范化与可复现快照。

只修正文档层：运行时成功/错误信封仍由 app.core.envelope 负责。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import FastAPI

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
ENVELOPE_SCHEMA_NAME = "UnifiedEnvelope"
ENVELOPE_REF = f"#/components/schemas/{ENVELOPE_SCHEMA_NAME}"
GENERATION_META_SCHEMA_NAME = "GenerationMeta"
GENERATION_OPERATION_IDS = {
    "cornell_cues_api_v1_learning_cornell_cues_post",
    "dashboard_evaluation_api_v1_dashboard_evaluation_get",
    "dialogue_api_v1_profile_dialogue_post",
    "diagram_api_v1_resource_diagram__kp_id__get",
    "doc_chat_api_v1_document_chat_post",
    "external_aggregate_api_v1_resource_external_aggregate_post",
    "feynman_api_v1_learning_feynman_post",
    "gen_diagram_api_v1_document_generate_diagram_post",
    "gen_flashcards_api_v1_document_generate_flashcards_post",
    "gen_lecture_api_v1_document_generate_lecture_post",
    "gen_mindmap_api_v1_document_generate_mindmap_post",
    "gen_overview_api_v1_document_generate_overview_post",
    "gen_quiz_api_v1_document_generate_quiz_post",
    "gen_video_api_v1_document_generate_video_post",
    "generate_lecture_api_v1_resource_lecture_post",
    "generate_video_api_v1_resource_video_post",
    "job_match_api_v1_job_market_match_post",
    "mindmap_api_v1_resource_mindmap__kp_id__get",
    "narrative_api_v1_profile_narrative_post",
    "parse_api_v1_profile_parse_post",
    "reinforce_api_v1_reinforce_post",
    "submit_quiz_api_v1_quiz__kp_id__submit_post",
    "tutor_chat_api_v1_resource_tutor_chat_post",
    "tutor_generate_api_v1_resource_tutor_generate_post",
    "tutor_suggest_api_v1_resource_tutor_suggest_post",
}
SSE_OPERATION_IDS = {
    "dialogue_api_v1_profile_dialogue_post",
    "tutor_chat_api_v1_resource_tutor_chat_post",
    "feynman_api_v1_learning_feynman_post",
    "doc_chat_api_v1_document_chat_post",
}


def _envelope_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": ENVELOPE_REF}}},
    }


def normalize_openapi(schema: dict[str, Any]) -> dict[str, Any]:
    """给全部 HTTP 操作标注统一信封，并对齐实际的 400 校验错误。"""
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components[ENVELOPE_SCHEMA_NAME] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["code", "message", "data", "traceId"],
        "properties": {
            "code": {"type": "integer"},
            "message": {"type": "string"},
            "data": {},
            "traceId": {"type": "string"},
        },
    }
    components[GENERATION_META_SCHEMA_NAME] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["provider", "model", "source", "degraded", "fallbackReason"],
        "properties": {
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "source": {
                "type": "string",
                "enum": ["builtin", "custom", "mock", "cache", "fallback", "deterministic"],
            },
            "degraded": {"type": "boolean"},
            "fallbackReason": {"type": ["string", "null"]},
        },
    }
    for path_item in schema.get("paths", {}).values():
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if operation is None:
                continue
            operation["x-zhixue-envelope"] = True
            if operation.get("operationId") in GENERATION_OPERATION_IDS:
                operation["x-zhixue-generation-meta"] = True
            responses = operation.setdefault("responses", {})
            success = responses.setdefault("200", _envelope_response("Unified success envelope"))
            success_content = success.setdefault("content", {})
            success_content["application/json"] = {"schema": {"$ref": ENVELOPE_REF}}
            if operation.get("operationId") in SSE_OPERATION_IDS:
                success_content["text/event-stream"] = {"schema": {"type": "string"}}
            if "422" in responses:
                responses.pop("422")
                responses["400"] = _envelope_response("Validation error envelope (code=1001)")
            for status, response in responses.items():
                if status.startswith(("4", "5")) and status != "400":
                    response.setdefault("content", {})["application/json"] = {
                        "schema": {"$ref": ENVELOPE_REF}
                    }
    return schema


def install_openapi_contract(app: FastAPI) -> None:
    """安装一次性 OpenAPI 后处理器，不影响实际路由处理函数。"""
    original_openapi = app.openapi

    def contracted_openapi() -> dict[str, Any]:
        if app.openapi_schema is None or not app.openapi_schema.get("x-zhixue-contract"):
            schema = normalize_openapi(original_openapi())
            schema["x-zhixue-contract"] = "openapi-v1"
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi_schema = None
    app.openapi = contracted_openapi  # type: ignore[method-assign]


def _without_documentation_noise(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_documentation_noise(item)
            for key, item in sorted(value.items())
            if key not in {"description", "summary", "externalDocs"}
        }
    if isinstance(value, list):
        return [_without_documentation_noise(item) for item in value]
    return value


def build_openapi_snapshot(schema: dict[str, Any]) -> dict[str, Any]:
    """保留路径、方法、请求/响应 Schema 与组件模型，去除纯文案噪声。"""
    operations: dict[str, Any] = {}
    for path, path_item in sorted(schema.get("paths", {}).items()):
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if operation is None:
                continue
            key = f"{method.upper()} {path}"
            operations[key] = {
                field: deepcopy(operation[field])
                for field in (
                    "operationId",
                    "tags",
                    "parameters",
                    "requestBody",
                    "responses",
                    "security",
                    "deprecated",
                    "x-zhixue-envelope",
                    "x-zhixue-generation-meta",
                )
                if field in operation
            }
    schemas = deepcopy(schema.get("components", {}).get("schemas", {}))
    return _without_documentation_noise(
        {
            "snapshotVersion": 1,
            "openapi": schema.get("openapi"),
            "contract": schema.get("x-zhixue-contract"),
            "operationCount": len(operations),
            "schemaCount": len(schemas),
            "operations": operations,
            "schemas": schemas,
        }
    )

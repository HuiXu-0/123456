"""TASK-001：统一日志与配置管理测试。"""
from __future__ import annotations

import logging

import pytest

from app.core.config import Settings
from app.core.envelope import _trace_id_ctx
from app.core.logging import PiiMaskingFilter, RequestContextFilter, mask_pii, setup_logging


def test_log_settings_are_normalized_and_validated():
    configured = Settings(log_level="debug", log_format="JSON")
    assert configured.log_level == "DEBUG"
    assert configured.log_format == "json"
    with pytest.raises(ValueError):
        Settings(log_level="verbose")
    with pytest.raises(ValueError):
        Settings(log_format="xml")


def test_sensitive_values_are_masked():
    masked = mask_pii("手机13800138000 邮箱carmela@teachers.org key=sk-abcdefgh123456")
    assert "138****8000" in masked
    assert "c****a@teachers.org" in masked
    assert "sk-****3456" in masked
    assert "13800138000" not in masked
    assert "carmela@teachers.org" not in masked


def test_request_context_filter_injects_trace_id():
    token = _trace_id_ctx.set("trace-task001")
    try:
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "ok", (), None)
        assert RequestContextFilter().filter(record)
        assert record.trace_id == "trace-task001"
    finally:
        _trace_id_ctx.reset(token)


def test_setup_logging_is_idempotent():
    setup_logging()
    setup_logging()
    root = logging.getLogger()
    assert sum(isinstance(item, PiiMaskingFilter) for item in root.filters) == 1
    assert sum(isinstance(item, RequestContextFilter) for item in root.filters) == 1

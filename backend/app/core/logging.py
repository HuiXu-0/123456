"""日志脱敏（需求文档 7.4 / CLAUDE.md 工程纪律）。

在日志输出前对手机号、邮箱做掩码，避免 PII 落盘：
- 手机号 13800138000 → 138****8000
- 邮箱 carmela@teachers.org → c****a@teachers.org

实现为 logging.Filter，挂到 root 及 uvicorn 各 logger；对 record.msg 与
record.args（格式化参数）一并脱敏，确保 `logger.info("%s", phone)` 也被覆盖。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from app.core.config import settings

# 中国大陆手机号：1 开头第二位 3-9，共 11 位（避免误伤更长数字串用边界约束）
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{4}(\d{4})(?!\d)")
# 邮箱
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]*([A-Za-z0-9])(@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})")
# API Key（模型管理 21.3+ 红线兜底）：sk-/ms- 开头的令牌串（DeepSeek/魔搭等常见形态）。
# 主防线是「不打含 key 的日志 + llm_userconf.redact 清洗异常串」，此处为过滤器级兜底。
_API_KEY_RE = re.compile(r"\b(sk|ms)-[A-Za-z0-9\-_]{8,}\b")


def mask_pii(text: str) -> str:
    """对单个字符串做手机号/邮箱/API Key 掩码。"""
    text = _PHONE_RE.sub(r"\g<1>****\g<2>", text)
    text = _EMAIL_RE.sub(_mask_email, text)
    text = _API_KEY_RE.sub(lambda m: f"{m.group(1)}-****{m.group(0)[-4:]}", text)
    return text


def _mask_email(m: re.Match[str]) -> str:
    first, last, domain = m.group(1), m.group(2), m.group(3)
    return f"{first}****{last}{domain}"


class PiiMaskingFilter(logging.Filter):
    """掩码 record 的消息与字符串参数。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_pii(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._mask_arg(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._mask_arg(a) for a in record.args)
        return True

    @staticmethod
    def _mask_arg(value: object) -> object:
        return mask_pii(value) if isinstance(value, str) else value


class RequestContextFilter(logging.Filter):
    """为所有日志补充当前请求 traceId；非请求上下文使用短横线。"""

    def filter(self, record: logging.LogRecord) -> bool:
        # 延迟导入，避免 logging 与 envelope 在应用启动时形成循环依赖。
        from app.core.envelope import current_trace_id

        record.trace_id = current_trace_id() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """最小结构化日志格式；message 在过滤阶段已经完成敏感信息掩码。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "traceId": getattr(record, "trace_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _formatter() -> logging.Formatter:
    if settings.log_format == "json":
        return JsonFormatter()
    return logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] [traceId=%(trace_id)s] %(message)s"
    )


def setup_logging() -> None:
    """统一配置应用与 Uvicorn 日志（级别、格式、traceId、脱敏，幂等）。"""
    pii_filter = PiiMaskingFilter()
    context_filter = RequestContextFilter()
    formatter = _formatter()
    targets = [
        logging.getLogger(),  # root
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.access"),
        logging.getLogger("uvicorn.error"),
    ]
    logging.getLogger().setLevel(settings.log_level)
    for logger in targets:
        if not any(isinstance(f, PiiMaskingFilter) for f in logger.filters):
            logger.addFilter(pii_filter)
        if not any(isinstance(f, RequestContextFilter) for f in logger.filters):
            logger.addFilter(context_filter)
        for handler in logger.handlers:
            if not any(isinstance(f, PiiMaskingFilter) for f in handler.filters):
                handler.addFilter(pii_filter)
            if not any(isinstance(f, RequestContextFilter) for f in handler.filters):
                handler.addFilter(context_filter)
            handler.setFormatter(formatter)

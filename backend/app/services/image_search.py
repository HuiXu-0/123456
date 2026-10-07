"""可插拔「图片搜索 Provider」（免版权图源，讲义真实配图专用）。

类比 ``app/services/web_search.py`` 的可插拔设计，但**面向讲义配图**单独抽象一层，
原因：之前讲义配图复用 Tavily 图片搜索，返回的多是带防盗链 / 会过期的 CDN 链接
（byteimg 等），页面加载被拒、几乎总是「图片暂不可用」。本模块改用**免版权、URL 稳定、
可标注来源**的图源：

- ``none``：无图源能力 → ``online=False``、``search_images()`` 返回 []（上层不插真实图）；
- ``wikimedia``（默认）：Wikimedia Commons（MediaWiki API）。免版权 / 可商用，图片 URL 取自
  ``upload.wikimedia.org``（**不防盗链、URL 稳定**），并记录文件标题 + Commons 文件页链接用于标注；
- ``pexels``（**预留兜底**）：Pexels Photo API。需配 ``PEXELS_API_KEY`` 才在线；本次默认不强接——
  配了 Key 即自动作为 Wikimedia 没命中时的兜底（接口已留好）。

设计纪律（与 web_search 一致）：
- 返回的 ``ImageHit`` 仅含**真实命中**字段，图片 URL 一律取自图源结果（**绝不让 LLM 编造链接**），
  交由上层（``lecture_media``）做防幻觉 http(s) 二次校验后嵌入讲义；
- 任何网络 / 解析失败均**不致命**（不抛错，回 []），讲义本身照常返回；
- Wikimedia / Pexels 均无需「让 LLM 画图」——禁止 AI 生成图片的红线不变。

> mock 模式由上层（``lecture_media``：``llm.is_mock``）拦截，走确定性内联 SVG 占位、
> **不会调用本模块**，故无任何真实请求；本模块仅在真实 LLM 模式下被调用。
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from app.core.config import settings

logger = logging.getLogger("app.services.image_search")

# Wikimedia API 礼貌要求带描述性 User-Agent（否则可能被限流）。
_USER_AGENT = "ZhixueHub/1.0 (educational lecture enrichment; contact: admin@zhixue.local)"

# 仅接受真实图片 MIME（排除 PDF / 视频 / 音频等非图资源）。
_IMAGE_MIME_PREFIX = "image/"


class ImageProvider(Protocol):
    """图片搜索 Provider 协议（讲义配图专用）。"""

    name: str
    online: bool

    def search_images(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        """返回图片命中 ``[{url, source, source_url, title, description, license}]``。

        URL 一律取自图源结果（不编造）；无能力 / 失败 → []（上层不插真实图）。
        """
        ...


class _OfflineProvider:
    """无图源能力：``online=False``，``search_images`` 恒返回 []（上层不插真实图）。"""

    name = "none"
    online = False

    def search_images(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        return []


class _WikimediaProvider:
    """Wikimedia Commons 图片搜索（MediaWiki API，免版权 / 可商用，URL 稳定不防盗链）。

    用 ``generator=search`` 在 File 命名空间按关键词搜图，取 ``imageinfo`` 中的稳定缩略图 URL
    （``upload.wikimedia.org``，按相关性排序），并记录文件标题 + Commons 文件页链接用于标注。
    """

    name = "wikimedia"
    online = True  # Commons API 免密钥，联网即可用（无网时调用失败自动回 []）

    def __init__(self, api_url: str, timeout: float, thumb_width: int = 960) -> None:
        self.api_url = api_url
        self.timeout = timeout
        self.thumb_width = thumb_width

    def search_images(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        try:
            import requests  # 延迟导入，避免无图源能力时的硬依赖

            resp = requests.get(
                self.api_url,
                params={
                    "action": "query",
                    "format": "json",
                    "generator": "search",
                    "gsrsearch": query,
                    "gsrnamespace": 6,  # File: 命名空间
                    "gsrlimit": max_results,
                    "prop": "imageinfo",
                    "iiprop": "url|mime|extmetadata|size",
                    "iiurlwidth": self.thumb_width,  # 返回稳定的缩放 thumburl（加载快、不防盗链）
                },
                headers={"User-Agent": _USER_AGENT},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 联网失败不致命 → 不插真实图
            logger.warning("Wikimedia 图片搜索失败，跳过配图：%s", exc)
            return []

        pages = ((data or {}).get("query") or {}).get("pages") or {}
        # generator=search 用 index 字段保序（dict 无序），按相关性还原排序
        ordered = sorted(pages.values(), key=lambda p: p.get("index", 1 << 30))
        hits: list[dict[str, Any]] = []
        for page in ordered:
            info_list = page.get("imageinfo") or []
            if not info_list:
                continue
            info = info_list[0]
            mime = str(info.get("mime") or "")
            if not mime.startswith(_IMAGE_MIME_PREFIX):
                continue  # 跳过 PDF / 视频 / 音频等非图资源
            # 优先稳定缩略 URL（upload.wikimedia.org，缩放后加载快），回落原图 url
            img_url = str(info.get("thumburl") or info.get("url") or "").strip()
            if not (img_url.startswith("http://") or img_url.startswith("https://")):
                continue
            title = str(page.get("title") or "").removeprefix("File:").strip()
            meta = info.get("extmetadata") or {}
            description = _strip_html(_meta_value(meta, "ImageDescription")) or title
            license_name = _meta_value(meta, "LicenseShortName")
            hits.append(
                {
                    "url": img_url,
                    "source": "Wikimedia Commons",
                    "source_url": str(info.get("descriptionurl") or "").strip(),
                    "title": title,
                    "description": description,
                    "license": license_name,
                }
            )
        return hits


class _PexelsProvider:
    """Pexels Photo API（**预留兜底**，免版权摄影图；需 ``PEXELS_API_KEY``）。

    接口已留好：配 Key 即在线，可作为 Wikimedia 没命中时的兜底。Pexels CDN（images.pexels.com）
    URL 稳定、不防盗链；按 Pexels 许可需标注来源（这里记录摄影师页链接 ``url`` 用于标注）。
    """

    name = "pexels"

    def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.online = bool(api_key)

    def search_images(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        if not self.online:
            return []
        query = (query or "").strip()
        if not query:
            return []
        try:
            import requests  # 延迟导入

            resp = requests.get(
                f"{self.base_url}/v1/search",
                params={"query": query, "per_page": max_results},
                headers={"Authorization": self.api_key, "User-Agent": _USER_AGENT},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 联网失败不致命 → 不插真实图
            logger.warning("Pexels 图片搜索失败，跳过配图：%s", exc)
            return []
        hits: list[dict[str, Any]] = []
        for photo in (data.get("photos") or [])[:max_results]:
            src = (photo or {}).get("src") or {}
            img_url = str(src.get("large") or src.get("medium") or src.get("original") or "").strip()
            if not (img_url.startswith("http://") or img_url.startswith("https://")):
                continue
            hits.append(
                {
                    "url": img_url,
                    "source": "Pexels",
                    "source_url": str(photo.get("url") or "").strip(),
                    "title": str(photo.get("alt") or "").strip(),
                    "description": str(photo.get("alt") or "").strip(),
                    "license": "Pexels License",
                }
            )
        return hits


class _CompositeProvider:
    """按顺序尝试多个图源，返回首个非空命中（用于「Wikimedia 主 + Pexels 兜底」）。"""

    name = "composite"

    def __init__(self, providers: list[ImageProvider]) -> None:
        self.providers = providers
        self.online = any(getattr(p, "online", False) for p in providers)

    def search_images(self, query: str, *, max_results: int) -> list[dict[str, Any]]:
        for provider in self.providers:
            if not getattr(provider, "online", False):
                continue
            hits = provider.search_images(query, max_results=max_results)
            if hits:
                return hits
        return []


def _meta_value(meta: dict[str, Any], key: str) -> str:
    """从 Wikimedia extmetadata 取值（结构为 ``{key: {value: ...}}``）。"""
    entry = (meta or {}).get(key) or {}
    return str(entry.get("value") or "").strip()


def _strip_html(text: str) -> str:
    """去除 extmetadata 描述里的简单 HTML 标签 / 实体（仅用于 alt 文本）。"""
    import re

    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    cleaned = cleaned.replace("&nbsp;", " ").replace("&amp;", "&")
    return " ".join(cleaned.split())


def get_provider() -> ImageProvider:
    """按 ``settings.image_provider`` 选择图源；配了 Pexels Key 时自动追加为兜底。

    - ``none`` → offline（不插真实图）；
    - ``wikimedia``（默认）→ Wikimedia Commons；若另配 ``PEXELS_API_KEY``，组合为
      「Wikimedia 主 + Pexels 兜底」；
    - ``pexels`` → 仅在配 Key 时在线，否则回落 offline。
    """
    provider = (settings.image_provider or "none").lower()
    if provider == "none":
        return _OfflineProvider()

    chain: list[ImageProvider] = []
    if provider == "wikimedia":
        chain.append(
            _WikimediaProvider(settings.wikimedia_api_url, settings.image_search_timeout_seconds)
        )
    elif provider == "pexels":
        if settings.pexels_api_key:
            chain.append(
                _PexelsProvider(
                    settings.pexels_api_key,
                    settings.pexels_base_url,
                    settings.image_search_timeout_seconds,
                )
            )

    # 预留兜底：主源非 pexels 且配了 Pexels Key → 追加 Pexels 作为 fallback。
    if provider != "pexels" and settings.pexels_api_key:
        chain.append(
            _PexelsProvider(
                settings.pexels_api_key,
                settings.pexels_base_url,
                settings.image_search_timeout_seconds,
            )
        )

    if not chain:
        return _OfflineProvider()
    if len(chain) == 1:
        return chain[0]
    return _CompositeProvider(chain)

"""SubDl 公共工具模块

集中管理常量、日志、文件IO、网络请求等基础功能。
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import dataclass
from http.client import HTTPResponse
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import json5  # type: ignore[import-untyped]


# ========== 路径常量 ==========

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
TEMPLATE_DIR = PROJECT_ROOT / 'config_template'
RULESET_DIR = PROJECT_ROOT / 'ruleset'
RULESET_SOURCE = RULESET_DIR / 'ruleset_source.txt'
RULESET_JSON_DIR = RULESET_DIR / 'json'
RULESET_SRS_DIR = RULESET_DIR / 'srs'
CONVERT_SCRIPT = SCRIPT_DIR / 'convert.mjs'
TEMPLATE_BASE = TEMPLATE_DIR / 'sing-box.jsonc'


# ========== 网络配置 ==========

HTTP_RETRY = 3
HTTP_TIMEOUT = 30
USER_AGENT = "ClashMetaForAndroid/2.11.30"
_RETRYABLE_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


# ========== 日志 ==========

def _log(level: str, msg: str) -> None:
    """输出日志到 stderr（避免污染 stdout 数据流）"""
    prefix = f"[{level}] " if level != "INFO" else ""
    print(f"{prefix}{msg}", file=sys.stderr)


def log_debug(msg: str) -> None:
    _log("DEBUG", msg)


def log_info(msg: str) -> None:
    _log("INFO", msg)


def log_warn(msg: str) -> None:
    _log("WARN", msg)


def log_error(msg: str) -> None:
    _log("ERROR", msg)


# 兼容旧导入: from utils import logger
class logger:
    """兼容层 —— 新代码请直接使用 log_debug / log_info / log_warn / log_error"""
    debug = staticmethod(log_debug)
    info = staticmethod(log_info)
    warn = staticmethod(log_warn)
    error = staticmethod(log_error)


# ========== 数据模型 ==========

@dataclass(frozen=True)
class FlowInfo:
    """订阅流量信息（不可变）"""
    upload: int = 0
    download: int = 0
    total: int = 0
    expire: int | None = None


def _extract_name_from_url(url: str) -> str:
    try:
        name = urlparse(url).netloc.replace("www.", "").split(":")[0]
        return name or f"unknown_{hashlib.md5(url.encode()).hexdigest()[:8]}"
    except Exception:
        return f"unknown_{hashlib.md5(url.encode()).hexdigest()[:8]}"


@dataclass(frozen=True)
class Subscription:
    """订阅配置（不可变）"""
    name: str
    url: str
    filename: str
    use_gist: bool = False

    @classmethod
    def from_url(cls, url: str, name: str | None = None, use_gist: bool = False) -> Subscription:
        if name is None:
            name = _extract_name_from_url(url)
        return cls(name=name, url=url, filename=f"{name}.yaml", use_gist=use_gist)


@dataclass
class DownloadResult:
    """订阅下载结果"""
    name: str
    status: str  # "ok" | "invalid" | "error"
    flow: FlowInfo | None = None
    reason: str = "未知"
    filename: str = ""
    raw_content: str | None = None
    is_converted: bool = False  # True 表示内容已是从 Gist 备份获取的 sing-box 格式

    @property
    def is_success(self) -> bool:
        return self.status == "ok"


@dataclass
class SubscriptionInfo:
    """订阅统计信息"""
    name: str
    flow: FlowInfo | None
    node_count: int
    from_backup: bool = False  # True 表示数据来自 Gist 备份


# ========== 基础工具函数 ==========

def load_jsonc(filepath: str | Path) -> Any:
    """加载 JSONC/JSON5 文件（支持 // 和 /* */ 注释）"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json5.load(f)


# ========== 网络请求（基于 urllib，无第三方依赖） ==========

@dataclass
class HttpResponse:
    """轻量 HTTP 响应封装"""
    status_code: int
    text: str
    headers: dict[str, str]


def http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: int = HTTP_TIMEOUT,
) -> HttpResponse:
    """通用 HTTP 请求"""
    data = json.dumps(json_body).encode('utf-8') if json_body is not None else None
    req = Request(url, data=data, headers=headers or {}, method=method)
    if json_body is not None:
        req.add_header('Content-Type', 'application/json')

    resp: HTTPResponse = urlopen(req, timeout=timeout)
    raw_headers = {k.lower(): v for k, v in resp.headers.items()}
    body = resp.read().decode('utf-8', errors='replace')
    return HttpResponse(status_code=resp.status, text=body, headers=raw_headers)


def http_get_with_retry(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = HTTP_TIMEOUT,
    max_retries: int = HTTP_RETRY,
    backoff_factor: int = 2,
) -> HttpResponse:
    """HTTP GET with exponential backoff retry"""
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                sleep_time = backoff_factor ** (attempt - 1)
                log_debug(f"  重试 {attempt}/{max_retries}，等待 {sleep_time}s...")
                time.sleep(sleep_time)

            resp = http_request('GET', url, headers=headers, timeout=timeout)

            if resp.status_code >= 400:
                if resp.status_code in _RETRYABLE_CODES:
                    raise HTTPError(url='', code=resp.status_code, msg=f"HTTP {resp.status_code}", hdrs=None, fp=None)
                raise HTTPError(url='', code=resp.status_code, msg=f"HTTP {resp.status_code}", hdrs=None, fp=None)
            return resp

        except (HTTPError, URLError, OSError) as e:
            last_error = e
            log_debug(f"  请求失败: {e}")

    assert last_error is not None
    raise last_error
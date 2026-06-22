"""SubDl 公共工具模块

集中管理常量、日志、文件IO、网络请求等基础功能。
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from http.client import HTTPResponse
from pathlib import Path
from typing import Any, IO
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
WORKFLOW_PATH = PROJECT_ROOT / '.github' / 'workflows' / 'subscriptions-update.yml'
CONVERT_SCRIPT = SCRIPT_DIR / 'convert.mjs'
TEMPLATE_BASE = TEMPLATE_DIR / 'sing-box_template.jsonc'


# ========== 网络配置 ==========

HTTP_RETRY = 3
HTTP_TIMEOUT = 30
USER_AGENT = "clash-verge/v2.4.4"


# ========== 日志系统 ==========

class LogLevel(Enum):
    """日志级别枚举（值为数值，支持大小比较）"""
    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40


class Logger:
    """统一日志管理器
    
    输出到 stderr，避免污染 stdout 数据流。
    支持按级别过滤，便于 CI 调试。
    """
    
    def __init__(self, min_level: LogLevel = LogLevel.INFO, file: IO[str] = sys.stderr):
        self.min_level = min_level
        self.file = file
    
    def _log(self, level: LogLevel, msg: str) -> None:
        if level.value >= self.min_level.value:
            prefix = f"[{level.name}]" if level != LogLevel.INFO else ""
            print(f"{prefix} {msg}" if prefix else msg, file=self.file)
    
    def debug(self, msg: str) -> None:
        self._log(LogLevel.DEBUG, msg)
    
    def info(self, msg: str) -> None:
        self._log(LogLevel.INFO, msg)
    
    def warn(self, msg: str) -> None:
        self._log(LogLevel.WARN, msg)
    
    def error(self, msg: str) -> None:
        self._log(LogLevel.ERROR, msg)


# 全局日志实例
logger = Logger(min_level=LogLevel.INFO)


# ========== 数据模型 ==========

@dataclass(frozen=True)
class FlowInfo:
    """订阅流量信息（不可变）"""
    upload: int = 0
    download: int = 0
    total: int = 0
    expire: int | None = None
    
    @property
    def used(self) -> int:
        return self.upload + self.download
    
    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)
    
    @property
    def is_expired(self) -> bool:
        return bool(self.expire and self.expire < time.time())
    
    @property
    def is_expiring_soon(self) -> bool:
        if not self.expire:
            return False
        return self.expire - time.time() < 7 * 24 * 3600
    
    @property
    def is_traffic_exhausted(self) -> bool:
        return self.total > 0 and self.used >= self.total
    
    @property
    def status(self) -> str:
        if self.is_expired:
            return "❌ 已过期"
        if self.is_traffic_exhausted:
            return "❌ 流量用完"
        if self.is_expiring_soon:
            return "⚠️ 即将到期"
        return "✅ 正常"


@dataclass(frozen=True)
class Subscription:
    """订阅配置（不可变）"""
    name: str
    url: str
    filename: str
    
    @classmethod
    def from_url(cls, url: str, name: str | None = None) -> Subscription:
        if name is None:
            name = _extract_name_from_url(url)
        return cls(name=name, url=url, filename=f"{name}.yaml")


@dataclass
class DownloadResult:
    """订阅下载结果"""
    name: str
    status: str  # "ok" | "invalid" | "error"
    flow: FlowInfo | None = None
    reason: str = "未知"
    filename: str = ""
    raw_content: str | None = None
    
    @property
    def is_success(self) -> bool:
        return self.status == "ok"


@dataclass
class SubscriptionInfo:
    """订阅统计信息"""
    name: str
    flow: FlowInfo | None
    node_count: int
    status: str


# ========== 基础工具函数 ==========

def load_jsonc(filepath: str | Path) -> Any:
    """加载 JSONC/JSON5 文件（支持 // 和 /* */ 注释）"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json5.load(f)


def discover_template_files(template_dir: str | Path) -> list[tuple[str, str]]:
    """发现模板目录中所有 .json 和 .jsonc 文件，返回 [(完整路径, 基本名), ...]"""
    template_path = Path(template_dir)
    if not template_path.exists():
        return []
    
    files = [(str(e), e.stem) for e in template_path.iterdir() if e.suffix in ('.json', '.jsonc')]
    files.sort(key=lambda e: (e[1] != "sing-box_template", e[1]))

    return files


_B64_PATTERN: re.Pattern[str] = re.compile(r'^[A-Za-z0-9+/=]+$')


def try_decode_base64(content: str) -> str:
    """尝试将内容作为 Base64 解码，失败则原样返回"""
    try:
        cleaned = ''.join(content.split())
        if cleaned and _B64_PATTERN.match(cleaned):
            cleaned += "=" * (-len(cleaned) % 4)
            return base64.b64decode(cleaned).decode("utf-8")
    except Exception:
        pass
    return content


# ========== 网络请求（基于 urllib，无第三方依赖） ==========

@dataclass
class HttpResponse:
    """轻量 HTTP 响应封装，兼容 requests.Response 的常用接口"""
    status_code: int
    text: str
    headers: dict[str, str]
    
    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 600:
            raise HTTPError(url='', code=self.status_code, msg=f"HTTP {self.status_code}", hdrs=None, fp=None)


_RETRYABLE_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def http_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = HTTP_TIMEOUT,
    allow_redirects: bool = True,
) -> HttpResponse:
    """使用 urllib 发送 GET 请求"""
    req = Request(url, headers=headers or {}, method='GET')
    if not allow_redirects:
        # urllib 默认跟随重定向，通过自定义 opener 禁用
        import urllib.request
        opener = urllib.request.OpenerDirector()
        opener.add_handler(urllib.request.HTTPHandler())
        opener.add_handler(urllib.request.HTTPSHandler())
        resp: HTTPResponse = opener.open(req, timeout=timeout)
    else:
        resp = urlopen(req, timeout=timeout)
    
    raw_headers = dict(resp.headers.items())
    body = resp.read().decode('utf-8', errors='replace')
    return HttpResponse(status_code=resp.status, text=body, headers=raw_headers)


def http_get_with_retry(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = HTTP_TIMEOUT,
    allow_redirects: bool = True,
    max_retries: int = HTTP_RETRY,
    backoff_factor: int = 2,
) -> HttpResponse:
    """HTTP GET with exponential backoff retry"""
    last_error: Exception | None = None
    
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                sleep_time = backoff_factor ** (attempt - 1)
                logger.debug(f"  重试 {attempt}/{max_retries}，等待 {sleep_time}s...")
                time.sleep(sleep_time)
            
            resp = http_get(url, headers=headers, timeout=timeout, allow_redirects=allow_redirects)
            
            if resp.status_code in _RETRYABLE_CODES:
                raise HTTPError(url='', code=resp.status_code, msg=f"HTTP {resp.status_code}", hdrs=None, fp=None)
            
            resp.raise_for_status()
            return resp
            
        except (HTTPError, URLError, OSError) as e:
            last_error = e
            logger.debug(f"  请求失败: {e}")
    
    assert last_error is not None
    raise last_error


def http_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: int = HTTP_TIMEOUT,
) -> HttpResponse:
    """通用 HTTP 请求（支持 POST/PATCH 等，用于 Gist API 等）"""
    data = json.dumps(json_body).encode('utf-8') if json_body is not None else None
    req = Request(url, data=data, headers=headers or {}, method=method)
    if json_body is not None:
        req.add_header('Content-Type', 'application/json')
    
    resp = urlopen(req, timeout=timeout)
    raw_headers = dict(resp.headers.items())
    body = resp.read().decode('utf-8', errors='replace')
    return HttpResponse(status_code=resp.status, text=body, headers=raw_headers)


# ========== 格式化工具 ==========

def format_bytes(n: int) -> str:
    """将字节数格式化为人类可读字符串（B / KB / MB / GB / TB）"""
    if n == 0:
        return "0 B"
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    i = min(int(n.bit_length() - 1) // 10, len(units) - 1)
    return f"{n / (1024 ** i):.2f} {units[i]}"


def format_expire(timestamp: int | None) -> str:
    if not timestamp:
        return "无"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except Exception:
        return "无"


_DOMAIN_SANITIZE_RE: re.Pattern[str] = re.compile(r'[^a-zA-Z0-9_-]')


def _extract_name_from_url(url: str) -> str:
    try:
        domain = urlparse(url).netloc.replace("www.", "").split(":")[0]
        name = _DOMAIN_SANITIZE_RE.sub('_', domain)
        return name[:50] if name else f"unknown_{int(time.time())}"
    except Exception:
        return f"unknown_{int(time.time())}"


def get_env_var(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    """获取环境变量"""
    value = os.environ.get(name, default)
    if required and not value:
        raise ValueError(f"环境变量 {name} 未设置")
    return value
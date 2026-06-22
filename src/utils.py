"""SubDl 公共工具模块

集中管理常量、日志、文件IO、网络请求等基础功能。
"""

from __future__ import annotations

import base64
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, IO
from urllib.parse import urlparse

import json5  # type: ignore[import-untyped]
import requests


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
        """内部日志输出"""
        if level.value >= self.min_level.value:
            prefix = f"[{level.name}]" if level != LogLevel.INFO else ""
            print(f"{prefix} {msg}" if prefix else msg, file=self.file)
    
    def debug(self, msg: str) -> None:
        """调试信息"""
        self._log(LogLevel.DEBUG, msg)
    
    def info(self, msg: str) -> None:
        """普通信息"""
        self._log(LogLevel.INFO, msg)
    
    def warn(self, msg: str) -> None:
        """警告信息"""
        self._log(LogLevel.WARN, msg)
    
    def error(self, msg: str) -> None:
        """错误信息"""
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
        """已用流量"""
        return self.upload + self.download
    
    @property
    def remaining(self) -> int:
        """剩余流量"""
        return max(0, self.total - self.used)
    
    @property
    def is_expired(self) -> bool:
        """是否已过期"""
        return bool(self.expire and self.expire < time.time())
    
    @property
    def is_expiring_soon(self) -> bool:
        """是否即将过期（7天内）"""
        if not self.expire:
            return False
        return self.expire - time.time() < 7 * 24 * 3600
    
    @property
    def is_traffic_exhausted(self) -> bool:
        """流量是否用完"""
        return self.total > 0 and self.used >= self.total
    
    @property
    def status(self) -> str:
        """获取状态描述"""
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
        """从 URL 创建订阅"""
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
            # 利用负数取模特性自动补零：-len % 4 在整除时返回 0
            cleaned += "=" * (-len(cleaned) % 4)
            return base64.b64decode(cleaned).decode("utf-8")
    except Exception:
        pass
    return content


# ========== 网络请求 ==========

@dataclass
class RetryConfig:
    """重试配置"""
    max_retries: int = HTTP_RETRY
    backoff_factor: int = 2
    retryable_status_codes: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    timeout: int = HTTP_TIMEOUT


def http_get_with_retry(
    url: str,
    retry_config: RetryConfig | None = None,
    **kwargs: Any
) -> requests.Response:
    """HTTP GET with timeout and exponential backoff retry
    
    Args:
        url: 请求 URL
        retry_config: 重试配置，None 使用默认配置
        **kwargs: 传递给 requests.get 的其他参数
    
    Returns:
        requests.Response: 响应对象
    
    Raises:
        requests.RequestException: 所有重试失败后抛出最后的异常
    """
    if retry_config is None:
        retry_config = RetryConfig()
    
    kwargs.setdefault("timeout", retry_config.timeout)
    last_error: Exception | None = None
    
    for attempt in range(1, retry_config.max_retries + 1):
        try:
            if attempt > 1:
                sleep_time = retry_config.backoff_factor ** (attempt - 1)
                logger.debug(f"  重试 {attempt}/{retry_config.max_retries}，等待 {sleep_time}s...")
                time.sleep(sleep_time)
            
            resp = requests.get(url, **kwargs)
            
            # 检查是否需要重试的状态码
            if resp.status_code in retry_config.retryable_status_codes:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            
            resp.raise_for_status()
            return resp
            
        except requests.RequestException as e:
            last_error = e
            logger.debug(f"  请求失败: {e}")
    
    assert last_error is not None, "重试循环应至少执行一次"
    raise last_error


def format_bytes(n: int) -> str:
    """将字节数格式化为人类可读字符串（B / KB / MB / GB / TB）"""
    if n == 0:
        return "0 B"
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    i = min(int(n.bit_length() - 1) // 10, len(units) - 1)
    return f"{n / (1024 ** i):.2f} {units[i]}"


def format_expire(timestamp: int | None) -> str:
    """格式化到期时间"""
    if not timestamp:
        return "无"
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
    except Exception:
        return "无"


_DOMAIN_SANITIZE_RE: re.Pattern[str] = re.compile(r'[^a-zA-Z0-9_-]')


def _extract_name_from_url(url: str) -> str:
    """从 URL 提取域名作为订阅名称"""
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
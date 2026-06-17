"""SubDl 公共工具模块"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, IO

import json5  # type: ignore[import-untyped]
import requests


def log(msg: str, *, file: IO[str] = sys.stderr) -> None:
    """统一日志输出到 stderr，避免污染 stdout 数据流"""
    print(msg, file=file)


def load_jsonc(filepath: str) -> dict[str, Any]:
    """加载 JSONC/JSON5 文件（支持 // 和 /* */ 注释）"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json5.load(f)


def discover_template_files(template_dir: str) -> list[tuple[str, str]]:
    """发现模板目录中所有 .json 和 .jsonc 文件，返回 [(完整路径, 基本名), ...]"""
    files: list[tuple[str, str]] = []
    for name in os.listdir(template_dir):
        if name.endswith('.jsonc'):
            base = name[:-6]
        elif name.endswith('.json'):
            base = name[:-5]
        else:
            continue
        files.append((os.path.join(template_dir, name), base))

    # 基础模板恒在首位，其余按字母序排列
    def _sort_key(entry: tuple[str, str]) -> tuple[int, str]:
        return (0, "") if entry[1] == "sing-box_template" else (1, entry[1])
    files.sort(key=_sort_key)

    return files


HTTP_RETRY: int = 3
HTTP_TIMEOUT: int = 30


def http_get_with_retry(url: str, **kwargs: Any) -> requests.Response:
    """HTTP GET with timeout and exponential backoff retry"""
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    last_error: Exception | None = None
    for attempt in range(1, HTTP_RETRY + 1):
        try:
            if attempt > 1:
                time.sleep(2 ** (attempt - 1))  # 指数退避：2s, 4s, 8s...
            resp = requests.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_error = e
    raise last_error if last_error else Exception(f"下载失败: {url}")

"""SubDl 公共工具模块"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, IO

import json5  # type: ignore[import-untyped]


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
    return files
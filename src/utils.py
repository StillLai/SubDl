"""SubDl 公共工具模块"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from typing import IO


def log(msg: str, *, file: IO[str] = sys.stderr) -> None:
    """统一日志输出到 stderr，避免污染 stdout 数据流"""
    print(msg, file=file)


def load_jsonc(filepath: str) -> dict[str, Any]:
    """加载 JSONC 文件（支持注释）"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 移除 JSONC 注释（但要避免误删 URL 中的 //）
    lines: list[str] = []
    for line in content.split('\n'):
        # 移除行首的 // 注释（允许前面有空格）
        stripped = line.lstrip()
        if stripped.startswith('//'):
            # 整行都是注释
            indent = line[:len(line) - len(line.lstrip())]
            lines.append(indent)
        else:
            lines.append(line)

    content = '\n'.join(lines)
    return json.loads(content)


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
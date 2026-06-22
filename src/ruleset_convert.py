"""规则集转换脚本 — 将 Clash/Surge 等规则源转换为 sing-box 格式"""

from __future__ import annotations

import json
import csv
import subprocess
import yaml
import ipaddress
import re
from io import StringIO
from collections.abc import Callable
from pathlib import Path
from typing import Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils import (
    logger, log, http_get_with_retry,
    RULESET_SOURCE, RULESET_JSON_DIR, RULESET_SRS_DIR,
)


# ========== 预编译正则 ==========

_PACKAGE_PART_RE: re.Pattern[str] = re.compile(r'^[a-zA-Z0-9_]+$')
_IP_LIKE_RE: re.Pattern[str] = re.compile(r'^[\d./:a-fA-F]+$')


# ========== 规则映射表 ==========

_MAP_DICT: dict[str, str] = {
    'DOMAIN-SUFFIX': 'domain_suffix',
    'HOST-SUFFIX': 'domain_suffix',
    'DOMAIN': 'domain',
    'HOST': 'domain',
    'DOMAIN-KEYWORD': 'domain_keyword',
    'HOST-KEYWORD': 'domain_keyword',
    'IP-CIDR': 'ip_cidr',
    'IP-CIDR6': 'ip_cidr',
    'IP6-CIDR': 'ip_cidr',
    'SRC-IP-CIDR': 'source_ip_cidr',
    'GEOIP': 'geoip',
    'DST-PORT': 'port',
    'SRC-PORT': 'source_port',
    'URL-REGEX': 'domain_regex',
    'PROCESS-NAME': 'process_name',
}

_OTHER_SYSTEM_EXTENSIONS: frozenset[str] = frozenset(
    ('.exe', '.dll', '.app', '.dmg', '.msi', '.deb', '.rpm', '.pkg')
)
_ANDROID_COMMON_PREFIXES: frozenset[str] = frozenset(
    ('com', 'org', 'net', 'edu', 'gov', 'mil', 'android', 'google')
)


# ========== 网络下载与解析 ==========

def _fetch_parsed(url: str, parser: str) -> Any:
    """下载 URL 内容并按指定格式解析（'json' / 'yaml' / 'csv'）"""
    response = http_get_with_retry(url)
    return _PARSERS[parser](response.text)


def is_ipv4_or_ipv6(address: str) -> str | None:
    """判断地址是 IPv4 还是 IPv6，都不是返回 None"""
    if not _IP_LIKE_RE.match(address):
        return None
    try:
        if ':' in address:
            ipaddress.IPv6Network(address, strict=False)
            return 'ipv6'
        ipaddress.IPv4Network(address, strict=False)
        return 'ipv4'
    except ValueError:
        return None


def is_android_package_name(text: str) -> bool:
    """判断是否为安卓程序包名"""
    if not text or '.' not in text:
        return False

    text_lower = text.lower()
    if any(text_lower.endswith(ext) for ext in _OTHER_SYSTEM_EXTENSIONS):
        return False

    parts = text.split('.')
    if not all(part and part[0].isalpha() and _PACKAGE_PART_RE.match(part) for part in parts):
        return False

    return parts[0] in _ANDROID_COMMON_PREFIXES or len(parts) >= 2


# ========== 规则行解析器 ==========

def _parse_yaml_rows(yaml_data: Any) -> list[dict[str, str | None]]:
    """从 YAML 数据解析规则行"""
    rows: list[dict[str, str | None]] = []
    items = yaml_data.get('payload', []) if isinstance(yaml_data, dict) else yaml_data.split()

    for item in items:
        address_addr = item.strip("'")
        if not address_addr:
            continue
        if ',' not in item:
            if is_ipv4_or_ipv6(item):
                pattern = 'IP-CIDR'
            elif address_addr[0] in ('+', '.'):
                pattern = 'DOMAIN-SUFFIX'
                address_addr = address_addr.lstrip('+')
            else:
                pattern = 'DOMAIN'
        else:
            pattern, address_addr = item.split(',', 1)
        rows.append({'pattern': pattern.strip(), 'address': address_addr.strip(), 'other': None})
    return rows


def _parse_csv_rows(data: str) -> list[dict[str, str | None]]:
    """从 CSV 数据解析规则行"""
    reader = csv.reader(StringIO(data))
    rows: list[dict[str, str | None]] = []
    for row in reader:
        if not row or not row[0].strip():
            continue
        pattern = row[0].strip() if len(row) >= 1 else ""
        address = row[1].strip() if len(row) >= 2 else ""
        other = row[2].strip() if len(row) >= 3 else None
        if pattern and address:
            rows.append({'pattern': pattern, 'address': address, 'other': other})
    return rows


_PARSERS: dict[str, Callable[[str], Any]] = {
    'json': json.loads,
    'yaml': yaml.safe_load,
    'csv': _parse_csv_rows,
}


def parse_and_convert_to_rows(link: str) -> list[dict[str, str | None]]:
    """下载并解析规则链接，返回 list[dict] (pattern, address, other)"""
    if link.endswith('.yaml') or link.endswith('.txt'):
        try:
            return _parse_yaml_rows(_fetch_parsed(link, 'yaml'))
        except Exception as e:
            logger.warn(f"  YAML 解析失败 {link}: {e}，回退到 CSV 格式解析")
            return _fetch_parsed(link, 'csv')  # type: ignore[return-value]
    return _fetch_parsed(link, 'csv')  # type: ignore[return-value]


# ========== SRS 编译 ==========

def _compile_srs(file_name: str) -> None:
    """使用 subprocess 安全调用 sing-box 编译 SRS，输出到 file_name 同级的 srs/ 目录"""
    src = Path(file_name)
    srs_dir = RULESET_SRS_DIR
    srs_filename = src.stem + '.srs'
    srs_path = srs_dir / srs_filename
    srs_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["sing-box", "rule-set", "compile", "--output", str(srs_path), file_name],
            check=True, capture_output=True, text=True
        )
    except Exception as e:
        logger.error(f"  SRS 编译失败: {srs_filename} - {e}")


def prioritize_version_key(obj: Any) -> Any:
    """保持原有顺序，仅将 'version' 键置顶"""
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        if "version" in obj:
            result["version"] = prioritize_version_key(obj["version"])
        for k in obj:
            if k != "version":
                result[k] = prioritize_version_key(obj[k])
        return result
    elif isinstance(obj, list):
        return [prioritize_version_key(x) for x in obj]
    return obj


# ========== 规则分组与生成 ==========

def _group_by_mapped(
    rows: list[dict[str, str | None]], map_dict: dict[str, str]
) -> dict[str, list[str]]:
    """将行列表按 mapped_pattern 分组，返回 {mapped_pattern: [address, ...]}"""
    groups: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()

    for row in rows:
        pattern: str = row['pattern']  # type: ignore[assignment]
        if '#' in pattern:
            continue
        pattern_upper = pattern.upper()
        mapped = map_dict.get(pattern_upper)
        if mapped is None:
            continue
        address_raw = row.get('address')
        if not address_raw or not address_raw.strip():
            continue
        address: str = address_raw.strip()
        if pattern_upper == 'PROCESS-NAME':
            mapped = 'package_name' if is_android_package_name(address) else 'process_name'
        key = (mapped, address)
        if key in seen:
            continue
        seen.add(key)
        groups.setdefault(mapped, []).append(address)

    return groups


def parse_list_file(link: str, output_directory: str) -> str | None:
    """解析规则链接并生成 JSON/SRS 文件"""
    out_dir = Path(output_directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    file_name = str(out_dir / f"{Path(link).stem}.json")

    # 如果是 .json 链接，仅处理 sing-box 规则集格式
    if link.endswith('.json'):
        try:
            json_data = _fetch_parsed(link, 'json')
            if isinstance(json_data, dict) and 'version' in json_data and 'rules' in json_data:
                with open(file_name, 'w', encoding='utf-8') as output_file:
                    json.dump(prioritize_version_key(json_data), output_file, ensure_ascii=False, indent=2)
                _compile_srs(file_name)
                return file_name
            else:
                logger.warn(f"  {link} 不是 sing-box 规则集格式，跳过")
                return None
        except Exception as e:
            logger.error(f"  处理 JSON 文件失败 {link}: {e}")
            return None

    rows = parse_and_convert_to_rows(link)
    groups = _group_by_mapped(rows, _MAP_DICT)

    result_rules: dict[str, Any] = {"version": 4, "rules": []}
    domain_suffix_set: set[str] = set(groups.get('domain_suffix', []))

    domain_entries: list[str] = []
    for mapped, addresses in groups.items():
        if mapped == 'domain_suffix':
            result_rules["rules"].append({'domain_suffix': addresses})
        elif mapped == 'domain':
            filtered = [a for a in addresses if a not in domain_suffix_set]
            domain_entries.extend(filtered)
        elif mapped in ('port', 'source_port'):
            result_rules["rules"].append({mapped: [int(a) for a in addresses]})
        else:
            result_rules["rules"].append({mapped: addresses})

    domain_entries = list(set(domain_entries))
    if domain_entries:
        result_rules["rules"].insert(0, {'domain': domain_entries})

    with open(file_name, 'w', encoding='utf-8') as output_file:
        json.dump(prioritize_version_key(result_rules), output_file, ensure_ascii=False, indent=2)

    _compile_srs(file_name)
    return file_name


def main() -> None:
    """主入口：并行处理规则源文件"""
    links = [
        l.strip() for l in RULESET_SOURCE.read_text(encoding='utf-8').splitlines()
        if l.strip() and not l.strip().startswith("#")
    ]

    RULESET_JSON_DIR.mkdir(parents=True, exist_ok=True)
    RULESET_SRS_DIR.mkdir(parents=True, exist_ok=True)

    result_file_names: list[str] = []
    log(f"→ 并行处理 {len(links)} 个规则源...")

    with ThreadPoolExecutor(max_workers=min(len(links), 8)) as executor:
        futures = {
            executor.submit(parse_list_file, link, str(RULESET_JSON_DIR)): link
            for link in links
        }
        for future in as_completed(futures):
            link = futures[future]
            try:
                result = future.result()
                if result:
                    result_file_names.append(result)
            except Exception as e:
                logger.error(f"  跳过 {link}: {e}")

    log(f"✓ 共生成 {len(result_file_names)}/{len(links)} 个规则集（JSON + SRS）")
    for file_name in result_file_names:
        logger.info(f"  ✓ {Path(file_name).stem}")


if __name__ == "__main__":
    main()
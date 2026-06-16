"""规则集转换脚本 — 将 Clash/Surge 等规则源转换为 sing-box 格式"""

from __future__ import annotations

import os
import json
import csv
import subprocess
import time
import requests
import yaml
import ipaddress
import re
from io import StringIO
from typing import Any

from utils import log

HTTP_TIMEOUT: int = 30
HTTP_RETRY: int = 3

# 预编译正则，避免重复编译开销
_PACKAGE_PART_RE: re.Pattern[str] = re.compile(r'^[a-zA-Z0-9_]+$')
_IP_LIKE_RE: re.Pattern[str] = re.compile(r'^[\d./:a-fA-F]+$')


def _http_get(url: str, **kwargs: Any) -> requests.Response:
    """HTTP GET with timeout and retry"""
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    last_error: Exception | None = None
    for attempt in range(1, HTTP_RETRY + 1):
        try:
            if attempt > 1:
                time.sleep(2)
            resp = requests.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_error = e
    raise last_error if last_error else Exception(f"下载失败: {url}")


def read_json_from_url(url: str) -> dict[str, Any]:
    """下载并解析 sing-box JSON 格式的规则集"""
    response = _http_get(url)
    json_data: dict[str, Any] = json.loads(response.text)
    return json_data


def is_singbox_ruleset(json_data: Any) -> bool:
    """判断 JSON 数据是否为 sing-box 规则集格式"""
    if isinstance(json_data, dict):
        return 'version' in json_data and 'rules' in json_data
    return False


def read_yaml_from_url(url: str) -> Any:
    """下载并解析 YAML 格式的规则集"""
    response = _http_get(url)
    yaml_data = yaml.safe_load(response.text)
    return yaml_data


def read_list_from_url(url: str) -> list[dict[str, str | None]]:
    """使用标准库 csv 读取规则列表，返回 list[dict]"""
    response = _http_get(url)
    reader = csv.reader(StringIO(response.text))
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


def is_ipv4_or_ipv6(address: str) -> str | None:
    """判断地址是 IPv4 还是 IPv6，都不是返回 None"""
    if not _IP_LIKE_RE.match(address):
        return None
    try:
        ipaddress.IPv4Network(address, strict=False)
        return 'ipv4'
    except ValueError:
        try:
            ipaddress.IPv6Network(address, strict=False)
            return 'ipv6'
        except ValueError:
            return None


def is_android_package_name(text: str) -> bool:
    """判断是否为安卓程序包名"""
    if not text or not isinstance(text, str):
        return False

    # 排除明显是其他系统的程序
    other_system_extensions = ['.exe', '.dll', '.app', '.dmg', '.msi', '.deb', '.rpm', '.pkg']
    if any(text.lower().endswith(ext) for ext in other_system_extensions):
        return False

    if '/' in text or '\\' in text:
        return False
    if ' ' in text:
        return False
    if '.' not in text:
        return False

    parts = text.split('.')
    for part in parts:
        if not part:
            return False
        if not part[0].isalpha():
            return False
        if not _PACKAGE_PART_RE.match(part):
            return False

    common_prefixes = ['com', 'org', 'net', 'edu', 'gov', 'mil', 'android', 'google']
    if parts[0] in common_prefixes:
        return True

    return len(parts) >= 2


def _try_int(v: str) -> int | str:
    """尝试将字符串转为 int，失败返回原值"""
    try:
        return int(v)
    except ValueError:
        return v


def parse_and_convert_to_rows(link: str) -> list[dict[str, str | None]]:
    """下载并解析规则链接，返回 list[dict] (pattern, address, other)"""
    if link.endswith('.yaml') or link.endswith('.txt'):
        try:
            yaml_data: Any = read_yaml_from_url(link)
            rows: list[dict[str, str | None]] = []
            if not isinstance(yaml_data, str):
                items = yaml_data.get('payload', [])
            else:
                lines = yaml_data.splitlines()
                line_content = lines[0]
                items = line_content.split()
            for item in items:
                address = item.strip("'")
                if ',' not in item:
                    if is_ipv4_or_ipv6(item):
                        pattern = 'IP-CIDR'
                    else:
                        if address.startswith('+') or address.startswith('.'):
                            pattern = 'DOMAIN-SUFFIX'
                            if address.startswith('+'):
                                address = address[1:]
                        else:
                            pattern = 'DOMAIN'
                else:
                    pattern, address = item.split(',', 1)
                rows.append({'pattern': pattern.strip(), 'address': address.strip(), 'other': None})
            return rows
        except Exception as e:
            log(f"  ⚠ YAML 解析失败 {link}: {e}，回退到 CSV 格式解析")
            return read_list_from_url(link)
    else:
        return read_list_from_url(link)


def _compile_srs(file_name: str, srs_dir: str = "./ruleset/srs/") -> None:
    """使用 subprocess 安全调用 sing-box 编译 SRS"""
    srs_filename = os.path.basename(file_name).replace(".json", ".srs")
    srs_path = os.path.join(srs_dir, srs_filename)
    os.makedirs(srs_dir, exist_ok=True)
    try:
        subprocess.run(
            ["sing-box", "rule-set", "compile", "--output", srs_path, file_name],
            check=True, capture_output=True, text=True
        )
        log(f"  ✓ SRS: {srs_filename}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        log(f"  ✗ SRS 编译失败: {srs_filename} - {e}")
    except Exception as e:
        log(f"  ✗ SRS 错误: {srs_filename} - {e}")


def sort_dict(obj: Any) -> Any:
    """保持原有顺序，仅将 'version' 键置顶"""
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        if "version" in obj:
            result["version"] = sort_dict(obj["version"])
        for k in obj:
            if k != "version":
                result[k] = sort_dict(obj[k])
        return result
    elif isinstance(obj, list):
        return [sort_dict(x) for x in obj]
    else:
        return obj


def _group_by_mapped(
    rows: list[dict[str, str | None]], map_dict: dict[str, str]
) -> dict[str, list[str]]:
    """将行列表按 mapped_pattern 分组，返回 {mapped_pattern: [address, ...]}"""
    groups: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    for row in rows:
        pattern: str = row['pattern']  # type: ignore[assignment]
        if pattern not in map_dict:
            continue
        if '#' in pattern:
            continue
        address: str = row['address'].strip()  # type: ignore[union-attr]
        mapped: str = map_dict[pattern]
        if pattern == 'PROCESS-NAME':
            mapped = 'package_name' if is_android_package_name(address) else 'process_name'
        key = (mapped, address)
        if key in seen:
            continue
        seen.add(key)
        groups.setdefault(mapped, []).append(address)
    return groups


_MAP_DICT: dict[str, str] = {
    'DOMAIN-SUFFIX': 'domain_suffix',
    'HOST-SUFFIX': 'domain_suffix',
    'DOMAIN': 'domain',
    'HOST': 'domain',
    'host': 'domain',
    'DOMAIN-KEYWORD': 'domain_keyword',
    'HOST-KEYWORD': 'domain_keyword',
    'host-keyword': 'domain_keyword',
    'IP-CIDR': 'ip_cidr',
    'ip-cidr': 'ip_cidr',
    'IP-CIDR6': 'ip_cidr',
    'IP6-CIDR': 'ip_cidr',
    'SRC-IP-CIDR': 'source_ip_cidr',
    'GEOIP': 'geoip',
    'DST-PORT': 'port',
    'SRC-PORT': 'source_port',
    'URL-REGEX': 'domain_regex',
    'PROCESS-NAME': 'process_name',
}


def parse_list_file(link: str, output_directory: str) -> str | None:
    """解析规则链接并生成 JSON/SRS 文件"""
    os.makedirs(output_directory, exist_ok=True)
    file_name = os.path.join(output_directory, f"{os.path.basename(link).split('.')[0]}.json")

    # 如果是 .json 链接，仅处理 sing-box 规则集格式，失败不继续
    if link.endswith('.json'):
        try:
            json_data = read_json_from_url(link)
            if is_singbox_ruleset(json_data):
                with open(file_name, 'w', encoding='utf-8') as output_file:
                    json.dump(sort_dict(json_data), output_file, ensure_ascii=False, indent=2)
                _compile_srs(file_name)
                return file_name
            else:
                log(f"  ⚠ {link} 不是 sing-box 规则集格式，跳过")
                return None
        except Exception as e:
            log(f"  ✗ 处理 JSON 文件失败 {link}: {e}")
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
            port_numbers = [_try_int(a) for a in addresses]
            result_rules["rules"].append({mapped: port_numbers})
        else:
            result_rules["rules"].append({mapped: addresses})

    domain_entries = list(set(domain_entries))
    if domain_entries:
        result_rules["rules"].insert(0, {'domain': domain_entries})

    with open(file_name, 'w', encoding='utf-8') as output_file:
        json.dump(sort_dict(result_rules), output_file, ensure_ascii=False, indent=2)

    _compile_srs(file_name)
    return file_name


def main() -> None:
    with open("ruleset/ruleset_source.txt", 'r', encoding='utf-8') as links_file:
        links = links_file.read().splitlines()

    links = [l for l in links if l.strip() and not l.strip().startswith("#")]

    output_dir = "./ruleset/json/"
    result_file_names: list[str] = []

    for link in links:
        try:
            result_file_name = parse_list_file(link, output_directory=output_dir)
            if result_file_name:
                result_file_names.append(result_file_name)
        except Exception as e:
            log(f"✗ 跳过 {link}: {e}")

    for file_name in result_file_names:
        log(file_name)


if __name__ == "__main__":
    main()
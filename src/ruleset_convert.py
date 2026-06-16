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

HTTP_TIMEOUT = 30
HTTP_RETRY = 3

def _http_get(url, **kwargs):
    """HTTP GET with timeout and retry"""
    kwargs.setdefault("timeout", HTTP_TIMEOUT)
    last_error = None
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

def read_json_from_url(url):
    """下载并解析 sing-box JSON 格式的规则集"""
    response = _http_get(url)
    json_data = json.loads(response.text)
    return json_data

def is_singbox_ruleset(json_data):
    """判断 JSON 数据是否为 sing-box 规则集格式"""
    if isinstance(json_data, dict):
        return 'version' in json_data and 'rules' in json_data
    return False

def read_yaml_from_url(url):
    """下载并解析 YAML 格式的规则集"""
    response = _http_get(url)
    yaml_data = yaml.safe_load(response.text)
    return yaml_data

def read_list_from_url(url):
    """使用标准库 csv 读取规则列表，返回 list[dict]"""
    response = _http_get(url)
    reader = csv.reader(StringIO(response.text))
    rows = []
    for row in reader:
        if not row or not row[0].strip():
            continue
        pattern = row[0].strip() if len(row) >= 1 else ""
        address = row[1].strip() if len(row) >= 2 else ""
        other = row[2].strip() if len(row) >= 3 else None
        if pattern and address:
            rows.append({'pattern': pattern, 'address': address, 'other': other})
    return rows

def is_ipv4_or_ipv6(address):
    try:
        ipaddress.IPv4Network(address, strict=False)
        return 'ipv4'
    except ValueError:
        try:
            ipaddress.IPv6Network(address, strict=False)
            return 'ipv6'
        except ValueError:
            return None

def is_android_package_name(text):
    """
    判断是否为安卓程序包名
    安卓包名通常符合以下特征：
    1. 包含点分隔符（如 com.example.app）
    2. 每部分以字母开头，包含字母、数字、下划线
    3. 通常以 com., org., net. 等常见域名开头
    
    同时排除其他系统的程序特征：
    - 以 .exe, .dll, .app, .dmg 等结尾的文件
    - 包含路径分隔符（/ 或 \）的文件路径
    - 其他明显不是包名的格式
    """
    if not text or not isinstance(text, str):
        return False
    
    # 排除明显是其他系统的程序
    other_system_extensions = ['.exe', '.dll', '.app', '.dmg', '.msi', '.deb', '.rpm', '.pkg']
    if any(text.lower().endswith(ext) for ext in other_system_extensions):
        return False
    
    # 排除包含路径分隔符的路径
    if '/' in text or '\\' in text:
        return False
    
    # 排除包含空格的文件名
    if ' ' in text:
        return False
    
    # 基本格式检查：包含点分隔符
    if '.' not in text:
        return False
    
    # 检查每部分是否符合包名规范
    parts = text.split('.')
    for part in parts:
        if not part:  # 空部分
            return False
        if not part[0].isalpha():  # 每部分必须以字母开头
            return False
        if not re.match(r'^[a-zA-Z0-9_]+$', part):  # 只包含字母、数字、下划线
            return False
    
    # 常见的包名前缀
    common_prefixes = ['com', 'org', 'net', 'edu', 'gov', 'mil', 'android', 'google']
    if parts[0] in common_prefixes:
        return True
    
    # 如果不符合常见前缀，但格式正确，也认为是包名
    return len(parts) >= 2  # 至少有两部分

def _try_int(v):
    """尝试将字符串转为 int，失败返回原值"""
    try:
        return int(v)
    except ValueError:
        return v

def parse_and_convert_to_rows(link):
    """下载并解析规则链接，返回 list[dict] (pattern, address, other)"""
    if link.endswith('.yaml') or link.endswith('.txt'):
        try:
            yaml_data = read_yaml_from_url(link)
            rows = []
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
        except Exception:
            return read_list_from_url(link)
    else:
        return read_list_from_url(link)

def _compile_srs(file_name, srs_dir="./ruleset/srs/"):
    """使用 subprocess 安全调用 sing-box 编译 SRS"""
    srs_filename = os.path.basename(file_name).replace(".json", ".srs")
    srs_path = os.path.join(srs_dir, srs_filename)
    os.makedirs(srs_dir, exist_ok=True)
    try:
        subprocess.run(
            ["sing-box", "rule-set", "compile", "--output", srs_path, file_name],
            check=True, capture_output=True, text=True
        )
        print(f"  ✓ SRS: {srs_filename}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"  ✗ SRS 编译失败: {srs_filename} - {e}")
    except Exception as e:
        print(f"  ✗ SRS 错误: {srs_filename} - {e}")

def sort_dict(obj):
    if isinstance(obj, dict):
        sorted_keys = sorted(obj.keys())
        if "version" in sorted_keys:
            sorted_keys.remove("version")
            sorted_keys.insert(0, "version")
        return {k: sort_dict(obj[k]) for k in sorted_keys}
    elif isinstance(obj, list) and all(isinstance(elem, dict) for elem in obj):
        return sorted([sort_dict(x) for x in obj], key=lambda d: sorted(d.keys())[0])
    elif isinstance(obj, list):
        return sorted(sort_dict(x) for x in obj)
    else:
        return obj

def _group_by_mapped(rows, map_dict):
    """将行列表按 mapped_pattern 分组，返回 {mapped_pattern: [address, ...]}"""
    groups = {}
    seen = set()
    for row in rows:
        pattern = row['pattern']
        if pattern not in map_dict:
            continue
        if '#' in pattern:
            continue
        address = row['address'].strip()
        mapped = map_dict[pattern]
        if pattern == 'PROCESS-NAME':
            mapped = 'package_name' if is_android_package_name(address) else 'process_name'
        key = (mapped, address)
        if key in seen:
            continue
        seen.add(key)
        groups.setdefault(mapped, []).append(address)
    return groups

def parse_list_file(link, output_directory):
    os.makedirs(output_directory, exist_ok=True)
    file_name = os.path.join(output_directory, f"{os.path.basename(link).split('.')[0]}.json")

    # 如果是 .json 链接，先检查是否为 sing-box 规则集格式
    if link.endswith('.json'):
        try:
            json_data = read_json_from_url(link)
            if is_singbox_ruleset(json_data):
                with open(file_name, 'w', encoding='utf-8') as output_file:
                    json.dump(sort_dict(json_data), output_file, ensure_ascii=False, indent=2)
                _compile_srs(file_name)
                return file_name
        except Exception as e:
            print(f"处理 JSON 文件失败 {link}: {e}")

    rows = parse_and_convert_to_rows(link)

    map_dict = {
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
        'PROCESS-NAME': 'process_name'
    }

    groups = _group_by_mapped(rows, map_dict)

    result_rules = {"version": 4, "rules": []}
    domain_suffix_set = set(groups.get('domain_suffix', []))

    domain_entries = []
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

def main():
    with open("ruleset/ruleset_source.txt", 'r') as links_file:
        links = links_file.read().splitlines()

    links = [l for l in links if l.strip() and not l.strip().startswith("#")]

    output_dir = "./ruleset/json/"
    result_file_names = []

    for link in links:
        try:
            result_file_name = parse_list_file(link, output_directory=output_dir)
            result_file_names.append(result_file_name)
        except Exception as e:
            print(f"✗ 跳过 {link}: {e}")

    for file_name in result_file_names:
        print(file_name)

if __name__ == "__main__":
    main()

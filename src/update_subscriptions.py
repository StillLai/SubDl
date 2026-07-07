#!/usr/bin/env python3
"""订阅下载、转换、合并与上传模块

主流程：
  1. 解析环境变量获取订阅列表
  2. 并行下载 + 验证（不做转换）
  3. 批量转换（单次 Node.js 进程）
  4. 合并配置、生成 README、上传 Gist
"""

import base64
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from utils import (
    log_info, log_warn, log_error, format_bin_error,
    load_jsonc, http_get_with_retry, http_request,
    FlowInfo, Subscription, DownloadResult, SubscriptionInfo, HttpResponse,
    PROJECT_ROOT, TEMPLATE_DIR, CONVERT_SCRIPT, USER_AGENT,
    SubDlError, ConfigError, DownloadError, ConversionError, TemplateError,
    UploadError,
)
from merge_config import merge_config
from svg import generate_status_svg, LIGHT_THEME, DARK_THEME


# ========== 常量 ==========

_B64_PATTERN: re.Pattern[str] = re.compile(r'^[A-Za-z0-9+/=\s]+$')
_WORKERS = int(os.environ.get('WORKERS', '8'))


# ========== Base64 解码 ==========

def _try_decode_base64(content: str) -> str:
    """尝试将内容作为 Base64 解码，失败则原样返回

    防护措施：
    - 短内容（< 100 字符）跳过检测，避免纯英文短文本被误判
    - 解码后检查可打印字符占比（≥ 90%），防止乱码内容被采纳
    """
    try:
        cleaned = ''.join(content.split())
        if len(cleaned) < 100:
            return content
        if cleaned and _B64_PATTERN.match(cleaned):
            cleaned += "=" * (-len(cleaned) % 4)
            decoded = base64.b64decode(cleaned).decode("utf-8")
            printable_ratio = sum(c.isprintable() or c in '\n\r\t' for c in decoded) / len(decoded)
            if printable_ratio >= 0.9:
                return decoded
    except Exception:
        pass
    return content


# ========== 流量解析 ==========

_FLOW_KEY_PATTERN: re.Pattern[str] = re.compile(r'(\w+)=(\d+)')


def parse_flow_info(headers: dict[str, str]) -> FlowInfo | None:
    """从响应头解析流量信息"""
    flow_header = headers.get('subscription-userinfo', '')
    if not flow_header:
        return None

    fields: dict[str, int | None] = {'upload': 0, 'download': 0, 'total': 0, 'expire': None}
    for match in _FLOW_KEY_PATTERN.finditer(flow_header):
        key, value = match.group(1), int(match.group(2))
        if key in fields:
            fields[key] = value

    return FlowInfo(
        upload=fields['upload'] or 0,
        download=fields['download'] or 0,
        total=fields['total'] or 0,
        expire=fields['expire'],
    )


# ========== 订阅解析与验证 ==========

_VALID_INDICATORS: tuple[str, ...] = (
    'proxies', 'proxy-providers', 'proxy-groups', 'servers',
    'outbounds', 'endpoints', 'vmess', 'trojan', 'ssid', 'wireguard',
)


def _validate_subscription(content: str) -> str | None:
    """验证订阅内容，返回失败原因或 None（有效）"""
    if not content or len(content.strip()) < 10:
        return "内容为空或过短"

    content_lower = content.lower().strip()
    if content_lower.startswith('<!doctype') or content_lower.startswith('<html'):
        return "返回的是网页而非订阅内容"

    if not any(indicator in content_lower for indicator in _VALID_INDICATORS):
        return "内容不包含有效的订阅配置"

    return None


def parse_subscriptions() -> list[Subscription]:
    """解析订阅配置 — 从 providers.jsonc 读取配置，环境变量提供纯 URL

    providers.jsonc 中的 url 字段为 $ENV_VAR 格式的占位符。
    环境变量前缀决定订阅模式：
      - SUB_URL / SUB_URL_N → 常规订阅（provider URL 指向原始订阅）
      - GIST_URL / GIST_URL_N → Gist 订阅（provider URL 指向 Gist 上已转换的文件）
    """
    providers_raw = load_jsonc(TEMPLATE_DIR / 'providers.jsonc')
    providers = providers_raw.get('providers', [])

    subscriptions: list[Subscription] = []
    for provider in providers:
        url_ref = provider.get('url', '')
        if not url_ref.startswith('$'):
            continue
        env_name = url_ref[1:]  # 去掉 $ 前缀
        url = os.environ.get(env_name, '').strip()
        if not url:
            continue

        use_gist = env_name.startswith('GIST_URL')
        tag = provider.get('tag', '')
        if not tag:
            log_warn(f"  providers.jsonc 中存在无 tag 的 provider，已跳过 (env: {env_name})")
            continue

        sub = Subscription.from_url(url, name=tag, use_gist=use_gist, env_name=env_name)
        subscriptions.append(sub)
    return subscriptions


# ========== 订阅下载 ==========

def _download_subscription(sub: Subscription, user_agent: str) -> DownloadResult:
    """下载单个订阅，返回结果"""
    try:
        response = http_get_with_retry(sub.url, headers={"User-Agent": user_agent})
        content = response.text

        decoded = _try_decode_base64(content)
        if decoded is not content:
            log_info(f"    {sub.name}: 内容已从 Base64 解码")
            content = decoded

        flow = parse_flow_info(response.headers)
        reason = _validate_subscription(content)
        if reason:
            return DownloadResult(name=sub.name, status="invalid", flow=flow, reason=reason, filename=sub.filename, env_name=sub.env_name)

        return DownloadResult(name=sub.name, status="ok", flow=flow, filename=sub.filename, raw_content=content, env_name=sub.env_name)

    except Exception as e:
        return DownloadResult(name=sub.name, status="error", reason=str(e), filename=sub.filename, env_name=sub.env_name)


def _download_all(subscriptions: list[Subscription], user_agent: str) -> list[DownloadResult]:
    """并行下载所有订阅，按原始订阅顺序返回结果"""
    log_info(f"→ 并行下载 {len(subscriptions)} 个订阅...")
    results: dict[int, DownloadResult] = {}

    with ThreadPoolExecutor(max_workers=min(len(subscriptions), _WORKERS)) as executor:
        futures = {
            executor.submit(_download_subscription, sub, user_agent): (i, sub)
            for i, sub in enumerate(subscriptions)
        }
        for future in as_completed(futures):
            i, sub = futures[future]
            result = future.result()
            results[i] = result
            if result.is_success:
                log_info(f"  ✓ {result.name}: 下载成功")
            else:
                log_warn(f"  ✗ {result.name}: {result.reason}")

    return [results[i] for i in range(len(subscriptions))]


def _fetch_from_gist_fallback(sub_name: str, gist_id: str, gist_owner: str, env_name: str = "") -> DownloadResult | None:
    """当原始订阅下载失败时，尝试从 Gist 备份获取已转换的 sing-box 配置

    地址格式: https://gist.github.com/{gist_owner}/{gist_id}/raw/{sub_name}-singbox.json
    """
    url = f"https://gist.github.com/{gist_owner}/{gist_id}/raw/{sub_name}-singbox.json"
    try:
        log_info(f"  → 尝试从 Gist 备份获取: {sub_name}")
        response = http_get_with_retry(url)
        content = response.text
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or ('outbounds' not in parsed and 'endpoints' not in parsed):
            log_warn(f"  Gist fallback 内容格式不正确 ({sub_name})")
            return None
        log_info(f"  ✓ {sub_name}: 从 Gist 备份获取成功")
        return DownloadResult(
            name=sub_name, status="ok", filename=f"{sub_name}-singbox.json",
            raw_content=content, is_converted=True, env_name=env_name,
        )
    except Exception as e:
        log_warn(f"  Gist fallback 失败 ({sub_name}): {e}")
        return None


def _try_gist_fallback(
    download_results: list[DownloadResult],
    gist_id: str,
    gist_owner: str,
) -> list[DownloadResult]:
    """对下载失败的订阅尝试从 Gist 备份获取（并行），成功则替换原结果"""
    failed_indices = [
        i for i, r in enumerate(download_results)
        if not r.is_success
    ]
    if not failed_indices:
        return download_results

    log_info(f"→ {len(failed_indices)} 个订阅下载失败，尝试 Gist fallback（并行）...")

    with ThreadPoolExecutor(max_workers=min(len(failed_indices), _WORKERS)) as executor:
        futures = {
            executor.submit(_fetch_from_gist_fallback, download_results[i].name, gist_id, gist_owner, download_results[i].env_name): i
            for i in failed_indices
        }
        for future in as_completed(futures):
            i = futures[future]
            fallback = future.result()
            if fallback is not None:
                download_results[i] = fallback

    return download_results


# ========== 批量转换 ==========

def _convert_batch(contents_dict: dict[str, str]) -> dict[str, dict[str, Any] | None]:
    """批量转换：将所有 Clash 内容一次性传给单个 Node.js 进程"""
    if not contents_dict:
        return {}

    batch_input_file: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', suffix='.json', delete=False, encoding='utf-8'
        ) as f:
            json.dump(contents_dict, f, ensure_ascii=False)
            batch_input_file = f.name

        log_info(f"  → 批量转换 {len(contents_dict)} 个订阅（单进程）...")
        result = subprocess.run(
            ['node', str(CONVERT_SCRIPT), 'batch-convert', batch_input_file],
            capture_output=True, text=True, encoding='utf-8'
        )

        if result.returncode != 0:
            raise ConversionError(
                f"批量转换失败 (exit {result.returncode}): {result.stderr}",
                context={"subscriptions": ", ".join(contents_dict.keys())},
            )

        stdout = result.stdout.strip()
        if not stdout:
            raise ConversionError(
                f"批量转换输出为空 (stderr: {result.stderr.strip() or '(无)'})",
                context={"subscriptions": ", ".join(contents_dict.keys())},
            )

        raw_result = json.loads(stdout)
        return {
            name: val if isinstance(val, dict) else None
            for name, val in ((n, raw_result.get(n)) for n in contents_dict)
        }

    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError(
            f"批量转换异常: {e}",
            context={"subscriptions": ", ".join(contents_dict.keys())},
        ) from e
    finally:
        if batch_input_file:
            os.unlink(batch_input_file)


# ========== 结果聚合 ==========


def _get_singbox_nodes(config: dict[str, Any]) -> list[dict[str, Any]]:
    """从 sing-box 配置中提取所有节点（outbounds + endpoints）"""
    return config.get('outbounds', []) + config.get('endpoints', [])


def _aggregate_results(
    download_results: list[DownloadResult],
) -> tuple[dict[str, str], list[SubscriptionInfo], dict[str, list[dict[str, Any]]]]:
    """聚合下载结果：批量转换 → 收集文件、订阅信息和节点字典

    对于 is_converted=True 的结果（来自 Gist 备份），跳过转换直接使用。
    """
    contents_to_convert = {
        r.name: r.raw_content
        for r in download_results
        if r.is_success and r.raw_content and not r.is_converted
    }
    converted = _convert_batch(contents_to_convert) if contents_to_convert else {}

    files: dict[str, str] = {}
    subscription_info: list[SubscriptionInfo] = []
    subs_nodes_dict: dict[str, list[dict[str, Any]]] = {}
    skipped: list[str] = []

    for result in download_results:
        if not result.is_success:
            skipped.append(f"{result.name} ({result.reason})")
            subscription_info.append(SubscriptionInfo(
                name=result.name, flow=result.flow, node_count=0, env_name=result.env_name,
            ))
            continue

        if result.raw_content is None:
            skipped.append(f"{result.name} (原始内容为空)")
            continue

        if result.is_converted:
            files[result.filename] = result.raw_content
            try:
                singbox_config = json.loads(result.raw_content)
            except json.JSONDecodeError:
                skipped.append(f"{result.name} (Gist 备份 JSON 解析失败)")
                subscription_info.append(SubscriptionInfo(
                    name=result.name, flow=result.flow, node_count=0, env_name=result.env_name,
                ))
                continue

            singbox_nodes = _get_singbox_nodes(singbox_config)
            node_count = len(singbox_nodes)

            if node_count > 0:
                subs_nodes_dict[result.name] = singbox_nodes
                log_info(f"  ✓ {result.name}: 使用 Gist 备份 ({len(result.raw_content)} 字节, {node_count} 个节点)")
            else:
                skipped.append(f"{result.name} (Gist 备份空节点)")

            subscription_info.append(SubscriptionInfo(
                name=result.name, flow=result.flow, node_count=node_count, from_backup=True, env_name=result.env_name,
            ))
            continue

        files[result.filename] = result.raw_content

        singbox_config = converted.get(result.name)
        if singbox_config is None:
            skipped.append(f"{result.name} (转换失败)")
            subscription_info.append(SubscriptionInfo(
                name=result.name, flow=result.flow, node_count=0, env_name=result.env_name,
            ))
            continue

        singbox_nodes = _get_singbox_nodes(singbox_config)
        node_count = len(singbox_nodes)
        singbox_content = json.dumps(singbox_config, indent=2, ensure_ascii=False)
        files[f"{result.name}-singbox.json"] = singbox_content

        if node_count > 0:
            subs_nodes_dict[result.name] = singbox_nodes
            log_info(f"  ✓ {result.name}: 转换成功 ({len(singbox_content)} 字节, {node_count} 个节点)")
        else:
            skipped.append(f"{result.name} (空节点)")

        subscription_info.append(SubscriptionInfo(
            name=result.name, flow=result.flow, node_count=node_count, env_name=result.env_name,
        ))

    if skipped:
        log_warn(f"⚠️ {len(skipped)} 个订阅跳过: {', '.join(skipped)}")

    return files, subscription_info, subs_nodes_dict


# ========== 模板处理 ==========

def _load_templates() -> list[tuple[str, dict[str, Any]]]:
    """从模块化零件文件组装配置模板

    读取 config_template/ 下的公共零件（base/dns/providers/outbounds/route）
    和 inbounds/ 下的变体文件，组装成完整的配置模板。
    输出文件名自动推导为 sing-box-{变体名}。
    """
    inbounds_dir = TEMPLATE_DIR / 'inbounds'
    if not inbounds_dir.is_dir():
        raise TemplateError(
            f"inbounds 目录不存在: {inbounds_dir}",
            context={"path": str(inbounds_dir)},
        )

    # 加载公共零件（只读一次）
    # dns/route/outbounds 带 $schema 包装，需解包；providers 仅带 key 包装
    try:
        base = load_jsonc(TEMPLATE_DIR / 'base.jsonc')

        dns_raw = load_jsonc(TEMPLATE_DIR / 'dns.jsonc')
        dns_raw.pop('$schema', None)
        dns = dns_raw['dns']

        providers_raw = load_jsonc(TEMPLATE_DIR / 'providers.jsonc')
        providers = providers_raw['providers']

        outbounds_raw = load_jsonc(TEMPLATE_DIR / 'outbounds.jsonc')
        outbounds_raw.pop('$schema', None)
        outbounds = outbounds_raw['outbounds']

        route_raw = load_jsonc(TEMPLATE_DIR / 'route.jsonc')
        route_raw.pop('$schema', None)
        route = route_raw['route']
    except Exception as e:
        raise TemplateError(
            f"公共零件加载失败: {e}",
            context={"directory": str(TEMPLATE_DIR)},
        ) from e

    templates: list[tuple[str, dict[str, Any]]] = []
    for f in sorted(inbounds_dir.iterdir()):
        if f.suffix not in ('.json', '.jsonc'):
            continue
        variant = f.stem
        try:
            inbounds_raw = load_jsonc(f)
            inbounds_raw.pop('$schema', None)
            inbounds = inbounds_raw.get('inbounds', inbounds_raw)
            config: dict[str, Any] = {
                **base,
                "inbounds": inbounds,
                "dns": dns,
                "providers": providers,
                "outbounds": outbounds,
                "route": route,
            }
            templates.append((f'sing-box-{variant}', config))
        except Exception as e:
            raise TemplateError(
                f"模板组装失败 {f.name}: {e}",
                context={"file": str(f)},
            ) from e

    return templates


def merge_all_templates(
    subs_nodes_dict: dict[str, list[dict[str, Any]]],
    templates: list[tuple[str, dict[str, Any]]],
) -> dict[str, str]:
    """遍历所有模板文件并生成合并配置"""
    total_nodes = sum(len(nodes) for nodes in subs_nodes_dict.values())
    results: dict[str, str] = {}
    failed = 0
    for base_name, template in templates:
        config_filename = base_name + '.json'
        log_info(f"  → 处理模板: {base_name}.jsonc")
        try:
            merged = merge_config(template, subs_nodes_dict)
        except Exception as e:
            log_error(f"  合并异常: {e}")
            failed += 1
            continue
        content = json.dumps(merged, indent=2, ensure_ascii=False)
        log_info(f"    ✓ 生成 {config_filename} ({len(content)} 字节, {total_nodes} 个节点)")
        results[config_filename] = content
    if failed:
        log_warn(f"  ⚠️ {len(results)}/{len(templates)} 个模板成功，{failed} 个失败")
    return results


def generate_provider_configs(
    subscriptions: list[Subscription],
    templates: list[tuple[str, dict[str, Any]]],
    gist_owner: str = '',
    gist_id: str = '',
) -> dict[str, str]:
    """生成 providers 版本的配置文件

    将模板中 provider 的 $ENV_VAR 占位符替换为真实 URL。
    use_gist 的订阅指向 Gist 上已转换的 sing-box 文件，其余指向原始订阅。

    Args:
        subscriptions: 订阅列表（含 env_name、use_gist 等信息）
        templates: 模板列表
        gist_owner: Gist 所有者用户名
        gist_id: Gist ID
    """
    sub_by_env = {sub.env_name: sub for sub in subscriptions}

    results: dict[str, str] = {}
    for base_name, template in templates:
        try:
            # deep copy 避免原地修改 $ENV_VAR 占位符影响共享 providers 列表
            template_copy = copy.deepcopy(template)
            filled = 0
            for provider in template_copy.get('providers', []):
                url_ref = provider.get('url', '')
                if not url_ref.startswith('$'):
                    continue
                env_name = url_ref[1:]
                sub = sub_by_env.get(env_name)
                if sub is None:
                    continue

                if sub.use_gist and gist_owner and gist_id:
                    provider['url'] = f"https://ghfast.top/https://gist.github.com/{gist_owner}/{gist_id}/raw/{sub.name}-singbox.json"
                else:
                    provider['url'] = sub.url
                filled += 1
            if filled > 0:
                config_filename = base_name + '-providers.json'
                log_info(f"  → {base_name}.jsonc -> {config_filename} ({filled} 个 providers)")
                results[config_filename] = json.dumps(template_copy, indent=2, ensure_ascii=False)
        except Exception as e:
            log_error(f"  Provider 配置生成异常 {base_name}: {e}")
    return results


# ========== 版本获取 ==========

def _fetch_version(repo: str, headers: dict[str, str]) -> str:
    """获取单个仓库的最新 release 版本"""
    try:
        resp = http_get_with_retry(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers=headers,
        )
        return json.loads(resp.text).get('tag_name', '未知')
    except Exception as e:
        log_warn(f"获取 {repo} 版本失败: {e}")
        return '获取失败'


def fetch_latest_versions() -> dict[str, str]:
    """通过 GitHub API 并行获取 sing-box 官方版和 reF1nd 分支的最新 release 版本"""
    repos = {
        'official': 'SagerNet/sing-box',
        'reF1nd': 'reF1nd/sing-box-releases',
    }
    headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
    gh_token = os.environ.get("GH_TOKEN")
    if gh_token:
        headers["Authorization"] = f"token {gh_token}"

    versions: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(_fetch_version, repo, headers): key for key, repo in repos.items()}
        for future in as_completed(futures):
            versions[futures[future]] = future.result()
    return versions


# ========== 配置校验 ==========

def _validate_configs(files: dict[str, str]) -> dict[str, str]:
    """用 sing-box check 校验配置文件

    非 providers 配置用 SING_BOX_BIN（官方版），providers 配置用 SING_BOX_REF1ND_BIN。
    校验失败的文件不上传到 Gist。

    Returns:
        校验失败的文件字典 {filename: error_msg}，空 dict 表示全部通过。
    """
    official_bin = os.environ.get('SING_BOX_BIN', '')
    ref1nd_bin = os.environ.get('SING_BOX_REF1ND_BIN', '')
    if not official_bin:
        raise ConfigError(
            "SING_BOX_BIN 未设置，无法进行配置校验",
            context={"env": "SING_BOX_BIN"},
        )

    # 检查二进制文件是否存在
    if not os.path.isfile(official_bin):
        raise ConfigError(
            f"SING_BOX_BIN ({official_bin}) 不存在，无法进行配置校验",
            context={"path": official_bin},
        )

    failures: dict[str, str] = {}
    checked = 0
    for filename, content in files.items():
        if not filename.startswith('sing-box') or not filename.endswith('.json'):
            continue

        is_providers = '-providers.json' in filename
        bin_path = ref1nd_bin if is_providers and ref1nd_bin and os.path.isfile(ref1nd_bin) else official_bin

        tmp_file: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False, encoding='utf-8'
            ) as f:
                f.write(content)
                tmp_file = f.name

            result = subprocess.run(
                [bin_path, 'check', '-c', tmp_file],
                capture_output=True, text=True, timeout=30
            )
            checked += 1
            if result.returncode != 0:
                err = result.stderr.strip()[:200] or '未知错误'
                failures[filename] = err
                log_error(f"  ✗ {filename}: 校验失败 — {err}")
            else:
                log_info(f"  ✓ {filename}: 校验通过")
        except FileNotFoundError as e:
            log_warn(f"  ✗ {filename}: 校验异常 — {format_bin_error(e, bin_path)}")
        except Exception as e:
            log_warn(f"  ✗ {filename}: 校验异常 — {e}")
        finally:
            if tmp_file:
                os.unlink(tmp_file)

    if failures:
        log_warn(f"  ⚠️ {len(failures)}/{checked} 个配置校验失败")
    else:
        log_info(f"  ✓ 全部 {checked} 个配置校验通过")

    return failures


# ========== Gist 上传 ==========

# SubDl 托管的文件名匹配模式（用于安全清理旧文件）
_MANAGED_FILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'^.+\.yaml$'),              # 原始订阅: 山海.yaml
    re.compile(r'^.+-singbox\.json$'),       # 转换节点: 山海-singbox.json
    re.compile(r'^sing-box.*\.json$'),       # 合并配置: sing-box.json, sing-box-mixed.json, sing-box-tun-win-providers.json 等
)


def _is_managed_file(filename: str) -> bool:
    """判断文件名是否属于 SubDl 托管的文件"""
    return any(p.match(filename) for p in _MANAGED_FILE_PATTERNS)


def _check_rate_limit(response: HttpResponse, api_name: str) -> None:
    """检查 GitHub API 速率限制，配额不足时发出警告"""
    remaining = response.headers.get('x-ratelimit-remaining')
    if remaining is not None and int(remaining) < 10:
        reset_ts = response.headers.get('x-ratelimit-reset', '0')
        try:
            reset_time = datetime.fromtimestamp(int(reset_ts)).strftime('%H:%M:%S') if reset_ts.isdigit() else '未知'
        except (ValueError, OSError):
            reset_time = '未知'
        log_warn(f"  ⚠️ GitHub API 速率限制即将耗尽 ({api_name}): 剩余 {remaining} 次，重置时间 {reset_time}")


def _fetch_gist_filenames(github_token: str, gist_id: str) -> list[str]:
    """获取 Gist 中当前所有文件名，失败时返回空列表"""
    try:
        headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
        resp = http_request("GET", f"https://api.github.com/gists/{gist_id}", headers=headers)
        if resp.status_code >= 400:
            log_warn(f"  获取 Gist 文件列表失败: HTTP {resp.status_code}")
            return []
        _check_rate_limit(resp, "获取 Gist 文件列表")
        data = json.loads(resp.text)
        return list(data.get('files', {}).keys())
    except Exception as e:
        log_warn(f"  获取 Gist 文件列表失败: {e}")
        return []


def _cleanup_old_gist_files(
    github_token: str,
    gist_id: str,
    current_files: dict[str, str],
) -> dict[str, None]:
    """获取 Gist 中已有的托管文件，将本次不在上传集合中的旧文件标记为删除

    Returns:
        需要删除的文件字典 {filename: None}，可直接合并到上传 payload
    """
    existing_names = _fetch_gist_filenames(github_token, gist_id)
    if not existing_names:
        return {}

    current_keys = set(current_files.keys())
    to_delete: dict[str, None] = {}

    for name in existing_names:
        if _is_managed_file(name) and name not in current_keys:
            to_delete[name] = None

    return to_delete


def upload_to_gist(github_token: str, gist_id: str, files: dict[str, str | None]) -> None:
    """上传文件到 GitHub Gist（GIST_ID 必须已在 Secrets 中配置）

    files 中值为 None 的条目会被删除（GitHub API 用 null 表示删除）
    """
    headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
    gist_files: dict[str, dict[str, str] | None] = {name: None if content is None else {"content": content} for name, content in files.items()}

    log_info(f"    更新 Gist: {gist_id}")
    resp = http_request("PATCH", f"https://api.github.com/gists/{gist_id}", headers=headers, json_body={"files": gist_files})
    _check_rate_limit(resp, "Gist 上传")
    if resp.status_code >= 400:
        raise UploadError(
            f"Gist 上传失败: HTTP {resp.status_code}",
            context={"gist_id": gist_id, "response": resp.text[:200]},
        )
    log_info("    ✓ 更新成功")


# ========== 主流程 ==========

def _get_gist_owner(github_token: str) -> str:
    """通过 GitHub API 获取当前认证用户的用户名"""
    headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
    resp = http_request("GET", "https://api.github.com/user", headers=headers)
    _check_rate_limit(resp, "获取 Gist 所有者")
    if resp.status_code >= 400:
        raise UploadError(
            f"获取 Gist 所有者失败: HTTP {resp.status_code}",
            context={"api": "GET /user"},
        )
    return json.loads(resp.text)["login"]


def _generate_and_upload(
    files: dict[str, str],
    subs_nodes_dict: dict[str, list[dict[str, Any]]],
    subscriptions: list[Subscription],
    subscription_info: list[SubscriptionInfo],
    github_token: str,
    gist_id: str,
    gist_owner: str,
) -> int:
    """生成合并配置、providers 配置，并上传到 Gist

    Returns:
        校验失败的配置数量（0 表示全部通过）
    """
    log_info("→ 生成合并配置和 providers 配置...")
    templates = _load_templates()

    gist_subs = [sub for sub in subscriptions if sub.use_gist]
    if gist_subs:
        log_info(f"  → {len(gist_subs)} 个订阅的 provider 将指向 Gist: {gist_owner}/{gist_id}")

    merged = merge_all_templates(subs_nodes_dict, templates)
    files.update(merged)
    if merged:
        log_info(f"  ✓ 共生成 {len(merged)} 个配置文件")

    providers = generate_provider_configs(subscriptions, templates, gist_owner, gist_id)
    files.update(providers)
    if providers:
        log_info(f"  ✓ 共生成 {len(providers)} 个 providers 配置文件")

    versions = fetch_latest_versions()
    log_info(f"  → sing-box 版本: 官方 {versions.get('official', '?')} | reF1nd {versions.get('reF1nd', '?')}")

    svg_light = generate_status_svg(subscription_info, versions, LIGHT_THEME)
    svg_dark = generate_status_svg(subscription_info, versions, DARK_THEME)
    (PROJECT_ROOT / "status-light.svg").write_text(svg_light, encoding="utf-8")
    (PROJECT_ROOT / "status-dark.svg").write_text(svg_dark, encoding="utf-8")
    log_info("✓ 状态 SVG 已生成（浅色 + 深色）")

    # 校验配置文件（校验失败的移除后上传剩余，最终以非零退出码告警）
    failures = _validate_configs(files)
    failure_count = len(failures)
    if failures:
        for name in failures:
            files.pop(name, None)
        log_warn(f"⚠️ 已从上传中移除 {failure_count} 个校验失败的配置")

    # 清理 Gist 中不再需要的旧文件
    to_delete = _cleanup_old_gist_files(github_token, gist_id, files)
    if to_delete:
        log_info(f"→ 清理 {len(to_delete)} 个旧文件: {', '.join(to_delete.keys())}")

    upload_payload: dict[str, str | None] = dict(files)
    upload_payload.update(to_delete)

    log_info(f"上传 {len(files)} 个文件到 Gist...")
    upload_to_gist(github_token, gist_id, upload_payload)

    return failure_count


def main() -> None:
    """主入口

    所有 SubDlError 子类异常在此统一捕获，输出结构化错误报告后以非零退出码退出。
    未预期的异常也会被捕获并输出。
    """
    log_info("=" * 60)
    log_info("SubDl - Subscription Downloader")
    log_info(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log_info("=" * 60)

    try:
        github_token = os.environ.get("GH_TOKEN")
        if not github_token:
            raise ConfigError("环境变量 GH_TOKEN 未设置", context={"env": "GH_TOKEN"})
        gist_id = os.environ.get("GIST_ID", "")
        if not gist_id:
            raise ConfigError(
                "环境变量 GIST_ID 未设置，请在 GitHub Secrets 中手动配置",
                context={"env": "GIST_ID"},
            )
        gist_owner = _get_gist_owner(github_token)
        log_info(f"Gist 所有者: {gist_owner}")

        subscriptions = parse_subscriptions()
        if not subscriptions:
            raise ConfigError("未找到订阅配置（providers.jsonc 中无有效的 $SUB_URL / $GIST_URL 引用）")
        log_info(f"找到 {len(subscriptions)} 个订阅")

        log_info("::group::Download & Convert subscriptions")
        download_results = _download_all(subscriptions, USER_AGENT)
        log_info("::endgroup::")

        download_results = _try_gist_fallback(download_results, gist_id, gist_owner)

        valid_results = [r for r in download_results if r.is_success]
        if not valid_results:
            raise DownloadError("所有订阅下载失败或内容无效")
        log_info(f"✓ 有效订阅: {len(valid_results)}/{len(subscriptions)}")

        files, subscription_info, subs_nodes_dict = _aggregate_results(download_results)

        if not subs_nodes_dict:
            raise ConversionError("没有有效的订阅节点，将不上传配置文件")
        log_info(f"✓ 合并节点: {len(subs_nodes_dict)}/{len(subscriptions)}")

        log_info("::group::Generate configs & Upload to Gist")
        failure_count = _generate_and_upload(
            files, subs_nodes_dict, subscriptions, subscription_info,
            github_token, gist_id, gist_owner,
        )
        log_info("::endgroup::")

        log_info(f"完成! 成功处理 {len(valid_results)} 个订阅，共 {len(files)} 个文件")

        # 校验有失败时以非零退出码告警（CI 会标红），但配置仍已上传
        if failure_count > 0:
            log_warn(f"⚠️ {failure_count} 个配置校验失败，请检查配置模板")
            sys.exit(1)

    except SubDlError as e:
        error_type = type(e).__name__
        log_error(f"❌ [{error_type}] {e}")
        if e.context:
            for k, v in e.context.items():
                log_error(f"  {k}: {v}")
        sys.exit(1)
    except Exception as e:
        log_error(f"❌ 未预期的错误: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

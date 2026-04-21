#!/usr/bin/env python3
"""
Subscription Downloader and Gist Uploader

定期拉取 Clash 订阅并上传到 GitHub Gists。
"""

import os
import sys
import json
import base64
import re
import time
import gzip
import io
from urllib.parse import urlparse
from datetime import datetime

import requests


def get_env_var(name, default=None, required=False):
    """获取环境变量"""
    value = os.environ.get(name, default)
    if required and not value:
        raise ValueError(f"环境变量 {name} 未设置")
    return value


def decode_base64_content(content):
    """尝试解码 base64 编码的内容"""
    # 保存原始内容前100字符用于调试
    preview = content[:100].replace("\n", "\\n")
    print(f"  原始内容前100字符: {preview}")
    
    # 移除空白字符
    cleaned = content.strip().replace(" ", "").replace("\n", "").replace("\r", "")
    
    # 检查是否像 base64（只包含 base64 字符）
    if not re.match(r'^[A-Za-z0-9+/=]+$', cleaned):
        print(f"  内容不像 Base64，直接返回")
        return content
    
    print(f"  检测到 Base64 编码，尝试解码...")
    
    # 尝试解码
    try:
        # 补齐 base64 填充
        padding_needed = 4 - len(cleaned) % 4
        if padding_needed != 4:
            cleaned += "=" * padding_needed
        
        decoded_bytes = base64.b64decode(cleaned)
        
        # 尝试 UTF-8 解码
        try:
            decoded = decoded_bytes.decode("utf-8")
            print(f"  ✓ Base64 解码成功 (UTF-8)")
            return decoded
        except UnicodeDecodeError:
            # 可能包含二进制数据，尝试其他编码
            try:
                decoded = decoded_bytes.decode("utf-8-sig")
                print(f"  ✓ Base64 解码成功 (UTF-8-SIG)")
                return decoded
            except:
                # 可能是二进制内容，返回原始 base64 并记录警告
                print(f"  ⚠ 解码后的内容不是纯文本，返回原始内容")
                return content
                
    except Exception as e:
        print(f"  ✗ Base64 解码失败: {e}")
        return content


def try_decode_content(data):
    """尝试解码内容（处理 gzip 和 base64）"""
    # 首先尝试解压 gzip
    if isinstance(data, bytes) and len(data) > 2 and data[:2] == b'\x1f\x8b':
        print(f"  检测到 gzip 压缩，正在解压...")
        try:
            data = gzip.decompress(data)
            print(f"  ✓ gzip 解压成功")
        except Exception as e:
            print(f"  ✗ gzip 解压失败: {e}")
    
    # 转换为字符串
    if isinstance(data, bytes):
        # 尝试 UTF-8
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            # 尝试其他编码
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = data.decode('latin-1')  # 最后的尝试
    else:
        text = data
    
    # 尝试 base64 解码
    text = decode_base64_content(text)
    
    return text


def download_subscription(url, user_agent, timeout=30000):
    """下载订阅内容"""
    print(f"正在下载订阅: {url[:60]}...")
    
    headers = {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }
    
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=timeout / 1000,  # 转换为秒
            allow_redirects=True
        )
        response.raise_for_status()
        
        # 打印响应头信息
        print(f"  Content-Encoding: {response.headers.get('Content-Encoding', 'none')}")
        print(f"  Content-Type: {response.headers.get('Content-Type', 'unknown')}")
        
        # 获取原始二进制内容
        raw_content = response.content
        print(f"  原始数据类型: {type(raw_content)}, 长度: {len(raw_content)}")
        print(f"  原始数据前10字节(hex): {raw_content[:10].hex()}")
        
        # 尝试解码（处理 gzip 和 base64）
        content = try_decode_content(raw_content)
        
        # 检查内容是否为空
        if not content or len(content.strip()) == 0:
            raise ValueError("订阅内容为空")
        
        # 检查内容是否有效
        content_stripped = content.replace(" ", "").replace("\n", "").replace("\r", "")
        if len(content_stripped) == 0:
            raise ValueError("订阅内容为空")
        
        print(f"  ✓ 下载成功 ({len(content)} 字节)")
        return content
        
    except requests.exceptions.Timeout:
        raise TimeoutError(f"下载超时: {url}")
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"下载失败: {e}")


def parse_subscriptions():
    """解析订阅配置 - 从环境变量 SUB_URL 或 SUB_URL_1, SUB_URL_2... 读取
    格式: 名称|URL 或直接 URL
    """
    subscriptions = []
    
    # 首先检查 SUB_URL（单个订阅）
    sub_url = os.environ.get("SUB_URL", "").strip()
    if sub_url:
        subscriptions.append(parse_single_subscription(sub_url))
    
    # 然后检查 SUB_URL_1, SUB_URL_2...（多个订阅）
    index = 1
    while True:
        env_name = f"SUB_URL_{index}"
        sub_url = os.environ.get(env_name, "").strip()
        if not sub_url:
            break
        subscriptions.append(parse_single_subscription(sub_url))
        index += 1
    
    return [s for s in subscriptions if s]


def parse_single_subscription(value):
    """解析单个订阅配置
    格式1: 名称|URL → 使用指定名称
    格式2: URL → 自动从域名提取名称
    """
    value = value.strip()
    if not value:
        return None
    
    # 检查是否包含分隔符 |
    if "|" in value:
        parts = value.split("|", 1)
        name = parts[0].strip()
        url = parts[1].strip()
        if name and url:
            return {
                "name": name,
                "url": url,
                "filename": f"{name}.yaml"
            }
    
    # 没有分隔符，直接作为 URL 处理
    url = value
    name = extract_name_from_url(url)
    return {
        "name": name,
        "url": url,
        "filename": f"{name}.yaml"
    }


def extract_name_from_url(url):
    """从 URL 提取名称"""
    try:
        parsed = urlparse(url)
        # 使用域名作为名称
        domain = parsed.netloc.replace("www.", "").split(":")[0]
        # 移除特殊字符
        name = re.sub(r'[^a-zA-Z0-9_-]', '_', domain)
        return name[:50]  # 限制长度
    except:
        # 使用时间戳作为备用
        return f"sub_{int(time.time())}"


def upload_to_gist(github_token, gist_id, files, description="SubDl Subscriptions"):
    """上传文件到 Gist"""
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "SubDl/1.0"
    }
    
    # 准备文件数据
    gist_files = {}
    for filename, content in files.items():
        gist_files[filename] = {"content": content}
    
    # 如果没有指定 gist_id，创建新的 gist
    if not gist_id:
        print("创建新的 Gist...")
        url = "https://api.github.com/gists"
        data = {
            "description": description,
            "public": False,  # 私密 gist
            "files": gist_files
        }
        
        response = requests.post(
            url,
            headers=headers,
            json=data,
            timeout=30
        )
        response.raise_for_status()
        
        result = response.json()
        gist_id = result["id"]
        print(f"  ✓ Gist 创建成功: {gist_id}")
        print(f"  ✓ Gist URL: {result['html_url']}")
        return gist_id
    
    # 更新现有 gist
    print(f"更新 Gist: {gist_id}...")
    url = f"https://api.github.com/gists/{gist_id}"
    
    # 首先获取现有的 gist 信息
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code == 404:
        print("  ! 指定的 Gist ID 不存在，将创建新的 Gist")
        return upload_to_gist(github_token, None, files, description)
    
    response.raise_for_status()
    existing_gist = response.json()
    
    # 构建更新数据
    data = {"files": gist_files}
    
    response = requests.patch(
        url,
        headers=headers,
        json=data,
        timeout=30
    )
    response.raise_for_status()
    
    print(f"  ✓ Gist 更新成功")
    return gist_id


def main():
    """主函数"""
    print("=" * 60)
    print("SubDl - Subscription Downloader")
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    try:
        # 获取配置
        github_token = get_env_var("GH_TOKEN", required=True)
        gist_id = get_env_var("GIST_ID", default="")
        user_agent = get_env_var("USER_AGENT", default="clash-verge/v2.4.4")
        
        # 解析订阅配置
        subscriptions = parse_subscriptions()
        
        if not subscriptions:
            print("错误: 未找到有效的订阅配置")
            sys.exit(1)
        
        print(f"\n找到 {len(subscriptions)} 个订阅")
        print("-" * 60)
        
        # 下载所有订阅
        files = {}
        failed = []
        
        for sub in subscriptions:
            name = sub["name"]
            url = sub["url"]
            filename = sub["filename"]
            
            if not url:
                print(f"跳过 {name}: URL 为空")
                continue
            
            try:
                content = download_subscription(url, user_agent)
                files[filename] = content
            except Exception as e:
                print(f"  ✗ 下载失败: {e}")
                failed.append({"name": name, "url": url, "error": str(e)})
        
        print("-" * 60)
        
        if not files:
            print("错误: 所有订阅下载失败")
            sys.exit(1)
        
        # 添加上传时间戳文件
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        files[".last_update"] = f"Last updated: {timestamp}\n"
        
        # 如果存在失败记录，添加错误日志
        if failed:
            error_log = f"Failed subscriptions ({len(failed)}):\n\n"
            for item in failed:
                error_log += f"- {item['name']}: {item['error']}\n"
            files[".errors"] = error_log
        
        # 上传到 Gist
        print(f"\n上传 {len(files)} 个文件到 Gist...")
        new_gist_id = upload_to_gist(github_token, gist_id, files)
        
        # 如果创建了新的 gist，提示用户更新配置
        if new_gist_id != gist_id:
            print(f"\n" + "=" * 60)
            print("重要提示: 已创建新的 Gist")
            print(f"请在 Repository secrets 中设置 GIST_ID = {new_gist_id}")
            print("=" * 60)
        
        print(f"\n完成! 成功处理 {len(files) - 1 - (1 if failed else 0)} 个订阅")
        print(f"更新时间: {timestamp}")
        
        # 设置输出变量（供后续步骤使用）
        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write(f"gist_id={new_gist_id}\n")
                f.write(f"success_count={len(files) - 1 - (1 if failed else 0)}\n")
        
        # 如果有失败，以非零退出码退出
        if failed:
            print(f"\n警告: {len(failed)} 个订阅下载失败")
            sys.exit(2)  # 部分成功
            
    except Exception as e:
        print(f"\n错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
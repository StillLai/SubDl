#!/usr/bin/env python3
"""
Subscription Downloader and Gist Uploader

定期拉取 Clash 订阅并上传到 GitHub Gists。
"""

import os
import sys
import base64
import re
import time
from urllib.parse import urlparse
from datetime import datetime

import requests


def get_env_var(name, default=None, required=False):
    """获取环境变量"""
    value = os.environ.get(name, default)
    if required and not value:
        raise ValueError(f"环境变量 {name} 未设置")
    return value


def download_subscription(url, user_agent, timeout=30000):
    """下载订阅内容"""
    print(f"正在下载订阅: {url[:60]}...")
    
    headers = {
        "User-Agent": user_agent,
    }
    
    try:
        # requests 会自动处理 gzip/br 解压
        response = requests.get(
            url,
            headers=headers,
            timeout=timeout / 1000,
            allow_redirects=True
        )
        response.raise_for_status()
        
        content = response.text
        
        # 尝试 base64 解码（有些订阅是 base64 编码的）
        try:
            cleaned = content.strip().replace(" ", "").replace("\n", "").replace("\r", "")
            if re.match(r'^[A-Za-z0-9+/=]+$', cleaned):
                decoded = base64.b64decode(cleaned + "=" * (4 - len(cleaned) % 4))
                content = decoded.decode("utf-8")
                print(f"  ✓ Base64 解码成功")
        except Exception:
            pass  # 不是 base64，保持原样
        
        print(f"  ✓ 下载成功 ({len(content)} 字节)")
        return content
        
    except requests.exceptions.Timeout:
        raise TimeoutError(f"下载超时: {url}")
    except requests.exceptions.RequestException as e:
        raise ConnectionError(f"下载失败: {e}")


def parse_subscriptions():
    """解析订阅配置"""
    subscriptions = []
    
    # 检查 SUB_URL, SUB_URL_1, SUB_URL_2...
    for env_name in ["SUB_URL"] + [f"SUB_URL_{i}" for i in range(1, 10)]:
        value = os.environ.get(env_name, "").strip()
        if not value:
            continue
            
        # 格式: 名称|URL 或 直接 URL
        if "|" in value:
            name, url = value.split("|", 1)
            name, url = name.strip(), url.strip()
        else:
            url = value
            name = extract_name_from_url(url)
        
        if name and url:
            subscriptions.append({
                "name": name,
                "url": url,
                "filename": f"{name}.yaml"
            })
    
    return subscriptions


def extract_name_from_url(url):
    """从 URL 提取名称"""
    try:
        domain = urlparse(url).netloc.replace("www.", "").split(":")[0]
        name = re.sub(r'[^a-zA-Z0-9_-]', '_', domain)
        return name[:50]
    except:
        return f"sub_{int(time.time())}"


def upload_to_gist(github_token, gist_id, files):
    """上传文件到 Gist"""
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    
    gist_files = {filename: {"content": content} for filename, content in files.items()}
    
    if not gist_id:
        print("创建新的 Gist...")
        response = requests.post(
            "https://api.github.com/gists",
            headers=headers,
            json={
                "description": "SubDl Subscriptions",
                "public": False,
                "files": gist_files
            },
            timeout=30
        )
        result = response.json()
        print(f"  ✓ Gist 创建成功: {result['id']}")
        return result["id"]
    
    print(f"更新 Gist: {gist_id}...")
    response = requests.patch(
        f"https://api.github.com/gists/{gist_id}",
        headers=headers,
        json={"files": gist_files},
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
        github_token = get_env_var("GH_TOKEN", required=True)
        gist_id = get_env_var("GIST_ID", default="")
        user_agent = get_env_var("USER_AGENT", default="clash-verge/v2.4.4")
        
        subscriptions = parse_subscriptions()
        
        if not subscriptions:
            print("错误: 未找到有效的订阅配置")
            sys.exit(1)
        
        print(f"\n找到 {len(subscriptions)} 个订阅")
        print("-" * 60)
        
        files = {}
        failed = []
        
        for sub in subscriptions:
            try:
                content = download_subscription(sub["url"], user_agent)
                files[sub["filename"]] = content
            except Exception as e:
                print(f"  ✗ 下载失败: {e}")
                failed.append({"name": sub["name"], "error": str(e)})
        
        print("-" * 60)
        
        if not files:
            print("错误: 所有订阅下载失败")
            sys.exit(1)
        
        # 添加时间戳
        files[".last_update"] = f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        
        # 上传
        print(f"\n上传 {len(files)} 个文件到 Gist...")
        new_gist_id = upload_to_gist(github_token, gist_id, files)
        
        if new_gist_id != gist_id:
            print(f"\n重要提示: 已创建新的 Gist ID: {new_gist_id}")
            print("请在 Repository secrets 中设置 GIST_ID")
        
        print(f"\n完成! 成功处理 {len(files) - 1} 个订阅")
        
        if failed:
            print(f"\n警告: {len(failed)} 个订阅下载失败")
            sys.exit(2)
            
    except Exception as e:
        print(f"\n错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
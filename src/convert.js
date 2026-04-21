#!/usr/bin/env node
/**
 * Clash to Sing-box 转换脚本
 * 使用 npx sub-store 命令行工具
 */

import { execSync } from 'child_process';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const DEPS_DIR = path.join(__dirname, 'deps');
const SUBSTORE_CLI = path.join(DEPS_DIR, 'sub-store.bundle.js');

/**
 * 检查并安装 sub-store
 */
function ensureSubStore() {
    if (!fs.existsSync(DEPS_DIR)) {
        fs.mkdirSync(DEPS_DIR, { recursive: true });
    }
    
    // 检查是否已安装
    if (fs.existsSync(SUBSTORE_CLI)) {
        return;
    }
    
    console.log('[Convert] 安装 Sub-Store CLI...');
    try {
        // 下载 sub-store bundle
        const url = 'https://github.com/sub-store-org/Sub-Store/releases/latest/download/sub-store.bundle.js';
        execSync(`curl -sL "${url}" -o "${SUBSTORE_CLI}"`, { stdio: 'inherit' });
        console.log('[Convert] Sub-Store CLI 安装成功');
    } catch (e) {
        console.error('[Convert] 安装失败:', e.message);
        throw e;
    }
}

/**
 * 转换Clash配置到Sing-box格式
 * 使用简单的解析逻辑
 */
function convertClashToSingbox(clashContent) {
    // 解析YAML内容
    const lines = clashContent.split('\n');
    const proxies = [];
    let inProxies = false;
    let currentProxy = null;
    
    for (let line of lines) {
        const trimmed = line.trim();
        
        // 检测proxies: 部分
        if (trimmed === 'proxies:') {
            inProxies = true;
            continue;
        }
        
        // 检测新的代理节点 (以 - name: 开头)
        if (inProxies && trimmed.startsWith('- name:')) {
            if (currentProxy) {
                proxies.push(currentProxy);
            }
            currentProxy = {
                name: trimmed.replace('- name:', '').trim(),
                type: 'unknown'
            };
            continue;
        }
        
        // 解析代理属性
        if (currentProxy && trimmed && !trimmed.startsWith('-') && trimmed.includes(':')) {
            const [key, ...valueParts] = trimmed.split(':');
            const value = valueParts.join(':').trim();
            
            // 移除注释
            const cleanValue = value.split('#')[0].trim();
            
            if (key === 'type') {
                currentProxy.type = cleanValue;
            } else if (key === 'server') {
                currentProxy.server = cleanValue;
            } else if (key === 'port') {
                currentProxy.port = parseInt(cleanValue) || cleanValue;
            } else if (key === 'uuid') {
                currentProxy.uuid = cleanValue;
            } else if (key === 'password') {
                currentProxy.password = cleanValue;
            } else if (key === 'cipher') {
                currentProxy.cipher = cleanValue;
            } else if (key === 'network') {
                currentProxy.network = cleanValue;
            } else if (key === 'tls' || key === 'skip-cert-verify') {
                currentProxy[key] = cleanValue === 'true';
            } else if (key.endsWith('-opts')) {
                try {
                    currentProxy[key] = JSON.parse(cleanValue);
                } catch {
                    currentProxy[key] = cleanValue;
                }
            } else {
                currentProxy[key] = cleanValue;
            }
        }
        
        // 检测代理列表结束
        if (inProxies && trimmed && !trimmed.startsWith(' ') && !trimmed.startsWith('-') && !trimmed.startsWith('#')) {
            if (currentProxy) {
                proxies.push(currentProxy);
                currentProxy = null;
            }
            if (trimmed !== 'proxies:' && !trimmed.startsWith('proxy-')) {
                inProxies = false;
            }
        }
    }
    
    // 添加最后一个代理
    if (currentProxy) {
        proxies.push(currentProxy);
    }
    
    // 转换为sing-box格式
    const singboxProxies = proxies.map(p => convertToSingboxFormat(p)).filter(Boolean);
    
    return singboxProxies;
}

/**
 * 将单个代理转换为sing-box格式
 */
function convertToSingboxFormat(proxy) {
    const type = proxy.type?.toLowerCase();
    const result = {
        tag: proxy.name,
        type: mapType(type),
        server: proxy.server,
        server_port: parseInt(proxy.port) || 443
    };
    
    switch (type) {
        case 'ss':
            result.method = proxy.cipher || 'none';
            result.password = proxy.password || '';
            break;
        case 'vmess':
            result.uuid = proxy.uuid || '';
            result.security = proxy.cipher || 'auto';
            if (proxy.alterId) result.alter_id = parseInt(proxy.alterId);
            break;
        case 'vless':
            result.uuid = proxy.uuid || '';
            if (proxy.flow) result.flow = proxy.flow;
            break;
        case 'trojan':
            result.password = proxy.password || '';
            break;
        case 'hysteria2':
        case 'hy2':
            result.password = proxy.password || '';
            if (proxy['obfs-password']) {
                result.obfs = {
                    type: proxy.obfs || 'salamander',
                    password: proxy['obfs-password']
                };
            }
            break;
        case 'tuic':
            result.uuid = proxy.uuid || '';
            result.password = proxy.password || '';
            result.congestion_control = proxy['congestion-controller'] || 'cubic';
            break;
        case 'wireguard':
            // WireGuard特殊处理
            break;
        default:
            // 不支持的类型，返回null
            return null;
    }
    
    // 处理TLS
    if (proxy.tls || proxy.type === 'trojan' || proxy.type === 'vless') {
        const tls = {};
        if (proxy.sni) tls.server_name = proxy.sni;
        if (proxy['skip-cert-verify']) tls.insecure = true;
        if (Object.keys(tls).length > 0) {
            result.tls = tls;
        }
    }
    
    // 处理传输层
    if (proxy.network) {
        const transport = convertTransport(proxy);
        if (transport) {
            result.transport = transport;
        }
    }
    
    return result;
}

function mapType(type) {
    const typeMap = {
        'ss': 'shadowsocks',
        'vmess': 'vmess',
        'vless': 'vless',
        'trojan': 'trojan',
        'hysteria': 'hysteria',
        'hysteria2': 'hysteria2',
        'hy2': 'hysteria2',
        'tuic': 'tuic',
        'wireguard': 'wireguard',
        'socks': 'socks',
        'http': 'http'
    };
    return typeMap[type] || type;
}

function convertTransport(proxy) {
    const network = proxy.network;
    if (!network) return null;
    
    const transport = { type: network };
    
    if (network === 'ws' && proxy['ws-opts']) {
        const opts = proxy['ws-opts'];
        if (opts.path) transport.path = opts.path;
        if (opts.headers?.Host) transport.headers = { Host: opts.headers.Host };
    } else if (network === 'grpc' && proxy['grpc-opts']) {
        const opts = proxy['grpc-opts'];
        if (opts['grpc-service-name']) transport.service_name = opts['grpc-service-name'];
    } else if (network === 'http' && proxy['http-opts']) {
        const opts = proxy['http-opts'];
        if (opts.path) transport.path = Array.isArray(opts.path) ? opts.path[0] : opts.path;
        if (opts.headers?.Host) transport.headers = { Host: Array.isArray(opts.headers.Host) ? opts.headers.Host[0] : opts.headers.Host };
    }
    
    return Object.keys(transport).length > 1 ? transport : null;
}

/**
 * 主函数
 */
async function main() {
    const args = process.argv.slice(2);
    const command = args[0];

    try {
        if (command === 'convert') {
            const inputFile = args[1];
            if (!inputFile) {
                console.error('用法: node convert.js convert <input-file>');
                process.exit(1);
            }

            const clashContent = fs.readFileSync(inputFile, 'utf8');
            const singboxConfig = convertClashToSingbox(clashContent);
            
            console.log(JSON.stringify(singboxConfig, null, 2));
        } else {
            console.log('用法:');
            console.log('  node convert.js convert <input-file>  转换Clash配置到Sing-box');
            process.exit(1);
        }
    } catch (err) {
        console.error('[Convert] 错误:', err.message);
        console.error(err.stack);
        process.exit(1);
    }
}

main();
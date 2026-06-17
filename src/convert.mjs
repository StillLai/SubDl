#!/usr/bin/env node
/**
 * Clash to Sing-box 转换脚本
 * 使用 Sub-Store 的 proxy-utils.esm.mjs 作为依赖
 * 使用jsdom模拟浏览器环境
 *
 * ═══ 架构前提 ═══
 * 本脚本每次由 Python 侧 subprocess.run 启动为全新的 Node 进程，
 * 进程退出时所有全局状态（global 变量、console.log 重定向等）自动清理。
 * 因此全局修改不需要 try-finally / 互斥锁等保护，不存在跨调用污染。
 */

import fs from 'fs';
import path from 'path';
import https from 'https';
import { fileURLToPath } from 'url';
import { JSDOM } from 'jsdom';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const DEPS_DIR = path.join(__dirname, 'deps');
const PROXY_UTILS_FILE = path.join(DEPS_DIR, 'proxy-utils.esm.mjs');
const VERSION_FILE = path.join(DEPS_DIR, '.version');

const RELEASES_API = 'https://api.github.com/repos/sub-store-org/Sub-Store/releases/latest';
const PROXY_UTILS_NAME = 'proxy-utils.esm.mjs';

/**
 * 下载文件
 */
function downloadFile(url, dest, headers = {}) {
    return new Promise((resolve, reject) => {
        const file = fs.createWriteStream(dest);
        https.get(url, { headers, timeout: 30000 }, (response) => {
            if (response.statusCode === 302 || response.statusCode === 301) {
                file.close();
                if (fs.existsSync(dest)) fs.unlinkSync(dest);
                downloadFile(response.headers.location, dest, headers).then(resolve).catch(reject);
                return;
            }
            if (response.statusCode !== 200) {
                file.close();
                if (fs.existsSync(dest)) fs.unlinkSync(dest);
                reject(new Error(`下载失败: ${response.statusCode}`));
                return;
            }
            response.pipe(file);
            file.on('finish', () => file.close(resolve));
        }).on('error', (err) => {
            if (fs.existsSync(dest)) fs.unlinkSync(dest);
            reject(err);
        });
    });
}

/**
 * 获取GitHub Releases信息
 */
function getLatestRelease(githubToken) {
    return new Promise((resolve, reject) => {
        const headers = {
            'User-Agent': 'SubDl-Converter',
            'Accept': 'application/vnd.github.v3+json'
        };
        if (githubToken) headers['Authorization'] = `token ${githubToken}`;

        https.get(RELEASES_API, { headers }, (response) => {
            let data = '';
            if (response.statusCode === 403) {
                reject(new Error('API限流'));
                return;
            }
            if (response.statusCode !== 200) {
                reject(new Error(`API请求失败: ${response.statusCode}`));
                return;
            }
            response.on('data', (chunk) => data += chunk);
            response.on('end', () => {
                try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
            });
        }).on('error', reject);
    });
}

/**
 * 检查并更新依赖
 */
async function checkAndUpdateDeps(githubToken) {
    // 6 小时内已检查过且本地缓存存在，跳过 GitHub API 调用
    const SIX_HOURS_MS = 6 * 60 * 60 * 1000;
    try {
        if (fs.existsSync(VERSION_FILE) && fs.existsSync(PROXY_UTILS_FILE)) {
            const cached = fs.readFileSync(VERSION_FILE, 'utf8').trim();
            const lines = cached.split('\n');
            const cachedVersion = lines[0] || '';
            const cachedTime = parseInt(lines[1] || '0', 10);
            if (cachedTime && (Date.now() - cachedTime) < SIX_HOURS_MS) {
                console.error(`[Convert] 使用缓存版本 (${cachedVersion}, ${Math.round((Date.now() - cachedTime) / 3600000)}h前检查)`);
                return;
            }
        }

        console.error('[Convert] 检查 Sub-Store 依赖更新...');
        const release = await getLatestRelease(githubToken);
        const tagName = release.tag_name;
        
        let currentVersion = '';
        if (fs.existsSync(VERSION_FILE)) {
            const cached = fs.readFileSync(VERSION_FILE, 'utf8').trim();
            currentVersion = cached.split('\n')[0] || '';
        }

        if (currentVersion === tagName) {
            // 版本未变，仅刷新时间戳
            fs.writeFileSync(VERSION_FILE, `${tagName}\n${Date.now()}`);
            console.error(`[Convert] 已是最新版本: ${tagName}`);
            return;
        }

        console.error(`[Convert] 发现新版本: ${tagName}`);

        const asset = release.assets.find(a => 
            a.uploader?.login === 'github-actions[bot]' && 
            a.name === PROXY_UTILS_NAME
        );

        if (!asset) {
            if (!fs.existsSync(PROXY_UTILS_FILE)) throw new Error('未找到依赖文件');
            fs.writeFileSync(VERSION_FILE, `${currentVersion}\n${Date.now()}`);
            console.error('[Convert] 使用本地缓存版本');
            return;
        }

        console.error('[Convert] 下载依赖...');
        await downloadFile(asset.browser_download_url, PROXY_UTILS_FILE + '.tmp');
        if (fs.existsSync(PROXY_UTILS_FILE)) fs.unlinkSync(PROXY_UTILS_FILE);
        fs.renameSync(PROXY_UTILS_FILE + '.tmp', PROXY_UTILS_FILE);
        fs.writeFileSync(VERSION_FILE, `${tagName}\n${Date.now()}`);
        console.error('[Convert] 依赖更新成功');

    } catch (err) {
        console.error('[Convert] 检查更新失败:', err.message);
        if (!fs.existsSync(PROXY_UTILS_FILE)) throw new Error('依赖检查失败且本地无缓存');
        console.error('[Convert] 使用本地缓存版本');
    }
}

/**
 * 加载Sub-Store模块。在全局注入 require 和浏览器 API（window/document/navigator 等），
 * 因为 proxy-utils.esm.mjs 内部有 eval 使用 require，也需要浏览器环境。
 * 使用 try-finally 确保 import 抛出异常时也会清理注入的全局变量。
 */
async function loadProxyUtils() {
    // 保存原始值，便于加载后清理
    const GLOBAL_KEYS = ['require', 'window', 'document', 'self', 'navigator', 'location'];
    const originals = {};
    const existed = new Set();
    for (const key of GLOBAL_KEYS) {
        if (key in global) {
            originals[key] = global[key];
            existed.add(key);
        }
    }

    // 在全局注入require（这是proxy-utils.esm.mjs需要的）
    const { createRequire } = await import('module');
    global.require = createRequire(PROXY_UTILS_FILE);
    existed.add('require');
    
    // 创建jsdom环境，使用 pretendToBeVisual 避免 navigator 只读问题
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
        url: 'https://localhost',
        pretendToBeVisual: true
    });
    
    // 注入浏览器API — 用 try-catch 兼容 getter-only 属性
    const injectGlobal = (name, value) => {
        try {
            global[name] = value;
        } catch {
            Object.defineProperty(global, name, {
                value: value, configurable: true, writable: true, enumerable: true
            });
        }
    };

    injectGlobal('window', dom.window);
    injectGlobal('document', dom.window.document);
    injectGlobal('self', dom.window);
    injectGlobal('navigator', dom.window.navigator);
    injectGlobal('location', dom.window.location);
    
    // 直接用file://协议导入
    const modulePath = 'file://' + PROXY_UTILS_FILE;
    
    try {
        const mod = await import(modulePath);
        console.error('[Convert] 模块加载成功');
        return mod;
    } finally {
        // 清理注入的全局变量，恢复原始状态
        for (const key of GLOBAL_KEYS) {
            if (existed.has(key)) {
                try {
                    global[key] = originals[key];
                } catch {
                    Object.defineProperty(global, key, {
                        value: originals[key], configurable: true, writable: true, enumerable: true
                    });
                }
            } else {
                delete global[key];
            }
        }
    }
}

/**
 * 转换Clash配置到Sing-box格式
 */
async function convertClashToSingbox(clashContent) {
    const { parse, produce } = await loadProxyUtils();
    const proxies = parse(clashContent);
    return produce(proxies, 'singbox');
}

/**
 * 主函数
 */
async function main() {
    const args = process.argv.slice(2);
    const command = args[0];
    const githubToken = process.env.GH_TOKEN || '';

    const USAGE = '用法: node convert.mjs convert <input-file>';

    // 将所有日志输出到stderr，stdout只输出JSON结果。
    const originalLog = console.log;
    console.log = (...args) => console.error(...args);

    try {
        if (!fs.existsSync(DEPS_DIR)) fs.mkdirSync(DEPS_DIR, { recursive: true });
        await checkAndUpdateDeps(githubToken);

        if (command === 'convert') {
            const inputFile = args[1];
            if (!inputFile) {
                console.error(USAGE);
                process.exit(1);
            }

            const clashContent = fs.readFileSync(inputFile, 'utf8');
            const singboxConfig = await convertClashToSingbox(clashContent);
            
            // 恢复console.log，只输出JSON到stdout
            console.log = originalLog;
            
            // produce 函数返回的 singbox 格式是 JSON 字符串
            console.log(singboxConfig);
        } else {
            console.error(USAGE);
            process.exit(1);
        }
    } catch (err) {
        console.error('[Convert] 错误:', err.message);
        console.error(err.stack);
        process.exit(1);
    }
}

main();
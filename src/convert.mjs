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
async function downloadFile(url, dest, headers = {}) {
    const resp = await fetch(url, { headers });
    if (!resp.ok) {
        throw new Error(`下载失败: ${resp.status}`);
    }
    const buffer = Buffer.from(await resp.arrayBuffer());
    fs.writeFileSync(dest, buffer);
}

/**
 * 获取GitHub Releases信息
 */
async function getLatestRelease(githubToken) {
    const headers = {
        'User-Agent': 'SubDl-Converter',
        'Accept': 'application/vnd.github.v3+json'
    };
    if (githubToken) headers['Authorization'] = `token ${githubToken}`;

    const resp = await fetch(RELEASES_API, { headers });
    if (resp.status === 403) throw new Error('API限流');
    if (!resp.ok) throw new Error(`API请求失败: ${resp.status}`);
    return resp.json();
}

/**
 * 检查并更新依赖
 */
async function checkAndUpdateDeps(githubToken) {
    // 6 小时内已检查过且本地缓存存在，跳过 GitHub API 调用
    const SIX_HOURS_MS = 6 * 60 * 60 * 1000;
    let cachedVersion = '';
    try {
        if (fs.existsSync(VERSION_FILE) && fs.existsSync(PROXY_UTILS_FILE)) {
            const cached = fs.readFileSync(VERSION_FILE, 'utf8').trim();
            const lines = cached.split('\n');
            cachedVersion = lines[0] || '';
            const cachedTime = parseInt(lines[1] || '0', 10);
            if (cachedTime && (Date.now() - cachedTime) < SIX_HOURS_MS) {
                console.error(`[Convert] 使用缓存版本 (${cachedVersion}, ${Math.round((Date.now() - cachedTime) / 3600000)}h前检查)`);
                return;
            }
        }

        console.error('[Convert] 检查 Sub-Store 依赖更新...');
        const release = await getLatestRelease(githubToken);
        const tagName = release.tag_name;

        if (cachedVersion === tagName) {
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
            fs.writeFileSync(VERSION_FILE, `${cachedVersion}\n${Date.now()}`);
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
 * 批量转换：一次加载模块，转换多个订阅内容
 * @param {Object<string, string>} batchInput - { "sub_name": "clash_content", ... }
 * @returns {Object<string, any>} - { "sub_name": <singbox_result>, ... }
 */
async function batchConvertToSingbox(batchInput) {
    const GLOBAL_KEYS = ['require', 'window', 'document', 'self', 'navigator', 'location'];

    // 保存原始全局变量状态
    const originals = new Map();
    for (const key of GLOBAL_KEYS) {
        if (key in global) originals.set(key, global[key]);
    }

    const setGlobal = (key, value) => {
        try { global[key] = value; }
        catch { Object.defineProperty(global, key, { value, configurable: true, writable: true, enumerable: true }); }
    };

    // 注入 require
    // NOTE: dotenv 是 proxy-utils.esm.mjs (Sub-Store 模块) 内部 require('dotenv') 的隐式依赖，
    //       不是本项目自身使用的。如需移除，请先确认 Sub-Store 模块不再需要。
    const { createRequire } = await import('module');
    setGlobal('require', createRequire(PROXY_UTILS_FILE));

    // 创建 jsdom 环境
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
        url: 'https://localhost',
        pretendToBeVisual: true
    });
    for (const key of ['window', 'document', 'self', 'navigator', 'location']) {
        setGlobal(key, key === 'document' ? dom.window.document : key === 'location' ? dom.window.location : dom.window);
    }

    const modulePath = 'file://' + PROXY_UTILS_FILE;

    try {
        const { parse, produce } = await import(modulePath);
        console.error('[Convert] 模块加载成功，开始批量转换');

        const results = {};
        for (const [name, clashContent] of Object.entries(batchInput)) {
            try {
                results[name] = JSON.parse(produce(parse(clashContent), 'singbox'));
                console.error(`[Convert]   ✓ ${name} 转换成功`);
            } catch (err) {
                console.error(`[Convert]   ✗ ${name} 转换失败: ${err.message}`);
                results[name] = null;
            }
        }
        return results;
    } finally {
        // 恢复原始全局变量状态
        for (const key of GLOBAL_KEYS) {
            if (originals.has(key)) {
                setGlobal(key, originals.get(key));
            } else {
                delete global[key];
            }
        }
    }
}

/**
 * 主函数
 */
async function main() {
    const args = process.argv.slice(2);
    const command = args[0];
    const githubToken = process.env.GH_TOKEN || '';

    const USAGE = '用法: node convert.mjs batch-convert <input-file>';

    // 将所有日志输出到stderr，stdout只输出JSON结果。
    const originalLog = console.log;
    console.log = (...args) => console.error(...args);

    try {
        if (!fs.existsSync(DEPS_DIR)) fs.mkdirSync(DEPS_DIR, { recursive: true });
        await checkAndUpdateDeps(githubToken);

        if (command === 'batch-convert') {
            const inputFile = args[1];
            if (!inputFile) {
                console.error(USAGE);
                process.exit(1);
            }
            
            const batchInput = JSON.parse(fs.readFileSync(inputFile, 'utf8'));
            const results = await batchConvertToSingbox(batchInput);
            
            // 恢复console.log，只输出JSON到stdout
            console.log = originalLog;
            console.log(JSON.stringify(results));
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
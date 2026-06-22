#!/usr/bin/env node
/**
 * Clash to Sing-box 转换脚本
 * 使用 Sub-Store 的 proxy-utils.esm.mjs 作为依赖
 * 使用jsdom模拟浏览器环境
 *
 * ═══ 架构前提 ═══
 * 本脚本每次由 Python 侧 subprocess.run 启动为全新的 Node 进程，
 * 进程退出时所有全局状态自动清理，不存在跨调用污染。
 */

import fs from 'fs/promises';
import { existsSync, mkdirSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { JSDOM } from 'jsdom';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const DEPS_DIR = path.join(__dirname, 'deps');
const PROXY_UTILS_FILE = path.join(DEPS_DIR, 'proxy-utils.esm.mjs');
const VERSION_FILE = path.join(DEPS_DIR, '.version');

const RELEASES_API = 'https://api.github.com/repos/sub-store-org/Sub-Store/releases/latest';
const PROXY_UTILS_NAME = 'proxy-utils.esm.mjs';

// 可通过环境变量配置缓存 TTL（毫秒），默认 6 小时
const CACHE_TTL_MS = parseInt(process.env.SUB_STORE_CACHE_TTL || '21600000', 10);

async function getLatestRelease(githubToken) {
    const headers = {
        'User-Agent': 'SubDl-Converter',
        'Accept': 'application/vnd.github.v3+json',
    };
    if (githubToken) headers['Authorization'] = `token ${githubToken}`;

    const resp = await fetch(RELEASES_API, { headers });
    if (resp.status === 403) throw new Error('GitHub API 限流 (403)');
    if (!resp.ok) throw new Error(`GitHub API 请求失败: ${resp.status}`);
    return resp.json();
}

async function getCachedVersion() {
    try {
        const cached = (await fs.readFile(VERSION_FILE, 'utf8')).trim();
        const [version, timeStr] = cached.split('\n');
        return { version: version || '', time: parseInt(timeStr || '0', 10) };
    } catch {
        return { version: '', time: 0 };
    }
}

async function downloadAndInstall(asset, tagName) {
    console.error(`[Convert] 下载依赖 ${tagName}...`);
    const tmpFile = PROXY_UTILS_FILE + '.tmp';
    const resp = await fetch(asset.browser_download_url);
    if (!resp.ok) throw new Error(`下载失败: ${resp.status}`);
    await fs.writeFile(tmpFile, Buffer.from(await resp.arrayBuffer()));
    await fs.rename(tmpFile, PROXY_UTILS_FILE);
    await fs.writeFile(VERSION_FILE, `${tagName}\n${Date.now()}`);
    console.error('[Convert] 依赖更新成功');
}

async function checkAndUpdateDeps(githubToken) {
    const hasLocal = existsSync(PROXY_UTILS_FILE);

    try {
        const { version: cachedVersion, time: cachedTime } = await getCachedVersion();
        const age = Date.now() - cachedTime;

        // 缓存未过期，直接使用
        if (cachedVersion && cachedTime && age < CACHE_TTL_MS) {
            console.error(`[Convert] 使用缓存版本 (${cachedVersion}, ${Math.round(age / 3600000)}h 前检查)`);
            return;
        }

        console.error('[Convert] 检查 Sub-Store 依赖更新...');
        const release = await getLatestRelease(githubToken);
        const tagName = release.tag_name;

        // 版本未变，仅刷新时间戳
        if (cachedVersion === tagName) {
            await fs.writeFile(VERSION_FILE, `${tagName}\n${Date.now()}`);
            console.error(`[Convert] 已是最新版本: ${tagName}`);
            return;
        }

        console.error(`[Convert] 发现新版本: ${tagName}`);

        const asset = release.assets.find(a =>
            a.uploader?.login === 'github-actions[bot]' &&
            a.name === PROXY_UTILS_NAME
        );

        if (!asset) {
            if (!hasLocal) throw new Error('未找到依赖文件且本地无缓存');
            await fs.writeFile(VERSION_FILE, `${cachedVersion}\n${Date.now()}`);
            console.error('[Convert] 未找到新版本资产，使用本地缓存');
            return;
        }

        await downloadAndInstall(asset, tagName);

    } catch (err) {
        console.error(`[Convert] 更新检查失败: ${err.message}`);
        if (!hasLocal) throw new Error('依赖检查失败且本地无缓存');
        console.error('[Convert] 使用本地缓存版本');
    }
}

/**
 * 批量转换：一次加载模块，转换多个订阅内容
 */
async function batchConvertToSingbox(batchInput) {
    const GLOBAL_KEYS = ['require', 'window', 'document', 'self', 'navigator', 'location'];
    const DOM_KEYS = GLOBAL_KEYS.filter(key => key !== 'require');

    // 保存原始全局变量状态
    const originals = new Map();
    for (const key of GLOBAL_KEYS) {
        if (key in global) originals.set(key, global[key]);
    }

    const setGlobal = (key, value) => {
        try { global[key] = value; }
        catch { Object.defineProperty(global, key, { value, configurable: true, writable: true, enumerable: true }); }
    };

    // 注入 require（Sub-Store 内部隐式依赖 dotenv）
    const { createRequire } = await import('module');
    setGlobal('require', createRequire(PROXY_UTILS_FILE));

    // 创建 jsdom 环境
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
        url: 'https://localhost',
        pretendToBeVisual: true,
    });
    for (const key of DOM_KEYS) {
        setGlobal(key, key === 'document' ? dom.window.document : key === 'location' ? dom.window.location : dom.window);
    }

    try {
        const { parse, produce } = await import('file://' + PROXY_UTILS_FILE);
        console.error(`[Convert] 模块加载成功，开始转换 ${Object.keys(batchInput).length} 个订阅`);

        const results = {};
        for (const [name, clashContent] of Object.entries(batchInput)) {
            try {
                results[name] = JSON.parse(produce(parse(clashContent), 'singbox'));
                console.error(`[Convert]   ✓ ${name}`);
            } catch (err) {
                console.error(`[Convert]   ✗ ${name}: ${err.message}`);
                results[name] = null;
            }
        }
        return results;
    } finally {
        // 恢复原始全局变量
        for (const key of GLOBAL_KEYS) {
            if (originals.has(key)) setGlobal(key, originals.get(key));
            else delete global[key];
        }
        dom.window.close();
    }
}

async function main() {
    const args = process.argv.slice(2);
    const command = args[0];
    const githubToken = process.env.GH_TOKEN || '';

    // 所有日志输出到 stderr，stdout 只输出 JSON 结果
    const originalLog = console.log;
    console.log = (...a) => console.error(...a);

    try {
        if (!existsSync(DEPS_DIR)) mkdirSync(DEPS_DIR, { recursive: true });
        await checkAndUpdateDeps(githubToken);

        if (command !== 'batch-convert' || !args[1]) {
            console.error('用法: node convert.mjs batch-convert <input-file>');
            process.exit(1);
        }

        const batchInput = JSON.parse(await fs.readFile(args[1], 'utf8'));
        const results = await batchConvertToSingbox(batchInput);

        // 恢复 console.log，只输出 JSON 到 stdout
        console.log = originalLog;
        console.log(JSON.stringify(results));
    } catch (err) {
        console.error(`[Convert] 错误: ${err.message}`);
        console.error(err.stack);
        process.exit(1);
    }
}

main();
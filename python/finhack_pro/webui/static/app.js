/**
 * FinHack Pro WebUI - 前端JavaScript
 *
 * 功能:
 * - 页面导航逻辑(SPA风格)
 * - API调用封装
 * - WebSocket连接管理(自动重连)
 * - 图表初始化和更新
 * - 实时数据流处理
 * - 表单验证
 * - 通知系统(toast)
 */

// ============================================================
// 工具函数
// ============================================================

/**
 * 读取主题 CSS 变量，供 Chart.js 等无法直接使用 CSS 变量的场景。
 *
 * 主题变量以 "R G B" 分量格式存储（为了支持 Tailwind 的 /50 透明度修饰符），
 * 这里还原成 rgb() 字符串；本身已是完整颜色值的（如 rgba(...)）则原样返回。
 * 换主题后需配合 repaintCharts() 重绘图表。
 */
function TK(name, fallback) {
    var v = '';
    try {
        v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch (e) {
        v = '';
    }
    if (!v) return fallback || '#808080';
    return v.indexOf(' ') > -1 ? 'rgb(' + v + ')' : v;
}

// ============================================================================
// 布局可视化编辑（阶段7）：HTML5 原生 DnD 重排 + 宽度档 + 显隐
// 覆盖仪表盘/回测页卡片，偏好按页存 localStorage（fh.layout.{page}）
// ============================================================================
const LayoutKit = {
    page: '',
    items: [],       // [{id, el}]
    dragId: null,
    editing: false,

    init(page) {
        this.page = page;
        this.items = [];
        document.querySelectorAll('[data-card-id]').forEach(el => {
            const id = el.getAttribute('data-card-id');
            if (!id) return;
            this.items.push({ id, el });
            this._injectToolbar(el, id);
        });
        this.applyPrefs();
        if (this.editing) this.setEditing(true);
    },

    // 注入编辑工具条（仅编辑模式可见）：拖把手/宽度档/隐藏
    _injectToolbar(el, id) {
        if (el.querySelector('.layout-tools')) return;
        const bar = document.createElement('div');
        bar.className = 'layout-tools';
        bar.style.cssText = 'display:none;align-items:center;gap:4px;margin-bottom:8px;font-size:11px;user-select:none;';
        bar.innerHTML = `
            <span class="layout-drag" draggable="true" title="拖动排序"
                  style="cursor:grab;padding:0 6px;font-size:14px;line-height:1;color:var(--fg-subtle);">⠿</span>
            <button data-w="full" title="全宽" style="width:22px;height:20px;line-height:16px;text-align:center;border:1px solid var(--line);border-radius:2px;background:var(--bg-elevated);color:var(--fg-muted);cursor:pointer;font-size:10px;">▬</button>
            <button data-w="two" title="2/3 宽" style="width:22px;height:20px;line-height:16px;text-align:center;border:1px solid var(--line);border-radius:2px;background:var(--bg-elevated);color:var(--fg-muted);cursor:pointer;font-size:10px;">▮</button>
            <button data-w="half" title="1/2 宽" style="width:22px;height:20px;line-height:16px;text-align:center;border:1px solid var(--line);border-radius:2px;background:var(--bg-elevated);color:var(--fg-muted);cursor:pointer;font-size:10px;">▯</button>
            <button data-w="third" title="1/3 宽" style="width:22px;height:20px;line-height:16px;text-align:center;border:1px solid var(--line);border-radius:2px;background:var(--bg-elevated);color:var(--fg-muted);cursor:pointer;font-size:10px;">▭</button>
            <button data-hide="1" title="隐藏（可在恢复默认找回）" style="width:22px;height:20px;line-height:16px;text-align:center;border:1px solid var(--line);border-radius:2px;background:var(--bg-elevated);color:var(--fg-muted);cursor:pointer;font-size:10px;margin-left:4px;">✕</button>
        `;
        el.prepend(bar);

        bar.querySelectorAll('button[data-w]').forEach(btn => {
            btn.addEventListener('click', () => this.setWidth(id, btn.dataset.w));
        });
        bar.querySelector('button[data-hide]').addEventListener('click', () => this.hide(id));

        // DnD：把手可拖，整卡作为放置目标（同容器重排）
        const handle = bar.querySelector('.layout-drag');
        handle.addEventListener('dragstart', e => {
            this.dragId = id;
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', id);
        });
        handle.addEventListener('dragend', () => { this.dragId = null; });
        el.addEventListener('dragover', e => {
            if (this.dragId && this.dragId !== id) {
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                el.style.outline = '2px dashed var(--accent)';
            }
        });
        el.addEventListener('dragleave', () => { el.style.outline = this.editing ? '1px dashed var(--line-strong)' : ''; });
        el.addEventListener('drop', e => {
            e.preventDefault();
            el.style.outline = this.editing ? '1px dashed var(--line-strong)' : '';
            if (this.dragId && this.dragId !== id) this.moveBefore(this.dragId, id);
        });
    },

    setEditing(on) {
        this.editing = on;
        document.querySelectorAll('.layout-tools').forEach(b => { b.style.display = on ? 'flex' : 'none'; });
        document.querySelectorAll('[data-card-id]').forEach(el => {
            el.style.outline = on ? '1px dashed var(--line-strong)' : '';
        });
    },

    _containerCols(el) {
        const parent = el.parentElement;
        if (!parent) return 1;
        const tpl = getComputedStyle(parent).gridTemplateColumns || '';
        const cols = tpl.split(' ').filter(s => s.trim() && s !== 'none').length;
        return cols || 1;
    },

    setWidth(id, w) {
        const item = this.items.find(i => i.id === id);
        if (!item) return;
        const el = item.el;
        const cols = this._containerCols(el);
        const spanMap = {
            full: 0,   // 0 → 1 / -1
            two: Math.max(1, Math.ceil(cols * 2 / 3)),
            half: Math.max(1, Math.ceil(cols / 2)),
            third: Math.max(1, Math.ceil(cols / 3)),
        };
        const span = spanMap[w] !== undefined ? spanMap[w] : 1;
        el.style.gridColumn = w === 'full' ? '1 / -1' : `span ${span} / span ${span}`;
        item._width = w;
        this._save();
        this._resizeCharts();
    },

    hide(id) {
        const item = this.items.find(i => i.id === id);
        if (!item) return;
        item.el.style.display = 'none';
        this._save();
        this._resizeCharts();
    },

    moveBefore(fromId, beforeId) {
        const from = this.items.find(i => i.id === fromId);
        const before = this.items.find(i => i.id === beforeId);
        if (!from || !before) return;
        if (from.el.parentElement !== before.el.parentElement) return;  // 同容器重排
        before.el.parentElement.insertBefore(from.el, before.el);
        this._save();
        this._resizeCharts();
    },

    reset() {
        try { localStorage.removeItem('fh.layout.' + this.page); } catch (_) {}
        // 清空内联样式，恢复 DOM 原始顺序（按 data-card-id 在文档中的原序）
        const order = Array.from(document.querySelectorAll('[data-card-id]'))
            .sort((a, b) => (a._origIndex || 0) - (b._origIndex || 0));
        // 原序已在首次 init 时记录到 _origIndex
        document.querySelectorAll('[data-card-id]').forEach(el => {
            el.style.gridColumn = '';
            el.style.display = '';
        });
        // 按原序重排（逐父容器）
        const parents = new Set(this.items.map(i => i.el.parentElement));
        parents.forEach(parent => {
            const els = Array.from(parent.querySelectorAll('[data-card-id]'))
                .sort((a, b) => (a._origIndex || 0) - (b._origIndex || 0));
            els.forEach(el => parent.appendChild(el));
        });
        this._resizeCharts();
    },

    applyPrefs() {
        let prefs = null;
        try {
            const raw = localStorage.getItem('fh.layout.' + this.page);
            if (raw) {
                const parsed = JSON.parse(raw);
                prefs = Array.isArray(parsed) ? parsed : (parsed.cards || null);
            }
        } catch (_) { prefs = null; }
        if (!prefs) {
            // 无偏好：记录原序索引
            document.querySelectorAll('[data-card-id]').forEach((el, i) => { el._origIndex = i; });
            return;
        }
        // 记录原序
        document.querySelectorAll('[data-card-id]').forEach((el, i) => { el._origIndex = i; });
        const map = new Map(prefs.map(p => [p.id, p]));
        // 1) 重排（逐父容器，按偏好顺序）
        const parents = new Set(this.items.map(i => i.el.parentElement));
        parents.forEach(parent => {
            const idsInParent = prefs.filter(p => parent.querySelector(`[data-card-id="${p.id}"]`));
            idsInParent.forEach((p, idx) => {
                const el = parent.querySelector(`[data-card-id="${p.id}"]`);
                if (!el) return;
                if (idx === 0) parent.prepend(el);
                else {
                    const prev = parent.querySelector(`[data-card-id="${idsInParent[idx - 1].id}"]`);
                    if (prev && prev !== el) prev.after(el);
                }
            });
        });
        // 2) 宽度/显隐
        for (const item of this.items) {
            const p = map.get(item.id);
            if (!p) continue;
            item.el.style.display = p.visible === false ? 'none' : '';
            if (p.width && p.width !== 'auto') {
                item._width = p.width;
                const cols = this._containerCols(item.el);
                const spanMap = {
                    full: 0, two: Math.max(1, Math.ceil(cols * 2 / 3)),
                    half: Math.max(1, Math.ceil(cols / 2)), third: Math.max(1, Math.ceil(cols / 3)),
                };
                const span = spanMap[p.width] !== undefined ? spanMap[p.width] : 1;
                item.el.style.gridColumn = p.width === 'full' ? '1 / -1' : `span ${span} / span ${span}`;
            }
        }
    },

    _save() {
        try {
            const cards = this.items.map(i => ({
                id: i.id,
                visible: i.el.style.display !== 'none',
                width: i._width || 'auto',
            }));
            localStorage.setItem('fh.layout.' + this.page, JSON.stringify({ version: 1, cards }));
        } catch (_) { /* localStorage 不可用则忽略 */ }
    },

    _resizeCharts() {
        try {
            if (typeof Chart !== 'undefined' && Chart.instances) {
                Object.values(Chart.instances).forEach(c => { try { c.resize(); } catch (_) {} });
            }
        } catch (_) {}
    },
};

// 内置策略参数元数据（回测面板"策略参数（临时调整）"区数据源；
// 后端 /api/backtest/strategies 暂无 params 字段，前端内置与策略默认值保持一致）
const BUILTIN_PARAMS = {
    dual_thrust: [
        { name: 'k1', label: '上轨系数（开仓）', default: 0.5 },
        { name: 'k2', label: '下轨系数（平仓）', default: 0.5 },
        { name: 'lookback', label: '通道回看周期', default: 20 },
    ],
    mean_reversion: [
        { name: 'rsi_period', label: 'RSI 周期', default: 14 },
        { name: 'oversold', label: '超卖阈值', default: 30 },
        { name: 'overbought', label: '超买阈值', default: 70 },
        { name: 'bb_period', label: '布林周期', default: 20 },
    ],
    momentum: [
        { name: 'lookback', label: '动量回看周期', default: 20 },
        { name: 'top_k', label: '持仓数量', default: 5 },
        { name: 'rebalance_days', label: '调仓周期', default: 5 },
    ],
};

// Chart.js 实例注册表：图表颜色在 JS 配置里固化，不走 CSS 变量，
// 换主题后必须逐个重绘（见 repaintCharts）。用 class 继承包装以保留静态方法。
window.__charts = new Set();
if (typeof Chart !== 'undefined') {
    const _BaseChart = Chart;
    window.Chart = class extends _BaseChart {
        constructor(ctx, config) {
            super(ctx, config);
            window.__charts.add(this);
        }
    };
}

/**
 * 格式化运行时间(秒 -> 可读字符串)
 */
function formatUptime(seconds) {
    if (!seconds || seconds < 0) return '0秒';
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    if (hours > 0) return `${hours}小时${minutes}分${secs}秒`;
    if (minutes > 0) return `${minutes}分${secs}秒`;
    return `${secs}秒`;
}

/**
 * 格式化时间(ISO -> 简短格式)
 */
function formatTime(isoStr) {
    if (!isoStr) return '-';
    try {
        const d = new Date(isoStr);
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        const hour = String(d.getHours()).padStart(2, '0');
        const min = String(d.getMinutes()).padStart(2, '0');
        return `${month}-${day} ${hour}:${min}`;
    } catch {
        return isoStr;
    }
}

/**
 * 格式化金额
 */
function formatMoney(amount) {
    if (amount >= 1e8) return (amount / 1e8).toFixed(2) + '亿';
    if (amount >= 1e4) return (amount / 1e4).toFixed(2) + '万';
    return amount.toFixed(2);
}

/**
 * 格式化组件名称
 */
function formatComponentName(name) {
    const map = {
        'api': 'API服务',
        'config': '配置系统',
        'agents': 'Agent系统',
        'memory': '记忆系统',
    };
    return map[name] || name;
}

/**
 * 渲染Markdown内容
 */
function renderMarkdown(content) {
    if (!content) return '';
    try {
        if (typeof marked !== 'undefined') {
            return marked.parse(content);
        }
    } catch (e) {
        console.error('Markdown渲染失败:', e);
    }
    return content.replace(/\n/g, '<br>');
}

/**
 * 获取Agent颜色
 */
function getAgentColor(agentId) {
    const colors = {
        'market_analyzer': TK('--chart-c1'),     // 蓝色
        'news_analyst': TK('--chart-c2'),        // 紫色
        'fundamental_analyst': TK('--chart-c3'), // 青色
        'micro_event_agent': TK('--chart-c4'),   // 粉色 - 微观事件
        'strategy_generator': TK('--chart-c5'),  // 橙色
        'risk_manager': TK('--chart-c6'),        // 红色
        'trade_executor': TK('--chart-c7'),      // 绿色
    };
    return colors[agentId] || TK('--chart-c8');
}

/**
 * 获取记忆类型中文名
 */
function getMemoryTypeName(type) {
    const names = {
        'market_observation': '市场观察',
        'analysis_report': '分析报告',
        'news_event': '新闻事件',
        'sentiment': '舆情情感',
        'strategy_decision': '策略决策',
        'risk_decision': '风控决策',
        'execution_record': '执行记录',
        'trade_result': '交易结果',
        'agent_thought': 'Agent思考',
        'system_event': '系统事件',
    };
    return names[type] || type;
}

/**
 * 获取重要性中文名
 */
function getImportanceName(imp) {
    const names = {
        'critical': '关键',
        'high': '重要',
        'medium': '普通',
        'low': '低',
    };
    return names[imp] || imp;
}

// ============================================================
// API调用封装
// ============================================================

const API = {
    baseUrl: '',

    async request(method, path, data = null) {
        const url = this.baseUrl + path;
        const options = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };
        if (data) {
            options.body = JSON.stringify(data);
        }
        try {
            const resp = await fetch(url, options);
            const json = await resp.json();
            if (!resp.ok) {
                // detail 可能是字符串（FastAPI 默认）或对象（如 409 携带 active_run_id）
                const detail = json.detail;
                const msg = typeof detail === 'string' ? detail
                    : (detail && detail.message) || `HTTP ${resp.status}`;
                const err = new Error(msg);
                err.status = resp.status;
                if (detail && typeof detail === 'object') err.detail = detail;
                throw err;
            }
            return json;
        } catch (e) {
            console.error(`API请求失败 [${method}] ${path}:`, e);
            throw e;
        }
    },

    get(path) { return this.request('GET', path); },
    post(path, data) { return this.request('POST', path, data); },
    put(path, data) { return this.request('PUT', path, data); },
    delete(path) { return this.request('DELETE', path); },

    // 系统管理
    getSystemInfo() { return this.get('/api/system/info'); },
    getHealth() { return this.get('/api/system/health'); },

    // 配置管理
    getConfig() { return this.get('/api/config'); },
    getFullConfig() { return this.get('/api/config/full'); },
    updateConfig(data) { return this.put('/api/config', data); },
    testConnection(provider, apiKey, baseUrl, protocol) {
        return this.post('/api/config/test-connection', { provider, api_key: apiKey, base_url: baseUrl, protocol });
    },
    testDataSource(source, tushareToken) {
        return this.post('/api/data/test-connection', { source, tushare_token: tushareToken });
    },
    saveConfig() { return this.post('/api/config/save'); },

    // 回测管理
    runBacktest(data) { return this.post('/api/backtest/run', data); },
    getBacktestStatus(taskId) { return this.get(`/api/backtest/${taskId}/status`); },
    getBacktestResult(taskId) { return this.get(`/api/backtest/${taskId}/result`); },
    getBacktestHistory() { return this.get('/api/backtest/history'); },

    // Agent管理
    listAgents() { return this.get('/api/agents/list'); },
    getAgentStatus(agentId) { return this.get(`/api/agents/${agentId}/status`); },
    runPipeline(payload) { return this.post('/api/agents/run-pipeline', payload || { symbol: '600519.SH' }); },
    getPipelineHistory() { return this.get('/api/agents/pipeline/history'); },
    listPipelineRuns() { return this.get('/api/agents/pipeline/list'); },
    getPipelineRun(runId) { return this.get(`/api/agents/pipeline/${runId}`); },
    cancelPipeline(runId) { return this.post(`/api/agents/pipeline/${runId}/cancel`, {}); },

    // 共享记忆
    getMemoryStats() { return this.get('/api/memory/stats'); },
    searchMemory(params) {
        const query = new URLSearchParams();
        Object.entries(params).forEach(([k, v]) => { if (v) query.set(k, v); });
        return this.get('/api/memory/search?' + query.toString());
    },
    getRecentMemory(limit = 20) { return this.get(`/api/memory/recent?limit=${limit}`); },
    deleteMemory(id) { return this.delete(`/api/memory/${id}`); },

    // 工具集
    listTools() { return this.get('/api/tools/list'); },
    getToolStats() { return this.get('/api/tools/stats'); },
};

// ============================================================
// WebSocket管理
// ============================================================

class WSManager {
    constructor() {
        this.connections = {};
        this.reconnectInterval = 3000;
        this.maxReconnectAttempts = 10;
    }

    connect(channel, onMessage) {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/ws/${channel}`;

        if (this.connections[channel]) {
            this.disconnect(channel);
        }

        let attempts = 0;
        const ws = new WebSocket(url);
        ws._channel = channel;
        ws._onMessage = onMessage;

        ws.onopen = () => {
            console.log(`WebSocket [${channel}] 已连接`);
            attempts = 0;
            // 通知Alpine.js更新连接状态
            if (window.__alpineApp) {
                window.__alpineApp.wsConnected = true;
            }
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                // 后端心跳探测（{"type":"ping"}）→ 回 pong 保持连接。
                // 否则后端 60s 超时清理连接，长任务（流水线/回测）运行中断连，
                // 前端将永远收不到完成事件而卡在"运行中"。
                if (data.type === 'ping') {
                    ws.send(JSON.stringify({ type: 'pong' }));
                    return;
                }
                if (data.type === 'pong') return;
                if (onMessage) onMessage(data);
            } catch (e) {
                console.error(`WebSocket消息解析失败:`, e);
            }
        };

        ws.onclose = () => {
            console.log(`WebSocket [${channel}] 已断开`);
            // 自动重连
            if (attempts < this.maxReconnectAttempts) {
                attempts++;
                console.log(`WebSocket [${channel}] 将在 ${this.reconnectInterval}ms 后重连 (${attempts}/${this.maxReconnectAttempts})`);
                setTimeout(() => {
                    if (this.connections[channel] === ws) {
                        this.connect(channel, onMessage);
                    }
                }, this.reconnectInterval);
            }
        };

        ws.onerror = (e) => {
            console.error(`WebSocket [${channel}] 错误:`, e);
        };

        // 心跳
        ws._heartbeat = setInterval(() => {
            if (ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 30000);

        this.connections[channel] = ws;
        return ws;
    }

    disconnect(channel) {
        const ws = this.connections[channel];
        if (ws) {
            clearInterval(ws._heartbeat);
            ws.close();
            delete this.connections[channel];
        }
    }

    disconnectAll() {
        Object.keys(this.connections).forEach(ch => this.disconnect(ch));
    }
}

const wsManager = new WSManager();

// ============================================================
// 页面模板缓存
// ============================================================

const pageTemplates = {};

async function loadPageTemplate(name) {
    if (pageTemplates[name]) return pageTemplates[name];
    try {
        const resp = await fetch(`/static/${name}.html`);
        const html = await resp.text();
        pageTemplates[name] = html;
        return html;
    } catch (e) {
        console.error(`加载页面模板失败: ${name}`, e);
        return `<div class="text-center py-8 text-gray-500">页面加载失败: ${name}</div>`;
    }
}

// ============================================================
// Alpine.js 主应用
// ============================================================

function app() {
    return {
        // 导航
        currentPage: 'dashboard',
        // 布局编辑（阶段7）：编辑模式 + 恢复默认
        editLayout: false,
        // 图标由 index.html 顶部的 <symbol> sprite 提供，按 id 引用（#i-<id>），
        // 继承 currentColor，主题切换时自动变色，无需重新注入。
        navItems: [
            { id: 'dashboard', label: '仪表盘' },
            { id: 'config', label: 'API配置' },
            { id: 'backtest', label: '回测面板' },
            { id: 'agents', label: 'Agent监控' },
            { id: 'workshop', label: '策略工坊' },
            { id: 'memory', label: '记忆浏览器' },
        ],

        // 系统状态
        systemStatus: 'healthy',
        systemInfo: { version: '1.0.0', mode: 'backtest', uptime_seconds: 0, agent_count: 6, memory_count: 0, tool_count: 7 },
        healthComponents: {},
        wsConnected: false,

        // 共享数据
        agentList: [],
        memoryStats: { total_memories: 0, total_entries_ever: 0, by_type: {}, by_agent: {} },
        backtestHistory: [],
        pipelineHistory: [],

        // Toast通知
        toasts: [],
        _toastId: 0,

        // 页面模板缓存
        _loadedPages: {},

        // 主题与配色（纯视图层偏好，存 localStorage，不进后端配置）
        theme: 'dark',
        scheme: 'cn',
        themeId: 'mono-dark',
        themes: [],
        // 壁纸个性化（本浏览器生效）：上传即应用 + 蒙版不透明度 + 持久化
        wallpaperMsg: '',
        wallpaperOk: false,
        wallpaperPreviewUrl: '',
        wallpaperOverlay: 0.5,

        get currentPageTitle() {
            const item = this.navItems.find(n => n.id === this.currentPage);
            return item ? item.label : '';
        },

        async init() {
            window.__alpineApp = this;

            // 初始化主题：首屏已由 index.html 同步脚本用快照注入，这里做异步校准
            this.initTheme();

            // 布局系统（阶段7）：默认页 dashboard 模板注入后初始化
            setTimeout(() => LayoutKit.init('dashboard'), 200);
            setTimeout(() => LayoutKit.init('dashboard'), 800);

            // 加载系统数据
            await this.loadSystemData();

            // 建立WebSocket连接
            this.setupWebSocket();

            // 定期刷新数据
            setInterval(() => this.loadSystemData(), 30000);
        },

        // ---------- 主题与配色 ----------
        _lsGet(key) {
            try { return localStorage.getItem(key); } catch (e) { return null; }
        },

        _lsSet(key, val) {
            try { localStorage.setItem(key, val); } catch (e) { /* 隐私模式下忽略 */ }
        },

        initTheme() {
            this.theme = this._lsGet('fh.theme.mode') || 'dark';
            this.scheme = this._lsGet('fh.scheme') || 'cn';
            this.themeId = this._lsGet('fh.theme.id')
                || (this.theme === 'dark' ? 'mono-dark' : 'mono-light');
            this.refreshThemes(true);
            // 恢复用户上传的壁纸（独立于主题，刷新/重启后仍生效）
            this._initWallpaperFromStore();
        },

        // ---------- 壁纸个性化（上传即生效 + 蒙版 + 持久化） ----------
        _userWallpaper() {
            try {
                const raw = localStorage.getItem('fh.theme.wallpaper');
                return raw ? JSON.parse(raw) : null;
            } catch (_) { return null; }
        },

        _initWallpaperFromStore() {
            const wp = this._userWallpaper();
            if (wp && wp.url) {
                this.wallpaperPreviewUrl = wp.url;
                this.wallpaperOverlay = typeof wp.overlay === 'number' ? wp.overlay : 0.5;
                this.wallpaperOk = true;
                this.wallpaperMsg = '壁纸已加载（本浏览器保存）';
            }
            // 主题应用后由 applyTheme 里的 _applyWallpaper 统一渲染
        },

        async uploadWallpaper(event) {
            const input = event.target;
            const file = input.files && input.files[0];
            if (!file) return;

            this.wallpaperMsg = '上传中...';
            this.wallpaperOk = false;
            const form = new FormData();
            form.append('file', file);
            try {
                const res = await fetch('/api/themes/wallpaper', { method: 'POST', body: form });
                const json = await res.json();
                if (json && json.success) {
                    const wp = { url: json.data.url, overlay: this.wallpaperOverlay };
                    try { localStorage.setItem('fh.theme.wallpaper', JSON.stringify(wp)); } catch (_) {}
                    this.wallpaperPreviewUrl = wp.url;
                    this.wallpaperOk = true;
                    this.wallpaperMsg = '壁纸已生效（本浏览器保存，刷新后仍保留）';
                    this._applyWallpaper(wp);
                } else {
                    this.wallpaperOk = false;
                    this.wallpaperMsg = (json && json.detail) || (json && json.message) || '上传失败';
                }
            } catch (e) {
                this.wallpaperOk = false;
                this.wallpaperMsg = '上传失败：' + (e && e.message ? e.message : '网络错误');
            } finally {
                input.value = '';
            }
        },

        clearWallpaper() {
            try { localStorage.removeItem('fh.theme.wallpaper'); } catch (_) {}
            this.wallpaperPreviewUrl = '';
            this.wallpaperOk = false;
            this.wallpaperMsg = '壁纸已移除（回落到主题自带壁纸，如有）';
            this._applyWallpaper(null);
        },

        setWallpaperOverlay(v) {
            this.wallpaperOverlay = v;
            const wp = this._userWallpaper();
            if (wp && wp.url) {
                wp.overlay = v;
                try { localStorage.setItem('fh.theme.wallpaper', JSON.stringify(wp)); } catch (_) {}
                this._applyWallpaper(wp);
            }
        },

        _applyWallpaper(wp) {
            const root = document.documentElement;
            const keys = ['--wallpaper-url', '--wallpaper-size', '--wallpaper-repeat',
                          '--wallpaper-position', '--wallpaper-overlay', '--wallpaper-blur'];
            if (!wp || !wp.url) {
                keys.forEach(k => root.style.removeProperty(k));
                return;
            }
            const mode = wp.mode || 'cover';
            root.style.setProperty('--wallpaper-url', `url("${wp.url}")`);
            root.style.setProperty('--wallpaper-size',
                mode === 'contain' ? 'contain' : (mode === 'tile' ? 'auto' : 'cover'));
            root.style.setProperty('--wallpaper-repeat', mode === 'tile' ? 'repeat' : 'no-repeat');
            root.style.setProperty('--wallpaper-position', wp.position || 'center');
            // 默认 0.5 遮罩，避免壁纸喧宾夺主影响文字可读性
            root.style.setProperty('--wallpaper-overlay', String(wp.overlay != null ? wp.overlay : 0.5));
            root.style.setProperty('--wallpaper-blur', `${wp.blur || 0}px`);
        },

        async refreshThemes(apply) {
            try {
                const res = await fetch('/api/themes');
                const json = await res.json();
                if (json && json.success) this.themes = json.data.items || [];
            } catch (e) {
                return; // 主题服务不可用时沿用 CSS 内置默认值
            }
            if (apply) await this.applyTheme(this.themeId, this.theme, this.scheme);
        },

        async applyTheme(themeId, mode, scheme) {
            try {
                const res = await fetch('/api/themes/' + encodeURIComponent(themeId));
                const json = await res.json();
                if (!json || !json.success || !json.data) return;

                const data = json.data;
                const tokens = data.tokens || {};
                if (window.__fhApplyTokens) window.__fhApplyTokens(tokens);
                // 用户上传的壁纸优先于主题自带壁纸
                this._applyWallpaper(this._userWallpaper() || data.wallpaper);

                const nextMode = mode || data.mode || 'dark';
                const nextScheme = scheme || data.scheme || 'cn';
                const root = document.documentElement;
                root.dataset.theme = nextMode;
                root.dataset.scheme = nextScheme;
                root.classList.toggle('dark', nextMode === 'dark');

                this.themeId = themeId;
                this.theme = nextMode;
                this.scheme = nextScheme;

                // 快照供首屏同步注入（防闪烁），同时记住用户选择
                this._lsSet('fh.theme.tokens', JSON.stringify(tokens));
                this._lsSet('fh.theme.id', themeId);
                this._lsSet('fh.theme.mode', nextMode);
                this._lsSet('fh.scheme', nextScheme);

                // 自定义样式仅对非内置主题生效（内置主题保持纯净）
                this._applyCustomCss(data.type === 'builtin' ? null : data.customCss);
                this.repaintCharts();
            } catch (e) {
                // 主题拉取失败不应阻断主流程，沿用 CSS 内置默认
            }
        },

        toggleTheme() {
            // 明暗切换功能：已由"主题下拉框"覆盖（mono-dark / mono-light 等主题本身含明暗变体），
            // 独立快捷入口去除；保留方法以防外部调用残留。
            const next = this.theme === 'dark' ? 'light' : 'dark';
            const isBuiltin = this.themeId === 'mono-dark' || this.themeId === 'mono-light';
            const target = isBuiltin ? (next === 'dark' ? 'mono-dark' : 'mono-light') : this.themeId;
            this.applyTheme(target, next, this.scheme);
        },

        setScheme(scheme) {
            if (this.scheme === scheme) return;
            this.applyTheme(this.themeId, this.theme, scheme);
        },

        _applyCustomCss(css) {
            let el = document.getElementById('fh-theme-custom-css');
            if (!css) {
                if (el) el.remove();
                return;
            }
            if (!el) {
                el = document.createElement('style');
                el.id = 'fh-theme-custom-css';
                document.head.appendChild(el);
            }
            el.textContent = css;
        },

        repaintCharts() {
            // Chart.js 的颜色固化在 JS 配置里，换主题后必须逐一把新值写回再重绘
            const charts = window.__charts;
            if (!charts || !charts.size) return;
            charts.forEach(ch => {
                try {
                    const o = ch.options;
                    if (o && o.plugins) {
                        if (o.plugins.legend && o.plugins.legend.labels) {
                            o.plugins.legend.labels.color = TK('--chart-axis');
                        }
                        const tip = o.plugins.tooltip;
                        if (tip) {
                            tip.backgroundColor = TK('--chart-tip-bg');
                            tip.titleColor = TK('--chart-line');
                            tip.bodyColor = TK('--chart-axis');
                            tip.borderColor = TK('--chart-tip-line');
                        }
                    }
                    if (o && o.scales) {
                        Object.keys(o.scales).forEach(k => {
                            const s = o.scales[k];
                            if (s.ticks) s.ticks.color = TK('--chart-axis');
                            if (s.grid) s.grid.color = TK('--chart-grid');
                        });
                    }
                    ch.update('none');
                } catch (e) { /* 单个图表异常不影响其他图表 */ }
            });
        },

        navigate(pageId) {
            this.currentPage = pageId;
            // 预加载页面模板
            this.loadPage(pageId);
            // 页面切换时刷新数据
            if (pageId === 'memory') {
                setTimeout(() => this.loadMemoryData(), 100);
            }
            // 布局初始化（阶段7）：模板注入后卡片 DOM 才存在，延迟重试
            if (pageId === 'dashboard' || pageId === 'backtest') {
                setTimeout(() => LayoutKit.init(pageId), 100);
                setTimeout(() => LayoutKit.init(pageId), 500);
            }
        },

        toggleLayoutEdit() {
            this.editLayout = !this.editLayout;
            LayoutKit.setEditing(this.editLayout);
            window.__alpineApp && window.__alpineApp.showToast(
                this.editLayout ? '布局编辑已开启：拖动 ⠿ 排序、宽度按钮调宽、✕ 隐藏' : '布局已保存', 'info');
        },

        resetLayout() {
            LayoutKit.reset();
            window.__alpineApp && window.__alpineApp.showToast('已恢复默认布局', 'success');
        },

        async loadPage(name) {
            if (this._loadedPages[name]) return this._loadedPages[name];
            // 【竞态根治】缓存 Promise 而非结果字符串：Alpine x-html 响应式重估会
            // 触发重复调用 loadPage，若各自 await 模板加载，会二次注入 HTML → 页面
            // 重建 → 旧组件实例的延迟 setTimeout(initChart) 在新 canvas 上覆盖建空图
            // （权益曲线空白的跨实例竞态根源）。缓存 Promise 后重复调用共享同一加载。
            const p = loadPageTemplate(name)
                .then(html => {
                    this._loadedPages[name] = html;
                    return html;
                })
                .catch(err => {
                    // 加载失败：清缓存允许重试
                    delete this._loadedPages[name];
                    throw err;
                });
            this._loadedPages[name] = p;
            return p;
        },

        // 加载系统数据
        async loadSystemData() {
            try {
                const [infoResp, healthResp, agentsResp, memoryResp, historyResp, pipelineResp] = await Promise.allSettled([
                    API.getSystemInfo(),
                    API.getHealth(),
                    API.listAgents(),
                    API.getMemoryStats(),
                    API.getBacktestHistory(),
                    API.getPipelineHistory(),
                ]);

                if (infoResp.status === 'fulfilled' && infoResp.value.success) {
                    this.systemInfo = infoResp.value.data;
                }
                if (healthResp.status === 'fulfilled' && healthResp.value.success) {
                    this.healthComponents = healthResp.value.data.components || {};
                    this.systemStatus = healthResp.value.data.status;
                }
                if (agentsResp.status === 'fulfilled' && agentsResp.value.success) {
                    this.agentList = agentsResp.value.data;
                }
                if (memoryResp.status === 'fulfilled' && memoryResp.value.success) {
                    this.memoryStats = memoryResp.value.data;
                }
                if (historyResp.status === 'fulfilled' && historyResp.value.success) {
                    this.backtestHistory = historyResp.value.data;
                }
                if (pipelineResp.status === 'fulfilled' && pipelineResp.value.success) {
                    this.pipelineHistory = pipelineResp.value.data;
                }
            } catch (e) {
                console.error('加载系统数据失败:', e);
            }
        },

        async loadMemoryData() {
            // 由memory页面的Alpine组件自行处理
        },

        // WebSocket设置
        setupWebSocket() {
            // 回测频道
            wsManager.connect('backtest', (data) => {
                if (window.__alpineApp) {
                    const app = window.__alpineApp;
                    if (app.currentPage === 'backtest' && window.__backtestPage) {
                        window.__backtestPage.handleWSMessage(data);
                    }
                }
            });

            // Agent频道
            wsManager.connect('agents', (data) => {
                if (window.__alpineApp) {
                    const app = window.__alpineApp;
                    if (app.currentPage === 'agents' && window.__agentsPage) {
                        window.__agentsPage.handleWSMessage(data);
                    }
                }
            });

            // 系统频道
            wsManager.connect('system', (data) => {
                console.log('系统事件:', data);
            });
        },

        // Toast通知
        showToast(message, type = 'info') {
            const id = ++this._toastId;
            this.toasts.push({ id, message, type });
            setTimeout(() => this.removeToast(id), 4000);
        },

        removeToast(id) {
            this.toasts = this.toasts.filter(t => t.id !== id);
        },
    };
}

// ============================================================
// 配置页面 Alpine组件
// ============================================================

function configPage() {
    return {
        config: {
            llm: { openai_api_key: '', anthropic_api_key: '', openai_base_url: '', model: 'gpt-4o', temperature: 0.3, max_tokens: 4096, provider: 'openai' },
            data: { tushare_token: '', data_dir: './data' },
            risk: { max_position_pct: 30, max_drawdown_pct: 15, max_daily_loss_pct: 5, var_confidence: 0.95, stop_loss_pct: 5, take_profit_pct: 10 },
            backtest: { slippage: 0.001, commission_rate: 0.0003, stamp_tax_rate: 0.001 },
        },
        // 服务商预置表（与后端 PROVIDER_PRESETS 保持一致）
        PROVIDER_PRESETS: {
            orca: { label: 'OrcaRouter', base_url: 'https://api.orcarouter.ai/v1', default_model: 'orcarouter/auto' },
            deepseek: { label: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', default_model: 'deepseek-chat' },
            openai: { label: 'OpenAI', base_url: 'https://api.openai.com/v1', default_model: 'gpt-4o' },
            zhipu: { label: '智谱AI', base_url: 'https://open.bigmodel.cn/api/paas/v4', default_model: 'glm-4-plus' },
        },
        // 7 个 Agent 独立 LLM 配置
        agentDefs: [
            { name: 'market_analyzer', label: '市场分析' },
            { name: 'news_analyst', label: '新闻社媒' },
            { name: 'fundamental_analyst', label: '基本面' },
            { name: 'micro_event_agent', label: '微观事件' },
            { name: 'strategy_generator', label: '多空研究员' },
            { name: 'risk_manager', label: '风控' },
            { name: 'trade_executor', label: '交易执行' },
        ],
        agentConfigs: [],
        // 服务商选择（临时状态，不写入 llm.provider——后者恒为协议值 openai/anthropic）
        providerSelection: 'openai',
        testing: { openai: false, anthropic: false, akshare: false, tushare: false },
        testResults: { openai: null, anthropic: null, akshare: null, tushare: null },
        saving: false,
        sharing: false,
        shareCode: '',
        importing: false,
        importCode: '',
        importError: '',
        importSuccess: '',

        async init() {
            await this.loadConfig();
        },

        async loadConfig() {
            try {
                const resp = await API.getFullConfig();
                if (resp.success && resp.data) {
                    const d = resp.data;
                    if (d.llm) Object.assign(this.config.llm, d.llm);
                    if (d.data) Object.assign(this.config.data, d.data);
                    if (d.risk) Object.assign(this.config.risk, d.risk);
                    if (d.backtest) Object.assign(this.config.backtest, d.backtest);
                    // 服务商下拉按 base_url 回显（协议值 openai 不代表服务商）
                    const presets = this.PROVIDER_PRESETS;
                    this.providerSelection = Object.keys(presets).find(k => presets[k].base_url === this.config.llm.openai_base_url) || 'custom';

                    // 初始化 Agent 配置表单
                    const savedAgents = d.agents || {};
                    this.agentConfigs = this.agentDefs.map(def => {
                        const saved = savedAgents[def.name] || {};
                        return {
                            name: def.name,
                            label: def.label,
                            provider: saved.provider || 'openai',
                            providerSel: '',
                            openai_api_key: saved.openai_api_key || '',
                            openai_base_url: saved.openai_base_url || '',
                            model: saved.model || '',
                            followGlobal: !(saved.provider || saved.openai_api_key || saved.openai_base_url || saved.model),
                        };
                    });
                    // 回显 per-Agent 服务商下拉
                    this.agentConfigs.forEach(ac => {
                        ac.providerSel = Object.keys(presets).find(k => presets[k].base_url === ac.openai_base_url) || 'custom';
                    });
                }
            } catch (e) {
                console.error('加载配置失败:', e);
            }
        },

        // 选择预置服务商时自动填充 base_url 与默认 model
        // 注意：llm.provider 恒为"协议"（openai/anthropic），服务商名只用于预填
        // base_url/model，不写入 provider（调用层按协议分发）。
        applyProviderPreset(scope, agentCfg = null) {
            const presets = this.PROVIDER_PRESETS;
            if (scope === 'llm') {
                const target = this.config.llm;
                const sel = this.providerSelection;
                if (presets[sel]) {
                    target.openai_base_url = presets[sel].base_url;
                    if (!target.model) target.model = presets[sel].default_model;
                }
                target.provider = 'openai';  // 协议值
            } else if (agentCfg) {
                const sel = agentCfg.providerSel;
                if (presets[sel]) {
                    agentCfg.openai_base_url = presets[sel].base_url;
                    if (!agentCfg.model) agentCfg.model = presets[sel].default_model;
                }
                agentCfg.provider = 'openai';  // 协议值
                // 更新跟随全局状态
                agentCfg.followGlobal = !(agentCfg.provider || agentCfg.openai_api_key || agentCfg.openai_base_url || agentCfg.model);
            }
        },

        // 勾选"跟随全局"时清空该 Agent 覆盖字段
        syncFollowGlobal(agentCfg) {
            if (agentCfg.followGlobal) {
                agentCfg.provider = '';
                agentCfg.openai_api_key = '';
                agentCfg.openai_base_url = '';
                agentCfg.model = '';
            }
        },

        // 清除单 Agent 覆盖
        clearAgentOverride(agentCfg) {
            agentCfg.provider = '';
            agentCfg.openai_api_key = '';
            agentCfg.openai_base_url = '';
            agentCfg.model = '';
            agentCfg.followGlobal = true;
        },

        // 构建 agents 段 payload（只含非空覆盖字段；provider 恒为协议 openai，不提交）
        buildAgentsPayload() {
            const agents = {};
            for (const a of this.agentConfigs) {
                const cfg = {};
                if (a.openai_api_key) cfg.openai_api_key = a.openai_api_key;
                if (a.openai_base_url) cfg.openai_base_url = a.openai_base_url;
                if (a.model) cfg.model = a.model;
                // 有覆盖才提交
                if (Object.keys(cfg).length > 0) {
                    agents[a.name] = cfg;
                }
            }
            return agents;
        },

        togglePassword(event) {
            const input = event.target.closest('div').querySelector('input');
            input.type = input.type === 'password' ? 'text' : 'password';
        },

        async testConnection(provider, protocol = 'openai') {
            this.testing[provider] = true;
            this.testResults[provider] = null;
            try {
                let apiKey = null;
                let baseUrl = null;
                if (provider === 'openai') {
                    apiKey = this.config.llm.openai_api_key;
                    baseUrl = this.config.llm.openai_base_url;
                } else if (provider === 'anthropic') {
                    apiKey = this.config.llm.anthropic_api_key;
                }

                const resp = await API.testConnection(provider, apiKey, baseUrl, protocol);
                if (resp.success && resp.data) {
                    this.testResults[provider] = resp.data;
                } else {
                    this.testResults[provider] = { 
                        success: false, 
                        message: resp.message || '连接测试失败' 
                    };
                }
            } catch (e) {
                this.testResults[provider] = { success: false, message: e.message };
            } finally {
                this.testing[provider] = false;
            }
        },

        async testDataSource(source) {
            this.testing[source] = true;
            this.testResults[source] = null;
            try {
                const token = source === 'tushare' ? this.config.data.tushare_token : null;
                const resp = await API.testDataSource(source, token);
                if (resp.success && resp.data) {
                    this.testResults[source] = resp.data;
                } else {
                    this.testResults[source] = { success: false, message: resp.message || '数据源测试失败' };
                }
            } catch (e) {
                this.testResults[source] = { success: false, message: e.message };
            } finally {
                this.testing[source] = false;
            }
        },

        async saveConfig() {
            this.saving = true;
            try {
                // 先更新配置（含 per-Agent 覆盖）
                await API.updateConfig({
                    llm: this.config.llm,
                    data: this.config.data,
                    risk: this.config.risk,
                    execution: this.config.backtest,
                    agents: this.buildAgentsPayload(),
                });
                // 再保存到文件（后端保存后自动重建 Agent 系统）
                const saveResp = await API.saveConfig();
                const msg = saveResp && saveResp.message ? saveResp.message : '配置已保存';
                window.__alpineApp.showToast(msg, 'success');
            } catch (e) {
                window.__alpineApp.showToast('保存失败: ' + e.message, 'error');
            } finally {
                this.saving = false;
            }
        },

        resetConfig() {
            this.loadConfig();
            window.__alpineApp.showToast('配置已重置', 'info');
        },

        async shareStrategy() {
            this.sharing = true;
            this.shareCode = '';
            try {
                // 构建策略配置
                const strategyConfig = {
                    strategy: 'dual_thrust', // 默认策略
                    config: {
                        llm: this.config.llm,
                        data: this.config.data,
                        risk: this.config.risk,
                        backtest: this.config.backtest,
                    },
                };

                const response = await fetch('/api/export/strategy/share', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ strategy_config: strategyConfig }),
                });

                const result = await response.json();
                if (result.success) {
                    this.shareCode = result.data.share_code;
                    window.__alpineApp.showToast('分享码生成成功', 'success');
                } else {
                    throw new Error(result.message || '生成失败');
                }
            } catch (e) {
                window.__alpineApp.showToast('生成分享码失败: ' + e.message, 'error');
            } finally {
                this.sharing = false;
            }
        },

        copyShareCode() {
            if (navigator.clipboard && this.shareCode) {
                navigator.clipboard.writeText(this.shareCode).then(() => {
                    window.__alpineApp.showToast('分享码已复制到剪贴板', 'success');
                }).catch(() => {
                    // Fallback
                    const input = document.createElement('input');
                    input.value = this.shareCode;
                    document.body.appendChild(input);
                    input.select();
                    document.execCommand('copy');
                    document.body.removeChild(input);
                    window.__alpineApp.showToast('分享码已复制', 'success');
                });
            }
        },

        async importStrategy() {
            if (!this.importCode) return;

            this.importing = true;
            this.importError = '';
            this.importSuccess = '';

            try {
                const response = await fetch('/api/export/strategy/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ share_code: this.importCode }),
                });

                const result = await response.json();
                if (result.success) {
                    const config = result.data.strategy_config;
                    if (config && config.config) {
                        // 应用导入的配置
                        if (config.config.llm) Object.assign(this.config.llm, config.config.llm);
                        if (config.config.data) Object.assign(this.config.data, config.config.data);
                        if (config.config.risk) Object.assign(this.config.risk, config.config.risk);
                        if (config.config.backtest) Object.assign(this.config.backtest, config.config.backtest);

                        this.importSuccess = '策略配置导入成功';
                        window.__alpineApp.showToast('策略配置已导入', 'success');
                    }
                } else {
                    throw new Error(result.message || '导入失败');
                }
            } catch (e) {
                this.importError = '导入失败: ' + e.message;
                window.__alpineApp.showToast('导入策略失败: ' + e.message, 'error');
            } finally {
                this.importing = false;
            }
        },
    };
}

// ============================================================
// 回测页面 Alpine组件
// ============================================================

function backtestPage() {
    return {
        params: {
            strategy: 'dual_thrust',
            symbols: '600519.SH',
            start_date: '2024-01-01',
            end_date: '2024-12-31',
            initial_capital: 1000000,
            strategies: [],              // 多策略组合（≥2 时启用信号聚合+滤波）
            enableHighCostFilters: false, // 高开销滤波器（Transformer/粒子滤波）
            strategy_params: {},         // 策略参数临时覆盖（回测时传 runner.params）
        },
        strategyOptions: [],  // 内置 + 工坊自有策略
        paramMeta: [],        // 当前策略的参数元数据（[{name, default}]，回测面板"参数调整"区）
        paramsMetaAll: {},    // 全部策略参数元数据（/api/backtest/strategies 的 params 字段）
        running: false,
        progress: 0,
        progressMessage: '',
        metrics: {},
        trades: [],
        history: [],
        tradePage: 1,
        strategyDropdownOpen: false,  // 自定义策略下拉展开状态（原生 select 在部分浏览器不可靠）
        equityChart: null,
        returnsChart: null,
        currentTaskId: null,
        exporting: false,
        currentResult: null,
        validation: null,   // 策略验证报告（StrategyValidator 7 项）
        confidence: null,   // 置信度合成（阶段3）：{score, tier, factors}
        activeTrade: null,  // 交易溯源弹窗数据（表格详情/权益曲线点击共用）
        sweep: {
            strategy: 'dual_thrust',
            axes: {
                x: { name: 'k1', min: 0.2, max: 0.8, step: 0.1 },
                y: { name: 'k2', min: 0.2, max: 0.8, step: 0.1 },
            },
            registry: {},        // 策略 → 可扫描参数对
            helpOpen: false,     // 热力图帮助浮窗显示开关
            cells: [],           // SweepCell[]
            best: null,
            running: false,
            progress: 0,
            error: '',
            sweepTaskId: null,
            axisOptions(axis) {
                const rx = this.registry[this.strategy] || {};
                const self = this.axes[axis];
                return [rx.x, rx.y].filter(Boolean).map(p => ({ ...p, name: p.name }));
            },
            onStrategyChange() {
                const meta = this.registry[this.strategy];
                if (meta) {
                    this.axes.x = { name: meta.x.name, min: meta.x.min, max: meta.x.max, step: meta.x.step };
                    this.axes.y = { name: meta.y.name, min: meta.y.min, max: meta.y.max, step: meta.y.step };
                }
                this.cells = []; this.best = null; this.error = '';
            },
            onAxisChange(axis) {
                // 切换参数时套用元数据默认范围
                const meta = this.registry[this.strategy] || {};
                const p = meta[axis] || {};
                if (p.name === this.axes[axis].name) {
                    this.axes[axis].min = p.min;
                    this.axes[axis].max = p.max;
                    this.axes[axis].step = p.step;
                }
                this.cells = []; this.best = null; this.error = '';
            },
            async loadRegistry() {
                try {
                    const resp = await API.get('/api/backtest/sweep/params');
                    if (resp.success && resp.data) {
                        this.registry = resp.data;
                        this.onStrategyChange();
                    }
                } catch (e) {
                    console.error('加载扫描参数元数据失败:', e);
                }
            },
            async start() {
                if (this.running) return;
                this.error = '';
                const payload = {
                    strategy: this.strategy,
                    symbol: window.__backtestPage.params.symbols.split(',')[0].trim() || '600519.SH',
                    start_date: window.__backtestPage.params.start_date,
                    end_date: window.__backtestPage.params.end_date,
                    initial_capital: window.__backtestPage.params.initial_capital,
                    x_param: { name: this.axes.x.name, label: this.axes.x.name, min: this.axes.x.min, max: this.axes.x.max, step: this.axes.x.step },
                    y_param: { name: this.axes.y.name, label: this.axes.y.name, min: this.axes.y.min, max: this.axes.y.max, step: this.axes.y.step },
                };
                try {
                    const resp = await API.post('/api/backtest/sweep', payload);
                    if (resp.success && resp.data) {
                        this.running = true;
                        this.progress = 5;
                        this.sweepTaskId = resp.data.task_id;
                        this.pollSweep();
                    } else {
                        this.error = (resp.error || resp.message) || '扫描启动失败';
                    }
                } catch (e) {
                    this.error = '扫描启动失败: ' + (e.message || e);
                }
            },
            async pollSweep() {
                // 轮询直到完成（WS sweep_progress 只更新进度，结果走拉取）
                const interval = setInterval(async () => {
                    if (!this.sweepTaskId) { clearInterval(interval); return; }
                    try {
                        const resp = await API.get(`/api/backtest/sweep/${this.sweepTaskId}`);
                        if (resp.success && resp.data) {
                            const d = resp.data;
                            if (d.error) { this.error = d.error; this.running = false; clearInterval(interval); return; }
                            if (d.cells && d.cells.length) {
                                this.cells = d.cells;
                                this.best = d.best || null;
                                this.running = false;
                                this.progress = 100;
                                clearInterval(interval);
                                this.renderHeatmap();
                            }
                        }
                    } catch (e) { /* keep polling */ }
                }, 1500);
                // 兜底：60s 超时停止轮询
                setTimeout(() => { if (this.running) { this.running = false; this.error = '扫描超时'; clearInterval(interval); } }, 60000);
            },
            renderHeatmap() {
                // sweep 是 backtestPage 的子对象，此处 this=sweep，没有 $refs
                // 必须用 querySelector 跨上下文拿 svg 元素（x-ref 在 DOM 全局唯一）
                const svg = document.querySelector('[x-ref="heatmap"]');
                if (!svg) {
                    console.warn('[renderHeatmap] 未找到 [x-ref="heatmap"] 元素');
                    return;
                }
                if (!this.cells.length) return;
                const cells = this.cells;
                // 网格维度
                const xs = [...new Set(cells.map(c => c.x))].sort((a, b) => a - b);
                const ys = [...new Set(cells.map(c => c.y))].sort((a, b) => b - a); // y 轴自上而下
                const cellW = 56, cellH = 28, padL = 46, padT = 22;
                const W = padL + xs.length * cellW + 8;
                const H = padT + ys.length * cellH + 6;
                // 色阶：sharpe 归一化到 [-1,1] → 低=绿(--down)、高=红(--up)
                const sh = cells.map(c => c.sharpe);
                const lo = Math.min(...sh), hi = Math.max(...sh);
                const range = (hi - lo) || 1;
                const upRGB = TK('--up'), downRGB = TK('--down');
                const parseColor = (str) => {
                    if (!str) return [128, 128, 128];
                    const m = String(str).match(/rgb\(([^)]+)\)/);
                    if (m) return m[1].split(',').map(s => parseInt(s.trim(), 10));
                    const h = String(str).replace('#', '');
                    if (/^[0-9a-fA-F]{6}$/.test(h)) return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
                    return [128, 128, 128];
                };
                const mix = (t) => {
                    const a = parseColor(upRGB), b = parseColor(downRGB);
                    const r = Math.round(b[0] + (a[0] - b[0]) * t);
                    const g = Math.round(b[1] + (a[1] - b[1]) * t);
                    const bl = Math.round(b[2] + (a[2] - b[2]) * t);
                    return `rgb(${r}, ${g}, ${bl})`;
                };
                svg.setAttribute('width', W);
                svg.setAttribute('height', H);
                svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
                svg.innerHTML = '';
                // 轴标签
                for (let i = 0; i < xs.length; i++) {
                    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                    t.setAttribute('x', padL + i * cellW + cellW / 2);
                    t.setAttribute('y', padT - 6);
                    t.setAttribute('text-anchor', 'middle');
                    t.setAttribute('fill', 'var(--fg-subtle)');
                    t.setAttribute('font-size', '10');
                    t.textContent = String(xs[i]);
                    svg.appendChild(t);
                }
                for (let j = 0; j < ys.length; j++) {
                    const t = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                    t.setAttribute('x', padL - 6);
                    t.setAttribute('y', padT + j * cellH + cellH / 2 + 3);
                    t.setAttribute('text-anchor', 'end');
                    t.setAttribute('fill', 'var(--fg-subtle)');
                    t.setAttribute('font-size', '10');
                    t.textContent = String(ys[j]);
                    svg.appendChild(t);
                }
                // 单元格
                const cellMap = new Map(cells.map(c => [c.x + '|' + c.y, c]));
                for (let j = 0; j < ys.length; j++) {
                    for (let i = 0; i < xs.length; i++) {
                        const c = cellMap.get(xs[i] + '|' + ys[j]);
                        if (!c) continue;
                        const t = (c.sharpe - lo) / range;
                        const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                        rect.setAttribute('x', padL + i * cellW);
                        rect.setAttribute('y', padT + j * cellH);
                        rect.setAttribute('width', cellW - 2);
                        rect.setAttribute('height', cellH - 2);
                        rect.setAttribute('rx', '2');
                        rect.setAttribute('fill', mix(t));
                        const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
                        title.textContent = `${this.axes.x.name}=${c.x}, ${this.axes.y.name}=${c.y} | 夏普 ${c.sharpe.toFixed(2)} | 收益 ${c.total_return}% | 回撤 ${c.max_drawdown}%`;
                        rect.appendChild(title);
                        svg.appendChild(rect);
                        // 中心数值（夏普）
                        const txt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
                        txt.setAttribute('x', padL + i * cellW + (cellW - 2) / 2);
                        txt.setAttribute('y', padT + j * cellH + (cellH - 2) / 2 + 3);
                        txt.setAttribute('text-anchor', 'middle');
                        txt.setAttribute('fill', '#ffffff');
                        txt.setAttribute('font-size', '9');
                        txt.textContent = c.sharpe.toFixed(2);
                        svg.appendChild(txt);
                    }
                }
            },
        },
        signalLog: {
            rows: [], allRows: [], total: 0, sampled: false,
            extraKeys: [], strategies: [],
            strategyFilter: '', directionFilter: '', onlyTriggered: false,
            // 值格式化：null/NaN → —；数字 → 2 位小数；枚举串原样
            fmt(v) {
                if (v === null || v === undefined) return '—';
                if (typeof v === 'number' && isFinite(v)) return Number(v).toFixed(2);
                return String(v);
            },
            applyFilters() {
                const rows = this.allRows.filter(row => {
                    if (this.onlyTriggered && (!row.signals || !row.signals.length)) return false;
                    if (this.directionFilter && (!row.signals || !row.signals.some(s => s.direction === this.directionFilter))) return false;
                    if (this.strategyFilter && (!row.signals || !row.signals.some(s => (s.strategy_name || '') === this.strategyFilter))) return false;
                    return true;
                });
                this.rows = rows;
            },
            async reload() {
                if (window.__backtestPage && window.__backtestPage.currentTaskId) {
                    await window.__backtestPage.loadSignalLog(window.__backtestPage.currentTaskId);
                }
            },
        },

        async init() {
            window.__backtestPage = this;
            await Promise.all([this.loadStrategies(), this.loadHistory(), this.sweep.loadRegistry()]);
            // 回测页模板经 x-html 异步注入，Alpine init 时 canvas 可能尚未挂载，
            // 仅靠 $nextTick 会漏建图表 → 权益曲线永远空白。延时重试确保创建。
            // 用 ensureChart（含实例健康校验 + Chart.js 缺失 toast 提示）而非裸 initChart。
            this.$nextTick(() => this.ensureChart());
            setTimeout(() => this.ensureChart(), 300);
            setTimeout(() => this.ensureChart(), 1000);
        },

        async loadStrategies() {
            const BUILTIN_LABELS = {
                dual_thrust: 'Dual Thrust',
                momentum: '动量策略',
                mean_reversion: '均值回归',
                ml_strategy: 'ML策略',
                micro_cap: '小市值策略',
                event_driven: '事件驱动策略',
                sentiment_reversal: '情绪反转策略',
                dragon_tiger_follow: '龙虎榜跟随策略',
                alternative_cross: '另类数据交叉策略',
            };
            try {
                const resp = await API.get('/api/backtest/strategies');
                if (resp.success && resp.data) {
                    const opts = [];
                    for (const id of resp.data.builtin || []) {
                        opts.push({ id, name: BUILTIN_LABELS[id] || id });
                    }
                    for (const s of resp.data.custom || []) {
                        opts.push({
                            id: s.id,
                            name: `[自有] ${s.name}${s.status === 'enabled' ? '' : '（未验证）'}`,
                            disabled: s.status !== 'enabled',  // 阶段6：未验证策略置灰
                        });
                    }
                    this.strategyOptions = opts;
                    // 参数元数据：内置前端 BUILTIN_PARAMS；工坊策略从 manifest.params_schema；
                    // 后端如返回 params 字段则优先合并（向后兼容）
                    this.paramsMetaAll = { ...BUILTIN_PARAMS, ...(resp.data.params || {}) };
                    for (const s of resp.data.custom || []) {
                        const schema = s.params_schema || [];
                        this.paramsMetaAll[s.id] = schema.map(p => ({
                            name: p.name || p.key,
                            default: p.default !== undefined ? p.default : null,
                        })).filter(p => p.name);
                    }
                    this.syncParamMeta();
                }
            } catch (e) {
                console.error('加载策略列表失败:', e);
            }
        },

        // 策略切换时同步参数元数据；保留已填的覆盖值（按参数名）
        syncParamMeta() {
            const meta = (this.paramsMetaAll && this.paramsMetaAll[this.params.strategy]) || [];
            this.paramMeta = meta;
            if (!this.params.strategy_params) this.params.strategy_params = {};
            for (const p of meta) {
                if (!(p.name in this.params.strategy_params)) {
                    this.params.strategy_params[p.name] = p.default !== null && p.default !== undefined ? p.default : undefined;
                }
            }
        },

        async loadHistory() {
            try {
                const resp = await API.getBacktestHistory();
                if (resp.success) this.history = resp.data;
            } catch (e) {
                console.error('加载回测历史失败:', e);
            }
        },

        initChart() {
            // Chart.js 未加载（本地 vendor 也不可用）时静默跳过，由 ensureChart 提示
            if (typeof Chart === 'undefined') return;
            const canvas = document.getElementById('equity-chart');
            if (!canvas) return;

            // 【跨实例竞态根治】陈旧 Alpine 实例的延迟 setTimeout(initChart, 1000)
            // 在页面重建后的新 canvas 上执行时，this.equityChart 可能持有旧 canvas
            // 上的图表：数据守卫会误判"已有数据"而 return，或 destroy 后在新 canvas
            // 上建空图。先校验 canvas 归属：chart 不在当前 DOM canvas 上 → 销毁重置。
            if (this.equityChart && this.equityChart.canvas !== canvas) {
                console.log('[initChart] Stale chart on different canvas, destroying');
                try { this.equityChart.destroy(); } catch (_) { /* ignore */ }
                this.equityChart = null;
            }

            // 【关键修复】若已存在带数据的图表（renderEquityChart 已建好），不再销毁重建。
            // 旧代码无条件 destroy + 建空图，与 renderEquityChart 形成竞态：
            //   回测快速完成(<1s) → renderEquityChart 建数据图 → 1000ms 后 initChart() 销毁它 → 空白画布+Y轴0~1
            if (this.equityChart) {
                try {
                    const ds = this.equityChart.data?.datasets;
                    if (ds && ds[0] && ds[0].data && ds[0].data.length > 0) {
                        console.log('[initChart] Chart already has data (' + ds[0].data.length + ' points), skipping');
                        return;
                    }
                } catch (_) { /* fall through to destroy stale instance */ }
                this.equityChart.destroy();
            }

            const ctx = canvas.getContext('2d');
            this.equityChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [
                        {
                            label: '策略权益',
                            data: [],
                            borderColor: TK('--chart-line'),
                            backgroundColor: 'rgba(59, 130, 246, 0.1)',
                            fill: true,
                            tension: 0.3,
                            pointRadius: 0,
                            borderWidth: 2,
                        },
                        {
                            label: '基准',
                            data: [],
                            borderColor: TK('--chart-line2'),
                            backgroundColor: 'transparent',
                            borderDash: [5, 5],
                            tension: 0.3,
                            pointRadius: 0,
                            borderWidth: 1,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { intersect: false, mode: 'index' },
                    plugins: {
                        legend: {
                            labels: { color: TK('--chart-axis'), font: { size: 11 } },
                        },
                        tooltip: {
                            backgroundColor: TK('--chart-tip-bg'),
                            titleColor: TK('--chart-line'),
                            bodyColor: TK('--chart-axis'),
                            borderColor: TK('--chart-tip-line'),
                            borderWidth: 1,
                        },
                    },
                    scales: {
                        x: {
                            ticks: { color: TK('--chart-axis'), font: { size: 10 }, maxTicksLimit: 10 },
                            grid: { color: 'rgba(51, 65, 85, 0.3)' },
                        },
                        y: {
                            ticks: {
                                color: TK('--chart-grid'),
                                font: { size: 10 },
                                // 自适应金额刻度（万/亿），避免恒定数据/空数据时显示错误的"0万"
                                callback: (v) => {
                                    const n = Number(v);
                                    if (!isFinite(n)) return String(v);
                                    if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(1) + '亿';
                                    if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + '万';
                                    return String(n);
                                },
                            },
                            grid: { color: 'rgba(51, 65, 85, 0.3)' },
                        },
                    },
                },
            });
        },

        ensureChart() {
            // 页面切换（x-html 重新注入）后旧 chart 实例悬挂在已移除的 canvas 上：
            // 检测到 canvas 不存在或已脱离 DOM → 销毁旧实例等待重建
            const canvas = document.getElementById('equity-chart');
            if (!canvas || !canvas.isConnected) {
                console.log('[ensureChart] Canvas not found or not connected:', !!canvas, canvas?.isConnected);
                if (this.equityChart) {
                    try { this.equityChart.destroy(); } catch (e) { console.warn('[ensureChart] destroy error:', e); }
                    this.equityChart = null;
                }
                return false;
            }
            if (this.equityChart) {
                // 验证 chart 实例仍然关联到有效 canvas（防止 x-html 重建后悬挂）
                try {
                    const ctx = this.equityChart.ctx;
                    if (!ctx || !ctx.canvas) {
                        console.log('[ensureChart] Chart instance has stale context, destroying');
                        this.equityChart.destroy();
                        this.equityChart = null;
                    } else {
                        return true;
                    }
                } catch (e) {
                    console.warn('[ensureChart] Chart validation error, recreating:', e);
                    this.equityChart = null;
                }
            }
            if (typeof Chart === 'undefined') {
                window.__alpineApp && window.__alpineApp.showToast('图表库(Chart.js)加载失败，权益曲线无法显示', 'error');
                return false;
            }
            console.log('[ensureChart] Calling initChart()');
            this.initChart();
            return !!this.equityChart;
        },

        async startBacktest() {
            // 验证
            if (!this.params.symbols.trim()) {
                window.__alpineApp.showToast('请输入标的代码', 'warning');
                return;
            }

            this.running = true;
            this.progress = 0;
            this.progressMessage = '正在启动回测...';
            this.metrics = {};
            this.trades = [];
            this.tradePage = 1;
            this.validation = null;

            try {
                const symbols = this.params.symbols.split(',').map(s => s.trim()).filter(Boolean);
                const payload = {
                    strategy: this.params.strategy,
                    symbols,
                    start_date: this.params.start_date,
                    end_date: this.params.end_date,
                    initial_capital: this.params.initial_capital,
                };
                // 参数覆盖（临时调整区，只改默认值不改参数名）：过滤空值后传给后端
                if (this.params.strategy_params && Object.keys(this.params.strategy_params).length) {
                    const sp = {};
                    for (const [k, v] of Object.entries(this.params.strategy_params)) {
                        if (v !== undefined && v !== null && v !== '') sp[k] = v;
                    }
                    if (Object.keys(sp).length) payload.strategy_params = sp;
                }
                // 多策略组合（≥2）→ 启用信号聚合器+滤波管道，strategy 字段被后端忽略
                if ((this.params.strategies || []).length >= 2) {
                    payload.strategies = this.params.strategies;
                    payload.signal_filters = {
                        enable_high_cost: !!this.params.enableHighCostFilters,
                    };
                }
                const resp = await API.runBacktest(payload);

                if (resp.success) {
                    this.currentTaskId = resp.data.task_id;
                    // WebSocket 事件丢失兜底：轮询任务状态，避免永远卡"运行中"
                    this.startStatusPolling(resp.data.task_id);
                    window.__alpineApp.showToast('回测任务已启动', 'info');
                }
            } catch (e) {
                this.running = false;
                window.__alpineApp.showToast('启动回测失败: ' + e.message, 'error');
            }
        },

        startStatusPolling(taskId) {
            this.stopStatusPolling();
            this._pollTimer = setInterval(async () => {
                try {
                    const resp = await API.getBacktestHistory();
                    const rec = (resp.data || []).find(r => r.task_id === taskId);
                    if (!rec) return; // 任务可能尚未落历史
                    if (rec.status === 'completed') {
                        this.stopStatusPolling();
                        if (this.running) {
                            this.running = false;
                            this.progress = 100;
                            this.progressMessage = '回测完成';
                            this.fetchResult(taskId);
                            this.loadHistory();
                        }
                    } else if (rec.status === 'failed') {
                        this.stopStatusPolling();
                        if (this.running) {
                            this.running = false;
                            this.progressMessage = '回测失败: ' + (rec.error || '任务异常');
                            window.__alpineApp.showToast('回测失败: ' + (rec.error || ''), 'error');
                            this.loadHistory();
                        }
                    }
                } catch (e) { /* 忽略，下轮重试 */ }
            }, 4000);
        },

        stopStatusPolling() {
            if (this._pollTimer) {
                clearInterval(this._pollTimer);
                this._pollTimer = null;
            }
        },

        handleWSMessage(data) {
            // sweep_progress 使用独立 task_id（非回测任务），不被 currentTaskId 过滤
            if (data.type === 'sweep_progress') {
                this.sweep.progress = data.progress || this.sweep.progress;
                return;
            }
            if (data.task_id && data.task_id !== this.currentTaskId) return;

            switch (data.type) {
                case 'backtest_progress':
                    this.progress = data.progress;
                    this.progressMessage = data.current_bar
                        ? `正在处理第 ${data.current_bar}/${data.total_bars} 个交易日`
                        : '回测运行中...';                    // 实时更新权益(简化版)
                    if (data.equity && this.equityChart) {
                        const labels = this.equityChart.data.labels;
                        const equityData = this.equityChart.data.datasets[0].data;
                        labels.push(data.current_bar);
                        equityData.push(data.equity);
                        // 限制显示点数
                        if (labels.length > 200) {
                            labels.shift();
                            equityData.shift();
                        }
                        this.equityChart.update('none');
                    }
                    break;

                case 'backtest_completed':
                    this.stopStatusPolling();
                    this.running = false;
                    this.progress = 100;
                    this.progressMessage = '回测完成';
                    this.metrics = data.metrics || {};
                    this.validation = data.validation || null;
                    this.confidence = null;  // WS 不推置信度，由 fetchResult 填充

                    // 立即从 WS 事件数据渲染权益曲线
                    if (data.equity_curve && data.equity_curve.length > 0) {
                        console.log('[WS backtest_completed] Rendering chart from WS event:', data.equity_curve.length, 'points');
                        this.renderEquityChart(data);
                        this.renderReturnsChart(data);
                        if (data.trades) {
                            this.trades = data.trades;
                        }
                    }

                    // 仍异步获取完整结果（用于导出等需要完整数据的场景）
                    this.fetchResult(data.task_id);
                    break;

                case 'backtest_failed':
                    this.stopStatusPolling();
                    this.running = false;
                    this.progressMessage = '回测失败: ' + (data.error || '未知错误');
                    window.__alpineApp.showToast('回测失败: ' + (data.error || ''), 'error');
                    break;
            }
        },

        async fetchResult(taskId) {
            try {
                const resp = await API.getBacktestResult(taskId);
                if (resp.success && resp.data) {
                    const result = resp.data;
                    this.metrics = result.metrics || {};
                    this.trades = result.trades || [];
                    this.currentResult = result; // 保存完整结果用于导出
                    this.validation = result.validation || null;
                    this.confidence = result.confidence || null;

                    // 渲染权益曲线（采用"销毁重建"策略，避免 Chart 实例与 DOM 脱离导致的空白问题）
                    if (result.equity_curve && result.equity_curve.length > 0) {
                        console.log('[fetchResult] Rendering chart with', result.equity_curve.length, 'data points');
                        this.renderEquityChart(result);
                        this.renderReturnsChart(result);
                    } else {
                        console.warn('[fetchResult] No equity_curve data');
                    }

                    // 刷新历史
                    await this.loadHistory();
                    // 拉取信号调试日志（单独端点，不进 WS 全量推送）
                    await this.loadSignalLog(taskId);
                    window.__alpineApp.showToast('回测完成', 'success');
                }
            } catch (e) {
                console.error('获取回测结果失败:', e);
            }
        },

        showTradeDetail(trade) {
            this.activeTrade = trade;
        },

        async loadSignalLog(taskId) {            try {
                const resp = await API.get(`/api/backtest/${taskId}/signal_log`);
                if (resp.success && resp.data) {
                    const d = resp.data;
                    this.signalLog.allRows = d.rows || [];
                    this.signalLog.total = d.total || 0;
                    this.signalLog.sampled = !!d.sampled;
                    // 动态列：取首个非空行的 extra 键；策略列表：收集全部策略名
                    const keys = new Set();
                    const strats = new Set();
                    for (const row of this.signalLog.allRows) {
                        if (row.extra) Object.keys(row.extra).forEach(k => keys.add(k));
                        if (row.signals) row.signals.forEach(s => { if (s.strategy_name) strats.add(s.strategy_name); });
                    }
                    this.signalLog.extraKeys = Array.from(keys);
                    this.signalLog.strategies = Array.from(strats).sort();
                    this.signalLog.applyFilters();
                }
            } catch (e) {
                console.error('获取信号日志失败:', e);
            }
        },

        /**
         * 销毁旧图表并用数据创建新图表（根治"实例悬挂/数据不更新"问题）
         * @param {Object} result - 包含 equity_curve 和 benchmark_curve 的回测结果
         */
        renderEquityChart(result) {
            const canvas = document.getElementById('equity-chart');
            if (!canvas || !canvas.isConnected) {
                console.warn('[renderEquityChart] canvas not available');
                return;
            }
            if (typeof Chart === 'undefined') {
                console.warn('[renderEquityChart] Chart.js not loaded');
                try {
                    window.__alpineApp && window.__alpineApp.showToast('图表库(Chart.js)加载失败，权益曲线无法显示', 'error');
                } catch (_) { /* ignore */ }
                return;
            }

            // 1. 销毁旧实例（无论状态如何）：
            //    ① canvas 上任何残留实例（跨实例/模板二次注入残留 → 否则
            //       Chart.js 抛 "Canvas is already in use" 被静默 catch → 空白）
            //    ② 本实例持有的引用
            if (typeof Chart.getChart === 'function') {
                const existing = Chart.getChart(canvas);
                if (existing) {
                    console.log('[renderEquityChart] Destroying stale chart on canvas');
                    try { existing.destroy(); } catch (e) { /* ignore */ }
                }
            }
            if (this.equityChart) {
                try { this.equityChart.destroy(); } catch (e) { /* ignore */ }
                this.equityChart = null;
            }

            // 2. 准备数据
            const labels = result.equity_curve.map(p => p.date ? String(p.date).substring(0, 10) : '');
            const equityData = result.equity_curve.map(p => p.equity);
            const benchData = (result.benchmark_curve || []).map(p => p.equity);

            // 2.1 买卖点标记：按 trade.date 对齐 labels 下标，Chart.js 原生
            //     pointRadius/pointStyle/pointBackgroundColor 数组实现（零新依赖）。
            //     买入=红色三角、卖出=绿色菱形（中国红涨绿跌惯例）。
            const labelIndex = new Map(labels.map((d, i) => [d, i]));
            const pointRadius = new Array(labels.length).fill(0);
            const pointStyle = new Array(labels.length).fill('circle');
            const pointBg = new Array(labels.length).fill('transparent');
            const pointBorder = new Array(labels.length).fill('transparent');
            for (const t of (result.trades || [])) {
                const idx = labelIndex.get(t.date ? String(t.date).substring(0, 10) : '');
                if (idx === undefined) continue;
                if (t.direction === 'buy') {
                    pointRadius[idx] = 5;
                    pointStyle[idx] = 'triangle';
                    pointBg[idx] = TK('--up');      // 买入 红
                    pointBorder[idx] = TK('--bg-surface');
                } else {
                    pointRadius[idx] = 5;
                    pointStyle[idx] = 'rectRot';    // 菱形
                    pointBg[idx] = TK('--down');      // 卖出 绿
                    pointBorder[idx] = TK('--bg-surface');
                }
            }

            console.log('[renderEquityChart] labels:', labels.length, 'equity:', equityData.length, 'bench:', benchData.length,
                        'buy/sell markers:', pointStyle.filter(s => s !== 'circle').length,
                        'first equity:', equityData[0], 'last equity:', equityData[equityData.length - 1]);

            // 3. 创建新图表（数据直接注入，避免空图表→更新的时序窗口）
            try {
                const ctx = canvas.getContext('2d');
                this.equityChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: '策略权益',
                                data: equityData,
                                borderColor: TK('--chart-line'),
                                backgroundColor: 'rgba(59, 130, 246, 0.1)',
                                fill: true,
                                tension: 0.3,
                                pointRadius: pointRadius,
                                pointStyle: pointStyle,
                                pointBackgroundColor: pointBg,
                                pointBorderColor: pointBorder,
                                pointBorderWidth: 1.5,
                                borderWidth: 2,
                            },
                            {
                                label: '基准',
                                data: benchData,
                                borderColor: TK('--chart-line2'),
                                backgroundColor: 'transparent',
                                borderDash: [5, 5],
                                tension: 0.3,
                                pointRadius: 0,
                                borderWidth: 1,
                            },
                        ],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { intersect: false, mode: 'index' },
                        // 买卖点点击 → 交易溯源弹窗（阶段2）：按日期匹配当日交易
                        onClick: (evt, elements) => {
                            if (!elements || !elements.length) return;
                            const idx = elements[0].index;
                            const date = labels[idx];
                            const matched = (result.trades || []).filter(t =>
                                t.date && String(t.date).substring(0, 10) === date);
                            if (matched.length) {
                                // 同一日多笔时展示最后一笔，弹窗内信息以该笔为准
                                this.showTradeDetail(matched[matched.length - 1]);
                            }
                        },
                        plugins: {
                            legend: { labels: { color: TK('--chart-axis'), font: { size: 11 } } },
                            tooltip: {
                                backgroundColor: TK('--chart-tip-bg'),
                                titleColor: TK('--chart-line'),
                                bodyColor: TK('--chart-axis'),
                                borderColor: TK('--chart-tip-line'),
                                borderWidth: 1,
                            },
                        },
                        scales: {
                            x: {
                                ticks: { color: TK('--chart-axis'), font: { size: 10 }, maxTicksLimit: 10 },
                                grid: { color: 'rgba(51, 65, 85, 0.3)' },
                            },
                            y: {
                                ticks: {
                                    color: TK('--chart-grid'),
                                    font: { size: 10 },
                                    callback: (v) => {
                                        const n = Number(v);
                                        if (!isFinite(n)) return String(v);
                                        if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(1) + '亿';
                                        if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + '万';
                                        return String(v);
                                    },
                                },
                                grid: { color: 'rgba(51, 65, 85, 0.3)' },
                            },
                        },
                    },
                });
                console.log('[renderEquityChart] Chart created successfully with data');
            } catch (e) {
                console.error('[renderEquityChart] Error creating chart:', e);
            }
        },

        /**
         * 渲染每日收益率（柱）+ 回撤（面积）组合图
         * 数据来源：daily_returns（后端已算，经 API/WS 透出）+ equity_curve 派生回撤
         */
        renderReturnsChart(result) {
            const canvas = document.getElementById('returns-chart');
            if (!canvas || !canvas.isConnected) {
                console.warn('[renderReturnsChart] canvas not available');
                return;
            }
            if (typeof Chart === 'undefined') {
                console.warn('[renderReturnsChart] Chart.js not loaded');
                try {
                    window.__alpineApp && window.__alpineApp.showToast('图表库(Chart.js)加载失败，收益率曲线无法显示', 'error');
                } catch (_) { /* ignore */ }
                return;
            }

            // 1. 销毁旧实例：canvas 残留实例（跨实例竞态）+ 本实例引用
            if (typeof Chart.getChart === 'function') {
                const existing = Chart.getChart(canvas);
                if (existing) {
                    console.log('[renderReturnsChart] Destroying stale chart on canvas');
                    try { existing.destroy(); } catch (e) { /* ignore */ }
                }
            }
            if (this.returnsChart) {
                try { this.returnsChart.destroy(); } catch (e) { /* ignore */ }
                this.returnsChart = null;
            }

            // 2. 数据：labels 与权益图对齐；收益率转 %；回撤从权益派生（峰值回撤，负值显示）
            const labels = result.equity_curve.map(p => p.date ? String(p.date).substring(0, 10) : '');
            const dailyReturns = (result.daily_returns || []).map(r => Number((r * 100).toFixed(3)));
            let peak = -Infinity;
            const drawdowns = result.equity_curve.map(p => {
                if (p.equity > peak) peak = p.equity;
                return peak > 0 ? -Number(((peak - p.equity) / peak * 100).toFixed(3)) : 0;
            });

            console.log('[renderReturnsChart] labels:', labels.length,
                        'daily:', dailyReturns.length, 'drawdown pts:', drawdowns.length);

            // 3. 建图：每日收益率柱状（左轴）+ 回撤面积（右轴）
            try {
                const ctx = canvas.getContext('2d');
                this.returnsChart = new Chart(ctx, {
                    type: 'bar',
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: '每日收益率(%)',
                                type: 'bar',
                                data: dailyReturns,
                                yAxisID: 'y',
                                backgroundColor: dailyReturns.map(v => v >= 0 ? 'rgba(34, 197, 94, 0.6)' : 'rgba(239, 68, 68, 0.6)'),
                                borderWidth: 0,
                                order: 2,
                            },
                            {
                                label: '回撤(%)',
                                type: 'line',
                                data: drawdowns,
                                yAxisID: 'y2',
                                borderColor: TK('--chart-c5'),
                                backgroundColor: 'rgba(245, 158, 11, 0.15)',
                                fill: true,
                                tension: 0.3,
                                pointRadius: 0,
                                borderWidth: 1.5,
                                order: 1,
                            },
                        ],
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: { intersect: false, mode: 'index' },
                        plugins: {
                            legend: { labels: { color: TK('--chart-axis'), font: { size: 11 } } },
                            tooltip: {
                                backgroundColor: TK('--chart-tip-bg'),
                                titleColor: TK('--chart-line'),
                                bodyColor: TK('--chart-axis'),
                                borderColor: TK('--chart-tip-line'),
                                borderWidth: 1,
                            },
                        },
                        scales: {
                            x: {
                                ticks: { color: TK('--chart-axis'), font: { size: 10 }, maxTicksLimit: 8 },
                                grid: { color: 'rgba(51, 65, 85, 0.3)' },
                            },
                            y: {
                                position: 'left',
                                ticks: { color: TK('--chart-axis'), font: { size: 10 }, callback: v => v + '%' },
                                grid: { color: 'rgba(51, 65, 85, 0.3)' },
                            },
                            y2: {
                                position: 'right',
                                grid: { drawOnChartArea: false },
                                ticks: { color: TK('--chart-c5'), font: { size: 10 }, callback: v => v + '%' },
                            },
                        },
                    },
                });
                console.log('[renderReturnsChart] Chart created successfully');
            } catch (e) {
                console.error('[renderReturnsChart] Error creating chart:', e);
            }
        },

        async exportPDF() {
            if (!this.currentResult) {
                window.__alpineApp.showToast('请先运行回测', 'warning');
                return;
            }

            this.exporting = true;
            try {
                const response = await fetch('/api/export/backtest/pdf', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        format: 'pdf',
                        backtest_result: this.currentResult,
                        params: {
                            strategy: this.params.strategy,
                            symbols: this.params.symbols,
                            start_date: this.params.start_date,
                            end_date: this.params.end_date,
                            initial_capital: this.params.initial_capital,
                        },
                    }),
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || '导出失败');
                }

                // 下载文件
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `backtest_${this.params.strategy}_${new Date().toISOString().slice(0,10)}.pdf`;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);

                window.__alpineApp.showToast('PDF导出成功', 'success');
            } catch (e) {
                window.__alpineApp.showToast('导出失败: ' + e.message, 'error');
            } finally {
                this.exporting = false;
            }
        },

        async exportExcel() {
            if (!this.currentResult) {
                window.__alpineApp.showToast('请先运行回测', 'warning');
                return;
            }

            this.exporting = true;
            try {
                const response = await fetch('/api/export/backtest/excel', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        format: 'excel',
                        backtest_result: this.currentResult,
                        params: {
                            strategy: this.params.strategy,
                            symbols: this.params.symbols,
                            start_date: this.params.start_date,
                            end_date: this.params.end_date,
                            initial_capital: this.params.initial_capital,
                        },
                    }),
                });

                if (!response.ok) {
                    const error = await response.json();
                    throw new Error(error.detail || '导出失败');
                }

                // 下载文件
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `backtest_${this.params.strategy}_${new Date().toISOString().slice(0,10)}.xlsx`;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);

                window.__alpineApp.showToast('Excel导出成功', 'success');
            } catch (e) {
                window.__alpineApp.showToast('导出失败: ' + e.message, 'error');
            } finally {
                this.exporting = false;
            }
        },
    };
}

// ============================================================
// Agent监控页面 Alpine组件
// ============================================================

function agentsPage() {
    return {
        agentList: [],
        pipelineSymbol: '600519.SH',
        pipelineRunning: false,
        pipelineSteps: [],
        thoughtMessages: [],
        thinkingMessage: '',
        finalSignal: null,
        // 当前页面发起的流水线 run_id：事件按此过滤，防刷新/多标签页事件混淆
        currentRunId: null,
        // 任务列表（含磁盘检查点恢复的未完成任务）
        pipelineRuns: [],
        pipelineHistory: [],
        expandedLogs: [],
        decisionReport: null,   // 决策报告弹窗数据（阶段4：确定性生成）
        decisionReportMd: '',        // 研究报告 markdown（后端渲染）
        decisionReportMdLoading: false,
        decisionReportMdPath: '',    // 报告 .md 文件路径（下载用）
        _lastThinkingTs: 0,          // 看门狗：最后一次 agent_thinking 事件时间戳

        // 未完成任务（非当前发起 run）：提供续跑入口
        get pendingRun() {
            const run = this.pipelineRuns.find(r =>
                (r.status === 'running' || r.status === 'failed' || r.status === 'pending')
                && r.run_id !== this.currentRunId);
            return run || null;
        },
        // 续跑遇环境指纹漂移（LLM 配置已变更）→ 二次确认后强制续跑
        pendingDriftRun: null,

        async init() {
            window.__agentsPage = this;
            await this.loadData();
            // 看门狗定时器：检测长时间无更新的 running 步骤，提示用户
            this._watchdogTimer = setInterval(() => {
                if (!this.pipelineRunning) return;
                // 服务器重启/WS 断开场景：任务状态已不可信，明确提示刷新
                const appState = window.__alpineApp;
                if (appState && !appState.wsConnected) {
                    if (!this.thinkingMessage.includes('连接已断开')) {
                        this.thinkingMessage = '⚠ 实时连接已断开（服务器可能已重启），当前任务状态可能已失效——请刷新页面查看真实状态，或点"取消"归档';
                    }
                    return;
                }
                const runningStep = this.pipelineSteps.find(s => s.status === 'running');
                if (runningStep && this._lastThinkingTs > 0 && (Date.now() - this._lastThinkingTs) > 30000) {
                    if (!this.thinkingMessage.includes('响应较慢')) {
                        this.thinkingMessage += '（响应较慢，请稍候…）';
                    }
                }
            }, 10000);
        },

        async loadData() {
            try {
                const [agentsResp, historyResp, runsResp] = await Promise.allSettled([
                    API.listAgents(),
                    API.getPipelineHistory(),
                    API.listPipelineRuns(),
                ]);
                if (agentsResp.status === 'fulfilled' && agentsResp.value.success) {
                    this.agentList = agentsResp.value.data;
                }
                if (historyResp.status === 'fulfilled' && historyResp.value.success) {
                    this.pipelineHistory = historyResp.value.data;
                }
                if (runsResp.status === 'fulfilled' && runsResp.value.success) {
                    this.pipelineRuns = runsResp.value.data;
                    // 刷新后恢复：存在 running 任务 → 重新订阅其事件流（根治
                    // currentRunId=null 导致后台事件被 run_id 过滤丢弃、界面空白）
                    const running = runsResp.value.data.find(r => r.status === 'running');
                    if (running) {
                        this.currentRunId = running.run_id;
                        this.pipelineRunning = true;
                        this.pipelineSteps = (running.steps || []).map(s => ({ ...s }));
                    }
                }
            } catch (e) {
                console.error('加载Agent数据失败:', e);
            }
        },

        async loadPipelineLogs() {
            try {
                const resp = await API.getPipelineHistory();
                if (resp.success) {
                    this.pipelineHistory = resp.data;
                    window.__alpineApp.showToast('日志已刷新', 'success');
                }
            } catch (e) {
                window.__alpineApp.showToast('刷新日志失败: ' + e.message, 'error');
            }
        },

        async showDecisionReport(runId) {
            try {
                const resp = await API.get(`/api/agents/report/${runId}`);
                if (resp.success && resp.data && resp.data.report) {
                    this.decisionReport = resp.data.report;
                    // 加载研究报告 markdown（后端已渲染：执行摘要/分析链路/辩论/风控/风险）
                    this.decisionReportMd = '';
                    this.decisionReportMdLoading = true;
                    this.decisionReportMdPath = resp.data.md_path || '';
                    try {
                        const mdResp = await fetch(resp.data.md_path);
                        if (mdResp.ok) this.decisionReportMd = await mdResp.text();
                    } catch (_) { /* 报告 md 不可达时保留空态提示 */ }
                    this.decisionReportMdLoading = false;
                } else {
                    window.__alpineApp.showToast('决策报告生成失败', 'error');
                }
            } catch (e) {
                window.__alpineApp.showToast('获取决策报告失败: ' + e.message, 'error');
            }
        },

        togglePipelineLog(runId) {            const idx = this.expandedLogs.indexOf(runId);
            if (idx >= 0) {
                this.expandedLogs.splice(idx, 1);
            } else {
                this.expandedLogs.push(runId);
            }
        },

        async runPipeline(runId = null, extra = {}) {
            if (!this.pipelineSymbol.trim()) {
                window.__alpineApp.showToast('请输入标的代码', 'warning');
                return;
            }

            this.pipelineRunning = true;
            this.pipelineSteps = [];
            this.thoughtMessages = [];
            this.thinkingMessage = '';
            this.finalSignal = null;

            const payload = { symbol: this.pipelineSymbol };
            if (runId) {
                // 模块化续跑：复用 run_id，已完成步骤保留、未完成模块从头重跑
                payload.run_id = runId;
                payload.resume = true;
                Object.assign(payload, extra || {});
            }

            try {
                const resp = await API.runPipeline(payload);
                if (resp.success) {
                    // 记录本次发起的 run_id，事件流只展示本任务（防混淆）
                    this.currentRunId = resp.data && resp.data.run_id;
                    this.pendingDriftRun = null;
                    window.__alpineApp.showToast(runId ? '已从断点续跑' : '分析流水线已启动', 'info');
                }
            } catch (e) {
                this.pipelineRunning = false;
                // 409 并发隔离：已有任务在运行 → 明确提示并提供查看入口
                if (e.status === 409) {
                    const activeRunId = e.detail && e.detail.active_run_id;
                    window.__alpineApp.showToast(
                        '已有流水线正在运行' + (activeRunId ? `（${activeRunId}）` : '') + '，可等待完成、取消或续跑',
                        'warning');
                    this.loadData();
                } else {
                    window.__alpineApp.showToast('启动流水线失败: ' + e.message, 'error');
                }
            }
        },

        async cancelPipeline() {
            // 优先取消当前发起 run；页面刷新/服务器重启后 currentRunId 为空时，
            // 回退到磁盘恢复的未完成任务（pendingRun）——否则"一直运行"却点不动取消
            const target = this.currentRunId || (this.pendingRun && this.pendingRun.run_id);
            if (!target) return;
            try {
                const resp = await API.cancelPipeline(target);
                if (resp.success && resp.data && resp.data.cancel_requested) {
                    window.__alpineApp.showToast('取消请求已发送', 'info');
                } else if (resp.success) {
                    window.__alpineApp.showToast('任务已结束（状态: ' + (resp.data && resp.data.status) + '），无需取消', 'info');
                } else {
                    window.__alpineApp.showToast('任务不在运行中，状态: ' + (resp.data && resp.data.status), 'warning');
                }
                this.loadData();
            } catch (e) {
                window.__alpineApp.showToast('取消失败: ' + e.message, 'error');
            }
        },

        async resumePipeline(run) {
            if (!run || !run.run_id) return;
            // 用任务自身的标的代码续跑（防用户手改输入框导致 symbol 不一致被拒）
            if (run.symbol) {
                this.pipelineSymbol = run.symbol;
            }
            await this.runPipeline(run.run_id);
        },

        // 强制续跑：环境指纹漂移（LLM 配置已变更）时按新配置重跑未完成步骤
        async forceResumePipeline() {
            const run = this.pendingDriftRun;
            if (!run || !run.run_id) return;
            if (run.symbol) {
                this.pipelineSymbol = run.symbol;
            }
            await this.runPipeline(run.run_id, { resume_on_drift: true });
        },

        handleWSMessage(data) {
            // run_id 隔离：仅展示当前页面发起的流水线事件。
            // 刷新后 currentRunId 为 null → 忽略后台仍在运行的其它任务事件
            // （根治"没点击却自动运行/多标签页输出混淆"）
            if (data.run_id !== this.currentRunId) {
                return;
            }
            switch (data.type) {
                case 'pipeline_started':
                    this.pipelineRunning = true;
                    this.thinkingMessage = '流水线启动...';
                    break;

                case 'agent_thinking':
                    // 更新步骤状态
                    this._lastThinkingTs = Date.now(); // 看门狗心跳
                    const existingStep = this.pipelineSteps.find(s => s.step === data.step);
                    if (!existingStep) {
                        this.pipelineSteps.push({
                            step: data.step,
                            agent_name: data.agent_name,
                            agent_id: data.agent_id,
                            status: 'running',
                        });
                    }
                    // 实时思考文本（LLM 流式/推理链），截取最新片段展示滚动效果
                    if (data.thinking) {
                        let thinking = data.thinking;
                        // JSON 泄漏检测：推理模型回显工具原始数据时，thinking 会包含
                        // 大量 "key": value 结构（如 impact_level/url/tags/title/source 等）
                        const jsonKeyCount = (thinking.match(/"/g) || []).length;
                        if (jsonKeyCount > 8 && /"\w+"\s*:/.test(thinking)) {
                            thinking = '[数据分析中...]';
                        }
                        this.thinkingMessage = `${data.agent_name}：${thinking.slice(-300)}`;
                    } else {
                        this.thinkingMessage = `${data.agent_name} 思考中...`;
                    }
                    break;

                case 'agent_thought':
                    // 更新步骤为完成
                    const step = this.pipelineSteps.find(s => s.step === data.step);
                    if (step) {
                        step.status = 'completed';
                        step.duration_ms = data.duration_ms;
                    }

                    // 添加思考内容（含完整推理过程）
                    this.thoughtMessages.push({
                        step: data.step,
                        agent_id: data.agent_id,
                        agent_name: data.agent_name,
                        content: data.content,
                        reasoning: data.reasoning || '',
                        duration_ms: data.duration_ms,
                    });

                    this.thinkingMessage = '';
                    // 自动滚动到底部
                    this.$nextTick(() => {
                        const container = document.getElementById('thought-container');
                        if (container) container.scrollTop = container.scrollHeight;
                    });
                    break;

                case 'pipeline_completed':
                    this.pipelineRunning = false;
                    this.thinkingMessage = '';
                    this._lastThinkingTs = 0;
                    if (data.final_signal) {
                        this.finalSignal = data.final_signal;
                    }
                    // 刷新历史
                    this.loadData();
                    if (data.status === 'failed') {
                        window.__alpineApp.showToast('分析流水线失败', 'error');
                    } else {
                        window.__alpineApp.showToast('分析流水线完成', 'success');
                    }
                    break;

                case 'pipeline_error':
                    this.pipelineRunning = false;
                    this.thinkingMessage = '';
                    const errMsg = data.error || '未知错误';
                    // 环境指纹漂移：LLM 配置已变更 → 提供"强制续跑"入口（按新配置重跑未完成步骤）
                    if (errMsg.includes('环境指纹漂移')) {
                        this.pendingDriftRun = {
                            run_id: data.run_id || this.currentRunId,
                            symbol: this.pipelineSymbol,
                        };
                    }
                    // 记录错误消息到思考区
                    this.thoughtMessages.push({
                        step: 99,
                        agent_id: 'system',
                        agent_name: '系统错误',
                        content: '## 流水线执行失败\n\n```\n' + errMsg + '\n```',
                        duration_ms: 0,
                    });
                    this.loadData();
                    window.__alpineApp.showToast('分析流水线失败: ' + errMsg, 'error');
                    break;

                case 'pipeline_cancelled':
                    this.pipelineRunning = false;
                    this.thinkingMessage = '';
                    this._lastThinkingTs = 0;
                    this.thoughtMessages.push({
                        step: 98,
                        agent_id: 'system',
                        agent_name: '系统提示',
                        content: '## ⏹ 流水线已取消\n\n任务已停止，已完成步骤保留，可在任务列表中选择"续跑"。',
                        duration_ms: 0,
                    });
                    this.loadData();
                    window.__alpineApp.showToast('流水线已取消', 'warning');
                    break;
            }
        },
    };
}

// ============================================================
// 记忆浏览器页面 Alpine组件
// ============================================================

function memoryPage() {
    return {
        stats: { total_memories: 0, total_entries_ever: 0, by_type: {}, by_agent: {} },
        memories: [],
        loading: false,
        searchParams: {
            memory_type: '',
            keywords: '',
        },

        async init() {
            await this.loadStats();
            await this.loadRecent();
        },

        async loadStats() {
            try {
                const resp = await API.getMemoryStats();
                if (resp.success) {
                    this.stats = resp.data;
                }
            } catch (e) {
                console.error('加载记忆统计失败:', e);
            }
        },

        async loadRecent() {
            this.loading = true;
            try {
                const resp = await API.getRecentMemory(50);
                if (resp.success) {
                    this.memories = resp.data;
                }
            } catch (e) {
                console.error('加载最近记忆失败:', e);
            } finally {
                this.loading = false;
            }
        },

        async searchMemories() {
            this.loading = true;
            try {
                const params = {
                    memory_type: this.searchParams.memory_type || undefined,
                    keywords: this.searchParams.keywords || undefined,
                };
                const resp = await API.searchMemory(params);
                if (resp.success) {
                    this.memories = resp.data;
                }
            } catch (e) {
                console.error('搜索记忆失败:', e);
                window.__alpineApp.showToast('搜索失败: ' + e.message, 'error');
            } finally {
                this.loading = false;
            }
        },

        async deleteMemory(id) {
            if (!confirm('确定要删除这条记忆吗？')) return;
            try {
                await API.deleteMemory(id);
                this.memories = this.memories.filter(m => m.id !== id);
                await this.loadStats();
                window.__alpineApp.showToast('记忆已删除', 'success');
            } catch (e) {
                window.__alpineApp.showToast('删除失败: ' + e.message, 'error');
            }
        },
    };
}

// ============================================================
// 策略工坊页面 Alpine组件
// ============================================================

function workshopPage() {
    return {
        // Tab状态
        activeTab: 'ai_strategy',

        // 我的策略（阶段6 安全边界：draft → enabled 状态机）
        myStrategies: [],
        myStrategiesLoaded: false,

        // 云端市场状态（CloudBase）
        cloudPackages: [],
        cloudKeyword: '',
        cloudError: '',
        cloudLoading: false,
        cloudUploading: false,
        cloudUploadPath: 'data/workshop/my_strat-v1.0.0.zip',
        cloudCode: { name: '', code: '', version: '0.1.0', pkg_type: 'strategy' },  // 文本代码上传

        // AI策略生成状态
        strategyForm: {
            description: '',
            market: 'A',
            style: 'medium',
            risk_level: 'medium',
            instrument: 'stock',
        },
        strategyGenerating: false,
        strategyResult: null,

        // 快速测试（真实回测）状态
        testRunning: false,
        testResult: null,
        testError: '',
        tempParams: {},  // 参数临时覆盖 {name: value}（模板/工坊策略，只改默认值）
        deleteTarget: null,  // 待删除策略（自定义确认弹窗，替代被嵌入式浏览器拦截的原生 confirm）

        // AI因子生成状态
        factorForm: {
            description: '',
            data_type: 'daily',
            category: 'technical',
        },
        factorGenerating: false,
        factorResult: null,

        // 策略模板库状态
        templates: [],
        selectedTemplate: null,
        templateFilters: {
            category: '',
            style: '',
            difficulty: '',
        },

        // 因子编辑器状态
        editorForm: {
            name: '',
            description: '',
            category: 'technical',
            output_type: 'numeric',
            formula: '',
        },
        editorParams: [],
        editorConditions: [],
        editorCode: '',
        editorGenerating: false,
        editorValidation: null,

        // 计算属性：筛选后的模板
        get filteredTemplates() {
            return this.templates.filter(tpl => {
                if (this.templateFilters.category && tpl.category !== this.templateFilters.category) return false;
                if (this.templateFilters.style && tpl.style !== this.templateFilters.style) return false;
                if (this.templateFilters.difficulty && tpl.difficulty !== this.templateFilters.difficulty) return false;
                return true;
            });
        },

        async init() {
            await this.loadTemplates();
        },

        // ==================== AI策略生成 ====================
        async generateStrategy() {
            if (!this.strategyForm.description.trim()) {
                window.__alpineApp.showToast('请输入策略描述', 'warning');
                return;
            }

            this.strategyGenerating = true;
            this.strategyResult = null;

            try {
                const resp = await API.post('/api/strategy/generate', {
                    description: this.strategyForm.description,
                    market: this.strategyForm.market,
                    style: this.strategyForm.style,
                    risk_level: this.strategyForm.risk_level,
                    instruments: this.strategyForm.instrument,
                });

                if (resp.success) {
                    this.strategyResult = resp.data;
                    window.__alpineApp.showToast('策略生成成功', 'success');
                } else {
                    window.__alpineApp.showToast(resp.message || '策略生成失败', 'error');
                }
            } catch (e) {
                window.__alpineApp.showToast('策略生成失败: ' + e.message, 'error');
            } finally {
                this.strategyGenerating = false;
            }
        },

        // ==================== AI因子生成 ====================
        async generateFactor() {
            if (!this.factorForm.description.trim()) {
                window.__alpineApp.showToast('请输入因子描述', 'warning');
                return;
            }

            this.factorGenerating = true;
            this.factorResult = null;

            try {
                const resp = await API.post('/api/strategy/factors/generate', {
                    description: this.factorForm.description,
                    data_type: this.factorForm.data_type,
                    category: this.factorForm.category,
                });

                if (resp.success) {
                    this.factorResult = resp.data;
                    window.__alpineApp.showToast('因子生成成功', 'success');
                }
            } catch (e) {
                window.__alpineApp.showToast('因子生成失败: ' + e.message, 'error');
            } finally {
                this.factorGenerating = false;
            }
        },

        // ==================== 策略模板库 ====================
        async loadTemplates() {
            try {
                const resp = await API.get('/api/strategy/templates');
                if (resp.success) {
                    this.templates = resp.data;
                }
            } catch (e) {
                // 使用本地预置模板数据
                this.templates = this.getDefaultTemplates();
            }
        },

        getDefaultTemplates() {
            return [
                {
                    id: 'dual_thrust',
                    name: 'Dual Thrust',
                    category: 'breakout',
                    category_label: '突破',
                    difficulty: 'easy',
                    difficulty_level: 1,
                    style: 'short',
                    description: '基于开盘价与区间突破的经典日内策略，通过计算N日最高价和最低价的范围来确定上下轨。',
                    full_description: 'Dual Thrust是一种经典的区间突破策略。策略通过计算过去N日的最高价、最低价和收盘价，形成价格通道。当开盘价突破上轨时做多，突破下轨时做空。该策略适用于趋势明显的市场环境，在震荡市中容易产生假突破。',
                    tags: ['突破', '日内', '经典'],
                    params: [
                        { name: 'lookback', default: '20', range: '5-60', desc: '回看天数' },
                        { name: 'k1', default: '0.5', range: '0.1-1.0', desc: '上轨系数' },
                        { name: 'k2', default: '0.5', range: '0.1-1.0', desc: '下轨系数' },
                    ],
                    usage: '适用于期货和加密货币市场，建议在趋势明显的品种上使用。参数k1和k2可以不对称设置以调整多空偏向。',
                    code: `import numpy as np

class DualThrustStrategy:
    """Dual Thrust 突破策略"""

    def __init__(self, lookback=20, k1=0.5, k2=0.5):
        self.lookback = lookback
        self.k1 = k1
        self.k2 = k2
        self.name = "Dual Thrust"

    def on_bar(self, bars):
        if len(bars) < self.lookback + 1:
            return None

        # 计算N日范围
        hh = max(b.high for b in bars[-self.lookback:-1])
        hc = max(b.close for b in bars[-self.lookback:-1])
        lc = min(b.close for b in bars[-self.lookback:-1])
        ll = min(b.low for b in bars[-self.lookback:-1])

        range_val = max(hh - lc, hc - ll)
        open_price = bars[-1].open

        upper = open_price + self.k1 * range_val
        lower = open_price - self.k2 * range_val

        close = bars[-1].close
        if close > upper:
            return {'signal': 'buy', 'price': close}
        elif close < lower:
            return {'signal': 'sell', 'price': close}
        return None`,
                },
                {
                    id: 'rsi_mean_reversion',
                    name: 'RSI均值回归',
                    category: 'mean_reversion',
                    category_label: '均值回归',
                    difficulty: 'easy',
                    difficulty_level: 1,
                    style: 'medium',
                    description: '基于RSI超买超卖信号的均值回归策略，当RSI偏离均值时进行反向交易。',
                    full_description: 'RSI均值回归策略利用RSI指标的超买(>70)和超卖(<30)区域进行反向交易。当RSI进入超卖区域并出现拐头时买入，进入超买区域并拐头时卖出。策略包含趋势过滤条件，在强势趋势中避免逆势交易。',
                    tags: ['RSI', '均值回归', '震荡'],
                    params: [
                        { name: 'rsi_period', default: '14', range: '5-30', desc: 'RSI周期' },
                        { name: 'oversold', default: '30', range: '10-40', desc: '超卖阈值' },
                        { name: 'overbought', default: '70', range: '60-90', desc: '超买阈值' },
                        { name: 'ma_period', default: '200', range: '50-300', desc: '趋势过滤MA周期' },
                    ],
                    usage: '适用于震荡市场和均值回归特征明显的品种。建议配合成交量确认和趋势过滤使用。',
                    code: `import numpy as np

class RSIMeanReversionStrategy:
    """RSI 均值回归策略"""

    def __init__(self, rsi_period=14, oversold=30, overbought=70, ma_period=200):
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.ma_period = ma_period
        self.name = "RSI Mean Reversion"

    def calculate_rsi(self, bars, period):
        closes = [b.close for b in bars]
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def on_bar(self, bars):
        if len(bars) < max(self.rsi_period, self.ma_period) + 1:
            return None

        rsi = self.calculate_rsi(bars, self.rsi_period)
        ma = np.mean([b.close for b in bars[-self.ma_period:]])
        price = bars[-1].close

        # 趋势过滤：价格在MA上方才做多
        if rsi < self.oversold and price > ma:
            return {'signal': 'buy', 'price': price}
        elif rsi > self.overbought and price < ma:
            return {'signal': 'sell', 'price': price}
        return None`,
                },
                {
                    id: 'macd_cross',
                    name: 'MACD金叉死叉',
                    category: 'trend',
                    category_label: '趋势跟踪',
                    difficulty: 'easy',
                    difficulty_level: 2,
                    style: 'medium',
                    description: '基于MACD金叉和死叉信号的趋势跟踪策略，结合零轴过滤提高信号质量。',
                    full_description: 'MACD金叉死叉策略是最经典的技术分析策略之一。当MACD快线上穿慢线(金叉)时产生买入信号，快线下穿慢线(死叉)时产生卖出信号。策略增加了零轴过滤条件：只在MACD柱状图大于零时做多，小于零时做空，以避免在弱趋势中频繁交易。',
                    tags: ['MACD', '趋势', '金叉死叉'],
                    params: [
                        { name: 'fast_period', default: '12', range: '5-20', desc: '快线EMA周期' },
                        { name: 'slow_period', default: '26', range: '15-50', desc: '慢线EMA周期' },
                        { name: 'signal_period', default: '9', range: '3-15', desc: '信号线周期' },
                    ],
                    usage: '适用于趋势性较强的市场。在震荡市中容易产生虚假信号，建议配合ADX等趋势强度指标过滤。',
                    code: `import numpy as np

class MACDCrossStrategy:
    """MACD 金叉死叉策略"""

    def __init__(self, fast_period=12, slow_period=26, signal_period=9):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period
        self.name = "MACD Cross"

    def calculate_ema(self, data, period):
        multiplier = 2 / (period + 1)
        ema = [data[0]]
        for price in data[1:]:
            ema.append((price - ema[-1]) * multiplier + ema[-1])
        return ema

    def on_bar(self, bars):
        if len(bars) < self.slow_period + self.signal_period + 1:
            return None

        closes = [b.close for b in bars]
        fast_ema = self.calculate_ema(closes, self.fast_period)
        slow_ema = self.calculate_ema(closes, self.slow_period)

        macd_line = [f - s for f, s in zip(fast_ema, slow_ema)]
        signal_line = self.calculate_ema(macd_line, self.signal_period)

        # 金叉：MACD上穿信号线
        if macd_line[-2] <= signal_line[-2] and macd_line[-1] > signal_line[-1]:
            return {'signal': 'buy', 'price': bars[-1].close}
        # 死叉：MACD下穿信号线
        elif macd_line[-2] >= signal_line[-2] and macd_line[-1] < signal_line[-1]:
            return {'signal': 'sell', 'price': bars[-1].close}
        return None`,
                },
                {
                    id: 'bollinger_breakout',
                    name: '布林带突破',
                    category: 'breakout',
                    category_label: '突破',
                    difficulty: 'medium',
                    difficulty_level: 2,
                    style: 'medium',
                    description: '基于布林带收窄后突破的策略，利用波动率收缩后的扩张来捕捉趋势行情。',
                    full_description: '布林带突破策略通过监测布林带带宽的收缩来预判突破时机。当布林带带宽处于近N日最低水平时，表示波动率极度收缩，市场即将选择方向。价格突破上轨时做多，突破下轨时做空。策略结合RSI过滤假突破，并使用ATR进行动态止损。',
                    tags: ['布林带', '波动率', '突破'],
                    params: [
                        { name: 'bb_period', default: '20', range: '10-40', desc: '布林带周期' },
                        { name: 'bb_std', default: '2.0', range: '1.0-3.0', desc: '标准差倍数' },
                        { name: 'squeeze_lookback', default: '50', range: '20-100', desc: '收窄回看期' },
                        { name: 'atr_period', default: '14', range: '5-30', desc: 'ATR止损周期' },
                    ],
                    usage: '适用于波动率收缩后即将突破的市场环境。在低波动率环境中效果最佳，建议配合成交量确认。',
                    code: `import numpy as np

class BollingerBreakoutStrategy:
    """布林带突破策略"""

    def __init__(self, bb_period=20, bb_std=2.0, squeeze_lookback=50, atr_period=14):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.squeeze_lookback = squeeze_lookback
        self.atr_period = atr_period
        self.name = "Bollinger Breakout"

    def on_bar(self, bars):
        if len(bars) < max(self.bb_period, self.squeeze_lookback, self.atr_period) + 1:
            return None

        closes = [b.close for b in bars]
        sma = np.mean(closes[-self.bb_period:])
        std = np.std(closes[-self.bb_period:])
        upper = sma + self.bb_std * std
        lower = sma - self.bb_std * std

        # 计算布林带带宽
        bandwidth = (upper - lower) / sma
        bandwidths = []
        for i in range(len(closes) - self.bb_period - self.squeeze_lookback, len(closes) - self.bb_period):
            window = closes[i:i + self.bb_period]
            w_sma = np.mean(window)
            w_std = np.std(window)
            w_upper = w_sma + self.bb_std * w_std
            w_lower = w_sma - self.bb_std * w_std
            bandwidths.append((w_upper - w_lower) / w_sma)

        # 判断是否处于收窄状态
        is_squeeze = bandwidth <= np.percentile(bandwidths, 20) if bandwidths else False

        price = bars[-1].close
        if is_squeeze and price > upper:
            return {'signal': 'buy', 'price': price}
        elif is_squeeze and price < lower:
            return {'signal': 'sell', 'price': price}
        return None`,
                },
                {
                    id: 'momentum_rotation',
                    name: '动量轮动',
                    category: 'momentum',
                    category_label: '动量',
                    difficulty: 'medium',
                    difficulty_level: 2,
                    style: 'long',
                    description: '基于多品种动量排名的轮动策略，定期选择动量最强的品种进行投资。',
                    full_description: '动量轮动策略通过计算多个品种在过去N日的动量(收益率)，选择动量排名前K的品种等权持有。策略定期(如每月)进行再平衡，卖出动量排名下降的品种，买入新进入前列的品种。该策略利用动量效应，在趋势延续的市场中表现优异。',
                    tags: ['动量', '轮动', '多品种'],
                    params: [
                        { name: 'lookback', default: '60', range: '20-120', desc: '动量计算周期' },
                        { name: 'top_k', default: '3', range: '1-10', desc: '选择前K个品种' },
                        { name: 'rebalance_days', default: '20', range: '5-60', desc: '再平衡周期(天)' },
                    ],
                    usage: '适用于多品种组合投资，建议选择相关性较低的品种池。在趋势市场中表现最佳，需注意动量崩溃风险。',
                    code: `import numpy as np

class MomentumRotationStrategy:
    """动量轮动策略"""

    def __init__(self, lookback=60, top_k=3, rebalance_days=20):
        self.lookback = lookback
        self.top_k = top_k
        self.rebalance_days = rebalance_days
        self.name = "Momentum Rotation"
        self.day_count = 0

    def calculate_momentum(self, bars):
        if len(bars) < self.lookback:
            return 0
        past_close = bars[-self.lookback].close
        current_close = bars[-1].close
        return (current_close - past_close) / past_close

    def on_rebalance(self, all_bars):
        """再平衡时调用，返回目标持仓"""
        momentums = {}
        for symbol, bars in all_bars.items():
            momentums[symbol] = self.calculate_momentum(bars)

        # 按动量排序，选择前K个
        ranked = sorted(momentums.items(), key=lambda x: x[1], reverse=True)
        selected = [s[0] for s in ranked[:self.top_k]]
        return selected

    def on_bar(self, bars):
        self.day_count += 1
        if self.day_count % self.rebalance_days != 0:
            return None
        return {'signal': 'rebalance'}`,
                },
                {
                    id: 'turtle_trading',
                    name: '海龟交易法则',
                    category: 'trend',
                    category_label: '趋势跟踪',
                    difficulty: 'hard',
                    difficulty_level: 3,
                    style: 'long',
                    description: '基于唐奇安通道的经典趋势跟踪策略，采用金字塔加仓和ATR动态止损。',
                    full_description: '海龟交易法则是最著名的趋势跟踪策略之一，由理查德-丹尼斯提出。策略使用唐奇安通道判断突破方向：价格突破20日最高价时买入，突破10日最低价时卖出。仓位管理采用ATR(平均真实波幅)进行单位化计算，每次加仓1个单位，最多加仓4次。止损设在买入价减去2倍ATR的位置。',
                    tags: ['海龟', '唐奇安通道', 'ATR', '加仓'],
                    params: [
                        { name: 'entry_period', default: '20', range: '10-40', desc: '入场通道周期' },
                        { name: 'exit_period', default: '10', range: '5-20', desc: '出场通道周期' },
                        { name: 'atr_period', default: '20', range: '10-30', desc: 'ATR周期' },
                        { name: 'max_units', default: '4', range: '1-6', desc: '最大加仓次数' },
                        { name: 'stop_atr_mult', default: '2.0', range: '1.0-3.0', desc: '止损ATR倍数' },
                    ],
                    usage: '适用于趋势性强的期货和外汇市场。需要严格执行纪律，不因短期波动而改变策略。建议配合资金管理规则使用。',
                    code: `import numpy as np

class TurtleTradingStrategy:
    """海龟交易法则"""

    def __init__(self, entry_period=20, exit_period=10, atr_period=20,
                 max_units=4, stop_atr_mult=2.0):
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.atr_period = atr_period
        self.max_units = max_units
        self.stop_atr_mult = stop_atr_mult
        self.name = "Turtle Trading"
        self.position_units = 0
        self.entry_price = 0

    def calculate_atr(self, bars, period):
        trs = []
        for i in range(1, len(bars)):
            tr = max(
                bars[i].high - bars[i].low,
                abs(bars[i].high - bars[i-1].close),
                abs(bars[i].low - bars[i-1].close)
            )
            trs.append(tr)
        return np.mean(trs[-period:])

    def on_bar(self, bars):
        if len(bars) < max(self.entry_period, self.exit_period, self.atr_period) + 1:
            return None

        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        atr = self.calculate_atr(bars, self.atr_period)

        # 入场：突破N日最高价
        entry_high = max(highs[-self.entry_period-1:-1])
        exit_low = min(lows[-self.exit_period-1:-1])
        price = bars[-1].close

        if self.position_units == 0:
            if price > entry_high:
                self.position_units = 1
                self.entry_price = price
                return {'signal': 'buy', 'price': price, 'units': 1}
        else:
            # 加仓：价格上升1个ATR
            if price > self.entry_price + atr and self.position_units < self.max_units:
                self.position_units += 1
                self.entry_price = price
                return {'signal': 'buy', 'price': price, 'units': 1}

            # 止损：价格跌破入场价 - 2*ATR
            stop_price = self.entry_price - self.stop_atr_mult * atr
            if price < stop_price:
                self.position_units = 0
                self.entry_price = 0
                return {'signal': 'sell', 'price': price}

            # 出场：跌破N日最低价
            if price < exit_low:
                self.position_units = 0
                self.entry_price = 0
                return {'signal': 'sell', 'price': price}

        return None`,
                },
            ];
        },

        async selectTemplate(id) {
            try {
                const resp = await API.get('/api/strategy/templates/' + id);
                if (resp.success) {
                    this.selectedTemplate = resp.data;
                    this.initTempParams();
                    return;
                }
            } catch (e) {
                // 忽略API错误，使用本地数据
            }
            // 从本地数据查找
            const tpl = this.templates.find(t => t.id === id);
            if (tpl) {
                this.selectedTemplate = tpl;
                this.initTempParams();
            }
        },

        // 参数临时覆盖（只改默认值不改参数名）：从模板 params 初始化
        initTempParams() {
            this.tempParams = {};
            const params = (this.selectedTemplate && this.selectedTemplate.params) || [];
            for (const p of params) {
                if (p && p.name) this.tempParams[p.name] = p.default;
            }
        },

        // ==================== 因子编辑器 ====================
        addParam() {
            this.editorParams.push({
                name: '',
                label: '',
                type: 'float',
                default: '',
            });
        },

        removeParam(index) {
            this.editorParams.splice(index, 1);
        },

        addCondition() {
            this.editorConditions.push({
                field: 'close',
                operator: '>',
                value: '',
            });
        },

        removeCondition(index) {
            this.editorConditions.splice(index, 1);
        },

        async generateFactorCode() {
            if (!this.editorForm.name.trim()) {
                window.__alpineApp.showToast('请输入因子名称', 'warning');
                return;
            }

            this.editorGenerating = true;
            this.editorCode = '';
            this.editorValidation = null;

            try {
                const resp = await API.post('/api/strategy/visual-factor/create', {
                    name: this.editorForm.name,
                    description: this.editorForm.description,
                    category: this.editorForm.category,
                    output_type: this.editorForm.output_type,
                    params: this.editorParams,
                    formula: this.editorForm.formula,
                    conditions: this.editorConditions,
                });

                if (resp.success) {
                    this.editorCode = resp.data.code;
                    this.editorValidation = { valid: true, message: '代码生成成功' };
                    window.__alpineApp.showToast('因子代码生成成功', 'success');
                }
            } catch (e) {
                // 使用本地代码生成作为fallback
                this.editorCode = this.generateFactorCodeLocal();
                this.editorValidation = { valid: true, message: '代码生成成功(本地)' };
                window.__alpineApp.showToast('因子代码生成成功', 'success');
            } finally {
                this.editorGenerating = false;
            }
        },

        generateFactorCodeLocal() {
            const name = this.editorForm.name || 'custom_factor';
            const desc = this.editorForm.description || '';
            const category = this.editorForm.category || 'technical';
            const outputType = this.editorForm.output_type || 'numeric';
            const formula = this.editorForm.formula || 'bars[-1].close';
            const params = this.editorParams || [];
            const conditions = this.editorConditions || [];

            let paramStr = '';
            if (params.length > 0) {
                paramStr = params.map(p =>
                    `        self.${p.name} = ${p.type === 'str' ? `'${p.default}'` : (p.default || '0')}  # ${p.label || p.name}`
                ).join('\n');
            }

            let conditionStr = '';
            if (conditions.length > 0) {
                conditionStr = conditions.map(c =>
                    `        if bars[-1].${c.field} ${c.operator} ${c.type === 'str' ? `'${c.value}'` : c.value}:`
                ).join('\n');
            }

            return `import numpy as np

class ${name.replace(/[^a-zA-Z0-9_]/g, '_').replace(/^[0-9]/, '_')}(object):
    """
    ${desc || name}
    类别: ${category}
    输出类型: ${outputType}
    """

    def __init__(self${params.length > 0 ? ', ' + params.map(p => `${p.name}=${p.type === 'str' ? `'${p.default}'` : (p.default || '0')}`).join(', ') : ''}):
${paramStr || '        pass'}
        self.name = "${name}"

    def calculate(self, bars):
        """
        计算因子值

        参数:
            bars: K线数据列表, 每个元素包含 open/high/low/close/volume

        返回:
            因子值 (${outputType}类型)
        """
        if len(bars) < 2:
            return None

${conditions.length > 0 ? '        # 过滤条件\n' + conditions.map(c => `        if not (bars[-1].${c.field} ${c.operator} ${c.value}):\n            return None`).join('\n') + '\n' : ''}        # 计算公式
        try:
            value = ${formula}
            return value
        except Exception as e:
            return None

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name})"`;
        },

        // ==================== 通用方法 ====================
        async copyCode(code) {
            if (!code) return;
            try {
                if (navigator.clipboard) {
                    await navigator.clipboard.writeText(code);
                } else {
                    const textarea = document.createElement('textarea');
                    textarea.value = code;
                    document.body.appendChild(textarea);
                    textarea.select();
                    document.execCommand('copy');
                    document.body.removeChild(textarea);
                }
                window.__alpineApp.showToast('代码已复制到剪贴板', 'success');
            } catch (e) {
                window.__alpineApp.showToast('复制失败', 'error');
            }
        },

        async validateCode(code) {
            if (!code) return;
            try {
                const resp = await API.post('/api/strategy/validate', { code });
                if (resp.success) {
                    this.editorValidation = { valid: resp.data.valid, message: resp.data.message || (resp.data.valid ? '验证通过' : '验证失败') };
                }
            } catch (e) {
                this.editorValidation = { valid: false, message: '验证请求失败: ' + e.message };
            }
        },

        async saveTemplateToMine(tpl) {
            // 模板卡片快捷保存：不依赖 selectedTemplate，直接保存模板代码
            if (!tpl || !tpl.code) {
                window.__alpineApp.showToast('该模板没有可保存的代码', 'warning');
                return;
            }
            try {
                const resp = await API.post('/api/strategy/save', {
                    code: tpl.code,
                    name: tpl.name || '自定义策略',
                });
                if (resp.success) {
                    window.__alpineApp.showToast(resp.message || '策略已保存为草稿', 'success');
                    if (resp.data && resp.data.strategy_id) {
                        this.activeTab = 'my_strategies';
                        this.loadMyStrategies();
                    }
                } else {
                    window.__alpineApp.showToast((resp.detail || resp.message || '保存失败') + '（若为数据问题请检查代码）', 'error');
                }
            } catch (e) {
                window.__alpineApp.showToast('保存失败: ' + e.message, 'error');
            }
        },

        async saveStrategy() {
            const code = this.strategyResult ? this.strategyResult.code : (this.selectedTemplate ? this.selectedTemplate.code : null);
            if (!code) {
                window.__alpineApp.showToast('没有可保存的策略代码', 'warning');
                return;
            }
            try {
                const resp = await API.post('/api/strategy/save', {
                    code,
                    name: this.strategyResult && this.strategyResult.name ? this.strategyResult.name : '自定义策略',
                });
                if (resp.success) {
                    window.__alpineApp.showToast(resp.message || '策略已保存为草稿', 'success');
                    if (resp.data && resp.data.strategy_id) {
                        // 保存成功 → 自动切到"我的策略"tab 并刷新列表（否则用户看不到刚保存的策略）
                        this.activeTab = 'my_strategies';
                        this.loadMyStrategies();
                    }
                }
            } catch (e) {
                window.__alpineApp.showToast('保存失败: ' + e.message, 'error');
            }
        },

        async loadMyStrategies() {
            try {
                const resp = await API.get('/api/backtest/strategies');
                if (resp.success && resp.data && resp.data.custom) {
                    this.myStrategies = resp.data.custom.map(s => ({
                        id: s.id,
                        name: s.name || s.id,
                        status: s.status || 'draft',
                        validation_report: {},
                        showReport: false,
                    }));
                    // 逐个读取验证报告（manifest.validation_report）
                    await Promise.all(this.myStrategies.map(async (s) => {
                        try {
                            const r = await API.get(`/api/strategy/${s.id}/manifest`);
                            if (r.success && r.data) {
                                s.validation_report = r.data.validation_report || {};
                                s.status = r.data.status || s.status;
                            }
                        } catch (_) { /* 旧策略无 manifest 字段 */ }
                    }));
                    this.myStrategiesLoaded = true;
                }
            } catch (e) {
                window.__alpineApp.showToast('加载策略列表失败: ' + e.message, 'error');
            }
        },

        async renameStrategy(s) {
            if (!s || !s.id || !s.newName || !s.newName.trim()) return;
            try {
                const resp = await API.post(`/api/strategy/${s.id}/rename`, { name: s.newName.trim() });
                if (resp.success) {
                    s.name = s.newName.trim();
                    s.renaming = false;
                    window.__alpineApp.showToast('策略已重命名', 'success');
                    // 刷新回测面板策略列表（下拉显示新名称）
                    if (window.__backtestPage) window.__backtestPage.loadStrategies();
                } else {
                    window.__alpineApp.showToast(resp.detail || resp.message || '重命名失败', 'error');
                }
            } catch (e) {
                window.__alpineApp.showToast('重命名失败: ' + e.message, 'error');
            }
        },

        async confirmDelete() {
            if (!this.deleteTarget) return;
            const target = this.deleteTarget;
            this.deleteTarget = null;
            try {
                const resp = await API.delete(`/api/strategy/${target.id}`);
                if (resp.success) {
                    this.myStrategies = this.myStrategies.filter(s => s.id !== target.id);
                    window.__alpineApp.showToast('策略已删除', 'success');
                    if (window.__backtestPage) window.__backtestPage.loadStrategies();
                } else {
                    window.__alpineApp.showToast(resp.detail || resp.message || '删除失败', 'error');
                }
            } catch (e) {
                window.__alpineApp.showToast('删除失败: ' + e.message, 'error');
            }
        },

        async enableStrategy(strategyId) {
            window.__alpineApp.showToast('正在执行启用验证（真实回测 + 过拟合体检，可能需要 1-2 分钟）...', 'info');
            try {
                const resp = await API.post(`/api/strategy/${strategyId}/enable`, {});
                if (resp.success && resp.data) {
                    const s = this.myStrategies.find(x => x.id === strategyId);
                    if (s) {
                        s.status = resp.data.status || s.status;
                        s.validation_report = resp.data.validation_report || {};
                    }
                    window.__alpineApp.showToast(resp.message || '验证完成', resp.data.status === 'enabled' ? 'success' : 'warning');
                } else {
                    window.__alpineApp.showToast((resp.error || resp.message || '验证失败') + '（若为数据获取失败，请确认数据源可用）', 'error');
                }
            } catch (e) {
                window.__alpineApp.showToast('启用验证失败: ' + (e.message || e), 'error');
            }
        },

        async saveFactor() {
            const code = this.factorResult ? this.factorResult.code : this.editorCode;
            if (!code) {
                window.__alpineApp.showToast('没有可保存的因子代码', 'warning');
                return;
            }
            try {
                const resp = await API.post('/api/strategy/factors/save', {
                    code,
                    name: this.factorResult && this.factorResult.name ? this.factorResult.name : '自定义因子',
                });
                if (resp.success) {
                    window.__alpineApp.showToast('因子已保存: ' + (resp.data && resp.data.factor_id), 'success');
                }
            } catch (e) {
                window.__alpineApp.showToast('保存失败: ' + e.message, 'error');
            }
        },

        // ---- 云端市场（CloudBase）----
        async loadCloudPackages() {
            this.cloudLoading = true;
            this.cloudError = '';
            try {
                let path = '/api/workshop/cloud/packages';
                if (this.cloudKeyword.trim()) {
                    path += '?q=' + encodeURIComponent(this.cloudKeyword.trim());
                }
                const resp = await API.get(path);
                if (resp.success) {
                    this.cloudPackages = resp.data || [];
                } else {
                    this.cloudError = resp.message || '云端市场加载失败';
                    this.cloudPackages = [];
                }
            } catch (e) {
                this.cloudError = '无法连接云端市场: ' + e.message + '（需配置 workshop.base_url）';
                this.cloudPackages = [];
            } finally {
                this.cloudLoading = false;
            }
        },

        async uploadCloudCode() {
            const code = (this.cloudCode.code || '').trim();
            if (!code) {
                window.__alpineApp.showToast('请先粘贴策略/因子代码', 'warning');
                return;
            }
            this.cloudUploading = true;
            try {
                const resp = await API.post('/api/workshop/cloud/upload_code', {
                    code,
                    name: (this.cloudCode.name || '').trim() || '未命名策略',
                    version: (this.cloudCode.version || '0.1.0').trim() || '0.1.0',
                    pkg_type: this.cloudCode.pkg_type,
                });
                if (resp.success) {
                    window.__alpineApp.showToast(resp.message || '代码已上传云端', 'success');
                    this.cloudCode = { name: '', code: '', version: '0.1.0', pkg_type: 'strategy' };
                    await this.loadCloudPackages();
                } else {
                    window.__alpineApp.showToast(resp.detail || resp.message || '上传失败', 'error');
                }
            } catch (e) {
                window.__alpineApp.showToast('上传失败: ' + (e.message || e), 'error');
            } finally {
                this.cloudUploading = false;
            }
        },

        async installCloudPackage(packageId) {
            try {
                const resp = await API.post('/api/workshop/cloud/install', { package_id: packageId });
                if (resp.success) {
                    window.__alpineApp.showToast('云端策略已安装', 'success');
                } else {
                    window.__alpineApp.showToast('安装失败: ' + (resp.message || ''), 'error');
                }
            } catch (e) {
                window.__alpineApp.showToast('安装失败: ' + e.message, 'error');
            }
        },

        async uploadCloudPackage() {
            if (!this.cloudUploadPath.trim()) {
                window.__alpineApp.showToast('请输入策略包路径', 'warning');
                return;
            }
            this.cloudUploading = true;
            try {
                const resp = await API.post('/api/workshop/cloud/upload', { zip_path: this.cloudUploadPath.trim() });
                if (resp.success) {
                    window.__alpineApp.showToast('策略包已上传云端', 'success');
                    this.loadCloudPackages();
                } else {
                    window.__alpineApp.showToast('上传失败: ' + (resp.message || ''), 'error');
                }
            } catch (e) {
                window.__alpineApp.showToast('上传失败: ' + e.message, 'error');
            } finally {
                this.cloudUploading = false;
            }
        },

        async quickTest() {
            const code = this.strategyResult ? this.strategyResult.code : (this.selectedTemplate ? this.selectedTemplate.code : null);
            if (!code) {
                window.__alpineApp.showToast('没有可测试的策略代码', 'warning');
                return;
            }
            this.testRunning = true;
            this.testError = '';
            this.testResult = null;
            try {
                // 参数临时覆盖：模板/工坊策略的参数输入框值（只改默认值不改参数名）
                const paramsOverride = {};
                if (this.tempParams) {
                    for (const [k, v] of Object.entries(this.tempParams)) {
                        if (v !== '' && v !== null && v !== undefined) paramsOverride[k] = v;
                    }
                }
                const resp = await API.post('/api/strategy/test', {
                    code,
                    symbol: '600519.SH',
                    start_date: '2024-01-01',
                    end_date: '2024-06-30',
                    initial_capital: 100000,
                    params: paramsOverride,
                });
                if (resp.success && resp.data) {
                    if (resp.data.valid === false) {
                        // 数据获取失败/安全扫描拒绝
                        this.testError = resp.data.message || '测试未通过';
                        window.__alpineApp.showToast(this.testError, 'error');
                    } else {
                        this.testResult = resp.data;
                        const m = (resp.data.metrics || {});
                        const sum = `收益 ${(m.total_return || 0).toFixed(2)}% · 夏普 ${(m.sharpe_ratio || 0).toFixed(2)} · ${m.total_trades || 0} 笔`;
                        window.__alpineApp.showToast('快速测试完成: ' + sum, 'success');
                        // 渲染权益曲线
                        this.$nextTick(() => this.renderTestChart());
                    }
                } else {
                    this.testError = resp.message || '测试失败';
                    window.__alpineApp.showToast(this.testError, 'error');
                }
            } catch (e) {
                this.testError = e.message;
                window.__alpineApp.showToast('测试失败: ' + e.message, 'error');
            } finally {
                this.testRunning = false;
            }
        },

        renderTestChart() {
            const canvas = document.getElementById('test-equity-chart');
            if (!canvas || !this.testResult || !this.testResult.equity_curve || this.testResult.equity_curve.length === 0) return;
            const ctx = canvas.getContext('2d');
            if (window.__testChart) {
                window.__testChart.destroy();
            }
            const labels = this.testResult.equity_curve.map(p => p.date);
            const data = this.testResult.equity_curve.map(p => p.equity);
            window.__testChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels,
                    datasets: [{
                        label: '策略权益',
                        data,
                        borderColor: TK('--chart-line'),
                        backgroundColor: 'rgba(59,130,246,0.1)',
                        fill: true,
                        pointRadius: 0,
                        tension: 0.2,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { ticks: { color: TK('--chart-axis'), maxTicksLimit: 8 }, grid: { color: TK('--chart-grid') } },
                        y: { ticks: { color: TK('--chart-axis') }, grid: { color: TK('--chart-grid') } },
                    },
                    plugins: { legend: { labels: { color: TK('--chart-line') } } },
                },
            });
        },

        formatTestPct(v) {
            if (v === null || v === undefined || isNaN(v)) return '--';
            return v.toFixed(2) + '%';
        },
        formatTestNum(v) {
            if (v === null || v === undefined || isNaN(v)) return '--';
            return Number(v).toFixed(2);
        },
    };
}

// ============================================================
// 全局暴露
// ============================================================

window.formatUptime = formatUptime;
window.formatTime = formatTime;
window.formatMoney = formatMoney;
window.formatComponentName = formatComponentName;
window.renderMarkdown = renderMarkdown;
window.getAgentColor = getAgentColor;
window.getMemoryTypeName = getMemoryTypeName;
window.getImportanceName = getImportanceName;
window.app = app;
window.configPage = configPage;
window.backtestPage = backtestPage;
window.agentsPage = agentsPage;
window.memoryPage = memoryPage;
window.workshopPage = workshopPage;

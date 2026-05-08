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
        'market_analyzer': '#3b82f6',     // 蓝色
        'news_analyst': '#8b5cf6',        // 紫色
        'fundamental_analyst': '#06b6d4', // 青色
        'strategy_generator': '#f59e0b',  // 橙色
        'risk_manager': '#ef4444',        // 红色
        'trade_executor': '#10b981',      // 绿色
    };
    return colors[agentId] || '#6b7280';
}

/**
 * 获取记忆类型颜色
 */
function getMemoryTypeColor(type) {
    const colors = {
        'market_observation': 'bg-blue-900/50 text-blue-400',
        'analysis_report': 'bg-cyan-900/50 text-cyan-400',
        'news_event': 'bg-purple-900/50 text-purple-400',
        'sentiment': 'bg-pink-900/50 text-pink-400',
        'strategy_decision': 'bg-amber-900/50 text-amber-400',
        'risk_decision': 'bg-red-900/50 text-red-400',
        'execution_record': 'bg-green-900/50 text-green-400',
        'trade_result': 'bg-emerald-900/50 text-emerald-400',
        'agent_thought': 'bg-indigo-900/50 text-indigo-400',
        'system_event': 'bg-gray-700/50 text-gray-400',
    };
    return colors[type] || 'bg-gray-700/50 text-gray-400';
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
                throw new Error(json.detail || `HTTP ${resp.status}`);
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
    testConnection(provider, apiKey, baseUrl) {
        return this.post('/api/config/test-connection', { provider, api_key: apiKey, base_url: baseUrl });
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
    runPipeline(symbol) { return this.post('/api/agents/run-pipeline', { symbol }); },
    getPipelineHistory() { return this.get('/api/agents/pipeline/history'); },

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
        navItems: [
            { id: 'dashboard', label: '仪表盘', icon: '📊' },
            { id: 'config', label: 'API配置', icon: '⚙️' },
            { id: 'backtest', label: '回测面板', icon: '📈' },
            { id: 'agents', label: 'Agent监控', icon: '🤖' },
            { id: 'memory', label: '记忆浏览器', icon: '🧠' },
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

        get currentPageTitle() {
            const item = this.navItems.find(n => n.id === this.currentPage);
            return item ? item.label : '';
        },

        async init() {
            window.__alpineApp = this;

            // 加载系统数据
            await this.loadSystemData();

            // 建立WebSocket连接
            this.setupWebSocket();

            // 定期刷新数据
            setInterval(() => this.loadSystemData(), 30000);
        },

        navigate(pageId) {
            this.currentPage = pageId;
            // 预加载页面模板
            this.loadPage(pageId);
            // 页面切换时刷新数据
            if (pageId === 'memory') {
                setTimeout(() => this.loadMemoryData(), 100);
            }
        },

        async loadPage(name) {
            if (this._loadedPages[name]) return this._loadedPages[name];
            const html = await loadPageTemplate(name);
            this._loadedPages[name] = html;
            return html;
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
            llm: { openai_api_key: '', anthropic_api_key: '', model: 'gpt-4o', temperature: 0.3, max_tokens: 4096, provider: 'openai' },
            data: { tushare_token: '', data_dir: './data' },
            risk: { max_position_pct: 30, max_drawdown_pct: 15, max_daily_loss_pct: 5, var_confidence: 0.95, stop_loss_pct: 5, take_profit_pct: 10 },
            backtest: { slippage: 0.001, commission_rate: 0.0003, stamp_tax_rate: 0.001 },
        },
        testing: { openai: false, anthropic: false, tushare: false },
        testResults: { openai: null, anthropic: null, tushare: null },
        saving: false,

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
                }
            } catch (e) {
                console.error('加载配置失败:', e);
            }
        },

        togglePassword(event) {
            const input = event.target.closest('div').querySelector('input');
            input.type = input.type === 'password' ? 'text' : 'password';
        },

        async testConnection(provider) {
            this.testing[provider] = true;
            this.testResults[provider] = null;
            try {
                let apiKey = null;
                if (provider === 'openai') apiKey = this.config.llm.openai_api_key;
                else if (provider === 'anthropic') apiKey = this.config.llm.anthropic_api_key;
                else if (provider === 'tushare') apiKey = this.config.data.tushare_token;

                const resp = await API.testConnection(provider, apiKey);
                if (resp.success) {
                    this.testResults[provider] = resp.data;
                }
            } catch (e) {
                this.testResults[provider] = { success: false, message: e.message };
            } finally {
                this.testing[provider] = false;
            }
        },

        async saveConfig() {
            this.saving = true;
            try {
                // 先更新配置
                await API.updateConfig({
                    llm: this.config.llm,
                    data: this.config.data,
                    risk: this.config.risk,
                    execution: this.config.backtest,
                });
                // 再保存到文件
                await API.saveConfig();
                window.__alpineApp.showToast('配置已保存', 'success');
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
        },
        running: false,
        progress: 0,
        progressMessage: '',
        metrics: {},
        trades: [],
        history: [],
        tradePage: 1,
        equityChart: null,
        currentTaskId: null,

        async init() {
            window.__backtestPage = this;
            await this.loadHistory();
            this.$nextTick(() => this.initChart());
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
            const canvas = document.getElementById('equity-chart');
            if (!canvas) return;

            if (this.equityChart) {
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
                            borderColor: '#3b82f6',
                            backgroundColor: 'rgba(59, 130, 246, 0.1)',
                            fill: true,
                            tension: 0.3,
                            pointRadius: 0,
                            borderWidth: 2,
                        },
                        {
                            label: '基准',
                            data: [],
                            borderColor: '#6b7280',
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
                            labels: { color: '#94a3b8', font: { size: 11 } },
                        },
                        tooltip: {
                            backgroundColor: '#1e293b',
                            titleColor: '#e2e8f0',
                            bodyColor: '#94a3b8',
                            borderColor: '#334155',
                            borderWidth: 1,
                        },
                    },
                    scales: {
                        x: {
                            ticks: { color: '#64748b', font: { size: 10 }, maxTicksLimit: 10 },
                            grid: { color: 'rgba(51, 65, 85, 0.3)' },
                        },
                        y: {
                            ticks: {
                                color: '#64748b',
                                font: { size: 10 },
                                callback: (v) => (v / 10000).toFixed(0) + '万',
                            },
                            grid: { color: 'rgba(51, 65, 85, 0.3)' },
                        },
                    },
                },
            });
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

            try {
                const symbols = this.params.symbols.split(',').map(s => s.trim()).filter(Boolean);
                const resp = await API.runBacktest({
                    strategy: this.params.strategy,
                    symbols,
                    start_date: this.params.start_date,
                    end_date: this.params.end_date,
                    initial_capital: this.params.initial_capital,
                });

                if (resp.success) {
                    this.currentTaskId = resp.data.task_id;
                    window.__alpineApp.showToast('回测任务已启动', 'info');
                }
            } catch (e) {
                this.running = false;
                window.__alpineApp.showToast('启动回测失败: ' + e.message, 'error');
            }
        },

        handleWSMessage(data) {
            if (data.task_id && data.task_id !== this.currentTaskId) return;

            switch (data.type) {
                case 'backtest_progress':
                    this.progress = data.progress;
                    this.progressMessage = data.current_bar
                        ? `正在处理第 ${data.current_bar}/${data.total_bars} 个交易日`
                        : '回测运行中...';
                    // 实时更新权益(简化版)
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
                    this.running = false;
                    this.progress = 100;
                    this.progressMessage = '回测完成';
                    this.metrics = data.metrics || {};
                    // 获取完整结果
                    this.fetchResult(data.task_id);
                    break;

                case 'backtest_failed':
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

                    // 更新权益曲线
                    if (result.equity_curve && this.equityChart) {
                        this.equityChart.data.labels = result.equity_curve.map(p => p.date);
                        this.equityChart.data.datasets[0].data = result.equity_curve.map(p => p.equity);
                        if (result.benchmark_curve) {
                            this.equityChart.data.datasets[1].data = result.benchmark_curve.map(p => p.equity);
                        }
                        this.equityChart.update();
                    }

                    // 刷新历史
                    await this.loadHistory();
                    window.__alpineApp.showToast('回测完成', 'success');
                }
            } catch (e) {
                console.error('获取回测结果失败:', e);
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
        pipelineHistory: [],

        async init() {
            window.__agentsPage = this;
            await this.loadData();
        },

        async loadData() {
            try {
                const [agentsResp, historyResp] = await Promise.allSettled([
                    API.listAgents(),
                    API.getPipelineHistory(),
                ]);
                if (agentsResp.status === 'fulfilled' && agentsResp.value.success) {
                    this.agentList = agentsResp.value.data;
                }
                if (historyResp.status === 'fulfilled' && historyResp.value.success) {
                    this.pipelineHistory = historyResp.value.data;
                }
            } catch (e) {
                console.error('加载Agent数据失败:', e);
            }
        },

        async runPipeline() {
            if (!this.pipelineSymbol.trim()) {
                window.__alpineApp.showToast('请输入标的代码', 'warning');
                return;
            }

            this.pipelineRunning = true;
            this.pipelineSteps = [];
            this.thoughtMessages = [];
            this.thinkingMessage = '';
            this.finalSignal = null;

            try {
                const resp = await API.runPipeline(this.pipelineSymbol);
                if (resp.success) {
                    window.__alpineApp.showToast('分析流水线已启动', 'info');
                }
            } catch (e) {
                this.pipelineRunning = false;
                window.__alpineApp.showToast('启动流水线失败: ' + e.message, 'error');
            }
        },

        handleWSMessage(data) {
            switch (data.type) {
                case 'pipeline_started':
                    this.pipelineRunning = true;
                    this.thinkingMessage = '流水线启动...';
                    break;

                case 'agent_thinking':
                    // 更新步骤状态
                    const existingStep = this.pipelineSteps.find(s => s.step === data.step);
                    if (!existingStep) {
                        this.pipelineSteps.push({
                            step: data.step,
                            agent_name: data.agent_name,
                            agent_id: data.agent_id,
                            status: 'running',
                        });
                    }
                    this.thinkingMessage = `${data.agent_name} 思考中...`;
                    break;

                case 'agent_thought':
                    // 更新步骤为完成
                    const step = this.pipelineSteps.find(s => s.step === data.step);
                    if (step) {
                        step.status = 'completed';
                        step.duration_ms = data.duration_ms;
                    }

                    // 添加思考内容
                    this.thoughtMessages.push({
                        step: data.step,
                        agent_id: data.agent_id,
                        agent_name: data.agent_name,
                        content: data.content,
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
                    if (data.final_signal) {
                        this.finalSignal = data.final_signal;
                    }
                    // 刷新历史
                    this.loadData();
                    window.__alpineApp.showToast('分析流水线完成', 'success');
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
// 全局暴露
// ============================================================

window.formatUptime = formatUptime;
window.formatTime = formatTime;
window.formatMoney = formatMoney;
window.formatComponentName = formatComponentName;
window.renderMarkdown = renderMarkdown;
window.getAgentColor = getAgentColor;
window.getMemoryTypeColor = getMemoryTypeColor;
window.getMemoryTypeName = getMemoryTypeName;
window.getImportanceName = getImportanceName;
window.app = app;
window.configPage = configPage;
window.backtestPage = backtestPage;
window.agentsPage = agentsPage;
window.memoryPage = memoryPage;

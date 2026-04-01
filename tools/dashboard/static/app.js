/* ══════════════════════════════════════════════════════════════
   训练数据生成器 Dashboard — 前端逻辑
   ══════════════════════════════════════════════════════════════ */

const API = '';  // 同源

// ── 全局状态 ───────────────────────────────────────────────
let config = null;           // 后端配置
let commandsList = [];       // 当前 game 的 commands
let eventSource = null;      // SSE 连接
let outputCache = null;      // 输出数据缓存
let auditCache = null;       // 审计概览缓存
let auditRoundCache = new Map(); // 审计详情缓存
let currentFilter = 'all';   // 数据浏览器筛选

// ══════════════════════════════════════════════
//  初始化
// ══════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', async () => {
    initNavigation();
    initContentTabs();
    initProgressTabs();
    initThreshold();
    initModal();
    initFilterButtons();
    await loadConfig();
    await loadCommands('mmorpg');
    await Promise.allSettled([loadStats(), loadAuditData()]);
    connectSSE();
});


// ── 顶部导航切换 ──────────────────────────────────────────
function initNavigation() {
    document.querySelectorAll('.nav-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            document.getElementById(`page-${tab.dataset.page}`).classList.add('active');

            if (tab.dataset.page === 'commands') {
                renderCommandsPage();
            }
        });
    });
}

// ── 内容 Tab 切换 ─────────────────────────────────────────
function initContentTabs() {
    document.querySelectorAll('.content-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.content-tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            tab.classList.add('active');
            document.getElementById(`content-${tab.dataset.tab}`).classList.add('active');

            if (tab.dataset.tab === 'data') {
                loadOutputData();
            } else if (tab.dataset.tab === 'progress') {
                activateProgressTab('log');
            }
        });
    });
}

function initProgressTabs() {
    document.querySelectorAll('.progress-subtab').forEach(tab => {
        tab.addEventListener('click', () => {
            activateProgressTab(tab.dataset.progressTab);
        });
    });
}

function activateProgressTab(tabName) {
    document.querySelectorAll('.progress-subtab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.progressTab === tabName);
    });
    document.querySelectorAll('.progress-panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === `progress-panel-${tabName}`);
    });
}

// ── 去重阈值滑块 ──────────────────────────────────────────
function initThreshold() {
    const slider = document.getElementById('cfg-threshold');
    const display = document.getElementById('threshold-display');
    slider.addEventListener('input', () => { display.textContent = slider.value; });
}

// ── 弹窗 ──────────────────────────────────────────────────
function initModal() {
    document.getElementById('modal-close').addEventListener('click', closeModal);
    document.getElementById('modal-backdrop').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) closeModal();
    });
}
function openModal(title, content) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = content;
    document.getElementById('modal-backdrop').classList.remove('hidden');
}
function closeModal() {
    document.getElementById('modal-backdrop').classList.add('hidden');
}

// ── 数据筛选按钮 ──────────────────────────────────────────
function initFilterButtons() {
    document.querySelectorAll('.data-toolbar .btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.data-toolbar .btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentFilter = btn.dataset.filter;
            renderDataGrid();
        });
    });
}


// ══════════════════════════════════════════════
//  API 调用
// ══════════════════════════════════════════════

async function apiFetch(path) {
    const res = await fetch(`${API}${path}`);
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
}

async function apiPost(path, body = {}) {
    const res = await fetch(`${API}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
}


// ══════════════════════════════════════════════
//  加载配置
// ══════════════════════════════════════════════

async function loadConfig() {
    try {
        config = await apiFetch('/api/config');

        // 填充 game 下拉框
        const gameSelect = document.getElementById('cfg-game');
        const cmdGameSelect = document.getElementById('cmd-game-select');
        gameSelect.innerHTML = '';
        cmdGameSelect.innerHTML = '';
        config.games.forEach(g => {
            gameSelect.add(new Option(g, g));
            cmdGameSelect.add(new Option(g, g));
        });

        // 填充模型下拉框
        const modelSelect = document.getElementById('cfg-model');
        modelSelect.innerHTML = '';
        config.models.forEach(m => {
            modelSelect.add(new Option(m, m));
        });
        if (config.defaults.model) {
            modelSelect.value = config.defaults.model;
        }
        if (config.defaults.template_count != null) {
            document.getElementById('cfg-template').value = config.defaults.template_count;
        }
        if (config.defaults.adversarial_source != null) {
            document.getElementById('cfg-adversarial').value = config.defaults.adversarial_source;
        }
        if (config.defaults.paraphrase_source != null) {
            document.getElementById('cfg-paraphrase').value = config.defaults.paraphrase_source;
        }
        if (config.defaults.global_neg_rounds != null) {
            document.getElementById('cfg-rounds').value = config.defaults.global_neg_rounds;
        }
        if (config.defaults.dedup_threshold != null) {
            document.getElementById('cfg-threshold').value = config.defaults.dedup_threshold;
            document.getElementById('threshold-display').textContent = config.defaults.dedup_threshold;
        }
        if (config.defaults.audit_sample_count != null) {
            document.getElementById('cfg-audit-sample-count').value = config.defaults.audit_sample_count;
        }
        if (config.defaults.audit_rounds != null) {
            document.getElementById('cfg-audit-rounds').value = config.defaults.audit_rounds;
        }

        // game 切换时更新 commands 和审计/统计视图
        gameSelect.addEventListener('change', async () => {
            const game = gameSelect.value;
            auditRoundCache.clear();
            await loadCommands(game);
            await Promise.allSettled([loadStats(), loadAuditData()]);
            if (document.getElementById('content-data').classList.contains('active')) {
                await loadOutputData();
            }
        });
        cmdGameSelect.addEventListener('change', () => renderCommandsPage());

        // 思考模式 toggle 显示/隐藏等级下拉框
        const thinkModeToggle = document.getElementById('cfg-think-mode');
        const thinkLevelGroup = document.getElementById('cfg-think-level-group');
        thinkModeToggle.addEventListener('change', () => {
            thinkLevelGroup.style.display = thinkModeToggle.checked ? 'block' : 'none';
        });

        // 绑定按钮
        document.getElementById('btn-generate').addEventListener('click', startGenerate);
        document.getElementById('btn-stop').addEventListener('click', stopGenerate);
        document.getElementById('btn-validate').addEventListener('click', runValidate);
        document.getElementById('btn-refresh-audit').addEventListener('click', () => loadAuditData(true));

    } catch (e) {
        console.error('加载配置失败:', e);
    }
}


// ══════════════════════════════════════════════
//  加载 Commands 列表
// ══════════════════════════════════════════════

async function loadCommands(game) {
    try {
        const data = await apiFetch(`/api/commands/${game}`);
        commandsList = data.commands || [];

        const cmdSelect = document.getElementById('cfg-command');
        cmdSelect.innerHTML = '<option value="">全部</option>';
        commandsList.forEach(cmd => {
            cmdSelect.add(new Option(cmd.command_id, cmd.command_id));
        });
    } catch (e) {
        console.error('加载 commands 失败:', e);
    }
}


// ══════════════════════════════════════════════
//  触发生成
// ══════════════════════════════════════════════

async function startGenerate() {
    const params = {
        game: document.getElementById('cfg-game').value,
        model: document.getElementById('cfg-model').value || null,
        command_id: document.getElementById('cfg-command').value || null,
        think_mode: document.getElementById('cfg-think-mode').checked,
        think_level: document.getElementById('cfg-think-level').value,
        template_count: parseInt(document.getElementById('cfg-template').value),
        adversarial_source: parseInt(document.getElementById('cfg-adversarial').value),
        paraphrase_source: parseInt(document.getElementById('cfg-paraphrase').value),
        global_neg_rounds: parseInt(document.getElementById('cfg-rounds').value),
        dedup_threshold: parseFloat(document.getElementById('cfg-threshold').value),
        audit_sample_count: parseInt(document.getElementById('cfg-audit-sample-count').value),
        audit_rounds: parseInt(document.getElementById('cfg-audit-rounds').value),
        skip_vocab: document.getElementById('cfg-skip-vocab').checked,
        skip_aliases: document.getElementById('cfg-skip-aliases').checked,
        skip_global_negatives: document.getElementById('cfg-skip-global').checked,
    };

    try {
        const llmLogBaseline = await getLlmLogSize();
        await apiPost('/api/generate', params);
        setRunningState(true);
        clearLogs(llmLogBaseline);
        resetPipeline();

        // 切换到进度 tab
        document.querySelector('[data-tab="progress"]').click();
        activateProgressTab('log');
    } catch (e) {
        alert('启动失败: ' + e.message);
    }
}

async function stopGenerate() {
    try {
        await apiPost('/api/generate/stop');
    } catch (e) {
        alert('停止失败: ' + e.message);
    }
}


// ══════════════════════════════════════════════
//  SSE 连接
// ══════════════════════════════════════════════

function connectSSE() {
    if (eventSource) {
        eventSource.close();
    }
    eventSource = new EventSource(`${API}/api/stream`);

    eventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleSSEEvent(data);
        } catch (e) {
            console.warn('SSE 解析失败:', e);
        }
    };

    eventSource.onerror = () => {
        // 自动重连
        setTimeout(() => {
            if (eventSource.readyState === EventSource.CLOSED) {
                connectSSE();
            }
        }, 3000);
    };
}

function handleSSEEvent(data) {
    switch (data.type) {
        case 'log':
            appendLog(data.line);
            break;
        case 'step':
            updatePipeline(data.statuses);
            if (data.statuses) {
                // 如果有 running 状态则标记运行中
                if (data.statuses.some(s => s === 'running')) {
                    setRunningState(true);
                    startLlmLogPolling();  // 开始 LLM 日志轮询
                }
            }
            break;
        case 'done':
            updatePipeline(data.statuses);
            setRunningState(false);
            setStatusDone();
            stopLlmLogPolling();  // 停止 LLM 日志轮询
            loadStats();
            loadAuditData(true);
            break;
        case 'error':
            appendLog(`[ERROR] ${data.message}`, 'error-line');
            setRunningState(false);
            stopLlmLogPolling();
            setStatusError();
            break;
        case 'stopped':
            if (!data.statuses) {
                appendLog(`[STOP] ${data.message}`, 'error-line');
            }
            updatePipeline(data.statuses);
            setRunningState(false);
            stopLlmLogPolling();
            break;
    }
}


// ══════════════════════════════════════════════
//  UI 更新函数
// ══════════════════════════════════════════════

function setRunningState(running) {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const btnGen = document.getElementById('btn-generate');
    const btnStop = document.getElementById('btn-stop');

    if (running) {
        dot.className = 'status-dot running';
        text.textContent = '生成中...';
        btnGen.classList.add('hidden');
        btnStop.classList.remove('hidden');
    } else {
        dot.className = 'status-dot';
        text.textContent = '就绪';
        btnGen.classList.remove('hidden');
        btnStop.classList.add('hidden');
    }
}

function setStatusDone() {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    dot.className = 'status-dot';
    text.textContent = 'Done';
}

function setStatusError() {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    dot.className = 'status-dot error';
    text.textContent = 'Error';
}

// ── 日志 ──────────────────────────────────────────────────
let logCount = 0;
let llmLogCount = 0;
let lastLlmLogSize = 0;
let llmLogPollInterval = null;
const MAX_LLM_LOG_CHARS = 6000;

function clearLogs(llmLogBaseline = 0) {
    const win = document.getElementById('log-window');
    win.innerHTML = '';
    logCount = 0;
    document.getElementById('log-count').textContent = '0';

    // 清空 LLM 日志
    const llmWin = document.getElementById('llm-log-window');
    llmWin.innerHTML = '<div class="log-placeholder">等待 LLM 调用...</div>';
    llmLogCount = 0;
    document.getElementById('llm-log-count').textContent = '0';
    lastLlmLogSize = llmLogBaseline;
}

function appendLog(line, extraClass = '') {
    const win = document.getElementById('log-window');

    // 移除占位符
    const placeholder = win.querySelector('.log-placeholder');
    if (placeholder) placeholder.remove();

    // 自动分类
    let cls = 'log-line';
    if (extraClass) {
        cls += ` ${extraClass}`;
    } else if (line.includes('Step') || line.includes('===')) {
        cls += ' step-line';
    } else if (line.includes('[OK]') || line.includes('完成')) {
        cls += ' success-line';
    } else if (line.includes('[ERROR]') || line.includes('错误') || line.includes('Error')) {
        cls += ' error-line';
    }

    const div = document.createElement('div');
    div.className = cls;
    div.textContent = line;
    win.appendChild(div);

    logCount++;
    document.getElementById('log-count').textContent = logCount;

    // 自动滚动到底部
    win.scrollTop = win.scrollHeight;
}

// ── LLM 日志轮询 ─────────────────────────────────────────
function startLlmLogPolling() {
    if (llmLogPollInterval) return;

    // 先获取初始大小
    

    llmLogPollInterval = setInterval(async () => {
        try {
            const newSize = await getLlmLogSize();

            if (newSize > lastLlmLogSize) {
                // 有新内容，获取新增部分
                const res2 = await fetch(`${API}/api/llm-log?from=${lastLlmLogSize}`);
                const text = await res2.text();
                if (text) {
                    appendLlmLog(text);
                }
                lastLlmLogSize = newSize;
            } else if (newSize < lastLlmLogSize) {
                // 文件被重置，重新开始
                lastLlmLogSize = 0;
                const llmWin = document.getElementById('llm-log-window');
                llmWin.innerHTML = '<div class="log-placeholder">Waiting for LLM...</div>';
                llmLogCount = 0;
                document.getElementById('llm-log-count').textContent = '0';
            }
        } catch (e) {
            // 忽略轮询错误
        }
    }, 250);
}

function stopLlmLogPolling() {
    if (llmLogPollInterval) {
        clearInterval(llmLogPollInterval);
        llmLogPollInterval = null;
    }
}

async function getLlmLogSize() {
    try {
        const res = await fetch(`${API}/api/llm-log-size`);
        const data = await res.json();
        return data.size || 0;
    } catch (e) {
        return 0;
    }
}

function appendLlmLog(text) {
    const win = document.getElementById('llm-log-window');

    // 移除占位符
    const placeholder = win.querySelector('.log-placeholder');
    if (placeholder) placeholder.remove();

    // 按行分割并添加
    let stream = win.querySelector('.llm-log-stream');
    if (!stream) {
        stream = document.createElement('pre');
        stream.className = 'llm-log-stream';
        win.appendChild(stream);
    }

    const nextText = `${stream.textContent}${text}`;
    stream.textContent = nextText.slice(-MAX_LLM_LOG_CHARS);
    llmLogCount = stream.textContent.length;

    document.getElementById('llm-log-count').textContent = llmLogCount;

    // 保留最近 500 行

    // 自动滚动到底部
    win.scrollTop = win.scrollHeight;
}

// ── Pipeline 步骤条 ──────────────────────────────────────
const STEP_ICONS = {
    waiting: '⏳',
    running: '🔄',
    done: '✅',
    skipped: '⏭️',
};

function resetPipeline() {
    document.querySelectorAll('.pipe-step').forEach(el => {
        el.className = 'pipe-step';
        el.querySelector('.step-icon').textContent = '⏳';
    });
    document.getElementById('stats-section').classList.add('hidden');
}

function updatePipeline(statuses) {
    if (!statuses) return;
    statuses.forEach((status, idx) => {
        const el = document.querySelector(`.pipe-step[data-step="${idx}"]`);
        if (!el) return;
        el.className = `pipe-step ${status}`;
        el.querySelector('.step-icon').textContent = STEP_ICONS[status] || '⏳';
    });
}


// ══════════════════════════════════════════════
//  统计和图表
// ══════════════════════════════════════════════

async function loadStats() {
    const game = document.getElementById('cfg-game').value;
    try {
        const stats = await apiFetch(`/api/output/${game}`);
        outputCache = stats;
        renderStats(stats);
    } catch (e) {
        console.error('加载统计失败:', e);
    }
}

function renderStats(stats) {
    const section = document.getElementById('stats-section');
    section.classList.remove('hidden');

    document.getElementById('stat-total').textContent = stats.total;
    document.getElementById('stat-qc').textContent = stats.labels.quick_command || 0;
    document.getElementById('stat-tactical').textContent = stats.labels.tactical || 0;
    document.getElementById('stat-chat').textContent = stats.labels.chat || 0;

    // 绘制图表
    drawDonut('chart-labels', stats.labels, {
        quick_command: '#22c55e',
        tactical: '#f97316',
        chat: '#06b6d4',
    });

    drawBar('chart-sources', stats.source_types, {
        template: '#22c55e',
        adversarial: '#f97316',
        paraphrase: '#a855f7',
        global_negative: '#ef4444',
    });
}


// ── 环形图 ────────────────────────────────────────────────
async function loadAuditData(forceRefresh = false) {
    const game = document.getElementById('cfg-game').value;
    if (forceRefresh) {
        auditRoundCache.clear();
    }

    try {
        auditCache = await apiFetch(`/api/audit/${game}`);
        renderAuditSection(auditCache);
    } catch (e) {
        console.error('加载审计结果失败:', e);
        renderAuditSection({
            exists: false,
            error: e.message,
            rounds: [],
            derived_summary: null,
            summary: null,
        });
    }
}

function renderAuditSection(data) {
    const section = document.getElementById('audit-section');
    const roundsEl = document.getElementById('audit-rounds');
    const metaEl = document.getElementById('audit-meta');
    section.classList.remove('hidden');

    const summary = data && data.summary && typeof data.summary === 'object' ? data.summary : {};
    const derived = data && data.derived_summary && typeof data.derived_summary === 'object' ? data.derived_summary : {};
    const summaryRounds = Array.isArray(summary.round_summaries) ? summary.round_summaries : [];
    const rounds = Array.isArray(data?.rounds) ? data.rounds : [];

    const roundsRequested = summary.rounds_requested ?? summaryRounds.length ?? rounds.length;
    const roundsCompleted = summary.rounds_completed ?? derived.rounds_completed ?? rounds.length;
    const failTotal = derived.fail_count_total ?? summaryRounds.reduce((sum, item) => sum + (item.fail_count || 0), 0);
    const borderlineTotal = derived.borderline_count_total ?? summaryRounds.reduce((sum, item) => sum + (item.borderline_count || 0), 0);
    const worstRisk = derived.worst_overall_risk || pickWorstRisk(rounds.map(item => item.overall_risk));
    const statusText = getAuditStatusText(summary.status, rounds.length);

    document.getElementById('audit-status-value').innerHTML = renderAuditBadge(statusText.label, statusText.className);
    document.getElementById('audit-rounds-value').textContent = `${roundsCompleted}/${roundsRequested || 0}`;
    document.getElementById('audit-risk-value').innerHTML = renderAuditBadge(worstRisk || 'unknown', `risk-${normalizeAuditToken(worstRisk || 'unknown')}`);
    document.getElementById('audit-fail-value').textContent = `${failTotal} / ${borderlineTotal}`;

    if (!data || !data.exists) {
        metaEl.textContent = data?.error ? `审计读取失败: ${data.error}` : '当前没有质量抽查结果。';
        roundsEl.innerHTML = renderAuditEmptyState('暂无抽查结果，执行主流程后会在这里展示。');
        return;
    }

    const updatedAt = data.summary_file_updated_at || derived.latest_updated_at || '';
    const errors = Array.isArray(summary.errors) ? summary.errors : [];
    const roundErrors = Array.isArray(data.round_errors) ? data.round_errors : [];
    metaEl.textContent = [
        summary.input_path ? `数据集: ${summary.input_path}` : '',
        updatedAt ? `最近更新: ${formatDateTime(updatedAt)}` : '',
        errors.length || roundErrors.length ? `异常: ${errors.length + roundErrors.length}` : '',
    ].filter(Boolean).join(' | ');

    if (rounds.length === 0) {
        const errorText = errors.map(item => item.error).join('；') || roundErrors.map(item => item.error).join('；');
        roundsEl.innerHTML = renderAuditEmptyState(errorText || '有审计目录，但没有可展示的轮次结果。');
        return;
    }

    roundsEl.innerHTML = rounds.map(round => {
        const problemSamples = Array.isArray(round.problem_sample_indices) && round.problem_sample_indices.length > 0
            ? `问题样本: ${round.problem_sample_indices.join(', ')}`
            : '问题样本: 无';
        return `
        <button class="audit-round-card" type="button" onclick="viewAuditRound(${round.round_index})">
            <div class="audit-round-top">
                <div class="audit-round-title">第 ${round.round_index} 轮</div>
                <div class="audit-round-badges">
                    ${renderAuditBadge(round.overall_risk || 'unknown', `risk-${normalizeAuditToken(round.overall_risk || 'unknown')}`)}
                    ${renderAuditBadge(round.final_verdict || 'unknown', `verdict-${normalizeAuditToken(round.final_verdict || 'unknown')}`)}
                </div>
            </div>
            <div class="audit-round-stats">
                <span>样本 ${round.total_samples || round.sample_count_actual || 0}</span>
                <span>fail ${round.fail_count || 0}</span>
                <span>borderline ${round.borderline_count || 0}</span>
                <span>fatal ${round.fatal_count || 0}</span>
            </div>
            <div class="audit-round-meta">${escapeHtml(problemSamples)}</div>
            <div class="audit-round-meta">${escapeHtml(formatDateTime(round.updated_at))}</div>
        </button>`;
    }).join('');
}

async function viewAuditRound(roundIndex) {
    const game = document.getElementById('cfg-game').value;
    const cacheKey = `${game}:${roundIndex}`;

    try {
        let payload = auditRoundCache.get(cacheKey);
        if (!payload) {
            payload = await apiFetch(`/api/audit/${game}/rounds/${roundIndex}`);
            auditRoundCache.set(cacheKey, payload);
        }
        openModal(`质量抽查 · 第 ${roundIndex} 轮`, buildAuditRoundModal(payload));
    } catch (e) {
        alert('加载审计详情失败: ' + e.message);
    }
}

function buildAuditRoundModal(payload) {
    const auditResult = payload && typeof payload.audit_result === 'object' ? payload.audit_result : {};
    const summary = auditResult && typeof auditResult.audit_summary === 'object' ? auditResult.audit_summary : {};
    const findings = Array.isArray(auditResult.systemic_findings) ? auditResult.systemic_findings : [];
    const sampleResults = Array.isArray(auditResult.sample_results) ? auditResult.sample_results : [];

    const summaryHtml = `
    <div class="audit-detail-grid">
        <div class="audit-detail-card">
            <div class="audit-detail-label">风险</div>
            <div class="audit-detail-value">${renderAuditBadge(summary.overall_risk || 'unknown', `risk-${normalizeAuditToken(summary.overall_risk || 'unknown')}`)}</div>
        </div>
        <div class="audit-detail-card">
            <div class="audit-detail-label">结论</div>
            <div class="audit-detail-value">${renderAuditBadge(summary.final_verdict || 'unknown', `verdict-${normalizeAuditToken(summary.final_verdict || 'unknown')}`)}</div>
        </div>
        <div class="audit-detail-card">
            <div class="audit-detail-label">通过 / 边界 / 失败</div>
            <div class="audit-detail-value mono">${summary.pass_count || 0} / ${summary.borderline_count || 0} / ${summary.fail_count || 0}</div>
        </div>
        <div class="audit-detail-card">
            <div class="audit-detail-label">Fatal</div>
            <div class="audit-detail-value mono">${summary.fatal_count || 0}</div>
        </div>
    </div>`;

    const findingsHtml = findings.length > 0
        ? findings.map(item => `
        <div class="audit-finding">
            <div class="audit-finding-top">
                ${renderAuditBadge(item.severity || 'unknown', `risk-${normalizeAuditToken(item.severity || 'unknown')}`)}
                <strong>${escapeHtml(item.title || '未命名问题')}</strong>
            </div>
            <p>${escapeHtml(item.why_it_matters || '')}</p>
            <p>${escapeHtml(item.detail || '')}</p>
            <p class="audit-finding-fix">建议: ${escapeHtml(item.fix_suggestion || '无')}</p>
        </div>`).join('')
        : '<div class="audit-empty-inline">本轮没有系统性问题。</div>';

    const sampleRows = sampleResults.map(item => {
        const issues = Array.isArray(item.issues) ? item.issues : [];
        const issueText = issues.length > 0
            ? issues.map(issue => `${issue.type || 'issue'}: ${issue.detail || ''}`).join(' | ')
            : '-';
        return `
            <tr>
                <td>${item.sample_index ?? '-'}</td>
                <td>${renderAuditBadge(item.verdict || 'unknown', `verdict-${normalizeAuditToken(item.verdict || 'unknown')}`)}</td>
                <td>${escapeHtml(item.current_label || '-')}</td>
                <td>${escapeHtml(item.expected_label || '-')}</td>
                <td>${escapeHtml(item.utterance || '')}</td>
                <td>${escapeHtml(issueText)}</td>
            </tr>`;
    }).join('');

    const samplesHtml = `
    <table class="sample-table audit-sample-table">
        <thead>
            <tr>
                <th style="width:52px">#</th>
                <th style="width:110px">Verdict</th>
                <th style="width:110px">Current</th>
                <th style="width:110px">Expected</th>
                <th>Utterance</th>
                <th>Issues</th>
            </tr>
        </thead>
        <tbody>${sampleRows}</tbody>
    </table>`;

    return `
    <div class="audit-modal-section">
        <div class="audit-modal-meta">文件: ${escapeHtml(payload.file_name || '')} | 更新时间: ${escapeHtml(formatDateTime(payload.updated_at))}</div>
        ${summaryHtml}
    </div>
    <div class="audit-modal-section">
        <h4>系统问题</h4>
        <div class="audit-findings">${findingsHtml}</div>
    </div>
    <div class="audit-modal-section">
        <h4>样本结果</h4>
        ${samplesHtml}
    </div>`;
}

function renderAuditEmptyState(message) {
    return `<div class="audit-placeholder">${escapeHtml(message)}</div>`;
}

function getAuditStatusText(status, roundCount) {
    const normalized = normalizeAuditToken(status || '');
    if (normalized === 'completed') {
        return { label: 'completed', className: 'verdict-pass' };
    }
    if (normalized === 'completed-with-errors') {
        return { label: 'partial', className: 'verdict-borderline' };
    }
    if (normalized === 'skipped') {
        return { label: 'skipped', className: 'risk-unknown' };
    }
    if (roundCount > 0) {
        return { label: 'available', className: 'verdict-pass' };
    }
    return { label: 'none', className: 'risk-unknown' };
}

function normalizeAuditToken(value) {
    return String(value || 'unknown').toLowerCase().replace(/[^a-z0-9]+/g, '-');
}

function renderAuditBadge(text, className) {
    return `<span class="audit-badge ${className}">${escapeHtml(String(text || 'unknown'))}</span>`;
}

function pickWorstRisk(risks) {
    const rank = { unknown: -1, low: 0, medium: 1, high: 2 };
    let current = 'unknown';
    (risks || []).forEach(item => {
        const risk = String(item || 'unknown').toLowerCase();
        if ((rank[risk] ?? -1) > (rank[current] ?? -1)) {
            current = risk;
        }
    });
    return current;
}

function formatDateTime(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return String(value);
    }
    return date.toLocaleString('zh-CN', { hour12: false });
}

function drawDonut(canvasId, data, colors) {
    const canvas = document.getElementById(canvasId);
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    canvas.width = canvas.clientWidth * dpr;
    canvas.height = canvas.clientHeight * dpr;
    ctx.scale(dpr, dpr);

    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const cx = w / 2;
    const cy = h / 2;
    const r = Math.min(w, h) / 2 - 30;
    const innerR = r * 0.6;

    const total = Object.values(data).reduce((a, b) => a + b, 0);
    if (total === 0) return;

    let angle = -Math.PI / 2;
    const entries = Object.entries(data);

    entries.forEach(([key, value]) => {
        const sliceAngle = (value / total) * Math.PI * 2;
        const color = colors[key] || '#6e7681';

        ctx.beginPath();
        ctx.arc(cx, cy, r, angle, angle + sliceAngle);
        ctx.arc(cx, cy, innerR, angle + sliceAngle, angle, true);
        ctx.closePath();
        ctx.fillStyle = color;
        ctx.fill();

        // 标注
        const midAngle = angle + sliceAngle / 2;
        const labelR = r + 16;
        const lx = cx + Math.cos(midAngle) * labelR;
        const ly = cy + Math.sin(midAngle) * labelR;
        ctx.fillStyle = '#8b949e';
        ctx.font = '11px Inter, sans-serif';
        ctx.textAlign = Math.cos(midAngle) > 0 ? 'left' : 'right';
        ctx.textBaseline = 'middle';
        const pct = ((value / total) * 100).toFixed(1);
        ctx.fillText(`${pct}%`, lx, ly);

        angle += sliceAngle;
    });

    // 中心文字
    ctx.fillStyle = '#e6edf3';
    ctx.font = 'bold 20px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(total, cx, cy - 6);
    ctx.fillStyle = '#8b949e';
    ctx.font = '11px Inter, sans-serif';
    ctx.fillText('总计', cx, cy + 12);

    // 图例
    let ly2 = h - 8;
    let lx2 = 10;
    entries.forEach(([key]) => {
        const color = colors[key] || '#6e7681';
        ctx.fillStyle = color;
        ctx.fillRect(lx2, ly2 - 6, 8, 8);
        ctx.fillStyle = '#8b949e';
        ctx.font = '10px Inter, sans-serif';
        ctx.textAlign = 'left';
        ctx.fillText(key, lx2 + 12, ly2);
        lx2 += ctx.measureText(key).width + 24;
    });
}

// ── 条形图 ────────────────────────────────────────────────
function drawBar(canvasId, data, colors) {
    const canvas = document.getElementById(canvasId);
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    canvas.width = canvas.clientWidth * dpr;
    canvas.height = canvas.clientHeight * dpr;
    ctx.scale(dpr, dpr);

    const w = canvas.clientWidth;
    const h = canvas.clientHeight;
    const entries = Object.entries(data);
    if (entries.length === 0) return;

    const maxVal = Math.max(...entries.map(e => e[1]));
    const barWidth = Math.min(50, (w - 40) / entries.length - 10);
    const chartH = h - 50;
    const startX = (w - entries.length * (barWidth + 10) + 10) / 2;

    entries.forEach(([key, value], i) => {
        const barH = maxVal > 0 ? (value / maxVal) * (chartH - 20) : 0;
        const x = startX + i * (barWidth + 10);
        const y = chartH - barH;
        const color = colors[key] || '#6e7681';

        // 条形
        ctx.fillStyle = color;
        roundRect(ctx, x, y, barWidth, barH, 4);
        ctx.fill();

        // 数值
        ctx.fillStyle = '#e6edf3';
        ctx.font = 'bold 12px Inter, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';
        ctx.fillText(value, x + barWidth / 2, y - 4);

        // 标签
        ctx.fillStyle = '#8b949e';
        ctx.font = '10px Inter, sans-serif';
        ctx.textBaseline = 'top';
        // 简写过长名称
        let label = key;
        if (label === 'global_negative') label = 'global_neg';
        ctx.fillText(label, x + barWidth / 2, chartH + 6);
    });
}

function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h);
    ctx.lineTo(x, y + h);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
}


// ══════════════════════════════════════════════
//  数据浏览器
// ══════════════════════════════════════════════

async function loadOutputData() {
    const game = document.getElementById('cfg-game').value;
    try {
        outputCache = await apiFetch(`/api/output/${game}`);
        renderDataGrid();
    } catch (e) {
        console.error('加载输出数据失败:', e);
    }
}

function renderDataGrid() {
    const grid = document.getElementById('data-grid');
    if (!outputCache || !outputCache.commands) {
        grid.innerHTML = '<div class="data-placeholder"><span class="placeholder-icon">📦</span><p>暂无数据</p></div>';
        return;
    }

    let html = '';

    // 命令级卡片
    if (currentFilter === 'all' || ['template', 'adversarial', 'paraphrase'].includes(currentFilter)) {
        outputCache.commands.forEach(cmd => {
            const show = currentFilter === 'all' || cmd[currentFilter] > 0;
            if (!show) return;

            html += `
            <div class="data-card" onclick="viewCommandSamples('${cmd.command_id}')">
                <div class="data-card-title">${cmd.command_id}</div>
                <div class="data-card-desc">共 ${cmd.total} 条样本</div>
                <div class="data-card-stats">
                    <span class="mini-stat"><span class="mini-stat-dot template"></span>${cmd.template} template</span>
                    <span class="mini-stat"><span class="mini-stat-dot adversarial"></span>${cmd.adversarial} adversarial</span>
                    <span class="mini-stat"><span class="mini-stat-dot paraphrase"></span>${cmd.paraphrase} paraphrase</span>
                </div>
            </div>`;
        });
    }

    // 全局负样本卡片
    if (currentFilter === 'all' || currentFilter === 'global_negative') {
        const gnCount = outputCache.global_negatives || 0;
        if (gnCount > 0) {
            html += `
            <div class="data-card global-neg" onclick="viewGlobalNegatives()">
                <div class="data-card-title">🌐 全局负样本</div>
                <div class="data-card-desc">共 ${gnCount} 条</div>
                <div class="data-card-stats">
                    <span class="mini-stat">tactical + chat 混合</span>
                </div>
            </div>`;
        }
    }

    grid.innerHTML = html || '<div class="data-placeholder"><span class="placeholder-icon">📦</span><p>暂无匹配数据</p></div>';
}


// ── 查看 Command 样本 ────────────────────────────────────
async function viewCommandSamples(commandId) {
    const game = document.getElementById('cfg-game').value;
    try {
        const data = await apiFetch(`/api/output/${game}/${commandId}/merged`);
        const samples = data.samples || [];

        let tableHTML = `
        <table class="sample-table">
            <thead>
                <tr>
                    <th style="width:40px">#</th>
                    <th>文本</th>
                    <th style="width:110px">Label</th>
                    <th style="width:100px">Source</th>
                    <th style="width:140px">Slots</th>
                </tr>
            </thead>
            <tbody>`;

        samples.forEach((s, i) => {
            const label = s.label || '';
            const sourceType = s.source_type || '';
            const slots = s.slots && Object.keys(s.slots).length > 0
                ? Object.entries(s.slots).map(([k, v]) => `${k}:${v}`).join(', ')
                : '-';

            tableHTML += `
                <tr>
                    <td>${i + 1}</td>
                    <td>${escapeHtml(s.text || '')}</td>
                    <td><span class="label-badge ${label}">${label}</span></td>
                    <td><span class="source-badge">${sourceType}</span></td>
                    <td class="slots-display">${escapeHtml(slots)}</td>
                </tr>`;
        });

        tableHTML += '</tbody></table>';
        openModal(`${commandId} — ${samples.length} 条样本`, tableHTML);

    } catch (e) {
        alert('加载样本失败: ' + e.message);
    }
}

// ── 查看全局负样本 ────────────────────────────────────────
async function viewGlobalNegatives() {
    const game = document.getElementById('cfg-game').value;
    try {
        const data = await apiFetch(`/api/output/${game}/global_negatives`);
        const buckets = data.buckets || {};

        let tableHTML = `
        <table class="sample-table">
            <thead>
                <tr>
                    <th style="width:40px">#</th>
                    <th>文本</th>
                    <th style="width:100px">Label</th>
                    <th style="width:200px">Bucket</th>
                </tr>
            </thead>
            <tbody>`;

        let idx = 0;
        Object.entries(buckets).forEach(([bucket, samples]) => {
            samples.forEach(s => {
                idx++;
                const label = typeof s.label === 'object' ? s.label.type : (s.label || '');
                tableHTML += `
                    <tr>
                        <td>${idx}</td>
                        <td>${escapeHtml(s.input || '')}</td>
                        <td><span class="label-badge ${label}">${label}</span></td>
                        <td><span class="source-badge">${bucket}</span></td>
                    </tr>`;
            });
        });

        tableHTML += '</tbody></table>';
        openModal(`全局负样本 — ${data.total} 条`, tableHTML);

    } catch (e) {
        alert('加载全局负样本失败: ' + e.message);
    }
}


// ══════════════════════════════════════════════
//  Commands 查看页
// ══════════════════════════════════════════════

async function renderCommandsPage() {
    const game = document.getElementById('cmd-game-select').value || 'mmorpg';
    const grid = document.getElementById('commands-grid');

    try {
        const data = await apiFetch(`/api/commands/${game}`);
        const commands = data.commands || [];

        grid.innerHTML = commands.map(cmd => {
            const slotsHTML = cmd.slots.length > 0
                ? cmd.slots.map(s => `<span class="slot-tag">${s.name}:${s.type}</span>`).join('')
                : '<span class="no-slots">(无参数)</span>';

            const aliasesHTML = cmd.aliases.map(a => `<span class="alias-tag">${escapeHtml(a)}</span>`).join('');

            return `
            <div class="cmd-card">
                <div class="cmd-card-id">${cmd.command_id}</div>
                <div class="cmd-card-desc">${escapeHtml(cmd.desc)}</div>
                <div class="cmd-card-section">
                    <div class="cmd-card-section-title">Slots</div>
                    ${slotsHTML}
                </div>
                <div class="cmd-card-section">
                    <div class="cmd-card-section-title">Aliases</div>
                    ${aliasesHTML}
                </div>
            </div>`;
        }).join('');

    } catch (e) {
        grid.innerHTML = `<div class="data-placeholder"><p>加载失败: ${e.message}</p></div>`;
    }
}

// ── 校验 ──────────────────────────────────────────────────
async function runValidate() {
    const game = document.getElementById('cmd-game-select').value || 'mmorpg';
    const resultEl = document.getElementById('validate-result');

    try {
        const data = await apiFetch(`/api/commands/${game}/validate`);
        resultEl.classList.remove('hidden', 'pass', 'fail');
        resultEl.classList.add(data.passed ? 'pass' : 'fail');
        resultEl.textContent = data.output || data.errors || '无输出';
    } catch (e) {
        resultEl.classList.remove('hidden', 'pass', 'fail');
        resultEl.classList.add('fail');
        resultEl.textContent = '校验失败: ' + e.message;
    }
}


// ══════════════════════════════════════════════
//  工具函数
// ══════════════════════════════════════════════

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

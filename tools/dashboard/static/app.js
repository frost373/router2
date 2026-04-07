/* ══════════════════════════════════════════════════════════════
   训练数据生成器 Dashboard — 前端逻辑
   ══════════════════════════════════════════════════════════════ */

const API = '';  // 同源
const DEFAULT_MAIN_TASK = {
    task_type: 'main_pipeline',
    task_name: '主流程',
    steps: [
        { index: 0, name: '词库生成' },
        { index: 1, name: '别名扩写' },
        { index: 2, name: '模板填槽' },
        { index: 3, name: '对抗样本' },
        { index: 4, name: 'Paraphrase' },
        { index: 5, name: '合并输出' },
        { index: 6, name: '质量抽查' },
    ],
};
const DEFAULT_GLOBAL_NEGATIVE_TASK = {
    task_type: 'global_negative',
    task_name: '全局负样本',
    steps: [
        { index: 0, name: '全局负样本生成' },
    ],
};
const DEFAULT_AUDIT_TASK = {
    task_type: 'quality_audit',
    task_name: '质量抽查',
    steps: [
        { index: 0, name: '质量抽查' },
    ],
};
const DEFAULT_FULL_CHECK_TASK = {
    task_type: 'full_data_check',
    task_name: '全部数据检查',
    steps: [
        { index: 0, name: '构建快照' },
        { index: 1, name: '全量检查' },
        { index: 2, name: '汇总结果' },
    ],
};

// ── 全局状态 ───────────────────────────────────────────────
let config = null;           // 后端配置
let commandsList = [];       // 当前 game 的 commands
let eventSource = null;      // SSE 连接
let outputCache = null;      // 输出数据缓存
let auditCache = null;       // 审计概览缓存
let auditRoundCache = new Map(); // 审计详情缓存
let fullCheckCache = null;   // 全量检查概览缓存
let fullCheckIssuesCache = [];
let fullCheckSelected = new Set();
let fullCheckPollTimer = null;
let fullCheckLoading = false;
let fullCheckIssuesLoading = false;
let currentFilter = 'all';   // 数据浏览器筛选
let currentTaskState = normalizeTaskState(DEFAULT_MAIN_TASK);

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
    await Promise.allSettled([loadStats(), loadAuditData(), loadFullCheckData()]);
    await syncTaskState();
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
    const slider = document.getElementById('cfg-global-threshold');
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
            document.getElementById('cfg-global-rounds').value = config.defaults.global_neg_rounds;
        }
        if (config.defaults.dedup_threshold != null) {
            document.getElementById('cfg-global-threshold').value = config.defaults.dedup_threshold;
            document.getElementById('threshold-display').textContent = config.defaults.dedup_threshold;
        }
        if (config.defaults.audit_sample_count != null) {
            document.getElementById('cfg-audit-sample-count').value = config.defaults.audit_sample_count;
            document.getElementById('cfg-audit-task-sample-count').value = config.defaults.audit_sample_count;
        }
        if (config.defaults.audit_rounds != null) {
            document.getElementById('cfg-audit-rounds').value = config.defaults.audit_rounds;
            document.getElementById('cfg-audit-task-rounds').value = config.defaults.audit_rounds;
        }
        if (config.defaults.full_check_batch_size != null) {
            document.getElementById('cfg-full-check-batch-size').value = config.defaults.full_check_batch_size;
        }

        // game 切换时更新 commands 和审计/统计视图
        gameSelect.addEventListener('change', async () => {
            const game = gameSelect.value;
            auditRoundCache.clear();
            fullCheckSelected.clear();
            updateGlobalNegativePath();
            updateAuditInputPath();
            updateFullCheckInputPath();
            await loadCommands(game);
            await Promise.allSettled([loadStats(), loadAuditData(), loadFullCheckData()]);
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
        document.getElementById('btn-generate-audit').addEventListener('click', startQualityAudit);
        document.getElementById('btn-generate-full-check').addEventListener('click', startFullCheck);
        document.getElementById('btn-generate-global').addEventListener('click', startGlobalNegatives);
        document.getElementById('btn-stop').addEventListener('click', stopCurrentTask);
        document.getElementById('btn-validate').addEventListener('click', runValidate);
        document.getElementById('btn-refresh-audit').addEventListener('click', () => loadAuditData(true));
        document.getElementById('btn-refresh-full-check').addEventListener('click', () => loadFullCheckData(true));
        document.getElementById('btn-full-check-filter').addEventListener('click', () => loadFullCheckIssues());
        document.getElementById('btn-full-check-apply-selected').addEventListener('click', () => applySelectedFullCheckAction('apply_expected'));
        document.getElementById('btn-full-check-ignore-selected').addEventListener('click', () => applySelectedFullCheckAction('ignore'));
        updateGlobalNegativePath();
        updateAuditInputPath();
        updateFullCheckInputPath();
        updateGlobalNegativeSummary();
        updateAuditTaskSummary();
        updateFullCheckTaskSummary();
        setTaskState(currentTaskState);

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
        const selectedCommandIds = getSelectedCommandIds();
        cmdSelect.innerHTML = '';
        const allOption = new Option('全部', '');
        allOption.selected = selectedCommandIds.length === 0;
        cmdSelect.add(allOption);
        commandsList.forEach(cmd => {
            const option = new Option(cmd.command_id, cmd.command_id);
            option.selected = selectedCommandIds.includes(cmd.command_id);
            cmdSelect.add(option);
        });
        cmdSelect.size = Math.min(Math.max(commandsList.length + 1, 6), 10);
    } catch (e) {
        console.error('加载 commands 失败:', e);
    }
}

function getSelectedCommandIds() {
    const selectedOptions = Array.from(document.getElementById('cfg-command').selectedOptions);
    if (selectedOptions.some(option => option.value === '')) {
        return [];
    }
    return selectedOptions.map(option => option.value);
}


// ══════════════════════════════════════════════
//  触发生成
// ══════════════════════════════════════════════

async function startGenerate() {
    const params = {
        game: document.getElementById('cfg-game').value,
        model: document.getElementById('cfg-model').value || null,
        command_ids: getSelectedCommandIds(),
        think_mode: document.getElementById('cfg-think-mode').checked,
        think_level: document.getElementById('cfg-think-level').value,
        template_count: parseInt(document.getElementById('cfg-template').value),
        adversarial_source: parseInt(document.getElementById('cfg-adversarial').value),
        paraphrase_source: parseInt(document.getElementById('cfg-paraphrase').value),
        audit_sample_count: parseInt(document.getElementById('cfg-audit-sample-count').value),
        audit_rounds: parseInt(document.getElementById('cfg-audit-rounds').value),
        skip_vocab: document.getElementById('cfg-skip-vocab').checked,
        skip_aliases: document.getElementById('cfg-skip-aliases').checked,
    };

    try {
        clearLogs();
        await apiPost('/api/generate', params);
        setTaskState(buildMainTaskPreview());
        setRunningState(true, '主流程');

        // 切换到进度 tab
        document.querySelector('[data-tab="progress"]').click();
        activateProgressTab('log');
    } catch (e) {
        alert('启动失败: ' + e.message);
    }
}

async function startQualityAudit() {
    const params = {
        game: document.getElementById('cfg-game').value,
        model: document.getElementById('cfg-model').value || null,
        think_mode: document.getElementById('cfg-think-mode').checked,
        think_level: document.getElementById('cfg-think-level').value,
        sample_count: parseInt(document.getElementById('cfg-audit-task-sample-count').value),
        rounds: parseInt(document.getElementById('cfg-audit-task-rounds').value),
    };

    try {
        clearLogs();
        await apiPost('/api/audit/run', params);
        setTaskState(buildAuditTaskPreview());
        setRunningState(true, '质量抽查');

        document.querySelector('[data-tab="progress"]').click();
        activateProgressTab('log');
    } catch (e) {
        alert('启动失败: ' + e.message);
    }
}

async function startFullCheck() {
    const params = {
        game: document.getElementById('cfg-game').value,
        model: document.getElementById('cfg-model').value || null,
        think_mode: document.getElementById('cfg-think-mode').checked,
        think_level: document.getElementById('cfg-think-level').value,
        batch_size: parseInt(document.getElementById('cfg-full-check-batch-size').value, 10),
        restart: document.getElementById('cfg-full-check-restart').checked,
    };

    try {
        clearLogs();
        await apiPost('/api/full-check/run', params);
        setTaskState(buildFullCheckTaskPreview());
        setRunningState(true, '全部数据检查');

        document.querySelector('[data-tab="progress"]').click();
        activateProgressTab('log');
    } catch (e) {
        alert('启动失败: ' + e.message);
    }
}

async function startGlobalNegatives() {
    const params = {
        game: document.getElementById('cfg-game').value,
        model: document.getElementById('cfg-model').value || null,
        think_mode: document.getElementById('cfg-think-mode').checked,
        think_level: document.getElementById('cfg-think-level').value,
        rounds: parseInt(document.getElementById('cfg-global-rounds').value),
        dedup_threshold: parseFloat(document.getElementById('cfg-global-threshold').value),
    };

    try {
        clearLogs();
        await apiPost('/api/global-negatives', params);
        setTaskState(buildGlobalNegativeTaskPreview());
        setRunningState(true, '全局负样本');

        document.querySelector('[data-tab="progress"]').click();
        activateProgressTab('log');
    } catch (e) {
        alert('启动失败: ' + e.message);
    }
}

async function stopCurrentTask() {
    try {
        await apiPost('/api/generate/stop');
    } catch (e) {
        alert('停止失败: ' + e.message);
    }
}

async function syncTaskState() {
    try {
        const state = await apiFetch('/api/generate/status');
        setTaskState(state);
        if (state.running) {
            setRunningState(true, state.task_name);
        } else if (state.error) {
            setStatusError(state.task_name);
        } else if (state.finished) {
            setStatusDone(state.task_name);
        } else {
            setRunningState(false);
        }
    } catch (e) {
        setTaskState(DEFAULT_MAIN_TASK);
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
        case 'reset':
            clearLogs();
            setTaskState(data);
            break;
        case 'log':
            appendLog(data.line);
            break;
        case 'llm_log':
            appendLlmLog(data.text);
            break;
        case 'step':
            setTaskState(data);
            if (data.statuses) {
                // 如果有 running 状态则标记运行中
                if (data.statuses.some(s => s === 'running')) {
                    setRunningState(true, data.task_name);
                }
            }
            break;
        case 'done':
            setTaskState(data);
            setRunningState(false);
            setStatusDone(data.task_name);
            refreshAfterTaskCompletion(data.task_type);
            if (data.task_type === 'quality_audit') {
                activateProgressTab('audit');
            } else if (data.task_type === 'full_data_check') {
                activateProgressTab('full-check');
            }
            break;
        case 'error':
            setTaskState(data);
            appendLog(`[ERROR] ${data.message}`, 'error-line');
            setRunningState(false);
            setStatusError(data.task_name);
            break;
        case 'stopped':
            setTaskState(data);
            if (!data.statuses) {
                appendLog(`[STOP] ${data.message}`, 'error-line');
            }
            setRunningState(false);
            break;
    }
}


// ══════════════════════════════════════════════
//  UI 更新函数
// ══════════════════════════════════════════════

function setRunningState(running, taskName = currentTaskState.task_name) {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const btnMain = document.getElementById('btn-generate');
    const btnAudit = document.getElementById('btn-generate-audit');
    const btnFullCheck = document.getElementById('btn-generate-full-check');
    const btnGlobal = document.getElementById('btn-generate-global');
    const btnStop = document.getElementById('btn-stop');
    currentTaskState.running = running;

    if (running) {
        dot.className = 'status-dot running';
        text.textContent = `${taskName} 运行中...`;
        btnMain.disabled = true;
        btnAudit.disabled = true;
        btnFullCheck.disabled = true;
        btnGlobal.disabled = true;
        btnStop.classList.remove('hidden');
    } else {
        dot.className = 'status-dot';
        text.textContent = '就绪';
        btnMain.disabled = false;
        btnAudit.disabled = false;
        btnFullCheck.disabled = false;
        btnGlobal.disabled = false;
        btnStop.classList.add('hidden');
    }

    syncFullCheckPolling();
    updateFullCheckSelectionCount();
}

function setStatusDone(taskName = currentTaskState.task_name) {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    dot.className = 'status-dot';
    text.textContent = `${taskName} 已完成`;
}

function setStatusError(taskName = currentTaskState.task_name) {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    dot.className = 'status-dot error';
    text.textContent = `${taskName} 出错`;
}

function buildMainTaskPreview() {
    return normalizeTaskState({
        ...DEFAULT_MAIN_TASK,
        running: true,
    });
}

function buildGlobalNegativeTaskPreview() {
    return normalizeTaskState({
        ...DEFAULT_GLOBAL_NEGATIVE_TASK,
        current_step: 0,
        running: true,
        statuses: ['running'],
    });
}

function buildAuditTaskPreview() {
    return normalizeTaskState({
        ...DEFAULT_AUDIT_TASK,
        current_step: 0,
        running: true,
        statuses: ['running'],
    });
}

function buildFullCheckTaskPreview() {
    return normalizeTaskState({
        ...DEFAULT_FULL_CHECK_TASK,
        current_step: 0,
        running: true,
        statuses: ['running', 'waiting', 'waiting'],
    });
}

function normalizeTaskState(data) {
    const fallbackMap = {
        main_pipeline: DEFAULT_MAIN_TASK,
        global_negative: DEFAULT_GLOBAL_NEGATIVE_TASK,
        quality_audit: DEFAULT_AUDIT_TASK,
        full_data_check: DEFAULT_FULL_CHECK_TASK,
    };
    const fallback = fallbackMap[data?.task_type] || DEFAULT_MAIN_TASK;
    const steps = Array.isArray(data?.steps) && data.steps.length > 0
        ? data.steps.map((step, idx) => ({
            index: step.index ?? idx,
            name: step.name || `步骤 ${idx + 1}`,
        }))
        : fallback.steps.map((step, idx) => ({ index: idx, name: step.name }));
    const statusesSource = Array.isArray(data?.statuses)
        ? data.statuses
        : (Array.isArray(data?.step_statuses) ? data.step_statuses : []);
    const statuses = steps.map((_, idx) => statusesSource[idx] || 'waiting');

    return {
        task_type: data?.task_type || fallback.task_type,
        task_name: data?.task_name || fallback.task_name,
        steps,
        statuses,
        current_step: typeof data?.current_step === 'number' ? data.current_step : -1,
        running: Boolean(data?.running),
        finished: Boolean(data?.finished),
        error: Boolean(data?.error),
        stopped: Boolean(data?.stopped),
    };
}

function setTaskState(data) {
    currentTaskState = normalizeTaskState(data);
    renderPipeline(currentTaskState.steps, currentTaskState.statuses);

    const taskNameEl = document.getElementById('pipeline-task-name');
    if (taskNameEl) {
        taskNameEl.textContent = currentTaskState.task_name;
    }
}

function updateGlobalNegativePath() {
    const game = document.getElementById('cfg-game')?.value || 'mmorpg';
    const pathEl = document.getElementById('global-neg-path');
    if (pathEl) {
        pathEl.textContent = `output/${game}/global_negatives.jsonl`;
    }
}

function updateAuditInputPath() {
    const game = document.getElementById('cfg-game')?.value || 'mmorpg';
    const pathEl = document.getElementById('audit-input-path');
    if (pathEl) {
        pathEl.textContent = `output/${game}/merged_all.jsonl`;
    }
}

function updateFullCheckInputPath() {
    const game = document.getElementById('cfg-game')?.value || 'mmorpg';
    const pathEl = document.getElementById('full-check-input-path');
    if (pathEl) {
        pathEl.textContent = `output/${game}/merged_all.jsonl`;
    }
}

function updateGlobalNegativeSummary() {
    const countEl = document.getElementById('global-neg-count');
    if (!countEl) return;

    const count = outputCache?.global_negatives || 0;
    countEl.textContent = count > 0 ? `${count} 条` : '未生成';
}

function updateAuditTaskSummary(data = auditCache) {
    const statusEl = document.getElementById('audit-task-status');
    if (!statusEl) return;

    const summary = data && typeof data.summary === 'object' ? data.summary : {};
    const derived = data && typeof data.derived_summary === 'object' ? data.derived_summary : {};
    const rounds = Array.isArray(data?.rounds) ? data.rounds : [];
    const roundsRequested = summary.rounds_requested ?? rounds.length ?? 0;
    const roundsCompleted = summary.rounds_completed ?? derived.rounds_completed ?? rounds.length;
    const statusText = getAuditStatusText(summary.status, rounds.length).label;

    if (!data || !data.exists) {
        statusEl.textContent = data?.error ? '读取失败' : '未生成';
        return;
    }

    statusEl.textContent = `${statusText} · ${roundsCompleted}/${roundsRequested || 0}`;
}

function updateFullCheckTaskSummary(data = fullCheckCache) {
    const statusEl = document.getElementById('full-check-task-status');
    if (!statusEl) return;

    const summary = data && typeof data.summary === 'object' ? data.summary : {};
    if (!data || !data.exists || !summary) {
        statusEl.textContent = '未生成';
        return;
    }

    const statusText = summary.status || 'unknown';
    statusEl.textContent = `${statusText} · ${summary.completed_batches || 0}/${summary.batch_count || 0}`;
}

async function refreshAfterTaskCompletion(taskType) {
    const jobs = [];
    if (taskType === 'main_pipeline') {
        jobs.push(loadStats(), loadOutputData(), loadAuditData(true));
    } else if (taskType === 'global_negative') {
        jobs.push(loadStats(), loadOutputData());
    } else if (taskType === 'quality_audit') {
        jobs.push(loadAuditData(true));
    } else if (taskType === 'full_data_check') {
        jobs.push(loadFullCheckData(true));
    }
    await Promise.allSettled(jobs);
}

// ── 日志 ──────────────────────────────────────────────────
let logCount = 0;
let llmLogCount = 0;
const MAX_LLM_LOG_CHARS = 6000;

function clearLogs() {
    const win = document.getElementById('log-window');
    win.innerHTML = '';
    logCount = 0;
    document.getElementById('log-count').textContent = '0';

    // 清空 LLM 日志
    const llmWin = document.getElementById('llm-log-window');
    llmWin.innerHTML = '<div class="log-placeholder">等待 LLM 调用...</div>';
    llmLogCount = 0;
    document.getElementById('llm-log-count').textContent = '0';
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
    return;

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
    return;
    if (llmLogPollInterval) {
        clearInterval(llmLogPollInterval);
        llmLogPollInterval = null;
    }
}

async function getLlmLogSize() {
    return 0;
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
    const statuses = currentTaskState.steps.map(() => 'waiting');
    renderPipeline(currentTaskState.steps, statuses);
    document.getElementById('stats-section').classList.add('hidden');
}

function renderPipeline(steps, statuses) {
    const container = document.getElementById('pipeline-steps');
    if (!container) return;
    if (!Array.isArray(steps) || steps.length === 0) {
        container.innerHTML = '<div class="pipeline-placeholder">等待任务信息...</div>';
        return;
    }

    container.innerHTML = steps.map((step, idx) => {
        const status = statuses[idx] || 'waiting';
        const connector = idx > 0 ? '<div class="pipe-connector"></div>' : '';
        return `
            ${connector}
            <div class="pipe-step ${status}" data-step="${idx}">
                <div class="step-indicator"><span class="step-icon">${STEP_ICONS[status] || '⏳'}</span></div>
                <div class="step-label">${escapeHtml(step.name || `步骤 ${idx + 1}`)}</div>
            </div>`;
    }).join('');
}

function updatePipeline(statuses) {
    if (!statuses) return;
    currentTaskState.statuses = currentTaskState.steps.map((_, idx) => statuses[idx] || 'waiting');
    renderPipeline(currentTaskState.steps, currentTaskState.statuses);
}


// ══════════════════════════════════════════════
//  统计和图表
// ══════════════════════════════════════════════

async function loadStats() {
    const game = document.getElementById('cfg-game').value;
    try {
        const stats = await apiFetch(`/api/output/${game}`);
        outputCache = stats;
        updateGlobalNegativeSummary();
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

async function loadFullCheckData(forceRefresh = false) {
    if (fullCheckLoading) {
        return fullCheckCache;
    }

    const game = document.getElementById('cfg-game').value;
    fullCheckLoading = true;

    try {
        fullCheckCache = await apiFetch(`/api/full-check/${encodeURIComponent(game)}`);
        renderFullCheckSection(fullCheckCache);
        await loadFullCheckIssues();
        return fullCheckCache;
    } catch (e) {
        console.error('加载全部数据检查结果失败:', e);
        fullCheckCache = {
            exists: false,
            error: e.message,
            manifest: null,
            summary: null,
            unresolved_count: 0,
        };
        fullCheckIssuesCache = [];
        fullCheckSelected.clear();
        renderFullCheckSection(fullCheckCache);
        renderFullCheckIssues({
            total: 0,
            limit: 0,
            offset: 0,
            issues: [],
        });
        return fullCheckCache;
    } finally {
        fullCheckLoading = false;
    }
}

async function loadFullCheckIssues() {
    if (fullCheckIssuesLoading) {
        return {
            total: fullCheckIssuesCache.length,
            issues: fullCheckIssuesCache,
        };
    }

    const issuesEl = document.getElementById('full-check-issues');
    if (!fullCheckCache || !fullCheckCache.exists) {
        fullCheckIssuesCache = [];
        fullCheckSelected.clear();
        renderFullCheckIssues({
            total: 0,
            limit: 0,
            offset: 0,
            issues: [],
        });
        return {
            total: 0,
            issues: [],
        };
    }

    if (issuesEl) {
        issuesEl.innerHTML = renderAuditEmptyState('正在加载问题列表...');
    }

    const game = document.getElementById('cfg-game').value;
    const params = new URLSearchParams();
    const resolutionStatus = document.getElementById('full-check-filter-status').value;
    const verdict = document.getElementById('full-check-filter-verdict').value;
    const sourceType = document.getElementById('full-check-filter-source').value;
    const query = document.getElementById('full-check-filter-query').value.trim();

    if (resolutionStatus) params.set('resolution_status', resolutionStatus);
    if (verdict) params.set('verdict', verdict);
    if (sourceType) params.set('source_type', sourceType);
    if (query) params.set('q', query);
    params.set('limit', '500');
    params.set('offset', '0');

    fullCheckIssuesLoading = true;
    try {
        const data = await apiFetch(`/api/full-check/${encodeURIComponent(game)}/issues?${params.toString()}`);
        fullCheckIssuesCache = Array.isArray(data.issues) ? data.issues : [];
        const validIds = new Set(fullCheckIssuesCache.map(item => item.sample_id));
        fullCheckSelected = new Set([...fullCheckSelected].filter(sampleId => validIds.has(sampleId)));
        renderFullCheckIssues(data);
        return data;
    } catch (e) {
        console.error('加载全部数据检查问题失败:', e);
        fullCheckIssuesCache = [];
        fullCheckSelected.clear();
        renderFullCheckIssues({
            total: 0,
            limit: 0,
            offset: 0,
            issues: [],
            error: e.message,
        });
        return null;
    } finally {
        fullCheckIssuesLoading = false;
        updateFullCheckSelectionCount();
    }
}

function renderFullCheckSection(data) {
    const section = document.getElementById('full-check-section');
    const batchesEl = document.getElementById('full-check-batches');
    const issuesEl = document.getElementById('full-check-issues');
    const metaEl = document.getElementById('full-check-meta');
    section.classList.remove('hidden');
    updateFullCheckTaskSummary(data);

    const summary = data && typeof data.summary === 'object' ? data.summary : {};
    const manifest = data && typeof data.manifest === 'object' ? data.manifest : {};
    const statusMeta = getFullCheckStatusMeta(summary.status, Boolean(data?.exists));
    const resolutionCounts = summary && typeof summary.resolution_counts === 'object'
        ? summary.resolution_counts
        : {};
    const resolvedCount = (resolutionCounts.applied || 0) + (resolutionCounts.ignored || 0);

    document.getElementById('full-check-status-value').innerHTML = renderAuditBadge(statusMeta.label, statusMeta.className);
    document.getElementById('full-check-batches-value').textContent = `${summary.completed_batches || 0}/${summary.batch_count || 0}`;
    document.getElementById('full-check-issues-value').textContent = String(summary.total_issues || 0);
    document.getElementById('full-check-resolved-value').textContent = `${resolvedCount} / ${summary.total_issues || 0}`;

    if (!data || !data.exists) {
        metaEl.textContent = data?.error ? `读取失败: ${data.error}` : '当前没有全部数据检查结果。';
        batchesEl.innerHTML = renderAuditEmptyState('暂无批次结果。');
        issuesEl.innerHTML = renderAuditEmptyState('暂无问题结果。');
        return;
    }

    metaEl.textContent = [
        manifest.merged_all_path ? `数据集: ${manifest.merged_all_path}` : '',
        summary.latest_batch_updated_at ? `最近更新: ${formatDateTime(summary.latest_batch_updated_at)}` : '',
        summary.failed_batches ? `失败批次: ${summary.failed_batches}` : '',
        data.can_resume ? '可继续未完成 / 失败批次' : '',
        data.unresolved_count > 0 ? `待处理: ${data.unresolved_count}` : '',
    ].filter(Boolean).join(' | ');

    renderFullCheckBatches(Array.isArray(summary.batches) ? summary.batches : []);
    if (!fullCheckIssuesLoading) {
        issuesEl.innerHTML = renderAuditEmptyState('正在加载问题列表...');
    }
}

function renderFullCheckBatches(batches) {
    const batchesEl = document.getElementById('full-check-batches');
    if (!Array.isArray(batches) || batches.length === 0) {
        batchesEl.innerHTML = renderAuditEmptyState('还没有批次结果。');
        return;
    }

    batchesEl.innerHTML = batches.map(batch => {
        const statusMeta = getFullCheckStatusMeta(batch.status, true);
        const rangeStart = batch.range_start ?? 0;
        const rangeEnd = batch.range_end ?? -1;
        const verdict = batch.final_verdict || 'unknown';
        const risk = batch.overall_risk || 'unknown';
        const updatedAt = batch.updated_at ? formatDateTime(batch.updated_at) : '-';
        const errorHtml = batch.error
            ? `<div class="full-check-batch-meta">${escapeHtml(batch.error)}</div>`
            : '';
        return `
        <div class="full-check-batch-card">
            <div class="full-check-batch-top">
                <div>
                    <strong>Batch ${batch.batch_index || '-'}</strong>
                    <div class="full-check-batch-meta">样本 ${rangeStart} - ${rangeEnd} · ${batch.sample_count || 0} 条</div>
                </div>
                <div class="audit-round-badges">
                    ${renderAuditBadge(statusMeta.label, statusMeta.className)}
                    ${renderAuditBadge(verdict, `verdict-${normalizeAuditToken(verdict)}`)}
                    ${renderAuditBadge(risk, `risk-${normalizeAuditToken(risk)}`)}
                </div>
            </div>
            <div class="audit-round-stats">
                <span>issues ${batch.issue_count || 0}</span>
                <span>fail ${batch.fail_count || 0}</span>
                <span>borderline ${batch.borderline_count || 0}</span>
            </div>
            <div class="full-check-batch-meta">更新时间: ${escapeHtml(updatedAt)}</div>
            ${errorHtml}
        </div>`;
    }).join('');
}

function renderFullCheckIssues(payload) {
    const issuesEl = document.getElementById('full-check-issues');
    const issues = Array.isArray(payload?.issues) ? payload.issues : [];
    const total = payload?.total ?? issues.length;
    const totalIssues = fullCheckCache?.summary?.total_issues || 0;
    const running = Boolean(currentTaskState.running);

    if (!fullCheckCache || !fullCheckCache.exists) {
        issuesEl.innerHTML = renderAuditEmptyState(payload?.error || '暂无问题结果。');
        updateFullCheckSelectionCount();
        return;
    }

    if (issues.length === 0) {
        issuesEl.innerHTML = renderAuditEmptyState(payload?.error || '当前筛选下没有问题。');
        updateFullCheckSelectionCount();
        return;
    }

    const metaLine = `
        <div class="full-check-batch-meta">
            当前筛选 ${issues.length} / ${total} 条，问题总数 ${totalIssues}
        </div>`;

    issuesEl.innerHTML = metaLine + issues.map(issue => {
        const selected = fullCheckSelected.has(issue.sample_id);
        const resolutionStatus = issue.resolution_status || 'pending';
        const suggestedAction = getSuggestedFullCheckAction(issue);
        const suggestedLabel = getSuggestedActionLabel(issue);
        const suggestedDisabled = running || !suggestedAction;
        const actionDisabled = running ? 'disabled' : '';
        const suggestedDisabledAttr = suggestedDisabled ? 'disabled' : '';
        const resolutionMessage = issue.resolution_message
            ? `<div class="full-check-issue-path">处理信息: ${escapeHtml(issue.resolution_message)}</div>`
            : '';
        return `
        <div class="full-check-issue-card ${selected ? 'selected' : ''}" data-full-check-card="${issue.sample_id}">
            <div class="full-check-issue-main">
                <div class="full-check-issue-select">
                    <input
                        type="checkbox"
                        ${selected ? 'checked' : ''}
                        onchange="toggleFullCheckSelection('${issue.sample_id}', this.checked)"
                    >
                </div>
                <div class="full-check-issue-content">
                    <div class="full-check-issue-top">
                        <div class="full-check-issue-status">
                            ${renderAuditBadge(issue.verdict || 'unknown', `verdict-${normalizeAuditToken(issue.verdict || 'unknown')}`)}
                            ${renderResolutionBadge(resolutionStatus)}
                            ${renderRecommendedActionBadge(issue.recommended_action)}
                            ${renderSourceBadge(issue.source_type || '-')}
                        </div>
                        <div class="full-check-issue-meta">
                            #${issue.dataset_index ?? '-'} · batch ${issue.batch_index ?? '-'} · ${escapeHtml(formatDateTime(issue.batch_updated_at || issue.resolution_updated_at))}
                        </div>
                    </div>
                    <div class="full-check-issue-text">${escapeHtml(issue.utterance || '')}</div>
                    <div class="full-check-issue-summary">${escapeHtml(issue.issue_summary || issue.reason || '未提供摘要')}</div>
                    <div class="full-check-issue-compare">
                        ${renderFullCheckCompareBox('当前', issue.current_label, issue.current_command_id, issue.current_slots)}
                        ${renderFullCheckCompareBox('期望', issue.expected_label, issue.expected_command_id, issue.expected_slots)}
                    </div>
                    <div class="full-check-issue-path">${escapeHtml(`${issue.source_file || '-'}:${issue.source_line_number || '-'}`)}</div>
                    ${resolutionMessage}
                    <div class="full-check-issue-actions">
                        <button class="btn btn-sm btn-outline" type="button" onclick="viewFullCheckIssue('${issue.sample_id}')">详情</button>
                        <button class="btn btn-sm btn-primary" type="button" onclick="applySuggestedFullCheckAction('${issue.sample_id}')" ${suggestedDisabledAttr}>${escapeHtml(suggestedLabel)}</button>
                        <button class="btn btn-sm btn-outline" type="button" onclick="applyFullCheckAction('${issue.sample_id}', 'delete_sample')" ${actionDisabled}>删除样本</button>
                        <button class="btn btn-sm btn-outline" type="button" onclick="applyFullCheckAction('${issue.sample_id}', 'ignore')" ${actionDisabled}>忽略</button>
                    </div>
                </div>
            </div>
        </div>`;
    }).join('');

    updateFullCheckSelectionCount();
}

function toggleFullCheckSelection(sampleId, checked) {
    if (checked) {
        fullCheckSelected.add(sampleId);
    } else {
        fullCheckSelected.delete(sampleId);
    }
    const card = document.querySelector(`[data-full-check-card="${sampleId}"]`);
    if (card) {
        card.classList.toggle('selected', checked);
    }
    updateFullCheckSelectionCount();
}

function updateFullCheckSelectionCount() {
    const countEl = document.getElementById('full-check-selection-count');
    const btnApply = document.getElementById('btn-full-check-apply-selected');
    const btnIgnore = document.getElementById('btn-full-check-ignore-selected');
    const count = fullCheckSelected.size;

    if (countEl) {
        countEl.textContent = `已选 ${count} 条`;
    }
    if (btnApply) {
        btnApply.disabled = count === 0 || currentTaskState.running;
    }
    if (btnIgnore) {
        btnIgnore.disabled = count === 0 || currentTaskState.running;
    }
}

function syncFullCheckPolling() {
    const shouldPoll = currentTaskState.running && currentTaskState.task_type === 'full_data_check';
    if (shouldPoll) {
        if (!fullCheckPollTimer) {
            loadFullCheckData().catch(err => console.warn('轮询全部数据检查失败:', err));
            fullCheckPollTimer = window.setInterval(() => {
                loadFullCheckData().catch(err => console.warn('轮询全部数据检查失败:', err));
            }, 4000);
        }
        return;
    }

    if (fullCheckPollTimer) {
        window.clearInterval(fullCheckPollTimer);
        fullCheckPollTimer = null;
    }
}

function findFullCheckIssue(sampleId) {
    return fullCheckIssuesCache.find(item => item.sample_id === sampleId) || null;
}

function viewFullCheckIssue(sampleId) {
    const issue = findFullCheckIssue(sampleId);
    if (!issue) {
        alert('未找到问题详情，请先刷新列表。');
        return;
    }
    openModal(`全部数据检查 · #${issue.dataset_index ?? '-'}`, buildFullCheckIssueModal(issue));
}

function buildFullCheckIssueModal(issue) {
    const issues = Array.isArray(issue.issues) ? issue.issues : [];
    const systemicFindings = Array.isArray(issue.systemic_findings) ? issue.systemic_findings : [];
    const issueDetailsHtml = issues.length > 0
        ? issues.map(item => `
            <div class="audit-finding">
                <div class="audit-finding-top">
                    ${renderAuditBadge(item.severity || item.type || 'issue', `risk-${normalizeAuditToken(item.severity || 'unknown')}`)}
                    <strong>${escapeHtml(item.type || 'issue')}</strong>
                </div>
                <p>${escapeHtml(item.detail || '无')}</p>
            </div>
        `).join('')
        : '<div class="audit-empty-inline">当前样本没有细分 issues。</div>';

    const findingsHtml = systemicFindings.length > 0
        ? systemicFindings.map(item => `
            <div class="audit-finding">
                <div class="audit-finding-top">
                    ${renderAuditBadge(item.severity || 'unknown', `risk-${normalizeAuditToken(item.severity || 'unknown')}`)}
                    <strong>${escapeHtml(item.title || '未命名问题')}</strong>
                </div>
                <p>${escapeHtml(item.why_it_matters || '')}</p>
                <p>${escapeHtml(item.detail || '')}</p>
                <p class="audit-finding-fix">建议: ${escapeHtml(item.fix_suggestion || '无')}</p>
            </div>
        `).join('')
        : '<div class="audit-empty-inline">当前批次没有系统性问题。</div>';

    return `
    <div class="audit-modal-section">
        <div class="audit-modal-meta">
            样本 #${issue.dataset_index ?? '-'} | ${escapeHtml(issue.source_file || '-')}:${escapeHtml(String(issue.source_line_number || '-'))} | 更新时间: ${escapeHtml(formatDateTime(issue.batch_updated_at || issue.resolution_updated_at))}
        </div>
        <div class="audit-detail-grid">
            <div class="audit-detail-card">
                <div class="audit-detail-label">Verdict</div>
                <div class="audit-detail-value">${renderAuditBadge(issue.verdict || 'unknown', `verdict-${normalizeAuditToken(issue.verdict || 'unknown')}`)}</div>
            </div>
            <div class="audit-detail-card">
                <div class="audit-detail-label">处理状态</div>
                <div class="audit-detail-value">${renderResolutionBadge(issue.resolution_status || 'pending')}</div>
            </div>
            <div class="audit-detail-card">
                <div class="audit-detail-label">建议动作</div>
                <div class="audit-detail-value">${renderRecommendedActionBadge(issue.recommended_action)}</div>
            </div>
            <div class="audit-detail-card">
                <div class="audit-detail-label">来源</div>
                <div class="audit-detail-value">${renderSourceBadge(issue.source_type || '-')}</div>
            </div>
        </div>
    </div>
    <div class="audit-modal-section">
        <h4>文本与标签</h4>
        <div class="full-check-issue-text">${escapeHtml(issue.utterance || '')}</div>
        <div class="full-check-issue-compare">
            ${renderFullCheckCompareBox('当前', issue.current_label, issue.current_command_id, issue.current_slots)}
            ${renderFullCheckCompareBox('期望', issue.expected_label, issue.expected_command_id, issue.expected_slots)}
        </div>
    </div>
    <div class="audit-modal-section">
        <h4>问题说明</h4>
        <div class="audit-finding">
            <div class="audit-finding-top">
                <strong>${escapeHtml(issue.issue_summary || '未提供摘要')}</strong>
            </div>
            <p>${escapeHtml(issue.reason || '无')}</p>
            <p class="audit-finding-fix">建议: ${escapeHtml(issue.fix_suggestion || '无')}</p>
            ${issue.resolution_message ? `<p class="audit-finding-fix">处理信息: ${escapeHtml(issue.resolution_message)}</p>` : ''}
        </div>
    </div>
    <div class="audit-modal-section">
        <h4>细分 Issues</h4>
        <div class="audit-findings">${issueDetailsHtml}</div>
    </div>
    <div class="audit-modal-section">
        <h4>批次系统问题</h4>
        <div class="audit-findings">${findingsHtml}</div>
    </div>`;
}

async function applySuggestedFullCheckAction(sampleId) {
    const issue = findFullCheckIssue(sampleId);
    if (!issue) {
        alert('未找到问题，请刷新后重试。');
        return;
    }

    const action = getSuggestedFullCheckAction(issue);
    if (!action) {
        const message = issue.recommended_action === 'keep'
            ? '该问题的建议动作是 keep，没有可直接回写的修改。'
            : '该问题缺少可安全回写的期望内容，建议手动删除或忽略。';
        alert(message);
        return;
    }

    const response = await submitFullCheckActions([{ sample_id: sampleId, action }]);
    if (response && response.status === 'partial') {
        alert('处理部分成功，存在冲突，请刷新后重试。');
    }
}

async function applyFullCheckAction(sampleId, action) {
    const response = await submitFullCheckActions([{ sample_id: sampleId, action }]);
    if (response && response.status === 'partial') {
        alert('处理部分成功，存在冲突，请刷新后重试。');
    }
}

async function applySelectedFullCheckAction(mode) {
    const selectedIds = [...fullCheckSelected];
    if (selectedIds.length === 0) {
        alert('请先选择要处理的问题。');
        return;
    }

    const actions = [];
    const skipped = [];
    selectedIds.forEach(sampleId => {
        const issue = findFullCheckIssue(sampleId);
        if (!issue) {
            skipped.push(sampleId);
            return;
        }

        let action = mode;
        if (mode === 'apply_expected') {
            action = getSuggestedFullCheckAction(issue);
            if (!action) {
                skipped.push(sampleId);
                return;
            }
        }

        actions.push({ sample_id: sampleId, action });
    });

    if (actions.length === 0) {
        alert('所选问题没有可执行的动作。');
        return;
    }

    const response = await submitFullCheckActions(actions);
    if (!response) {
        return;
    }

    const conflicts = Array.isArray(response.results)
        ? response.results.filter(item => item.status === 'conflict').length
        : 0;
    if (skipped.length > 0 || conflicts > 0) {
        const parts = [];
        if (actions.length - conflicts > 0) {
            parts.push(`已处理 ${actions.length - conflicts} 条`);
        }
        if (skipped.length > 0) {
            parts.push(`跳过 ${skipped.length} 条无可执行建议的项`);
        }
        if (conflicts > 0) {
            parts.push(`冲突 ${conflicts} 条`);
        }
        alert(parts.join('，'));
    }
}

async function submitFullCheckActions(actions) {
    if (currentTaskState.running) {
        alert('当前有任务在运行，暂时不能处理检查结果。');
        return null;
    }

    const game = document.getElementById('cfg-game').value;
    try {
        const response = await apiPost(`/api/full-check/${encodeURIComponent(game)}/actions/apply`, {
            actions,
        });
        const handledIds = new Set(actions.map(item => item.sample_id));
        fullCheckSelected = new Set([...fullCheckSelected].filter(sampleId => !handledIds.has(sampleId)));
        updateFullCheckSelectionCount();
        await Promise.allSettled([
            loadFullCheckData(true),
            loadStats(),
            loadOutputData(),
        ]);
        return response;
    } catch (e) {
        alert('处理失败: ' + e.message);
        return null;
    }
}

function renderAuditSection(data) {
    const section = document.getElementById('audit-section');
    const roundsEl = document.getElementById('audit-rounds');
    const metaEl = document.getElementById('audit-meta');
    section.classList.remove('hidden');
    updateAuditTaskSummary(data);

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
        roundsEl.innerHTML = renderAuditEmptyState('暂无抽查结果，执行主流程或独立抽查后会在这里展示。');
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
        updateGlobalNegativeSummary();
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
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
}

function getFullCheckStatusMeta(status, hasData = false) {
    const normalized = normalizeAuditToken(status || '');
    if (normalized === 'completed') {
        return { label: 'completed', className: 'verdict-completed' };
    }
    if (normalized === 'completed-with-errors') {
        return { label: 'partial', className: 'verdict-partial' };
    }
    if (normalized === 'running') {
        return { label: 'running', className: 'verdict-available' };
    }
    if (normalized === 'error') {
        return { label: 'error', className: 'verdict-fail' };
    }
    if (normalized === 'empty') {
        return { label: 'empty', className: 'verdict-none' };
    }
    if (hasData) {
        return { label: 'available', className: 'verdict-available' };
    }
    return { label: 'none', className: 'verdict-none' };
}

function getResolutionMeta(status) {
    const normalized = normalizeAuditToken(status || 'pending');
    if (normalized === 'applied') {
        return { label: 'applied', className: 'resolution-applied' };
    }
    if (normalized === 'ignored') {
        return { label: 'ignored', className: 'resolution-ignored' };
    }
    if (normalized === 'conflict') {
        return { label: 'conflict', className: 'resolution-conflict' };
    }
    return { label: 'pending', className: 'resolution-pending' };
}

function getRecommendedActionMeta(action) {
    const normalized = normalizeAuditToken(action || 'keep');
    if (normalized === 'apply-expected') {
        return { label: 'apply_expected', className: 'verdict-partial' };
    }
    if (normalized === 'delete-sample') {
        return { label: 'delete_sample', className: 'verdict-fail' };
    }
    return { label: 'keep', className: 'verdict-pass' };
}

function renderResolutionBadge(status) {
    const meta = getResolutionMeta(status);
    return `<span class="audit-badge ${meta.className}">${escapeHtml(meta.label)}</span>`;
}

function renderRecommendedActionBadge(action) {
    const meta = getRecommendedActionMeta(action);
    return `<span class="audit-badge ${meta.className}">${escapeHtml(meta.label)}</span>`;
}

function renderLabelBadge(label) {
    const safeLabel = String(label || '-');
    const className = safeLabel === '-' ? '' : ` ${safeLabel}`;
    return `<span class="label-badge${className}">${escapeHtml(safeLabel)}</span>`;
}

function renderSourceBadge(sourceType) {
    return `<span class="source-badge">${escapeHtml(String(sourceType || '-'))}</span>`;
}

function formatSlotsHtml(slots) {
    const entries = Object.entries(slots || {}).filter(([, value]) => value != null && String(value) !== '');
    if (entries.length === 0) {
        return '-';
    }
    return entries.map(([key, value]) => `${escapeHtml(key)}: ${escapeHtml(String(value))}`).join('<br>');
}

function renderFullCheckCompareBox(title, label, commandId, slots) {
    return `
    <div class="full-check-issue-box">
        <div class="full-check-issue-box-label">${escapeHtml(title)}</div>
        <div class="full-check-issue-box-value">
            ${renderLabelBadge(label || '-')}
            <div class="mono">${escapeHtml(commandId || '-')}</div>
            <div class="slots-display">${formatSlotsHtml(slots)}</div>
        </div>
    </div>`;
}

function getSuggestedFullCheckAction(issue) {
    const action = issue?.recommended_action || 'keep';
    if (action === 'keep') {
        return '';
    }
    if (action === 'apply_expected' && issue?.source_type === 'global_negative') {
        return '';
    }
    if (action === 'apply_expected' && !issue?.can_apply_expected) {
        return '';
    }
    return action;
}

function getSuggestedActionLabel(issue) {
    const action = issue?.recommended_action || 'keep';
    if (action === 'delete_sample') {
        return '按建议删除';
    }
    if (action === 'apply_expected' && issue?.source_type === 'global_negative') {
        return '请删除或忽略';
    }
    if (action === 'apply_expected' && !issue?.can_apply_expected) {
        return '无法应用建议';
    }
    if (action === 'apply_expected') {
        return '应用建议';
    }
    return '建议保留';
}

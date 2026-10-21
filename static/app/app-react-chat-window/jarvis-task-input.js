/**
 * jarvis-task-input.js — a held JARVIS task's question in the chat (pending-input contract,
 * plugin/plugins/main_agent/input_request.py).
 *
 * index.html and chat.html both load it, after visit-chat.js and BEFORE resize-drag-and-api.js (which deletes
 * window.__appReactChatWindowParts). In Electron's multi-window mode only the chat follower window draws it, as
 * visit-chat.js does.
 *
 * What it does:
 * - asks main_agent which questions are waiting (pending_inputs) every 5 s while the page is visible, and right after an
 *   answer; the newest one becomes the React `taskPrompt` prop (TaskPromptCard: compact layer or full composer);
 * - sends the owner's answer (answer_input) or cancel (cancel_input). Typed answers here and spoken answers
 *   ("Hey Neko, the answer is …", run_task from the widget) end in the same main_agent code path, which validates,
 *   refuses stale or repeated answers and resumes the task from its checkpoint;
 * - when the list cannot be refreshed, the card says "not connected" instead of showing old data as live.
 *
 * Transport: the existing same-origin card-action route (/api/plugin-cards/<plugin>/action/<ui.action>), CSRF headers
 * from window.nekoLocalMutationSecurity, one retry after a refreshed token: no new endpoint, no new credential.
 * Answer values (secrets included) are only ever in the request body; nothing here stores, logs or toasts them.
 */
(function () {
    'use strict';

    var I = window.__appReactChatWindowParts || (window.__appReactChatWindowParts = {});
    var PLUGIN = 'main_agent';
    var CARD_ID = 'jarvis-task-input';
    var POLL_MS = 5000;
    var MAX_BACKOFF_MS = 60000;
    var state = {
        requests: [],          // [{run_id, task, request:{id, step, title, prompt, fields, ...}}], newest first
        collapsed: {},         // request id -> true (the owner pressed "Later"); the question keeps waiting
        submitting: null,      // request id of the answer in flight
        errors: {},            // request id -> the backend's refusal (safe text, never a value)
        offline: false,
        failures: 0,
        timer: null,
        started: false
    };

    function t(key, fallback, params) {
        var text = '';
        if (typeof window.t === 'function') {
            try { text = window.t(key, params || {}); } catch (_) { text = ''; }
        }
        if (typeof text !== 'string' || !text || text === key) text = fallback || key;
        if (params) {
            Object.keys(params).forEach(function (name) {
                text = text.split('{{' + name + '}}').join(String(params[name]));
            });
        }
        return text;
    }

    function isChatFollowerPath() {
        var pathname = (window.location && window.location.pathname) || '';
        return /^\/chat(?:_full)?(?:\/|$)/.test(pathname);
    }

    function isUiWindow() {
        return window.__NEKO_MULTI_WINDOW__ !== true || isChatFollowerPath();
    }

    function currentCatgirl() {
        var cfg = window.lanlan_config;
        return cfg && typeof cfg.lanlan_name === 'string' && cfg.lanlan_name ? cfg.lanlan_name : 'neko';
    }

    function render() {
        if (typeof I.renderWindow === 'function') I.renderWindow();
    }

    function toast(message) {
        if (message && typeof window.showStatusToast === 'function') window.showStatusToast(message, 3500);
    }

    async function mutationHeaders() {
        try {
            var sec = window.nekoLocalMutationSecurity;
            if (sec && typeof sec.getMutationHeaders === 'function') return (await sec.getMutationHeaders()) || {};
        } catch (_) { /* the server then answers 403 and the retry below refreshes the token */ }
        return {};
    }

    function errorText(payload, status) {
        var p = payload && typeof payload === 'object' ? payload : {};
        var d = p.detail && typeof p.detail === 'object' ? p.detail : {};
        var msg = d.message || p.message || p.error || (typeof p.detail === 'string' ? p.detail : '');
        return typeof msg === 'string' && msg ? msg.slice(0, 300) : t('chat.taskPromptFailed', 'JARVIS could not take the answer (HTTP {{status}}).', { status: status });
    }

    async function callAction(action, args, retried) {
        var headers = Object.assign({ 'Content-Type': 'application/json' }, await mutationHeaders());
        var response = await fetch('/api/plugin-cards/' + PLUGIN + '/action/' + action, {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ card_id: CARD_ID, target_lanlan: currentCatgirl(), args: args || {},
                                   locale: (document.documentElement && document.documentElement.lang) || 'en' })
        });
        var payload = {};
        try { payload = await response.json(); } catch (_) { payload = {}; }
        if (!retried && response.status === 403 && payload && payload.error_code === 'csrf_validation_failed') {
            var sec = window.nekoLocalMutationSecurity;
            if (sec && typeof sec.refreshToken === 'function') {
                await sec.refreshToken();
                return callAction(action, args, true);
            }
        }
        if (!response.ok) throw new Error(errorText(payload, response.status));
        return payload && typeof payload.result === 'object' && payload.result ? payload.result : {};
    }

    function schedule(ms) {
        if (state.timer) window.clearTimeout(state.timer);
        state.timer = window.setTimeout(poll, ms);
    }

    async function poll() {
        state.timer = null;
        if (document.hidden) { schedule(POLL_MS); return; }
        try {
            var res = await callAction('pending_inputs', {});
            state.requests = Array.isArray(res.requests) ? res.requests : [];
            state.offline = false;
            state.failures = 0;
            var open = {};
            state.requests.forEach(function (r) { if (r && r.request) open[r.request.id] = true; });
            Object.keys(state.errors).forEach(function (id) { if (!open[id]) delete state.errors[id]; });
            Object.keys(state.collapsed).forEach(function (id) { if (!open[id]) delete state.collapsed[id]; });
        } catch (_) {
            // keep the last question visible but say it may be stale; back off while the backend is away
            state.offline = true;
            state.failures += 1;
        }
        render();
        schedule(state.offline ? Math.min(POLL_MS * Math.pow(2, state.failures), MAX_BACKOFF_MS) : POLL_MS);
    }

    function toPrompt(row, count) {
        var req = row.request || {};
        var id = String(req.id || '');
        var status = state.submitting === id ? 'submitting' : state.errors[id] ? 'error' : state.offline ? 'offline' : 'open';
        return {
            requestId: id,
            runId: String(row.run_id || ''),
            title: typeof req.title === 'string' && req.title ? req.title : undefined,
            prompt: String(req.prompt || ''),
            task: typeof row.task === 'string' && row.task ? row.task : undefined,
            fields: (Array.isArray(req.fields) ? req.fields : []).map(function (f) {
                return {
                    name: String(f.name || ''),
                    kind: f.kind,
                    label: String(f.label || f.name || ''),
                    required: f.required !== false,
                    help: typeof f.help === 'string' && f.help ? f.help : undefined,
                    maxLen: typeof f.max_len === 'number' ? f.max_len : undefined,
                    options: Array.isArray(f.options) ? f.options.map(function (o) {
                        return { value: String(o.value), label: String(o.label || o.value) };
                    }) : undefined
                };
            }),
            status: status,
            error: state.errors[id] || undefined,
            collapsed: !!state.collapsed[id],
            count: count
        };
    }

    I.getJarvisTaskPrompt = function getJarvisTaskPrompt() {
        if (!isUiWindow() || !state.requests.length) return null;
        var row = state.requests[0];
        if (!row || !row.request || !row.request.id || !row.run_id) return null;
        return toPrompt(row, state.requests.length);
    };

    function findRow(requestId) {
        for (var i = 0; i < state.requests.length; i++) {
            var r = state.requests[i];
            if (r && r.request && r.request.id === requestId) return r;
        }
        return null;
    }

    async function send(action, requestId, extra) {
        var row = findRow(requestId);
        if (!row || state.submitting) return;
        state.submitting = requestId;
        delete state.errors[requestId];
        render();
        try {
            var result = await callAction(action, Object.assign({ run_id: row.run_id, request_id: requestId }, extra || {}));
            state.requests = state.requests.filter(function (r) { return !(r.request && r.request.id === requestId); });
            toast(result.message || (action === 'cancel_input'
                ? t('chat.taskPromptCancelled', 'Question cancelled.')
                : t('chat.taskPromptAnswered', 'Answer sent; the task is resuming.')));
        } catch (err) {
            state.errors[requestId] = err && err.message ? String(err.message).slice(0, 300) : t('chat.taskPromptFailed', 'JARVIS could not take the answer.');
        } finally {
            state.submitting = null;
            render();
            schedule(800);       // the task resumed (or the question was refused): read the real state back soon
        }
    }

    I.handleTaskPromptSubmit = function handleTaskPromptSubmit(requestId, values) {
        // values may hold a secret: it goes into this one request body and nowhere else
        return send('answer_input', requestId, { values: values && typeof values === 'object' ? values : {} });
    };

    I.handleTaskPromptCancel = function handleTaskPromptCancel(requestId) {
        return send('cancel_input', requestId);
    };

    I.handleTaskPromptCollapse = function handleTaskPromptCollapse(requestId, collapsed) {
        if (collapsed) state.collapsed[requestId] = true;
        else delete state.collapsed[requestId];
        render();
    };

    function start() {
        if (state.started || !isUiWindow()) return;
        state.started = true;
        poll();
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden) schedule(0);
        });
    }

    I.__jarvisTaskInputTestHooks = { state: state, poll: poll, toPrompt: toPrompt, send: send };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();

(function () {
  'use strict';

  var SESSION_STORAGE_KEY = 'morpheus-chat-session-id';
  var MAX_MESSAGE_LENGTH = 999; // spec AC CHAT-04: 1000+ chars is rejected inline

  var fab = document.getElementById('chatWidgetFab');
  var panel = document.getElementById('chatWidgetPanel');
  var closeBtn = document.getElementById('chatWidgetClose');
  var messagesEl = document.getElementById('chatWidgetMessages');
  var rateLimitEl = document.getElementById('chatWidgetRateLimit');
  var offlineEl = document.getElementById('chatWidgetOffline');
  var validationEl = document.getElementById('chatWidgetValidation');
  var whatsappEl = document.getElementById('chatWidgetWhatsapp');
  var formEl = document.getElementById('chatWidgetForm');
  var inputEl = document.getElementById('chatWidgetInput');
  var sendBtn = document.getElementById('chatWidgetSend');

  if (!fab || !panel || !formEl || !inputEl || !messagesEl) return;

  function apiBaseUrl() {
    return (window.MORPHEUS_CONFIG && window.MORPHEUS_CONFIG.apiBaseUrl) || 'http://localhost:8000';
  }

  function t(key, fallback) {
    if (window.MorpheusI18n && typeof window.MorpheusI18n.t === 'function') {
      var value = window.MorpheusI18n.t(key);
      if (value) return value;
    }
    return fallback;
  }

  /* ---------- Session id (client-generated, sessionStorage-scoped) ---------- */
  function getSessionId() {
    try { return sessionStorage.getItem(SESSION_STORAGE_KEY); } catch (e) { return null; }
  }

  function createSessionId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      return window.crypto.randomUUID();
    }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function getOrCreateSessionId() {
    var existing = getSessionId();
    if (existing) return existing;
    var id = createSessionId();
    try { sessionStorage.setItem(SESSION_STORAGE_KEY, id); } catch (e) {}
    return id;
  }

  /* ---------- Rendering ---------- */
  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  // AC CHAT-12: always textContent, never innerHTML/template-string HTML.
  function appendMessage(role, text) {
    var bubble = document.createElement('p');
    bubble.className = 'chat-widget__message ' +
      (role === 'user' ? 'chat-widget__message--user' : 'chat-widget__message--bot');
    bubble.textContent = text;
    messagesEl.appendChild(bubble);
    scrollToBottom();
  }

  function hideStatus() {
    if (rateLimitEl) rateLimitEl.hidden = true;
    if (offlineEl) offlineEl.hidden = true;
    if (validationEl) validationEl.hidden = true;
  }

  function showStatus(el) {
    hideStatus();
    if (el) el.hidden = false;
  }

  function setBusy(isBusy) {
    inputEl.disabled = isBusy;
    if (sendBtn) sendBtn.disabled = isBusy;
  }

  function showWhatsapp(url) {
    if (!whatsappEl || !url) return;
    whatsappEl.href = url;
    whatsappEl.hidden = false;
  }

  /* ---------- Panel open/close ---------- */
  function openPanel() {
    panel.hidden = false;
    fab.setAttribute('aria-expanded', 'true');
    fab.setAttribute('aria-label', 'Fechar chat');
    inputEl.focus();
    scrollToBottom();
  }

  function closePanel() {
    panel.hidden = true;
    fab.setAttribute('aria-expanded', 'false');
    fab.setAttribute('aria-label', 'Abrir chat');
  }

  fab.addEventListener('click', function () {
    if (panel.hidden) { openPanel(); } else { closePanel(); }
  });
  if (closeBtn) {
    closeBtn.addEventListener('click', closePanel);
  }

  /* ---------- History restore (P3 / OBS-01) ---------- */
  function restoreHistory(sessionId) {
    fetch(apiBaseUrl() + '/api/chat/' + encodeURIComponent(sessionId) + '/history')
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        (data.messages || []).forEach(function (message) {
          appendMessage(message.role, message.content);
        });
      })
      .catch(function () {
        // Best-effort restore: if it fails, the visitor still gets a fresh,
        // usable widget instead of a broken one.
      });
  }

  /* ---------- Sending a message ---------- */
  function sendMessage(text) {
    var sessionId = getOrCreateSessionId();
    appendMessage('user', text);
    setBusy(true); // AC CHAT-11: preserve ordering while a request is in flight

    fetch(apiBaseUrl() + '/api/chat/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: text })
    })
      .then(function (res) {
        if (res.status === 429) {
          showStatus(rateLimitEl); // distinct rate-limited state
          return null;
        }
        if (!res.ok) throw new Error('HTTP ' + res.status);
        return res.json();
      })
      .then(function (data) {
        if (!data) return;
        appendMessage('assistant', data.reply);
        if (data.lead_captured) {
          showWhatsapp(data.whatsapp_url);
        }
      })
      .catch(function () {
        showStatus(offlineEl); // distinct offline/fallback state
      })
      .then(function () {
        setBusy(false);
        inputEl.focus();
      });
  }

  formEl.addEventListener('submit', function (event) {
    event.preventDefault();
    var text = inputEl.value.trim();

    if (!text) return; // AC CHAT-03: block empty send, never call the backend

    if (text.length > MAX_MESSAGE_LENGTH) {
      showStatus(validationEl); // AC CHAT-04: inline error, never call the backend
      return;
    }

    hideStatus();
    inputEl.value = '';
    sendMessage(text);
  });

  /* ---------- Keep the placeholder translated across the PT/EN toggle ---------- */
  function refreshPlaceholder() {
    inputEl.placeholder = t('chatWidget.placeholder', inputEl.placeholder);
  }
  refreshPlaceholder();
  if ('MutationObserver' in window) {
    new MutationObserver(refreshPlaceholder).observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['lang']
    });
  }

  /* ---------- Init: restore a prior conversation on reload (P3) ---------- */
  var existingSessionId = getSessionId();
  if (existingSessionId) {
    restoreHistory(existingSessionId);
  }
})();

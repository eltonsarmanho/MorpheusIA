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

  /* ---------- Markdown leve ---------- */
  /* O modelo responde em markdown (`**negrito**`, `*itálico*`, listas). Em vez
     de mostrar os asteriscos crus, formatamos o texto — mas SEMPRE montando nós
     do DOM, nunca innerHTML (AC CHAT-12): o conteúdo vem de um LLM e não pode
     virar HTML executável. */

  // `**x**` e `__x__` são negrito; `*x*` e `_x_` itálico; `` `x` `` código.
  var INLINE_RE = /(\*\*|__)(?=\S)([\s\S]*?\S)\1|(\*|_)(?=\S)([\s\S]*?\S)\3|`([^`\n]+)`/;

  // `_` dentro de palavra (snake_case, nomes de arquivo) não é ênfase.
  function isWordChar(ch) {
    return !!ch && /[\wÀ-ɏ]/.test(ch);
  }

  function renderInline(text, parent) {
    var rest = text;
    var guard = 0;
    while (rest && guard++ < 500) {
      var m = INLINE_RE.exec(rest);
      if (!m) break;

      var marker = m[1] || m[3];
      var content = m[1] ? m[2] : (m[3] ? m[4] : m[5]);
      var start = m.index;
      var end = start + m[0].length;

      // Descarta um `_`/`__` colado em palavra e segue a busca depois dele.
      if (marker && marker.charAt(0) === '_' &&
          (isWordChar(rest.charAt(start - 1)) || isWordChar(rest.charAt(end)))) {
        parent.appendChild(document.createTextNode(rest.slice(0, end)));
        rest = rest.slice(end);
        continue;
      }

      if (start > 0) {
        parent.appendChild(document.createTextNode(rest.slice(0, start)));
      }

      var tag = 'code';
      if (marker === '**' || marker === '__') tag = 'strong';
      else if (marker === '*' || marker === '_') tag = 'em';

      var el = document.createElement(tag);
      if (tag === 'code') {
        el.textContent = content;
      } else {
        renderInline(content, el); // permite *itálico dentro de **negrito**
      }
      parent.appendChild(el);

      rest = rest.slice(end);
    }
    if (rest) parent.appendChild(document.createTextNode(rest));
  }

  var BULLET_RE = /^\s*[-*+]\s+(.*)$/;
  var ORDERED_RE = /^\s*\d+[.)]\s+(.*)$/;

  // Agrupa as linhas em parágrafos e listas antes de formatar cada uma.
  function renderMarkdown(text, parent) {
    var lines = String(text == null ? '' : text).split(/\r?\n/);
    var list = null;      // <ul>/<ol> em construção
    var paragraph = null; // <p> em construção

    function closeBlocks() {
      list = null;
      paragraph = null;
    }

    lines.forEach(function (line) {
      if (!line.trim()) { closeBlocks(); return; }

      var bullet = BULLET_RE.exec(line);
      var ordered = bullet ? null : ORDERED_RE.exec(line);

      if (bullet || ordered) {
        var wanted = bullet ? 'UL' : 'OL';
        if (!list || list.tagName !== wanted) {
          list = document.createElement(bullet ? 'ul' : 'ol');
          list.className = 'chat-widget__list';
          parent.appendChild(list);
        }
        paragraph = null;
        var li = document.createElement('li');
        renderInline((bullet || ordered)[1], li);
        list.appendChild(li);
        return;
      }

      list = null;
      if (!paragraph) {
        paragraph = document.createElement('p');
        paragraph.className = 'chat-widget__paragraph';
        parent.appendChild(paragraph);
      } else {
        // Quebra simples dentro do mesmo parágrafo.
        paragraph.appendChild(document.createElement('br'));
      }
      renderInline(line, paragraph);
    });
  }

  /* ---------- Rendering ---------- */
  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  // AC CHAT-12: nada de innerHTML — o texto vira nós do DOM.
  function appendMessage(role, text) {
    var isUser = role === 'user';
    var bubble = document.createElement('div');
    bubble.className = 'chat-widget__message ' +
      (isUser ? 'chat-widget__message--user' : 'chat-widget__message--bot');

    if (isUser) {
      bubble.textContent = text; // o que o visitante digitou vai literal
    } else {
      renderMarkdown(text, bubble);
    }

    messagesEl.appendChild(bubble);
    scrollToBottom();
    return bubble;
  }

  /* ---------- Indicador de "digitando" ---------- */
  /* A MariTalk leva alguns segundos; sem retorno visual o chat parece travado. */
  var typingEl = null;

  function showTyping() {
    if (typingEl) return;
    typingEl = document.createElement('div');
    typingEl.className = 'chat-widget__message chat-widget__message--bot chat-widget__typing';
    typingEl.setAttribute('aria-label', t('chatWidget.typing', 'Assistente digitando'));
    for (var i = 0; i < 3; i++) {
      typingEl.appendChild(document.createElement('span'));
    }
    messagesEl.appendChild(typingEl);
    scrollToBottom();
  }

  function hideTyping() {
    if (typingEl && typingEl.parentNode) typingEl.parentNode.removeChild(typingEl);
    typingEl = null;
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
    showTyping();

    fetch(apiBaseUrl() + '/api/chat/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, message: text })
    })
      .then(function (res) {
        hideTyping();
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
        // AC CHAT-09: the backend also supplies whatsapp_url on the LLM-outage
        // fallback (lead_captured=false), so key the button on the URL itself.
        if (data.whatsapp_url) {
          showWhatsapp(data.whatsapp_url);
        }
      })
      .catch(function () {
        hideTyping();
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

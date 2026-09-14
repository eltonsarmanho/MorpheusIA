(function () {
  'use strict';

  var STORAGE_KEY = 'morpheus-lang';
  var DEFAULT_LANG = 'pt';
  var SUPPORTED_LANGS = ['pt', 'en'];

  var currentLang = DEFAULT_LANG;
  var translations = {};

  function detectInitialLang() {
    var saved = null;
    try { saved = localStorage.getItem(STORAGE_KEY); } catch (e) {}
    if (saved && SUPPORTED_LANGS.indexOf(saved) !== -1) return saved;
    var browserLang = (navigator.language || '').toLowerCase();
    if (browserLang.indexOf('en') === 0) return 'en';
    return DEFAULT_LANG;
  }

  function loadTranslations(lang) {
    var url = 'data/content-' + lang + '.json';
    return fetch(url)
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      })
      .catch(function (err) {
        if (lang !== DEFAULT_LANG) {
          console.warn('[i18n] Falha ao carregar "' + lang + '", usando fallback.');
          return loadTranslations(DEFAULT_LANG);
        }
        console.warn('[i18n] Conteúdo indisponível, usando HTML padrão.');
        return null;
      });
  }

  function getByPath(obj, path) {
    var parts = path.split('.');
    var current = obj;
    for (var i = 0; i < parts.length; i++) {
      if (current === null || current === undefined) return undefined;
      current = current[parts[i]];
    }
    return current;
  }

  function applyTranslations() {
    var elements = document.querySelectorAll('[data-i18n]');
    var missingKeys = [];

    elements.forEach(function (el) {
      var key = el.getAttribute('data-i18n');
      var value = getByPath(translations, key);
      if (value !== undefined && value !== null) {
        if (el.hasAttribute('data-i18n-html')) {
          el.innerHTML = value;
        } else {
          el.textContent = value;
        }
      } else {
        missingKeys.push(key);
      }
    });

    var titleEl = document.querySelector('title[data-i18n]');
    if (titleEl) {
      var titleValue = getByPath(translations, titleEl.getAttribute('data-i18n'));
      if (titleValue) document.title = titleValue;
    }

    var metaDesc = document.querySelector('meta[name="description"][data-i18n]');
    if (metaDesc) {
      var descValue = getByPath(translations, metaDesc.getAttribute('data-i18n'));
      if (descValue) metaDesc.setAttribute('content', descValue);
    }

    document.documentElement.lang = currentLang === 'pt' ? 'pt-BR' : 'en';

    if (missingKeys.length > 0 && console.warn) {
      console.warn('[i18n] Chaves sem tradução para "' + currentLang + '":', missingKeys);
    }
  }

  function updateLangButtons() {
    document.querySelectorAll('.lang-btn').forEach(function (btn) {
      var isActive = btn.getAttribute('data-lang') === currentLang;
      btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
  }

  function setLanguage(lang) {
    if (SUPPORTED_LANGS.indexOf(lang) === -1) lang = DEFAULT_LANG;
    currentLang = lang;
    try { localStorage.setItem(STORAGE_KEY, lang); } catch (e) {}
    loadTranslations(lang).then(function (data) {
      if (data) { translations = data; applyTranslations(); }
      updateLangButtons();
    });
  }

  function init() {
    currentLang = detectInitialLang();
    loadTranslations(currentLang).then(function (data) {
      if (data) { translations = data; applyTranslations(); }
      updateLangButtons();
    });
    document.querySelectorAll('.lang-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        setLanguage(btn.getAttribute('data-lang'));
      });
    });
  }

  window.MorpheusI18n = {
    setLanguage: setLanguage,
    getCurrentLang: function () { return currentLang; },
    t: function (key) { return getByPath(translations, key); }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
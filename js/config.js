(function () {
  'use strict';

  // Chat widget backend base URL. chat-widget.js reads it and never hardcodes a
  // URL of its own.
  //
  // Em produção o backend fica atrás do mesmo nginx que serve este site
  // (ex.: https://srv1633081.hstgr.cloud/api/...), então a origem da própria
  // página já é a base correta. Em desenvolvimento local o site é servido por um
  // http.server estático numa porta diferente do backend, daí o fallback fixo.
  function defaultApiBaseUrl() {
    var host = window.location.hostname;
    var isLocal = host === 'localhost' || host === '127.0.0.1' || host === '';
    return isLocal ? 'http://localhost:8000' : window.location.origin;
  }

  window.MORPHEUS_CONFIG = window.MORPHEUS_CONFIG || {
    apiBaseUrl: defaultApiBaseUrl()
  };
})();

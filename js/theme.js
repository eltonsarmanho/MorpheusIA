(function () {
  'use strict';

  var toggle = document.getElementById('themeToggle');
  if (!toggle) return;

  var root = document.documentElement;
  var heroLogo = document.getElementById('heroLogo');

  function updateLogo(theme) {
    if (heroLogo) {
      heroLogo.src = theme === 'light' ? 'assets/logo-light.png' : 'assets/logo.png';
    }
  }

  var current = root.getAttribute('data-theme') || 'light';
  toggle.setAttribute('aria-pressed', current === 'light');
  updateLogo(current);

  toggle.addEventListener('click', function () {
    var now = root.getAttribute('data-theme') || 'light';
    var next = now === 'light' ? 'dark' : 'light';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('morpheus-theme', next); } catch (e) {}
    toggle.setAttribute('aria-pressed', next === 'light');
    updateLogo(next);
  });
})();
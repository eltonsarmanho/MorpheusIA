(function () {
  'use strict';

  /* ---------- Menu mobile ---------- */
  var navToggle = document.getElementById('navToggle');
  var navMenu = document.getElementById('navMenu');

  if (navToggle && navMenu) {
    navToggle.addEventListener('click', function () {
      var isOpen = navMenu.classList.contains('is-open');
      navToggle.setAttribute('aria-expanded', !isOpen);
      navToggle.setAttribute('aria-label', isOpen ? 'Abrir menu' : 'Fechar menu');
      navMenu.classList.toggle('is-open');
    });

    navMenu.querySelectorAll('a').forEach(function (link) {
      link.addEventListener('click', function () {
        navMenu.classList.remove('is-open');
        navToggle.setAttribute('aria-expanded', 'false');
        navToggle.setAttribute('aria-label', 'Abrir menu');
      });
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && navMenu.classList.contains('is-open')) {
        navMenu.classList.remove('is-open');
        navToggle.setAttribute('aria-expanded', 'false');
        navToggle.setAttribute('aria-label', 'Abrir menu');
        navToggle.focus();
      }
    });
  }

  /* ---------- Intersection Observer ---------- */
  if ('IntersectionObserver' in window) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-visible');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.15, rootMargin: '0px 0px -50px 0px' });

    var selectors = ['.value-card', '.solution-card', '.process-step', '.cost-card', '.about-content p'];
    selectors.forEach(function (selector) {
      document.querySelectorAll(selector).forEach(function (el, index) {
        el.classList.add('fade-in');
        el.style.transitionDelay = (index * 100) + 'ms';
        observer.observe(el);
      });
    });
  }

  /* ---------- Sombra no header ao rolar ---------- */
  var header = document.getElementById('header');
  if (header) {
    window.addEventListener('scroll', function () {
      if (window.pageYOffset > 10) {
        header.style.borderBottomColor = 'var(--color-border-hover)';
      } else {
        header.style.borderBottomColor = 'var(--color-border)';
      }
    }, { passive: true });
  }
})();
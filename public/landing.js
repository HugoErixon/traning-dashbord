/* Trainyze — landningssidans interaktion.
   Håller sig till progressiv förbättring: utan JS visas allt ändå,
   reveal-klasserna sätts direkt om IntersectionObserver saknas. */
(function () {
  'use strict';

  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ── Sticky nav: bakgrund först när sidan scrollats ── */
  var nav = document.getElementById('lp-nav');
  if (nav) {
    var syncNav = function () {
      nav.classList.toggle('is-scrolled', window.scrollY > 12);
    };
    syncNav();
    window.addEventListener('scroll', syncNav, { passive: true });
  }

  /* ── Räknare: 0 → målvärde när elementet blir synligt ── */
  function runCounter(el) {
    var target = parseFloat(el.getAttribute('data-count'));
    if (isNaN(target)) return;
    if (reduceMotion) { el.textContent = String(target); return; }

    var duration = 1400;
    var start = null;

    function frame(now) {
      if (start === null) start = now;
      var p = Math.min((now - start) / duration, 1);
      // easeOutExpo — snabb start, mjuk landning
      var eased = p === 1 ? 1 : 1 - Math.pow(2, -10 * p);
      el.textContent = String(Math.round(target * eased));
      if (p < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  /* ── CNS-ringen: rita upp bågen till rätt andel ── */
  function runRing(ring) {
    var pct = parseFloat(ring.getAttribute('data-ring'));
    var circle = ring.querySelector('.lp-ring-value');
    if (!circle || isNaN(pct)) return;

    var r = circle.r && circle.r.baseVal ? circle.r.baseVal.value : 41;
    var circumference = 2 * Math.PI * r;
    circle.style.strokeDasharray = String(circumference);
    circle.style.strokeDashoffset = String(circumference * (1 - pct / 100));
  }

  /* ── Reveal vid scroll ── */
  var revealables = Array.prototype.slice.call(document.querySelectorAll('.lp-reveal'));

  function activate(el) {
    el.classList.add('is-visible');
    Array.prototype.forEach.call(el.querySelectorAll('[data-count]'), runCounter);
    Array.prototype.forEach.call(el.querySelectorAll('[data-ring]'), runRing);
  }

  if (!('IntersectionObserver' in window) || reduceMotion) {
    revealables.forEach(activate);
    return;
  }

  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      activate(entry.target);
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.18, rootMargin: '0px 0px -60px 0px' });

  revealables.forEach(function (el, i) {
    // Liten trappa så kort i samma rad inte poppar in exakt samtidigt
    el.style.transitionDelay = Math.min(i % 4, 3) * 70 + 'ms';
    observer.observe(el);
  });
})();

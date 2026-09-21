/* DemoPort — 최소한의 동작 (prd.md 4.1, 4.6, 4.8)
   JavaScript 가 없어도 내용과 이메일 링크는 모두 동작한다. 여기서는 아래 네 가지만 더한다.
     1) 모바일 메뉴 접기/펼치기   2) 현재 섹션 강조   3) 이메일 주소 복사   4) 라이트/다크 테마 전환 */
(function () {
  'use strict';

  var root = document.documentElement;
  var THEME_KEY = 'demoport-theme';

  // ------------------------------------------------------------ 푸터 연도
  var year = document.getElementById('year');
  if (year) year.textContent = String(new Date().getFullYear());

  // ------------------------------------------------------------ 테마 전환 (FR-20)
  var themeBtn = document.getElementById('theme-toggle');
  var darkQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function currentTheme() {
    var forced = root.getAttribute('data-theme');
    if (forced === 'light' || forced === 'dark') return forced;
    return darkQuery && darkQuery.matches ? 'dark' : 'light';
  }

  function syncThemeButton() {
    if (themeBtn) themeBtn.setAttribute('aria-pressed', String(currentTheme() === 'dark'));
  }

  if (themeBtn) {
    themeBtn.addEventListener('click', function () {
      var next = currentTheme() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* 저장 실패해도 이번 방문에는 적용된다 */ }
      syncThemeButton();
    });
    // 직접 고르지 않은 상태에서 시스템 설정이 바뀌면 버튼 상태만 맞춘다
    if (darkQuery && darkQuery.addEventListener) darkQuery.addEventListener('change', syncThemeButton);
    syncThemeButton();
  }

  // ------------------------------------------------------------ 모바일 메뉴 (FR-2)
  var header = document.getElementById('site-header');
  var nav = document.getElementById('site-nav');
  var menuBtn = document.getElementById('menu-toggle');

  function setMenu(open, returnFocus) {
    if (!header || !menuBtn) return;
    header.classList.toggle('is-open', open);
    menuBtn.setAttribute('aria-expanded', String(open));
    if (open) {
      var first = nav && nav.querySelector('a');
      if (first) first.focus();          // 키보드 사용자가 바로 메뉴로 이동할 수 있게
    } else if (returnFocus) {
      menuBtn.focus();
    }
  }

  if (menuBtn && nav) {
    menuBtn.addEventListener('click', function () {
      setMenu(menuBtn.getAttribute('aria-expanded') !== 'true', false);
    });
    nav.addEventListener('click', function (e) {
      if (e.target.closest('a')) setMenu(false, false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && menuBtn.getAttribute('aria-expanded') === 'true') setMenu(false, true);
    });
    document.addEventListener('click', function (e) {
      if (menuBtn.getAttribute('aria-expanded') === 'true' && !header.contains(e.target)) setMenu(false, false);
    });
    // 화면이 넓어져 메뉴가 항상 보이는 상태가 되면 열림 표시를 정리한다
    window.addEventListener('resize', function () {
      if (window.innerWidth > 640 && menuBtn.getAttribute('aria-expanded') === 'true') setMenu(false, false);
    });
  }

  // ------------------------------------------------------------ 현재 섹션 강조 (FR-3)
  var links = nav ? Array.prototype.slice.call(nav.querySelectorAll('a[href^="#"]')) : [];
  var byId = {};
  links.forEach(function (a) { byId[a.getAttribute('href').slice(1)] = a; });

  function markCurrent(id) {
    links.forEach(function (a) {
      if (byId[id] === a) a.setAttribute('aria-current', 'location');
      else a.removeAttribute('aria-current');
    });
  }

  if ('IntersectionObserver' in window && links.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) markCurrent(entry.target.id);
      });
    }, { rootMargin: '-40% 0px -55% 0px', threshold: 0 });   // 화면 가운데 근처를 지나는 섹션을 현재 섹션으로 본다

    Object.keys(byId).forEach(function (id) {
      var section = document.getElementById(id);
      if (section) io.observe(section);
    });

    // 맨 위(히어로)에서는 강조를 지운다
    window.addEventListener('scroll', function () {
      if (window.scrollY < 80) markCurrent('');
    }, { passive: true });
  }

  // ------------------------------------------------------------ 이메일 주소 복사 (FR-17)
  var copyBtn = document.getElementById('copy-btn');
  var status = document.getElementById('copy-status');
  var statusTimer = null;

  function legacyCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;top:0;left:-9999px;';
    document.body.appendChild(ta);
    ta.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }

  function say(message) {
    if (!status) return;
    clearTimeout(statusTimer);
    status.textContent = '';                       // 같은 문구를 다시 읽어 주도록 한 번 비운다
    statusTimer = setTimeout(function () {
      status.textContent = message;
      statusTimer = setTimeout(function () { status.textContent = ''; }, 3000);
    }, 40);
  }

  if (copyBtn) {
    copyBtn.addEventListener('click', function () {
      var text = copyBtn.getAttribute('data-copy') || '';
      var done = function (ok) {
        copyBtn.focus();
        say(ok ? '복사되었습니다' : '복사하지 못했습니다. 주소를 직접 선택해 복사해 주세요.');
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(legacyCopy(text)); });
      } else {
        done(legacyCopy(text));
      }
    });
  }
})();

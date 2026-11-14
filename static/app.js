(() => {
  // Progressive navigation: Django remains responsible for validation and access.
  let pending = false, queued = null, obsolete = false;
  history.scrollRestoration = 'manual';
  const remember = () => history.replaceState({...history.state, scroll: [scrollX, scrollY]}, '', location.href);
  remember();
  let scrollTimer;
  addEventListener('scroll', () => {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(remember, 100);
  }, {passive: true});

  function eligible(url) {
    return url.origin === location.origin && !/\/(export|receipt|avatar|access|enter|logout|leave)(\/|$)/.test(url.pathname);
  }
  function notice(message) {
    let element = document.getElementById('request-notice');
    if (!element) {
      element = document.createElement('div');
      element.id = 'request-notice';
      element.setAttribute('role', 'alert');
      document.body.append(element);
    }
    element.textContent = message;
  }
  function swap(selector, doc) {
    const old = document.querySelector(selector), fresh = doc.querySelector(selector);
    if (old && fresh) old.replaceWith(document.importNode(fresh, true));
  }
  async function visit(url, {method = 'GET', body, restore, pop = false, source} = {}) {
    if (pending) { queued = [url, {method, body, restore, pop, source}]; obsolete = true; return; }
    pending = true;
    obsolete = false;
    clearTimeout(scrollTimer);
    if (!pop) remember();
    const position = restore || [scrollX, scrollY];
    const opened = [...document.querySelectorAll('details')].map((node, index) => node.open ? index : -1);
    const buttons = source ? [...source.querySelectorAll('button, input[type=submit]')] : [];
    const disabled = buttons.map(button => button.disabled);
    buttons.forEach(button => { button.disabled = true; });
    document.documentElement.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch(url, {
        method, body, credentials: 'same-origin', mode: 'same-origin',
        headers: {'X-Requested-With': 'XMLHttpRequest'}, cache: 'no-store',
      });
      if (!response.ok) {
        if (response.status === 403) throw new Error('Не удалось подтвердить доступ. Обновите страницу и повторите действие.');
        throw new Error('Не удалось выполнить запрос. Проверьте состояние записи перед повторной отправкой.');
      }
      if (obsolete) return;
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (!doc.querySelector('main#content')) {
        // Login/expired-link pages keep their normal navigation behavior.
        location.assign(response.url);
        return;
      }
      const destination = new URL(method === 'POST' && !response.redirected ? location.href : response.url);
      if (url.hash && destination.pathname === url.pathname) destination.hash = url.hash;
      if (!pop && destination.href !== location.href) history.pushState({scroll: position}, '', destination);
      document.title = doc.title;
      document.body.className = doc.body.className;
      swap('main#content', doc);
      swap('.sidebar', doc);
      swap('.topbar', doc);
      document.querySelectorAll('details').forEach((node, index) => { if (opened.includes(index)) node.open = true; });
      document.getElementById('request-notice')?.remove();
      // Explicit anchors (the lesson journal) are the only automatic scroll targets.
      requestAnimationFrame(() => {
        window.scrollTo({left: position[0], top: position[1], behavior: 'instant'});
        if (!pop && url.hash) document.getElementById(decodeURIComponent(url.hash.slice(1)))?.scrollIntoView();
        remember();
      });
    } catch (error) {
      if (!obsolete) notice(error.message || 'Связь с сервером прервалась. Проверьте результат перед повторной отправкой.');
    } finally {
      pending = false;
      document.documentElement.removeAttribute('aria-busy');
      buttons.forEach((button, index) => { button.disabled = disabled[index]; });
      if (queued) { const next = queued; queued = null; visit(...next); }
    }
  }
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download')) return;
    const url = new URL(link.href);
    if (!eligible(url) || (url.hash && url.pathname === location.pathname && url.search === location.search)) return;
    event.preventDefault();
    visit(url);
  });
  document.addEventListener('submit', event => {
    const form = event.target;
    const url = new URL(form.action || location.href);
    // Provider redirects and sign-out keep native browser navigation.
    if (!eligible(url) || form.target || event.submitter?.value === 'start') return;
    event.preventDefault();
    if (pending) return;
    const data = new FormData(form, event.submitter);
    const method = (form.method || 'GET').toUpperCase();
    if (method === 'GET') {
      url.search = new URLSearchParams(data).toString();
      visit(url, {source: form});
    } else {
      visit(url, {method, body: data, source: form});
    }
  });
  addEventListener('popstate', event => {
    visit(new URL(location.href), {pop: true, restore: event.state?.scroll || [0, 0]});
  });
  // Delegated input handling also works on forms loaded without a page reload.
  document.addEventListener('input', event => {
    const input = event.target;
    if (!input.matches('[data-phone]')) return;
    let digits = input.value.replace(/\D/g, '').slice(0, 11);
    if (!digits) { input.value = ''; return; }
    if (digits[0] === '7') digits = '8' + digits.slice(1);
    if (digits[0] !== '8') digits = ('8' + digits).slice(0, 11);
    input.value = digits[0] + (digits.length > 1 ? ' (' + digits.slice(1,4) : '') +
      (digits.length > 4 ? ') ' + digits.slice(4,7) : '') +
      (digits.length > 7 ? ' ' + digits.slice(7) : '');
  });
})();

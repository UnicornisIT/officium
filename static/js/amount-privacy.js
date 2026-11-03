(() => {
  const root = document.documentElement;
  const toggle = document.getElementById('amountPrivacyToggle');
  if (!toggle) return;
  // Keep one control and its state; place it in the page's existing action row.
  let slot = document.querySelector('[data-amount-privacy-slot]');
  if (!slot) {
    const main = document.querySelector('main');
    const heading = main.querySelector('h1');
    const header = heading?.closest('.page-header, .finance-header, .plan-header, .goals-header, .vehicle-detail-header, header');
    if (header) {
      slot = header.querySelector('.finance-actions, .plan-header__actions, .page-actions');
      if (!slot) {
        slot = document.createElement('div');
        slot.className = 'page-actions';
        Array.from(header.children).filter(node => node.matches('a, button')).forEach(node => slot.append(node));
        header.append(slot);
      }
      slot.prepend(toggle);
    } else if (heading) {
      slot = document.createElement('div');
      slot.className = 'amount-heading';
      heading.before(slot);
      slot.append(heading);
    } else {
      slot = document.createElement('div');
      slot.className = 'amount-privacy-corner';
      main.prepend(slot);
    }
  }
  if (!slot.contains(toggle)) slot.append(toggle);
  document.getElementById('amountPrivacySource')?.remove();
  const currency = /[-−+]?\d(?:[\d \u00a0\u202f.,]*\d)?[ \u00a0\u202f]*(?:₽|RUB|руб(?:лей|ля|ль|\.)?)(?![A-Za-zА-Яа-я])/g;
  const ignored = 'input,textarea,select,option,script,style,noscript,canvas,[data-private-money],[contenteditable="true"],[data-no-amount-privacy]';
  const attributes = ['title', 'aria-label', 'data-bs-title', 'data-bs-original-title'];
  const originals = new Map();
  const chartLabels = new Map();
  let hidden = root.classList.contains('amounts-hidden');

  const replaceCurrency = value => value.replace(currency, '••••');
  function maskText(node) {
    if (!node.parentElement || node.parentElement.closest(ignored)) return;
    const text = node.nodeValue;
    const matches = [...text.matchAll(currency)];
    if (!matches.length) return;
    const fragment = document.createDocumentFragment();
    let index = 0;
    matches.forEach(match => {
      fragment.append(text.slice(index, match.index));
      const span = document.createElement('span');
      span.setAttribute('data-private-money', '');
      const value = document.createElement('span');
      value.className = 'money-value';
      value.textContent = match[0];
      const mask = document.createElement('span');
      mask.className = 'money-mask';
      mask.setAttribute('aria-label', 'Сумма скрыта');
      mask.textContent = '••••';
      span.append(value, mask);
      fragment.append(span);
      index = match.index + match[0].length;
    });
    fragment.append(text.slice(index));
    node.replaceWith(fragment);
  }

  function maskAttributes(element) {
    if (element.closest(ignored)) return;
    attributes.forEach(name => {
      const value = element.getAttribute(name);
      if (value === null || replaceCurrency(value) === value) {
        const saved = originals.get(element)?.get(name);
        // Bootstrap moves title into data-bs-original-title during lazy init.
        // Preserve the unmasked source even if it initializes while hidden.
        if (name === 'title' && saved && value === null &&
            element.getAttribute('data-bs-original-title') === replaceCurrency(saved)) {
          originals.get(element).set('data-bs-original-title', saved);
        }
        if (saved && value !== replaceCurrency(saved)) originals.get(element).delete(name);
        return;
      }
      if (!originals.has(element)) originals.set(element, new Map());
      originals.get(element).set(name, value);
      if (hidden) element.setAttribute(name, replaceCurrency(value));
    });
  }

  function chart(canvas) {
    if (!chartLabels.has(canvas)) {
      chartLabels.set(canvas, canvas.getAttribute('aria-hidden'));
      const message = document.createElement('p');
      message.className = 'money-chart-message';
      message.textContent = 'Денежный график скрыт. Нажмите «Показать суммы», чтобы открыть его.';
      canvas.before(message);
    }
    if (hidden) canvas.setAttribute('aria-hidden', 'true');
  }

  function scan(node) {
    if (node.nodeType === Node.TEXT_NODE) return maskText(node);
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (node.tagName === 'CANVAS') return chart(node);
    if (node.closest(ignored)) return;
    maskAttributes(node);
    if (node.tagName === 'CANVAS') chart(node);
    node.querySelectorAll('*').forEach(element => {
      if (element.tagName === 'CANVAS') chart(element);
      else maskAttributes(element);
    });
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
    const texts = [];
    while (walker.nextNode()) texts.push(walker.currentNode);
    texts.forEach(maskText);
  }

  function sync() {
    root.classList.toggle('amounts-hidden', hidden);
    toggle.setAttribute('aria-pressed', String(hidden));
    toggle.querySelector('span').textContent = hidden ? 'Показать суммы' : 'Скрыть суммы';
    toggle.title = hidden ? 'Показать суммы' : 'Скрыть суммы';
    toggle.querySelector('i').className = hidden ? 'bi bi-eye' : 'bi bi-eye-slash';
    originals.forEach((values, element) => {
      if (!element.isConnected) return originals.delete(element);
      values.forEach((value, name) => element.setAttribute(name, hidden ? replaceCurrency(value) : value));
      const title = values.get('data-bs-original-title') || values.get('data-bs-title') || values.get('title');
      const tooltip = window.bootstrap?.Tooltip?.getInstance(element);
      if (title && tooltip) tooltip.setContent({ '.tooltip-inner': hidden ? replaceCurrency(title) : title });
    });
    chartLabels.forEach((value, canvas) => {
      if (hidden) canvas.setAttribute('aria-hidden', 'true');
      else if (value === null) canvas.removeAttribute('aria-hidden');
      else canvas.setAttribute('aria-hidden', value);
    });
  }
  scan(document.body);
  sync();
  root.classList.remove('amounts-pending');
  const observer = new MutationObserver(records => {
    records.forEach(record => {
      if (record.type === 'attributes') maskAttributes(record.target);
      else if (record.type === 'characterData') maskText(record.target);
      else record.addedNodes.forEach(scan);
    });
  });
  observer.observe(document.body, { subtree: true, childList: true, characterData: true,
    attributes: true, attributeFilter: attributes });
  toggle.addEventListener('click', () => {
    hidden = !hidden;
    sync();
  });
  // Back/forward cache must also respect "hide on opening".
  window.addEventListener('pageshow', event => {
    if (event.persisted && root.dataset.amountsDefaultHidden === 'true') {
      hidden = true;
      sync();
    }
  });
  window.addEventListener('pagehide', () => {
    if (root.dataset.amountsDefaultHidden === 'true') {
      hidden = true;
      sync();
    }
  });
})();

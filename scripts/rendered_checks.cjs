'use strict';

// Narrow rendered-DOM evidence for explicit same-document skip links. This does
// not operate the keyboard or claim that focus transfer has been verified.
async function inspectSkipLinks(page) {
  return page.evaluate(() => {
    const skipText = /(?:\bskip\s+(?:to\s+(?:the\s+)?)?(?:main(?:\s+content)?|content|navigation)\b|\bjump\s+to\s+(?:the\s+)?(?:main(?:\s+content)?|content|navigation)\b|(?:דלג[וי]?|דילוג)\s+(?:(?:אל|ישירות)\s+)?(?:ל?ה?תוכן|ל?ה?ניווט))/i;
    const current = new URL(location.href);
    const selectorFor = (element) => {
      if (element.id && document.querySelectorAll('#' + CSS.escape(element.id)).length === 1) {
        return '#' + CSS.escape(element.id);
      }
      const parts = [];
      for (let node = element; node && node.nodeType === Node.ELEMENT_NODE; node = node.parentElement) {
        const siblings = node.parentElement
          ? [...node.parentElement.children].filter((child) => child.localName === node.localName) : [];
        parts.unshift(node.localName + (siblings.length > 1 ? ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')' : ''));
      }
      return parts.join(' > ');
    };
    const links = [];
    for (const link of document.querySelectorAll('a[href]')) {
      const text = (link.innerText || '') + ' ' + (link.getAttribute('aria-label') || '');
      if (!skipText.test(text) || link.closest('[hidden], [inert], [aria-hidden="true"]')) continue;
      const style = getComputedStyle(link);
      if (style.display === 'none' || style.visibility === 'hidden' || !link.getClientRects().length) continue;
      const href = link.getAttribute('href');
      let target;
      try { target = new URL(href, document.baseURI); } catch { continue; }
      if (target.origin !== current.origin || target.pathname !== current.pathname
          || target.search !== current.search || !target.hash || target.hash === '#') continue;
      let fragment;
      try { fragment = decodeURIComponent(target.hash.slice(1)); } catch { continue; }
      const namedAnchor = [...document.getElementsByName(fragment)].some((node) => node.localName === 'a');
      links.push({selector: selectorFor(link), href, fragment,
        target_exists: Boolean(document.getElementById(fragment) || namedAnchor),
        html: link.outerHTML.slice(0, 2000)});
    }
    return links;
  });
}

module.exports = { inspectSkipLinks };

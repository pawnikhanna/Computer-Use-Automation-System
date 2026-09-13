EXTRACT_JS = r"""
() => {
  function nearestRowLabel(el) {
    // Legacy layout convention here: <tr><td>Label:</td><td><input></td></tr>
    let row = el.closest('tr');
    if (!row) return null;
    let cells = Array.from(row.querySelectorAll('td'));
    let myCellIndex = cells.findIndex(td => td.contains(el));
    if (myCellIndex > 0) {
      return cells[myCellIndex - 1].innerText.trim();
    }
    return null;
  }

  function cssPath(el) {
    // Structural fallback locator -- last resort, most brittle.
    if (!(el instanceof Element)) return '';
    const path = [];
    while (el && el.nodeType === Node.ELEMENT_NODE && el.tagName !== 'BODY') {
      let selector = el.tagName.toLowerCase();
      let parent = el.parentElement;
      if (parent) {
        let sameTag = Array.from(parent.children).filter(c => c.tagName === el.tagName);
        if (sameTag.length > 1) {
          let idx = sameTag.indexOf(el) + 1;
          selector += `:nth-of-type(${idx})`;
        }
      }
      path.unshift(selector);
      el = parent;
    }
    return path.join(' > ');
  }

  const nodes = Array.from(document.querySelectorAll('input, select, button, a[href]'));
  const elements = [];
  let nextId = 0;
  nodes.forEach((el) => {
    const idx = nextId++;
    if (el.type === 'hidden') return;
    const tag = el.tagName.toLowerCase();
    let kind, text = null, label = null;

    if (tag === 'input') {
      if (el.type === 'submit' || el.type === 'button') {
        kind = 'button';
        text = el.value || el.innerText || '';
      } else {
        kind = 'input';
        label = nearestRowLabel(el);
      }
    } else if (tag === 'select') {
      kind = 'select';
      label = nearestRowLabel(el);
    } else if (tag === 'button') {
      kind = 'button';
      text = el.innerText.trim();
    } else if (tag === 'a') {
      kind = 'link';
      text = el.innerText.trim();
    }

    // Tag with a temporary attribute so the Python side can act on this exact
    // element after this observation returns (plain data, no live handles).
    // This attribute is NEVER persisted into the artifact -- it only exists
    // to let discovery.py execute the action the model chose this turn.
    el.setAttribute('data-cua-tmp-id', String(idx));

    elements.push({
      element_id: idx,
      kind: kind,
      label_text: label,
      text: text,
      name_attr: el.name || null,
      current_value: (tag === 'input') ? el.value : null,
      css_path: cssPath(el),
    });
  });

  // Labeled read-only data fields, e.g. <tr><td>Savings Balance</td><td>$4231.50</td></tr>
  // These are NOT interactive but the model needs to be able to "extract" them
  // by reference (kind: "field") rather than only reading loose body text.
  const rows = Array.from(document.querySelectorAll('tr'));
  rows.forEach((row) => {
    const cells = Array.from(row.querySelectorAll('td'));
    if (cells.length === 2 && cells[0].innerText.trim().length <= 40 && !cells[0].querySelector('input,select,button')) {
      const idx = nextId++;
      elements.push({
        element_id: idx,
        kind: 'field',
        label_text: cells[0].innerText.trim(),
        text: cells[1].innerText.trim(),
        name_attr: null,
        current_value: null,
        css_path: cssPath(cells[1]),
      });
    }
  });

  // Grab visible body text too, so the model can read balances / error
  // messages / business-outcome text, not just find controls.
  const bodyText = document.body.innerText.replace(/\s+/g, ' ').trim().slice(0, 2000);

  return {
    url: window.location.pathname + window.location.search,
    body_text: bodyText,
    elements: elements,
  };
}
"""


def extract_observation(page) -> dict:
    """Run the extraction JS against a live Playwright page and return the
    structured observation dict (url, visible text, interactive elements)."""
    return page.evaluate(EXTRACT_JS)
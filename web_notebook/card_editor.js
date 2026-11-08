/* Rich-text field for the Card Creators (notebook and PDF tab).
 *
 * A contenteditable that writes plain Anki HTML only (headings, bold/italic/underline, nested lists,
 * pictures, formulas as \( … \)), so the cards look the same in Anki on an iPad without this add-on.
 * The element behaves like a textarea for the page code: `.value` reads/writes the HTML, `.disabled`
 * locks it, and an `input` event fires on every change.
 *
 * Needs card_images.js (SynapseCardImages). Inlined by notebook_sidebar.py (setHtml has no base URL);
 * a plain <script src> loads it when a page is opened as a file. KaTeX comes from the page.
 */
(function () {
  if (window.SynapseCardEditor) return;
  var Images = window.SynapseCardImages;
  var LATEX_LIMIT = 2000;
  var ALLOWED = { DIV: 'div', P: 'p', BR: 'br', B: 'b', STRONG: 'b', I: 'i', EM: 'i', U: 'u', S: 's', STRIKE: 's', DEL: 's',
    SUB: 'sub', SUP: 'sup', UL: 'ul', OL: 'ol', LI: 'li', H1: 'h1', H2: 'h2', H3: 'h3', H4: 'h3', H5: 'h3', H6: 'h3' };
  var DROPPED = { SCRIPT: 1, STYLE: 1, TEMPLATE: 1, IFRAME: 1, OBJECT: 1, EMBED: 1, SVG: 1, MATH: 1, NOSCRIPT: 1, HEAD: 1, TITLE: 1,
    TEXTAREA: 1, SELECT: 1, BUTTON: 1, FORM: 1, CANVAS: 1, AUDIO: 1, VIDEO: 1, LINK: 1, META: 1 };
  var OL_TYPES = ['1', 'a', 'A', 'i', 'I'];
  var NESTED_TYPE = ['1', 'a', 'i'];
  var MATH_TEXT = /(^|[^\\$])\$(?!\s)([^$\n]*[^$\s\\])\$(?!\d)/;


  var CSS = '.ce-field{overflow:auto;word-break:break-word;text-align:left;cursor:text}' +
    '.ce-field.ce-empty::before{content:attr(placeholder);color:var(--text-gray,#888);pointer-events:none}' +
    '.ce-field.disabled{opacity:.6}' +
    '.ce-field h1{font-size:1.5em;margin:.25em 0}.ce-field h2{font-size:1.25em;margin:.25em 0}.ce-field h3{font-size:1.1em;margin:.25em 0}' +
    '.ce-field ul,.ce-field ol{margin:.15em 0;padding-left:1.5em}' +
    '.ce-field img{max-width:100%;height:auto;vertical-align:middle}' +
    '.ce-math{display:inline-block;cursor:pointer;padding:0 1px}.ce-math.invalid{color:#b75e5e}' +
    '.ce-toolbar{display:flex;flex-wrap:wrap;gap:2px;margin:0 0 4px}.ce-toolbar.disabled{opacity:.5;pointer-events:none}' +
    '.ce-btn{min-width:24px;height:24px;padding:0 5px;border:1px solid var(--border,#ccc);border-radius:5px;background:var(--bg,#fff);color:var(--text,#222);font:600 11px/1 inherit;cursor:pointer}' +
    '.ce-btn:hover{border-color:var(--text-gray,#888)}.ce-b{font-weight:800}.ce-i{font-style:italic}.ce-u{text-decoration:underline}';
  function injectStyle() {
    if (document.getElementById('ce-style')) return;
    var style = document.createElement('style'); style.id = 'ce-style'; style.textContent = CSS;
    (document.head || document.documentElement).appendChild(style);
  }

  function esc(text) { return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  // KaTeX: already on the page, inlined by the add-on (script tags with the source), or the bundled copy.
  var katexPromise = null;
  function katexReady() {
    if (window.katex) return Promise.resolve(window.katex);
    if (katexPromise) return katexPromise;
    katexPromise = new Promise(function (resolve, reject) {
      try {
        var js = document.getElementById('synapse-katex-js'), css = document.getElementById('synapse-katex-css');
        if (js && css) {
          var style = document.createElement('style'); style.textContent = css.textContent; document.head.appendChild(style);
          var script = document.createElement('script'); script.textContent = js.textContent; document.head.appendChild(script);
          if (window.katex) resolve(window.katex); else reject(new Error('KaTeX unavailable'));
        } else {
          var link = document.createElement('link'); link.rel = 'stylesheet'; link.href = '../vendor/katex/katex.min.css'; document.head.appendChild(link);
          var tag = document.createElement('script'); tag.src = '../vendor/katex/katex.min.js';
          tag.onload = function () { if (window.katex) resolve(window.katex); else reject(new Error('KaTeX unavailable')); };
          tag.onerror = function () { reject(new Error('KaTeX unavailable')); };
          document.head.appendChild(tag);
        }
      } catch (error) { reject(error); }
    });
    katexPromise.catch(function () { katexPromise = null; });
    return katexPromise;
  }

  /* ── formulas ─────────────────────────────────────────────── */
  function renderMath(atom) {
    var latex = (atom.getAttribute('data-latex') || '').slice(0, LATEX_LIMIT);
    var display = atom.getAttribute('data-display') === '1';
    atom.contentEditable = 'false';
    atom.setAttribute('role', 'img');
    atom.setAttribute('aria-label', latex);
    atom.textContent = latex;
    katexReady().then(function (katex) {
      if ((atom.getAttribute('data-latex') || '').slice(0, LATEX_LIMIT) !== latex) return;
      try {
        atom.innerHTML = katex.renderToString(latex, { displayMode: display, throwOnError: true, trust: false, maxExpand: 500, maxSize: 20, strict: 'ignore', output: 'html' });
        atom.classList.remove('invalid');
      } catch (error) {
        atom.classList.add('invalid');
        atom.textContent = latex;
      }
    }, function () { atom.textContent = latex; });
  }
  function makeMath(latex, display) {
    var atom = document.createElement('span');
    atom.className = 'ce-math';
    atom.setAttribute('data-latex', latex);
    if (display) atom.setAttribute('data-display', '1');
    renderMath(atom);
    return atom;
  }
  // Text containing \( … \) / \[ … \] becomes formula atoms. Returns the last atom created.
  function mathFromText(root) {
    var last = null, nodes = [];
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        if (node.parentNode.closest && node.parentNode.closest('.ce-math')) return NodeFilter.FILTER_REJECT;
        return /\\[(\[]/.test(node.data) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function (node) {
      var text = node.data, pattern = /\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\]/g, match, at = 0, parts = [];
      while ((match = pattern.exec(text))) {
        var latex = match[1] !== undefined ? match[1] : match[2];
        if (!latex.trim() || latex.length > LATEX_LIMIT) continue;
        if (match.index > at) parts.push(document.createTextNode(text.slice(at, match.index)));
        last = makeMath(latex.trim(), match[2] !== undefined);
        parts.push(last);
        at = match.index + match[0].length;
      }
      if (!parts.length) return;
      if (at < text.length) parts.push(document.createTextNode(text.slice(at)));
      parts.forEach(function (part) { node.parentNode.insertBefore(part, node); });
      node.parentNode.removeChild(node);
    });
    return last;
  }

  /* ── HTML in / out ───────────────────────────────────────── */
  // Anything (pasted, stored) -> a DOM fragment holding only allowed elements.
  function cleanInto(source, target, images) {
    Array.prototype.forEach.call(source.childNodes, function (node) {
      if (node.nodeType === 3) { target.appendChild(document.createTextNode(node.data.replace(/ /g, ' ').replace(/​/g, ''))); return; }
      if (node.nodeType !== 1) return;
      var name = node.nodeName.toUpperCase();
      if (DROPPED[name]) return;
      if (node.classList && node.classList.contains('ce-math')) { target.appendChild(makeMath(node.getAttribute('data-latex') || '', node.getAttribute('data-display') === '1')); return; }
      if (name === 'IMG') {
        var id = node.getAttribute('data-img');
        if (id && /^\d{1,6}$/.test(id) && images && images[id]) {
          var img = document.createElement('img');
          img.setAttribute('data-img', id); img.src = images[id]; img.alt = '';
          target.appendChild(img);
        }
        return;
      }
      var tag = ALLOWED[name];
      if (!tag) { cleanInto(node, target, images); return; }
      var copy = document.createElement(tag);
      if (tag === 'ol') {
        var start = parseInt(node.getAttribute('start'), 10);
        if (start > 1 && start <= 100000) copy.setAttribute('start', String(start));
        var type = node.getAttribute('type');
        if (type && type !== '1' && OL_TYPES.indexOf(type) >= 0) copy.setAttribute('type', type);
      }
      if (tag !== 'br') cleanInto(node, copy, images);
      target.appendChild(copy);
    });
  }
  function ancestors(node, root, names) {
    var count = 0;
    for (var up = node.parentNode; up && up !== root; up = up.parentNode) if (names.indexOf(up.nodeName) >= 0) count += 1;
    return count;
  }
  // Editor DOM -> storage / Anki HTML.
  function serialize(root) {
    function walk(node) {
      var out = '';
      Array.prototype.forEach.call(node.childNodes, function (child) {
        if (child.nodeType === 3) { out += esc(child.data.replace(/​/g, '').replace(/ /g, ' ')); return; }
        if (child.nodeType !== 1) return;
        var name = child.nodeName.toUpperCase();
        if (DROPPED[name]) return;
        if (child.classList.contains('ce-math')) {
          var latex = (child.getAttribute('data-latex') || '').replace(/\}\}/g, '} }');
          out += child.getAttribute('data-display') === '1' ? '\\[' + esc(latex) + '\\]' : '\\(' + esc(latex) + '\\)';
          return;
        }
        if (name === 'IMG') {
          var id = child.getAttribute('data-img');
          if (id && /^\d{1,6}$/.test(id)) out += '<img data-img="' + id + '">';
          return;
        }
        var tag = ALLOWED[name];
        if (!tag) { out += walk(child); return; }
        if (tag === 'br') { out += '<br>'; return; }
        var attrs = '';
        if (tag === 'ol') {
          var start = parseInt(child.getAttribute('start'), 10);
          if (start > 1 && start <= 100000) attrs += ' start="' + start + '"';
          var depth = ancestors(child, root, ['OL']);
          var type = NESTED_TYPE[depth % 3];
          if (type !== '1') attrs += ' type="' + type + '"';
        }
        out += '<' + tag + attrs + '>' + walk(child) + '</' + tag + '>';
      });
      return out;
    }
    var html = walk(root), before;
    do { before = html; html = html.replace(/(?:<br>|<div>(?:<br>)?<\/div>)+$/, '').replace(/^(?:<br>|<div>(?:<br>)?<\/div>)+/, ''); } while (html !== before);
    return html.trim();
  }
  // Text of the old editor (textarea with [[img:N]] markers) -> field HTML.
  function fromLegacy(text) {
    return esc(String(text || '').replace(/\r\n?/g, '\n').trim()).replace(/\[\[img:(\d{1,6})\]\]/g, '<img data-img="$1">').replace(/\n/g, '<br>');
  }

  /* ── the editor ──────────────────────────────────────────── */
  function create(field, options) {
    var text = options.text || function (key, fallback) { return fallback; };
    var busy = false;
    injectStyle();
    field.contentEditable = 'true';
    field.setAttribute('role', 'textbox');
    field.setAttribute('aria-multiline', 'true');
    field.spellcheck = true;
    field.classList.add('ce-field');
    try { document.execCommand('defaultParagraphSeparator', false, 'div'); } catch (error) { /* older engines */ }

    function images() { return options.images(); }
    function changed() { field.dispatchEvent(new Event('input', { bubbles: true })); }
    function selectionInside() {
      var selection = window.getSelection();
      if (!selection || !selection.rangeCount) return null;
      var range = selection.getRangeAt(0);
      return field.contains(range.commonAncestorContainer) ? range : null;
    }
    // The browser nests lists as <ul><li>a</li><ul>…</ul></ul> (invalid): the inner list belongs inside the item before it.
    function normalizeLists() {
      var selection = window.getSelection(), node = selection && selection.rangeCount ? selection.anchorNode : null, offset = selection ? selection.anchorOffset : 0;
      var moved = false;
      field.querySelectorAll('ul > ul, ul > ol, ol > ul, ol > ol').forEach(function (inner) {
        var before = inner.previousElementSibling;
        if (before && before.nodeName === 'LI') before.appendChild(inner);
        else { var item = document.createElement('li'); inner.parentNode.insertBefore(item, inner); item.appendChild(inner); }
        moved = true;
      });
      if (moved && node && node.isConnected && field.contains(node)) {
        var range = document.createRange();
        range.setStart(node, Math.min(offset, node.nodeType === 3 ? node.data.length : node.childNodes.length)); range.collapse(true);
        selection.removeAllRanges(); selection.addRange(range);
      }
      field.querySelectorAll('ol').forEach(function (list) {
        var type = NESTED_TYPE[ancestors(list, field, ['OL']) % 3];
        if (type === '1') list.removeAttribute('type'); else list.setAttribute('type', type);
      });
    }
    function updateEmpty() { field.classList.toggle('ce-empty', !serialize(field)); }
    function setValue(html) {
      field.textContent = '';
      var holder = document.createElement('div');
      holder.innerHTML = String(html || '');          // inert: never attached
      cleanInto(holder, field, images());
      mathFromText(field);
      updateEmpty();
    }
    Object.defineProperty(field, 'value', {
      configurable: true,
      get: function () { return serialize(field); },
      set: setValue
    });
    Object.defineProperty(field, 'disabled', {
      configurable: true,
      get: function () { return field.contentEditable !== 'true'; },
      set: function (flag) { field.contentEditable = flag ? 'false' : 'true'; field.classList.toggle('disabled', !!flag); if (options.toolbar) options.toolbar.classList.toggle('disabled', !!flag); }
    });

    /* formulas typed as $x^2$ */
    function commitMath(force) {
      if (field.contentEditable !== 'true') return false;
      var selection = window.getSelection(), caret = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
      var walker = document.createTreeWalker(field, NodeFilter.SHOW_TEXT, null), node, did = false;
      while ((node = walker.nextNode())) {
        if (node.data.indexOf('$') < 0) continue;
        if (node.parentNode.closest && node.parentNode.closest('.ce-math')) continue;
        var match = MATH_TEXT.exec(node.data);
        if (!match) continue;
        var start = match.index + match[1].length, end = match.index + match[0].length;
        var offset = caret && caret.collapsed && caret.startContainer === node ? caret.startOffset : -1;
        if (!force && offset > start && offset < end) continue;            // still typing inside $…$
        var latex = match[2];
        if (latex.length > LATEX_LIMIT) continue;
        var atom = makeMath(latex, false), after = node.splitText(end);
        node.deleteData(start, end - start);
        node.parentNode.insertBefore(atom, after);
        if (!after.data) after.data = '​';
        if (offset >= end) { var range = document.createRange(); range.setStart(after, Math.min(after.data.length, Math.max(0, offset - end))); range.collapse(true); selection.removeAllRanges(); selection.addRange(range); }
        did = true;
        walker.currentNode = after;
      }
      return did;
    }
    function editMath(atom) {
      var latex = atom.getAttribute('data-latex') || '';
      var textNode = document.createTextNode((atom.getAttribute('data-display') === '1' ? '\\[' : '$') + latex + (atom.getAttribute('data-display') === '1' ? '\\]' : '$'));
      atom.parentNode.replaceChild(textNode, atom);
      var range = document.createRange();
      range.setStart(textNode, textNode.data.length - 1); range.collapse(true);
      var selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      field.focus();
      changed();
    }
    field.addEventListener('click', function (event) {
      var atom = event.target.closest && event.target.closest('.ce-math');
      if (atom && field.contains(atom) && field.contentEditable === 'true') editMath(atom);
    });
    document.addEventListener('selectionchange', function () { if (selectionInside() && commitMath(false)) changed(); });
    field.addEventListener('blur', function () { if (commitMath(true)) changed(); });

    /* markdown-style starts: "- ", "1. ", "## " */
    function blockStart(node, offset) {
      var block = node.parentNode;
      while (block !== field && !/^(LI|DIV|P|H1|H2|H3)$/.test(block.nodeName)) block = block.parentNode;
      var range = document.createRange();
      range.selectNodeContents(block); range.setEnd(node, offset);
      return range.toString();
    }
    function markdownStart() {
      var selection = window.getSelection();
      if (!selection.rangeCount || !selection.isCollapsed) return false;
      var node = selection.anchorNode, offset = selection.anchorOffset;
      if (!node || node.nodeType !== 3 || !field.contains(node)) return false;
      var typed = blockStart(node, offset).replace(/\u200b/g, '').replace(/\u00a0/g, ' ');
      var match = /^(?:([-*•])|(\d{1,4})[.)]|(#{1,3})) $/.exec(typed);
      if (!match || node.data.slice(0, offset).replace(/\u200b/g, '').replace(/\u00a0/g, ' ') !== typed) return false;
      // Leave a zero-width space behind: an empty block would not take the new format.
      node.deleteData(0, offset);
      node.insertData(0, '\u200b');
      var caret = document.createRange(); caret.setStart(node, 1); caret.collapse(true);
      selection.removeAllRanges(); selection.addRange(caret);
      if (match[1]) document.execCommand('insertUnorderedList');
      else if (match[2]) {
        document.execCommand('insertOrderedList');
        var number = parseInt(match[2], 10), anchor = window.getSelection().anchorNode, list = anchor && anchor.parentNode && anchor.parentNode.closest && anchor.parentNode.closest('ol');
        if (list && number > 1) list.setAttribute('start', String(number));
        if (!list && anchor && anchor.nodeType === 1) { list = anchor.closest('ol'); if (list && number > 1) list.setAttribute('start', String(number)); }
      } else document.execCommand('formatBlock', false, 'h' + match[3].length);
      return true;
    }
    field.addEventListener('input', function (event) {
      if (busy) return;
      busy = true;
      try {
        if (event.inputType === 'insertText' && event.data === ' ') markdownStart();
        if (event.inputType === 'insertText' && event.data === '$') commitMath(false);
        normalizeLists();
        updateEmpty();
      } finally { busy = false; }
    });

    updateEmpty();

    /* keys */
    function currentItem() {
      var range = selectionInside();
      if (!range) return null;
      var node = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentNode;
      return node.closest ? node.closest('li') : null;
    }
    field.addEventListener('keydown', function (event) {
      if (event.key === 'Tab' && !event.ctrlKey && !event.altKey && !event.metaKey) {
        var item = currentItem();
        if (!item || !field.contains(item)) return;                       // outside lists Tab moves focus
        event.preventDefault();
        if (event.shiftKey) { document.execCommand('outdent'); }
        else {
          // the first item of a list cannot be indented further (nothing to nest it under)
          var previous = item.previousElementSibling;
          if (previous && previous.nodeName === 'LI') document.execCommand('indent');
        }
        normalizeLists(); changed();
        return;
      }
      if (event.key === 'Enter' && !event.shiftKey && commitMath(true)) changed();
    });

    /* paste: pictures, formulas and clean HTML */
    function insertHtml(html) { document.execCommand('insertHTML', false, html); }
    function addPictures(uris) {
      if (busy) return;
      busy = true;
      var saved = selectionInside() && selectionInside().cloneRange(), added = 0;
      uris.reduce(function (chain, source) {
        return chain.then(function () {
          if (Object.keys(images()).length >= Images.MAX_PER_SESSION) {
            options.status(text('card_img_limit', 'A session can hold at most {count} pictures.').replace('{count}', Images.MAX_PER_SESSION), true);
            return null;
          }
          return Images.shrink(source).then(function (uri) {
            var id = options.nextId();
            images()[id] = uri;
            field.focus();
            if (saved) { var selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(saved); }
            insertHtml('<img data-img="' + id + '" src="' + uri + '" alt="">');
            added += 1;
          }, function () { options.status(text('card_img_failed', 'This picture could not be added.'), true); });
        });
      }, Promise.resolve()).then(function () {
        busy = false;
        if (added) { options.changed(); changed(); }
      });
    }
    field.addEventListener('paste', function (event) {
      var data = event.clipboardData;
      if (!data) return;
      var jobs = Images.picturesFrom(data);
      if (jobs.length) {
        event.preventDefault();
        Promise.all(jobs).then(addPictures, function () { options.status(text('card_img_failed', 'This picture could not be added.'), true); });
        return;
      }
      var html = data.getData('text/html');
      var plain = data.getData('text/plain') || '';
      event.preventDefault();
      if (html && !/^\s*$/.test(html)) {
        var holder = document.createElement('div');
        holder.innerHTML = html;
        var clean = document.createElement('div');
        cleanInto(holder, clean, images());
        insertHtml(clean.innerHTML);
      } else document.execCommand('insertText', false, plain);
      var last = mathFromText(field);
      if (last) {
        var range = document.createRange(), selection = window.getSelection();
        var after = last.nextSibling;
        if (!after || after.nodeType !== 3) { after = document.createTextNode('​'); last.parentNode.insertBefore(after, last.nextSibling); }
        range.setStart(after, 0); range.collapse(true); selection.removeAllRanges(); selection.addRange(range);
      }
      normalizeLists();
      changed();
    });
    field.addEventListener('dragover', function (event) {
      if (event.dataTransfer && Array.prototype.indexOf.call(event.dataTransfer.types || [], 'Files') >= 0) event.preventDefault();
    });
    field.addEventListener('drop', function (event) {
      var jobs = Images.picturesFrom(event.dataTransfer);
      if (!jobs.length) { event.preventDefault(); return; }               // no dropped HTML: it would bypass the cleaner
      event.preventDefault();
      Promise.all(jobs).then(addPictures, function () { options.status(text('card_img_failed', 'This picture could not be added.'), true); });
    });
    // Copying keeps plain content: strip the ce-math rendering so other fields get \( … \) text.
    field.addEventListener('copy', function (event) {
      var range = selectionInside();
      if (!range || range.collapsed || !event.clipboardData) return;
      var box = document.createElement('div');
      box.appendChild(range.cloneContents());
      var holder = document.createElement('div');
      cleanInto(box, holder, images());
      event.clipboardData.setData('text/html', serialize(holder));
      event.clipboardData.setData('text/plain', box.textContent.replace(/​/g, ''));
      event.preventDefault();
    });

    /* helpers for the page */
    function command(name, value) {
      if (field.contentEditable !== 'true') return;
      field.focus();
      document.execCommand(name, false, value);
      normalizeLists(); changed();
    }
    function heading(level) {
      var range = selectionInside();
      var node = range ? (range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentNode) : null;
      var current = node && node.closest ? node.closest('h1,h2,h3') : null;
      command('formatBlock', current && current.nodeName === 'H' + level ? 'div' : 'h' + level);
    }
    function formula() {
      if (field.contentEditable !== 'true') return;
      field.focus();
      var range = selectionInside();
      var selected = range && !range.collapsed ? range.toString().trim() : '';
      if (selected && !/[$\n]/.test(selected)) { document.execCommand('insertText', false, '$' + selected + '$'); commitMath(true); changed(); return; }
      document.execCommand('insertText', false, '$$');
      var selection = window.getSelection();
      if (selection.anchorNode && selection.anchorNode.nodeType === 3) {
        var caret = document.createRange(); caret.setStart(selection.anchorNode, selection.anchorOffset - 1); caret.collapse(true);
        selection.removeAllRanges(); selection.addRange(caret);
      }
      changed();
    }
    // Cloze deletion around the selection: {{cN::…}} as text nodes (same text the add-on validates).
    function cloze(same) {
      var range = selectionInside();
      if (!range || range.collapsed) return 'empty';
      function html(fragmentRange) { var box = document.createElement('div'); box.appendChild(fragmentRange.cloneContents()); return serialize(box); }
      if (/\{\{|\}\}/.test(html(range))) return 'nested';
      var head = document.createRange(); head.selectNodeContents(field); head.setEnd(range.startContainer, range.startOffset);
      var before = html(head);
      if (before.lastIndexOf('{{c') > before.lastIndexOf('}}')) return 'nested';
      var numbers = (serialize(field).match(/\{\{c(\d+)::/g) || []).map(function (m) { return parseInt(m.slice(3), 10); });
      var number = same ? Math.max.apply(null, [1].concat(numbers)) : Math.max.apply(null, [0].concat(numbers)) + 1;
      if (number > 999) return 'limit';
      // Keep the markers outside inline formatting that the selection covers completely: {{c1::<b>x</b>}}.
      var INLINE = /^(B|I|U|S|SUB|SUP)$/;
      range = range.cloneRange();
      for (var up = range.endContainer; up !== field && up.parentNode && INLINE.test(up.parentNode.nodeName) && up.parentNode !== field; up = up.parentNode) {
        var size = up.nodeType === 3 ? up.data.length : up.childNodes.length;
        if (range.endContainer !== up || range.endOffset !== size || up.parentNode.lastChild !== up) break;
        range.setEndAfter(up.parentNode);
        break;
      }
      for (var down = range.startContainer; down !== field && down.parentNode && INLINE.test(down.parentNode.nodeName); down = down.parentNode) {
        if (range.startContainer !== down || range.startOffset !== 0 || down.parentNode.firstChild !== down) break;
        range.setStartBefore(down.parentNode);
        break;
      }
      var tail = range.cloneRange(); tail.collapse(false); tail.insertNode(document.createTextNode('}}'));
      var lead = range.cloneRange(); lead.collapse(true); lead.insertNode(document.createTextNode('{{c' + number + '::'));
      field.normalize();
      changed();
      return 'ok';
    }
    // Add a line of plain text (selection taken from the notebook or a PDF).
    function appendText(value) {
      var lines = String(value || '').replace(/\r\n?/g, '\n').split('\n');
      var empty = !serialize(field);
      lines.forEach(function (line, index) {
        var row = document.createElement('div');
        if (line) row.textContent = line; else row.appendChild(document.createElement('br'));
        if (empty && index === 0) { field.textContent = ''; }
        field.appendChild(row);
      });
      changed();
    }

    /* toolbar */
    var bar = null;
    if (options.toolbar !== false) {
      bar = document.createElement('div');
      bar.className = 'ce-toolbar';
      bar.setAttribute('role', 'toolbar');
      var tools = [
        ['B', 'card_fmt_bold', 'Bold (Ctrl+B)', function () { command('bold'); }, 'ce-b'],
        ['I', 'card_fmt_italic', 'Italic (Ctrl+I)', function () { command('italic'); }, 'ce-i'],
        ['U', 'card_fmt_underline', 'Underline (Ctrl+U)', function () { command('underline'); }, 'ce-u'],
        ['H1', 'card_fmt_h1', 'Heading 1', function () { heading(1); }],
        ['H2', 'card_fmt_h2', 'Heading 2', function () { heading(2); }],
        ['H3', 'card_fmt_h3', 'Heading 3', function () { heading(3); }],
        ['•', 'card_fmt_bullets', 'Bullet list', function () { command('insertUnorderedList'); }],
        ['1.', 'card_fmt_numbers', 'Numbered list', function () { command('insertOrderedList'); }],
        ['⇥', 'card_fmt_indent', 'Indent (Tab)', function () { var item = currentItem(); if (item && item.previousElementSibling) command('indent'); }],
        ['⇤', 'card_fmt_outdent', 'Outdent (Shift+Tab)', function () { command('outdent'); }],
        ['∑', 'card_fmt_formula', 'Formula ($…$)', formula]
      ];
      tools.forEach(function (tool) {
        var button = document.createElement('button');
        button.type = 'button'; button.className = 'ce-btn ' + (tool[4] || ''); button.textContent = tool[0];
        button.title = text(tool[1], tool[2]); button.setAttribute('aria-label', button.title);
        button.addEventListener('mousedown', function (event) { event.preventDefault(); });   // keep the selection
        button.addEventListener('click', tool[3]);
        bar.appendChild(button);
      });
      field.parentNode.insertBefore(bar, field);
      options.toolbar = bar;
    }
    return { cloze: cloze, appendText: appendText, bar: bar, focus: function () { field.focus(); } };
  }

  window.SynapseCardEditor = { create: create, serialize: serialize, fromLegacy: fromLegacy };
})();

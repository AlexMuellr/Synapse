/* Pictures inside the text of a card field (Card Creator of the notebook and of the PDF tab).
 *
 * A field stays a normal <textarea>. Pasting a picture (Ctrl+V), dropping one on the field or copying
 * one out of a note puts a marker such as [[img:3]] at the cursor, so text can be written above and
 * below it. The picture data lives in a map next to the cards (id -> data: URI); a strip under the
 * field shows the pictures in the order they appear. The add-on turns markers into <img> tags and
 * stores the files in Anki's media folder.
 *
 * Inlined into both pages by notebook_sidebar.py (setHtml has no base URL); a plain <script src> loads
 * it when a page is opened as a file.
 */
(function () {
  if (window.SynapseCardImages) return;
  var TOKEN = /\[\[img:(\d{1,6})\]\]/g;
  var DATA_URI = /^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+\/=]+$/;
  var MAX_PER_SESSION = 12;
  var MAX_BYTES = 2 * 1024 * 1024;       // per picture, after scaling
  var MAX_SIDE = 1600;                    // longest side after scaling

  function ids(text) {
    var found = [];
    String(text || '').replace(TOKEN, function (all, id) { found.push(id); return all; });
    return found;
  }
  function display(text) { return String(text || '').replace(TOKEN, '🖼'); }
  function bytesOf(uri) { return Math.floor((uri.length - uri.indexOf(',') - 1) * 3 / 4); }
  // Untrusted stored data: keep only well-formed pictures of a sane size.
  function clean(map) {
    var out = {};
    if (!map || typeof map !== 'object' || Array.isArray(map)) return out;
    Object.keys(map).slice(0, 60).forEach(function (key) {
      var uri = map[key];
      if (/^\d{1,6}$/.test(key) && typeof uri === 'string' && uri.length < MAX_BYTES * 2 && DATA_URI.test(uri)) out[key] = uri;
    });
    return out;
  }
  // Only pictures that some text still refers to.
  function prune(map, texts) {
    var used = {};
    texts.forEach(function (text) { ids(text).forEach(function (id) { used[id] = true; }); });
    var out = {};
    Object.keys(map || {}).forEach(function (key) { if (used[key]) out[key] = map[key]; });
    return out;
  }
  function readAsDataUri(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(String(reader.result)); };
      reader.onerror = function () { reject(new Error('read')); };
      reader.readAsDataURL(file);
    });
  }
  function loadImage(uri) {
    return new Promise(function (resolve, reject) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { reject(new Error('decode')); };
      img.src = uri;
    });
  }
  // Screenshots are often huge: scale down and re-encode (PNG while small, otherwise JPEG on white).
  function shrink(uri) {
    return loadImage(uri).then(function (img) {
      var width = img.naturalWidth, height = img.naturalHeight;
      if (!width || !height) throw new Error('empty');
      var scale = Math.min(1, MAX_SIDE / Math.max(width, height));
      var small = DATA_URI.test(uri) && scale === 1 && bytesOf(uri) <= MAX_BYTES;
      if (small) return uri;
      var canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(width * scale));
      canvas.height = Math.max(1, Math.round(height * scale));
      var context = canvas.getContext('2d');
      context.drawImage(img, 0, 0, canvas.width, canvas.height);
      var png = canvas.toDataURL('image/png');
      if (bytesOf(png) <= MAX_BYTES / 2) return png;
      context.globalCompositeOperation = 'destination-over';
      context.fillStyle = '#ffffff';
      context.fillRect(0, 0, canvas.width, canvas.height);
      var jpeg = canvas.toDataURL('image/jpeg', 0.88);
      if (bytesOf(jpeg) > MAX_BYTES) throw new Error('large');
      return jpeg;
    });
  }
  // Pictures on a clipboard / drop: image files, or <img src="data:…"> in HTML when there is no text.
  function picturesFrom(transfer) {
    var jobs = [];
    var files = transfer && transfer.files ? Array.prototype.slice.call(transfer.files) : [];
    files.forEach(function (file) { if (/^image\/(png|jpeg|gif|webp)$/.test(file.type)) jobs.push(readAsDataUri(file)); });
    if (!jobs.length && transfer && !(transfer.getData('text/plain') || '').trim()) {
      var html = transfer.getData('text/html') || '';
      var pattern = /<img[^>]+src=["'](data:image\/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+\/=]+)["']/gi, match;
      while ((match = pattern.exec(html)) && jobs.length < 6) jobs.push(Promise.resolve(match[1]));
    }
    return jobs;
  }
  function insertMarker(field, id) {
    var value = field.value, start = field.selectionStart, end = field.selectionEnd;
    var before = value.slice(0, start), after = value.slice(end);
    var text = (before && before.slice(-1) !== '\n' ? '\n' : '') + '[[img:' + id + ']]' + (after.charAt(0) === '\n' ? '' : '\n');
    if (field.maxLength > 0 && value.length - (end - start) + text.length > field.maxLength) return false;
    field.setRangeText(text, start, end, 'end');
    return true;
  }
  function removeMarker(field, id) {
    var pattern = new RegExp('(^|\\n)\\[\\[img:' + id + '\\]\\](\\n|$)', 'g');
    var next = field.value.replace(pattern, function (all, a, b) { return a && b ? '\n' : ''; })
      .replace(new RegExp('\\[\\[img:' + id + '\\]\\]', 'g'), '');
    if (next !== field.value) field.value = next;
  }

  /* attach(field, options) -> { refresh }
   *   options.images()      the mutable map id -> data: URI
   *   options.nextId()      a fresh numeric id as a string
   *   options.changed()     called after the field or the map changed (persist)
   *   options.status(m, e)  show a message
   *   options.preview       element that receives the thumbnail strip
   *   options.text(key, fallback) translation lookup
   */
  function attach(field, options) {
    var busy = false;
    function refresh() {
      var strip = options.preview, map = options.images();
      if (!strip) return;
      strip.textContent = '';
      var seen = {};
      ids(field.value).forEach(function (id) {
        if (!map[id] || seen[id]) return;
        seen[id] = true;
        var item = document.createElement('div');
        item.className = 'creator-image';
        var thumb = document.createElement('img');
        thumb.src = map[id]; thumb.alt = '';
        thumb.title = options.text('card_img_find', 'Click to find the picture in the text');
        thumb.addEventListener('click', function () {
          var token = '[[img:' + id + ']]', at = field.value.indexOf(token);
          field.focus();
          if (at >= 0) field.setSelectionRange(at, at + token.length);
        });
        var number = document.createElement('span');
        number.className = 'creator-image-number'; number.textContent = id;
        var remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'creator-image-remove'; remove.textContent = '×';
        remove.title = options.text('card_img_remove', 'Remove picture');
        remove.setAttribute('aria-label', remove.title);
        remove.addEventListener('click', function () {
          removeMarker(field, id);
          field.dispatchEvent(new Event('input', { bubbles: true }));
          refresh();
        });
        item.append(thumb, number, remove);
        strip.appendChild(item);
      });
      strip.hidden = !strip.firstChild;
    }
    function add(uris) {
      if (busy) return;
      busy = true;
      var added = 0;
      uris.reduce(function (chain, source) {
        return chain.then(function () {
          if (Object.keys(options.images()).length >= MAX_PER_SESSION) {
            options.status(options.text('card_img_limit', 'A session can hold at most {count} pictures.').replace('{count}', MAX_PER_SESSION), true);
            return null;
          }
          return shrink(source).then(function (uri) {
            var id = options.nextId();
            if (!insertMarker(field, id)) return;
            options.images()[id] = uri;
            added += 1;
          }, function () {
            options.status(options.text('card_img_failed', 'This picture could not be added.'), true);
          });
        });
      }, Promise.resolve()).then(function () {
        busy = false;
        if (added) { field.dispatchEvent(new Event('input', { bubbles: true })); options.changed(); }
        refresh();
      });
    }
    field.addEventListener('paste', function (event) {
      var jobs = picturesFrom(event.clipboardData);
      if (!jobs.length) return;                  // ordinary text paste
      event.preventDefault();
      Promise.all(jobs).then(add, function () { options.status(options.text('card_img_failed', 'This picture could not be added.'), true); });
    });
    field.addEventListener('dragover', function (event) {
      if (event.dataTransfer && Array.prototype.some.call(event.dataTransfer.types || [], function (t) { return t === 'Files'; })) event.preventDefault();
    });
    field.addEventListener('drop', function (event) {
      var jobs = picturesFrom(event.dataTransfer);
      if (!jobs.length) return;
      event.preventDefault();
      Promise.all(jobs).then(add, function () { options.status(options.text('card_img_failed', 'This picture could not be added.'), true); });
    });
    field.addEventListener('input', refresh);
    return { refresh: refresh };
  }

  window.SynapseCardImages = { attach: attach, ids: ids, display: display, clean: clean, prune: prune, MAX_PER_SESSION: MAX_PER_SESSION, nextId: function (map, counter) {
    var highest = Math.max(Number(counter) || 0, 0);
    Object.keys(map || {}).forEach(function (key) { highest = Math.max(highest, Number(key) || 0); });
    return String(highest + 1);
  } };
})();

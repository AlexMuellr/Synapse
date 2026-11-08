/* Picture helpers for the Card Creators (notebook and PDF tab); the editor itself is card_editor.js.
 *
 * A field holds <img data-img="3"> where a picture sits; the picture data lives in a map next to the
 * cards (id -> data: URI) so the field HTML stays small. The add-on stores the files in Anki's media
 * folder when the cards are created.
 *
 * Inlined into both pages by notebook_sidebar.py (setHtml has no base URL); a plain <script src> loads
 * it when a page is opened as a file.
 */
(function () {
  if (window.SynapseCardImages) return;
  var TOKEN = /data-img="(\d{1,6})"/g;
  var DATA_URI = /^data:image\/(png|jpeg|gif|webp);base64,[A-Za-z0-9+\/=]+$/;
  var MAX_PER_SESSION = 12;
  var MAX_BYTES = 2 * 1024 * 1024;       // per picture, after scaling
  var MAX_SIDE = 1600;                    // longest side after scaling

  function ids(text) {
    var found = [];
    String(text || '').replace(TOKEN, function (all, id) { found.push(id); return all; });
    return found;
  }
  // Plain-text preview of a field's HTML (card list): blocks become lines, pictures a symbol.
  function display(markup) {
    var box = document.createElement('div');
    box.innerHTML = String(markup || '').replace(/<img[^>]*>/gi, '🖼').replace(/<\/(div|p|li|h[1-3])>|<br\s*\/?>/gi, '\n');
    return (box.textContent || '').replace(/\n{2,}/g, '\n').trim();
  }
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
  window.SynapseCardImages = { shrink: shrink, picturesFrom: picturesFrom, ids: ids, display: display, clean: clean, prune: prune, MAX_PER_SESSION: MAX_PER_SESSION, nextId: function (map, counter) {
    var highest = Math.max(Number(counter) || 0, 0);
    Object.keys(map || {}).forEach(function (key) { highest = Math.max(highest, Number(key) || 0); });
    return String(highest + 1);
  } };
})();

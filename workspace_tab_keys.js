/* Keyboard switching between the notebook tabs (Notebook, To-do, PDF, Changes).
   Inlined into every tool page by notebook_sidebar.py; __BRIDGE__ is the bridge prefix.
   Ctrl+Tab / Ctrl+Shift+Tab go to the next / previous tab, Ctrl+PageDown / Ctrl+PageUp do the same. */
(function () {
  if (window.__synapseTabKeys) return;
  window.__synapseTabKeys = true;
  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || e.isComposing || !e.ctrlKey || e.altKey || e.metaKey) return;
    var delta = 0;
    if (e.key === 'Tab') delta = e.shiftKey ? -1 : 1;
    else if (!e.shiftKey && e.key === 'PageDown') delta = 1;
    else if (!e.shiftKey && e.key === 'PageUp') delta = -1;
    if (!delta) return;
    // Before the focused field sees it: Ctrl+Tab must never move focus or indent a list.
    e.preventDefault();
    e.stopPropagation();
    if (typeof pycmd === 'function') pycmd('__BRIDGE__:switch:' + (delta > 0 ? 'next' : 'prev'));
  }, true);
})();

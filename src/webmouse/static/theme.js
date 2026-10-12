/* Runs before first paint so the page never flashes the wrong theme. */
(function () {
  var theme = 'system';
  try {
    theme = JSON.parse(localStorage.getItem('webmouse.settings') || '{}').theme || 'system';
  } catch (e) {}
  var dark = theme === 'dark' ||
    (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.classList.toggle('dark', dark);
})();

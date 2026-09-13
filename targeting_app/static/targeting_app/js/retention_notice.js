/* ============================================================
   Retention notice — fills in "[data-retention-notice]" elements
   with the actual configured retention period (outputRetentionDays
   in the #js-config block), so the message shown to users always
   matches settings.OUTPUT_RETENTION_DAYS rather than a hardcoded
   guess that could drift out of sync.
   ============================================================ */
(function () {
  'use strict';

  function init() {
    const nodes = document.querySelectorAll('[data-retention-notice]');
    if (!nodes.length) return;

    let days = 14;
    const cfgEl = document.getElementById('js-config');
    try {
      const cfg = cfgEl ? JSON.parse(cfgEl.textContent) : {};
      if (cfg && Number.isFinite(cfg.outputRetentionDays)) days = cfg.outputRetentionDays;
    } catch (_) { /* keep default */ }

    const text =
      `Results are stored on the server for ${days} day${days === 1 ? '' : 's'} ` +
      `after generation, then deleted automatically — download anything you want to keep.`;
    nodes.forEach((node) => { node.textContent = text; });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

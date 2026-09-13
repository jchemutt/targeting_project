/* ============================================================
   Panel resize — generic drag-to-resize for the shared workspace
   layout (Suitability, Similarity, Statistics pages all use the
   same suit-main / suit-center / panel-layers / panel-side /
   panel-map / panel-table structure).

   Each resize handle is a small element carrying data attributes
   that say what to resize:

     <div class="resize-handle resize-handle-vertical"
          data-resize-handle
          data-resize-target=".panel-layers"
          data-resize-axis="width"
          data-resize-min="160" data-resize-max="720"></div>

   data-resize-invert="true" flips the drag direction — used when
   the target panel is anchored on the far side of the handle from
   the direction that visually grows it (e.g. the right-hand panel,
   or the panel above a horizontal handle).

   Sizes persist per page (by pathname) + target selector in
   localStorage, so a chosen layout survives reloads — the point of
   this being resizable at all is to fit different screens once,
   not redo it every visit. Double-clicking a handle resets that
   panel back to its CSS default size.
   ============================================================ */
(function () {
  'use strict';

  function clamp(v, min, max) {
    return Math.max(min, Math.min(max, v));
  }

  function storageKey(handle) {
    return `panelResize:${window.location.pathname}:${handle.dataset.resizeTarget}`;
  }

  function applySize(target, axis, px) {
    if (axis === 'width') target.style.width = `${px}px`;
    else target.style.height = `${px}px`;
  }

  function pointerPos(e, axis) {
    const src = e.touches && e.touches.length ? e.touches[0] : e;
    return axis === 'width' ? src.clientX : src.clientY;
  }

  function initHandle(handle) {
    const target = document.querySelector(handle.dataset.resizeTarget || '');
    if (!target) return;

    const axis = handle.dataset.resizeAxis === 'height' ? 'height' : 'width';
    const min = parseInt(handle.dataset.resizeMin, 10) || 100;
    const max = parseInt(handle.dataset.resizeMax, 10) || 2000;
    const invert = handle.dataset.resizeInvert === 'true';
    const key = storageKey(handle);

    // Restore a previously chosen size, if any and still sane.
    try {
      const saved = parseInt(localStorage.getItem(key), 10);
      if (Number.isFinite(saved)) applySize(target, axis, clamp(saved, min, max));
    } catch (_) { /* localStorage unavailable (private mode etc.) — skip persistence */ }

    let dragging = false;
    let startPos = 0;
    let startSize = 0;

    function onPointerDown(e) {
      dragging = true;
      startPos = pointerPos(e, axis);
      const rect = target.getBoundingClientRect();
      startSize = axis === 'width' ? rect.width : rect.height;
      handle.classList.add('is-dragging');
      document.body.classList.add(
        'is-resizing-panels',
        axis === 'width' ? 'is-resizing-vertical' : 'is-resizing-horizontal',
      );
      e.preventDefault();
    }

    function onPointerMove(e) {
      if (!dragging) return;
      let delta = pointerPos(e, axis) - startPos;
      if (invert) delta = -delta;
      applySize(target, axis, clamp(startSize + delta, min, max));
      e.preventDefault();
    }

    function onPointerUp() {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove('is-dragging');
      document.body.classList.remove(
        'is-resizing-panels', 'is-resizing-vertical', 'is-resizing-horizontal');
      try {
        const rect = target.getBoundingClientRect();
        const size = axis === 'width' ? rect.width : rect.height;
        localStorage.setItem(key, String(Math.round(size)));
      } catch (_) { /* ignore */ }
    }

    handle.addEventListener('mousedown', onPointerDown);
    handle.addEventListener('touchstart', onPointerDown, { passive: false });
    window.addEventListener('mousemove', onPointerMove, { passive: false });
    window.addEventListener('touchmove', onPointerMove, { passive: false });
    window.addEventListener('mouseup', onPointerUp);
    window.addEventListener('touchend', onPointerUp);

    handle.addEventListener('dblclick', () => {
      target.style[axis] = '';
      try { localStorage.removeItem(key); } catch (_) { /* ignore */ }
    });

    handle.setAttribute('title', 'Drag to resize · double-click to reset');
  }

  function init() {
    document.querySelectorAll('[data-resize-handle]').forEach(initHandle);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

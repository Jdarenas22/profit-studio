/* Mejoras de formularios para todo el sitio (mejora progresiva: sin JS todo sigue funcionando).
 *  1. Botón «Mostrar/Ocultar contraseña» en cada input de contraseña.
 *  2. Protección contra el doble envío: tras enviar un formulario se desactivan sus botones.
 * Sin dependencias. Estilos en línea para no depender de que Tailwind compile clases escritas aquí. */
(function () {
  'use strict';

  /* ── 1. Mostrar / ocultar contraseña ─────────────────────────────────────────── */
  function enhancePassword(input) {
    if (input.dataset.pwToggle === '1' || !input.parentNode) return;
    input.dataset.pwToggle = '1';

    var wrap = document.createElement('div');
    wrap.style.position = 'relative';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    input.style.paddingRight = '3rem';

    var button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('aria-label', 'Mostrar contraseña');
    button.setAttribute('aria-pressed', 'false');
    button.style.cssText = 'position:absolute;top:0;bottom:0;right:0;width:3rem;display:flex;align-items:center;' +
      'justify-content:center;background:none;border:0;padding:0;color:rgba(255,255,255,.6);cursor:pointer;';
    var icon = document.createElement('i');
    icon.className = 'fa fa-eye';
    icon.setAttribute('aria-hidden', 'true');
    button.appendChild(icon);

    button.addEventListener('click', function () {
      var show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      icon.className = show ? 'fa fa-eye-slash' : 'fa fa-eye';
      button.setAttribute('aria-pressed', show ? 'true' : 'false');
      button.setAttribute('aria-label', show ? 'Ocultar contraseña' : 'Mostrar contraseña');
      input.focus();
    });
    wrap.appendChild(button);

    // Antes de enviar vuelve a «password»: así el gestor de contraseñas del navegador la guarda bien
    if (input.form) {
      input.form.addEventListener('submit', function () {
        if (input.type !== 'password') {
          input.type = 'password';
          icon.className = 'fa fa-eye';
          button.setAttribute('aria-pressed', 'false');
          button.setAttribute('aria-label', 'Mostrar contraseña');
        }
      });
    }
  }

  function enhanceAllPasswords(root) {
    var inputs = (root || document).querySelectorAll('input[type="password"]');
    for (var i = 0; i < inputs.length; i++) enhancePassword(inputs[i]);
  }

  /* ── 2. Anti doble envío ──────────────────────────────────────────────────────── */
  var RESET_AFTER_MS = 15000;      // por si la respuesta no recarga la página (p. ej. una descarga)
  var BUTTONS = 'button[type="submit"], button:not([type]), input[type="submit"]';

  function usesHtmx(form) {
    for (var i = 0; i < form.attributes.length; i++) {
      if (form.attributes[i].name.indexOf('hx-') === 0 || form.attributes[i].name.indexOf('data-hx-') === 0) return true;
    }
    return false;
  }

  function setBusy(form, busy) {
    var buttons = form.querySelectorAll(BUTTONS);
    for (var i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      if (busy) {
        b.dataset.wasDisabled = b.disabled ? '1' : '0';
        b.disabled = true;
        b.setAttribute('aria-busy', 'true');
        b.style.opacity = '0.6';
        b.style.cursor = 'wait';
      } else if (b.dataset.wasDisabled !== undefined) {
        b.disabled = b.dataset.wasDisabled === '1';
        b.removeAttribute('aria-busy');
        b.style.opacity = '';
        b.style.cursor = '';
        delete b.dataset.wasDisabled;
      }
    }
    form.dataset.submitting = busy ? '1' : '';
  }

  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented) return;   // otro código (Alpine, fetch) lo maneja
    if ((form.getAttribute('method') || 'get').toLowerCase() === 'get') return;
    if (form.hasAttribute('data-allow-resubmit') || form.target === '_blank' || usesHtmx(form)) return;
    if (form.dataset.submitting === '1') {                                       // segundo clic mientras envía
      event.preventDefault();
      return;
    }
    form.dataset.submitting = '1';
    // Se desactiva DESPUÉS de que el navegador arma los datos (un botón desactivado no envía su name/value)
    setTimeout(function () { setBusy(form, true); }, 0);
    setTimeout(function () { setBusy(form, false); }, RESET_AFTER_MS);
  });

  // Al volver con «Atrás» el navegador puede restaurar la página con los botones desactivados
  window.addEventListener('pageshow', function (event) {
    if (!event.persisted) return;
    var forms = document.querySelectorAll('form');
    for (var i = 0; i < forms.length; i++) setBusy(forms[i], false);
  });

  /* ── Inicio ───────────────────────────────────────────────────────────────────── */
  function init() { enhanceAllPasswords(document); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
  // Contenido que HTMX inserta después de cargar la página
  document.addEventListener('htmx:afterSwap', function (event) { enhanceAllPasswords(event.target || document); });
})();

/* Los controles principales son HTML nativo; estas mejoras no requieren Alpine. */
(function () {
  'use strict';
  var printStates = [];
  window.addEventListener('beforeprint', function () {
    if (printStates.length) return;
    document.querySelectorAll('details').forEach(function (element) {
      printStates.push([element, element.open]);
      element.open = true;
    });
  });
  window.addEventListener('afterprint', function () {
    printStates.forEach(function (entry) { entry[0].open = entry[1]; });
    printStates = [];
  });
  function revealAnchor() {
    var id;
    try { id = decodeURIComponent(window.location.hash.slice(1)); } catch (_) { return; }
    if (!id) return;
    var element = document.getElementById(id);
    if (!element) return;
    var parent = element;
    while (parent) {
      if (parent.tagName === 'DETAILS') parent.open = true;
      parent = parent.parentElement;
    }
  }
  window.addEventListener('hashchange', revealAnchor);
  revealAnchor();
}());

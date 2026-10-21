/* Thuispagina: het 3D-logo, de rook en de tellers.
 *
 * Alles hier is versiering. Valt dit script weg, dan blijft de pagina
 * gewoon werken: de cijfers staan al in de HTML, het logo staat stil.
 * Wie op zijn toestel "minder beweging" heeft aangezet, krijgt niets
 * van de animaties.
 */
(function () {
  "use strict";

  var rustig = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var held = document.querySelector(".thuis-held");
  if (!held) return;
  if (rustig) { held.classList.add("klaar"); return; }

  /* -------------------------------------------------------- tellers -- */
  /* Elk cijfer met data-tel telt op van 0 naar zijn waarde. */
  function tel(el, vertraging) {
    var doel = parseInt(el.getAttribute("data-tel"), 10);
    if (isNaN(doel)) return;
    var duur = 1400, start = null;
    el.textContent = "0";
    function stap(t) {
      if (start === null) start = t + vertraging;
      var p = Math.min(1, Math.max(0, (t - start) / duur));
      var e = 1 - Math.pow(1 - p, 4);            // snel vertrekken, zacht landen
      el.textContent = Math.round(doel * e);
      if (p < 1) requestAnimationFrame(stap);
    }
    requestAnimationFrame(stap);
  }
  held.querySelectorAll(".held-cijfers [data-tel]").forEach(function (el, i) { tel(el, 150 + i * 120); });
  held.querySelectorAll(".podium [data-tel]").forEach(function (el) { tel(el, 700); });

  /* Eerst alles in de beginstand zetten, en een beeld later de opbouw
     starten (podium, logo). Zonder script blijft alles gewoon zichtbaar. */
  held.classList.add("wacht");
  requestAnimationFrame(function () {
    requestAnimationFrame(function () { held.classList.add("klaar"); });
  });

  /* ------------------------------------------------------- 3D-logo -- */
  /* Het logo kantelt naar de muis. Zonder muis (gsm) wiegt het zacht heen
     en weer. De hoeken gaan via CSS-variabelen naar de stylesheet. */
  var logo = held.querySelector(".logo3d-binnen");
  var doelX = 0, doelY = 0, nuX = 0, nuY = 0, laatsteMuis = 0;
  var heeftMuis = window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  if (logo && heeftMuis) {
    held.addEventListener("pointermove", function (e) {
      var r = logo.getBoundingClientRect();
      var dx = (e.clientX - (r.left + r.width / 2)) / window.innerWidth;
      var dy = (e.clientY - (r.top + r.height / 2)) / window.innerHeight;
      doelY = Math.max(-1, Math.min(1, dx * 2.2)) * 32;
      doelX = Math.max(-1, Math.min(1, -dy * 2.2)) * 24;
      laatsteMuis = performance.now();
    });
  }

  /* ---------------------------------------------------------- rook -- */
  /* De rook zelf staat in rook.js (gedeeld met het beamerscherm). Het licht
     staat waar het logo staat. */
  var doek = held.querySelector("canvas.rook");
  var rook = (window.Rook && doek) ? window.Rook(doek) : null;

  function maat() {
    if (!rook) return;
    var hr = held.getBoundingClientRect(), lr = (logo || held).getBoundingClientRect();
    rook.maat((lr.left + lr.width / 2 - hr.left) / hr.width,
              1 - (lr.top + lr.height / 2 - hr.top) / hr.height);
  }

  /* ----------------------------------------------------- hoofdlus -- */
  var zichtbaar = true, vorige = 0, loopt = false;

  function frame(t) {
    if (!zichtbaar || document.hidden) { loopt = false; return; }
    var dt = vorige ? Math.min(.05, (t - vorige) / 1000) : 0;
    vorige = t;

    if (logo) {
      /* Geen muis gezien in de laatste 2,5 s? Dan zelf zacht wiegen. */
      if (!heeftMuis || t - laatsteMuis > 2500) {
        doelY = Math.sin(t / 2400) * 22;
        doelX = Math.sin(t / 3100) * 9;
      }
      nuX += (doelX - nuX) * Math.min(1, dt * 5);
      nuY += (doelY - nuY) * Math.min(1, dt * 5);
      logo.style.setProperty("--rx", nuX.toFixed(2) + "deg");
      logo.style.setProperty("--ry", nuY.toFixed(2) + "deg");
    }

    if (rook) rook.teken(t);
    requestAnimationFrame(frame);
  }
  function start() {
    if (loopt) return;
    loopt = true; vorige = 0;
    requestAnimationFrame(frame);
  }

  maat();
  window.addEventListener("resize", maat);
  /* Enkel animeren als de kop in beeld is en het tabblad open staat:
     scheelt batterij op de gsm aan de tafel. */
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (items) {
      zichtbaar = items[0].isIntersecting;
      if (zichtbaar) start();
    }).observe(held);
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) start(); });
  start();
})();

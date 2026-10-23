/* De zwevende 3D-kroonkurk op een profiel (en in de editor).
 *
 * Gebouwd zoals het 3D-logo op de thuispagina: geen 3D-bibliotheek, maar een
 * stapel lagen in CSS-3D. Bovenaan de dop zelf (de afbeelding van de server),
 * daaronder een reeks donkere kopieën die samen de gekartelde zijkant vormen,
 * en achteraan de binnenkant van de dop.
 *
 * Hij zweeft en wiegt vanzelf. Sleep hem rond om hem te draaien; een tik
 * geeft hem een zwierige draai.
 *
 * Gebruik: <div class="dop3d" data-dop="/dop/s/12.svg?v=…"></div>
 *          var d = Dop3D(element);  d.zet(nieuwAdres);   (editor)
 */
(function () {
  "use strict";

  var LAGEN = 9;                 // aantal lagen voor de zijkant
  var rustig = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function maak(houder) {
    if (houder._dop3d) return houder._dop3d;
    var bron = houder.getAttribute("data-dop");
    houder.innerHTML = "";
    var schaduw = document.createElement("div");
    schaduw.className = "dop3d-schaduw";
    var draai = document.createElement("div");
    draai.className = "dop3d-draai";
    houder.appendChild(schaduw);
    houder.appendChild(draai);

    var beelden = [];
    // achterkant: de binnenkant van de dop (grijze voering)
    var achter = document.createElement("div");
    achter.className = "dop3d-laag dop3d-achter";
    var achterBeeld = document.createElement("img");
    achterBeeld.alt = ""; achterBeeld.draggable = false;
    achter.appendChild(achterBeeld);
    achter.appendChild(document.createElement("span"));
    draai.appendChild(achter);
    beelden.push(achterBeeld);
    // de zijkant
    for (var i = 0; i < LAGEN; i++) {
      var laag = document.createElement("img");
      laag.className = "dop3d-laag dop3d-zij";
      laag.alt = ""; laag.draggable = false;
      laag.style.setProperty("--i", i);
      draai.appendChild(laag);
      beelden.push(laag);
    }
    // de bovenkant
    var boven = document.createElement("img");
    boven.className = "dop3d-laag dop3d-boven";
    boven.alt = ""; boven.draggable = false;
    draai.appendChild(boven);
    beelden.push(boven);

    function zet(adres) { beelden.forEach(function (b) { b.src = adres; }); }
    if (bron) zet(bron);

    /* ------------------------------------------------- beweging -- */
    var hoek = 0, snelheid = 0;      // eigen draai rond de verticale as (graden, graden/s)
    var sleept = false, vorigeX = 0, vorigeT = 0, versleept = 0;
    var vorige = 0, zichtbaar = true, loopt = false;

    function frame(t) {
      if (!zichtbaar || document.hidden) { loopt = false; return; }
      var dt = vorige ? Math.min(0.05, (t - vorige) / 1000) : 0;
      vorige = t;
      if (!sleept) {
        hoek += snelheid * dt;
        snelheid *= Math.pow(0.12, dt);              // uitbollen
        // traag geworden? dan zacht terug naar de dichtste "voorkant"
        if (Math.abs(snelheid) < 60) {
          var doel = Math.round(hoek / 360) * 360;
          snelheid += (doel - hoek) * 9 * dt;
          snelheid *= Math.pow(0.02, dt);
        }
      }
      var wieg = rustig ? 0 : Math.sin(t / 2600) * 24;
      var kantel = rustig ? 10 : 12 + Math.sin(t / 3300) * 7;
      var zweef = rustig ? 0 : Math.sin(t / 1700) * 6;
      draai.style.transform = "translateY(" + zweef.toFixed(2) + "px) rotateX(" + kantel.toFixed(2) +
        "deg) rotateY(" + (hoek + wieg).toFixed(2) + "deg)";
      var s = 1 - zweef / 60;
      schaduw.style.transform = "scale(" + s.toFixed(3) + ")";
      schaduw.style.opacity = (0.55 - zweef / 40).toFixed(3);
      requestAnimationFrame(frame);
    }
    function start() {
      if (loopt) return;
      loopt = true; vorige = 0;
      requestAnimationFrame(frame);
    }

    houder.addEventListener("pointerdown", function (e) {
      sleept = true; versleept = 0;
      vorigeX = e.clientX; vorigeT = performance.now();
      snelheid = 0;
      try { houder.setPointerCapture(e.pointerId); } catch (x) {}
      start();
    });
    houder.addEventListener("pointermove", function (e) {
      if (!sleept) return;
      var nu = performance.now(), dx = e.clientX - vorigeX;
      hoek += dx * 0.9;
      versleept += Math.abs(dx);
      var dtt = Math.max(8, nu - vorigeT);
      snelheid = snelheid * 0.5 + (dx * 0.9 / dtt * 1000) * 0.5;
      vorigeX = e.clientX; vorigeT = nu;
    });
    function los() {
      if (!sleept) return;
      sleept = false;
      // Een tik zonder slepen: een vrolijke draai.
      if (versleept < 6) snelheid = (Math.random() < 0.5 ? -1 : 1) * 900;
      snelheid = Math.max(-1600, Math.min(1600, snelheid));
    }
    houder.addEventListener("pointerup", los);
    houder.addEventListener("pointercancel", los);

    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (items) {
        zichtbaar = items[0].isIntersecting;
        if (zichtbaar) start();
      }).observe(houder);
    }
    document.addEventListener("visibilitychange", function () { if (!document.hidden) start(); });
    start();

    houder._dop3d = { zet: zet };
    return houder._dop3d;
  }

  window.Dop3D = maak;
  document.querySelectorAll(".dop3d[data-dop]").forEach(function (el) { maak(el); });
})();

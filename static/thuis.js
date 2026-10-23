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
  aaibareVis(held.querySelector(".logo3d"), rustig);
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

  /* ------------------------------------------------ de vis aaien -- */
  /* Paasei: tik op de vis en hij springt op en knijpt zijn oog dicht.
     Blijf tikken en hij wordt blijer: iets
     hoger springen, wat sneller kwispelen, een vrolijk dansje en een
     rozer licht. Stop je, dan komt hij binnen een paar tellen tot rust.

     Alles in de eenheden van het SVG-vlak (100 x 100), dus even groot op
     pc en gsm. Een eigen kleine lus die enkel loopt zolang er iets beweegt. */
  function aaibareVis(logo, rustig) {
    if (!logo) return;
    /* We bewegen de hele vis-laag (via translate/rotate/scale, die bovenop de
       zwembeweging komen), niet de vorm binnenin de SVG: dat laatste moet de
       browser elk beeld opnieuw tekenen, en in het 3D-logo liet hij de vis
       dan soms gewoon weg. */
    var lijf = logo.querySelector(".laag-vis");
    var staart = logo.querySelector(".vis-staart");
    if (!lijf) return;
    var eenheid = 1;            // pixels per eenheid van het SVG-vlak

    var ZWAARTE = 620;          // valversnelling (eenheden/s²)
    var MAX_GELUK = 12;
    var y = 0, vy = 0;          // hoogte (negatief = omhoog) en snelheid
    var rek = 0, vrek = 0;      // uitrekken/platdrukken (veer)
    var kant = 0, vkant = 0;    // scheef hangen (veer, in graden)
    var geluk = 0;              // hoe blij: +1 per aai, zakt vlot weer weg
    var laatsteAai = -1e9, vorige = 0, loopt = false, links = 1;

    /* De staart. Gewoonlijk wiegt hij via CSS (keyframes "staart": van -10°
       naar +10° en terug, 0,9 s per kant). Tijdens het aaien neemt dit script
       het over met dezelfde golf, -10° x cos(fase): de golf versnelt en
       verbreedt een beetje als de vis blij is, en de fase loopt altijd
       gewoon door, zodat er nooit een schok in zit. Op het einde wachten we
       tot de staart weer op -10° staat, en geven hem dan naadloos terug aan CSS. */
    var GEWOON = Math.PI / 0.9;  // radialen per seconde van de CSS-golf
    var fase = 0, kwispel = 0, staartBezig = false;

    function staartOvernemen() {
      if (staartBezig || !staart) return;
      var hoek = -10;
      try {
        var m = new DOMMatrix(getComputedStyle(staart).transform);
        hoek = Math.atan2(m.b, m.a) * 180 / Math.PI;
      } catch (x) {}
      fase = Math.acos(Math.max(-1, Math.min(1, -hoek / 10)));
      staartBezig = true;
      logo.classList.add("kwispelt");
    }

    function aai(e) {
      if (e.button > 0) return;
      var nu = performance.now();
      geluk = Math.min(geluk + 1, MAX_GELUK);
      laatsteAai = nu;
      logo.classList.add("geaaid");
      staartOvernemen();
      if (navigator.vibrate) { try { navigator.vibrate(8); } catch (x) {} }

      if (!rustig) {
        // Elke aai mikt op een vaste hoogte (blijer = iets hoger), gerekend
        // vanaf waar de vis nu is: hoe vaak je ook tikt, hij danst op en neer
        // rond die hoogte en vliegt nooit van het scherm.
        var doel = 9 + geluk * .6;                       // 9 tot 16 eenheden hoog
        var nog = doel + y;                              // y is negatief in de lucht
        if (nog > .5) vy = Math.min(vy, -Math.sqrt(2 * ZWAARTE * nog));
        else vy = Math.min(vy, 0) + 25;                  // al hoog genoeg: even een duikje
        vrek += 5.5;                                     // even uitrekken
        links = -links;
        vkant += links * (90 + geluk * 12);              // om beurt naar links en rechts wiebelen
      }

      start();
    }

    function lus(t) {
      var dt = vorige ? Math.min(.04, (t - vorige) / 1000) : 0;
      vorige = t;

      // sprong en landing
      vy += ZWAARTE * dt; y += vy * dt;
      if (y > 0) {
        if (vy > 40) vrek -= vy * .028;                  // platdrukken bij de landing
        y = 0; vy = vy > 40 ? -vy * .18 : 0;             // klein nawipje
      }
      // veren voor rekken en scheef hangen
      vrek += (-170 * rek - 11 * vrek) * dt; rek += vrek * dt;
      vkant += (-120 * kant - 9 * vkant) * dt; kant += vkant * dt;
      rek = Math.max(-.35, Math.min(.35, rek));

      // geluk zakt vlot weg: na stevig spammen is hij binnen een paar tellen bedaard
      geluk = Math.max(0, geluk - dt * (2 + geluk * .5));

      var sy = 1 + rek, sx = 1 - rek * .55;
      // erg blij? dan een rustig heen-en-weer van het hele lijf
      var dans = geluk > 6 ? Math.sin(t / 160) * (geluk - 6) * .5 : 0;
      lijf.style.translate = "0 " + (y * eenheid).toFixed(2) + "px";
      lijf.style.rotate = (kant + dans).toFixed(2) + "deg";
      lijf.style.scale = sx.toFixed(3) + " " + sy.toFixed(3);

      // kwispelen: zacht meegroeien met het geluk, en even zacht weer zakken
      var klaarMetStaart = true;
      if (staartBezig) {
        kwispel += (Math.min(1, geluk / 8) - kwispel) * Math.min(1, dt * 4);
        var vorigeFase = fase;
        fase += dt * GEWOON * (1 + kwispel * .9);        // hoogstens iets minder dan 2x zo snel
        var hoek = -(10 + kwispel * 7) * Math.cos(fase); // hoogstens 17° in plaats van 10°
        staart.style.transform = "rotate(" + hoek.toFixed(2) + "deg)";
        // terug aan CSS geven zodra hij bedaard is én net door -10° gaat
        var rond = Math.floor(fase / (2 * Math.PI)) > Math.floor(vorigeFase / (2 * Math.PI));
        klaarMetStaart = false;
        if (geluk < .3 && kwispel < .08 && rond) {
          staartBezig = false; kwispel = 0;
          staart.style.transform = "";
          logo.classList.remove("kwispelt");
          klaarMetStaart = true;
        }
      }
      logo.style.setProperty("--roze", Math.min(1, geluk / 8).toFixed(2));

      // oog gaat pas weer open als je even gestopt bent met aaien
      var rust = t - laatsteAai > 650 && geluk < 1.2;
      var stil = Math.abs(y) < .05 && Math.abs(vy) < 1 && Math.abs(rek) < .002 &&
                 Math.abs(vrek) < .02 && Math.abs(kant) < .05 && Math.abs(vkant) < .2;
      if (rust) logo.classList.remove("geaaid");
      if (rust && stil && geluk < .05 && klaarMetStaart) {
        lijf.style.translate = lijf.style.rotate = lijf.style.scale = "";
        geluk = 0; loopt = false; return;
      }
      requestAnimationFrame(lus);
    }
    function start() {
      if (loopt) return;
      loopt = true; vorige = 0;
      eenheid = logo.offsetWidth / 100;
      requestAnimationFrame(lus);
    }

    logo.addEventListener("pointerdown", aai);
    logo.addEventListener("contextmenu", function (e) { e.preventDefault(); });
  }
})();

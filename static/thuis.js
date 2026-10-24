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

     Blijf je echt razendsnel tikken, dan loopt de druk op: de vis zwelt op
     als een kogelvis, begint te trillen en ontploft. Na zo'n zeven tellen
     schiet hij vanuit de ruit weer tevoorschijn.

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
    var druk = 0, ontploft = false;  // druk: +1 per tik, loopt weg als je trager tikt
    var KNAL = 24;                   // bij deze druk ontploft hij (~3 tellen spammen)
    var TERUG_NA = 7000;             // en zoveel ms later is hij terug

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
      if (e.button > 0 || ontploft) return;
      druk += 1;
      if (druk >= KNAL) { ontplof(); return; }
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
      if (ontploft) { loopt = false; return; }
      var dt = vorige ? Math.min(.04, (t - vorige) / 1000) : 0;
      vorige = t;
      druk = Math.max(0, druk - dt * 2.2);

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
      // de druk loopt op: opzwellen als een kogelvis, en steeds harder trillen
      var opblaas = Math.max(0, Math.min(1, (druk - 8) / (KNAL - 8)));
      var tril = rustig ? 0 : opblaas * opblaas * 3;
      sx *= 1 + .65 * opblaas; sy *= 1 + .65 * opblaas;
      var tx = (Math.random() - .5) * 2 * tril, ty = (Math.random() - .5) * 2 * tril;
      lijf.style.translate = (tx * eenheid).toFixed(2) + "px " + ((y + ty) * eenheid).toFixed(2) + "px";
      lijf.style.rotate = (kant + dans + (Math.random() - .5) * tril * 3).toFixed(2) + "deg";
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
      var stil = druk < .05 && Math.abs(y) < .05 && Math.abs(vy) < 1 && Math.abs(rek) < .002 &&
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

    /* ------------------------------------------------- de knal -- */
    var STAART = '<svg viewBox="58 33 24 34"><path fill="#fff" d="M61 50 76.5 36.5c3-2.3 4.5-1.3 3.3 1.7L73.2 50l6.6 11.8c1.2 3-.3 4-3.3 1.7z"/></svg>';

    function stuk(cx, cy, b, opties) {
      var el = document.createElement("span");
      el.className = "vis-stuk " + (opties.klasse || "");
      if (opties.html) el.innerHTML = opties.html;
      var maat = opties.maat;
      el.style.width = maat + "px"; el.style.height = (opties.hoogte || maat) + "px";
      el.style.left = (cx - maat / 2) + "px"; el.style.top = (cy - (opties.hoogte || maat) / 2) + "px";
      if (opties.vorm) el.style.clipPath = opties.vorm;
      logo.appendChild(el);
      var hoek = opties.hoek, ver = opties.ver * b;
      var dx = Math.cos(hoek) * ver, dy = Math.sin(hoek) * ver;
      var val = (25 + Math.random() * 35) * b;          // zwaartekracht
      var draai = (Math.random() - .5) * 900;
      // Per fase een eigen versnelling: wegschieten, even hangen, vallen en vervagen.
      var anim = el.animate([
        { transform: "translate(0,0) rotate(0deg) scale(.6)", opacity: 1, easing: "cubic-bezier(.15,.8,.4,1)" },
        { transform: "translate(" + dx * .75 + "px," + (dy * .75 - 6 * b) + "px) rotate(" + draai * .45 + "deg) scale(1)", opacity: 1, offset: .35, easing: "ease-in-out" },
        { transform: "translate(" + dx * .9 + "px," + (dy * .9 + val * .4) + "px) rotate(" + draai * .75 + "deg) scale(1)", opacity: 1, offset: .7, easing: "ease-in" },
        { transform: "translate(" + dx + "px," + (dy + val) + "px) rotate(" + draai + "deg) scale(.85)", opacity: 0 }
      ], { duration: 1300 + Math.random() * 600 });
      anim.onfinish = function () { el.remove(); };
    }

    function effect(klasse, cx, cy, maat, duur) {
      var el = document.createElement("span");
      el.className = klasse;
      el.style.width = el.style.height = maat + "px";
      el.style.left = (cx - maat / 2) + "px"; el.style.top = (cy - maat / 2) + "px";
      logo.appendChild(el);
      setTimeout(function () { el.remove(); }, duur);
    }

    function ontplof() {
      ontploft = true;
      var lr = logo.getBoundingClientRect(), vr = lijf.getBoundingClientRect();
      var cx = vr.left - lr.left + vr.width * .48, cy = vr.top - lr.top + vr.height * .5;
      var b = logo.offsetWidth / 100;
      // alles van het aaien terugzetten
      y = vy = rek = vrek = kant = vkant = 0; geluk = 0; druk = 0;
      staartBezig = false; kwispel = 0;
      if (staart) staart.style.transform = "";
      lijf.style.translate = lijf.style.rotate = lijf.style.scale = "";
      logo.classList.remove("geaaid", "kwispelt", "terug");
      logo.style.setProperty("--roze", "0");
      logo.classList.add("ontploft");
      if (navigator.vibrate) { try { navigator.vibrate([30, 40, 110]); } catch (x) {} }
      if (!rustig) {
        effect("vis-flits", cx, cy, 44 * b, 550);
        effect("vis-golf", cx, cy, 34 * b, 750);
        var stukken = 28;
        for (var i = 0; i < stukken; i++) {
          var punten = [], n = 3 + Math.floor(Math.random() * 2);
          for (var k = 0; k < n; k++) {
            var h = (k / n) * 2 * Math.PI + Math.random() * .8;
            punten.push((50 + Math.cos(h) * (30 + Math.random() * 20)).toFixed(0) + "% " +
                        (50 + Math.sin(h) * (30 + Math.random() * 20)).toFixed(0) + "%");
          }
          stuk(cx + (Math.random() - .5) * 20 * b, cy + (Math.random() - .5) * 10 * b, b, {
            maat: (5 + Math.random() * 10) * b, vorm: "polygon(" + punten.join(",") + ")",
            hoek: (i / stukken) * 2 * Math.PI + Math.random() * .5, ver: 22 + Math.random() * 32
          });
        }
        // twee herkenbare stukken: de staart en het oog
        stuk(cx + 16 * b, cy, b, { klasse: "vis-stuk-staart", html: STAART, maat: 14 * b, hoogte: 20 * b,
                                   hoek: -Math.PI / 6 + Math.random() * .4, ver: 40 });
        stuk(cx - 10 * b, cy - 1 * b, b, { klasse: "vis-stuk-oog", maat: 7 * b,
                                          hoek: -Math.PI * .85 + Math.random() * .3, ver: 36 });
        logo.animate([{ translate: "0 0" }, { translate: "-6px 4px" }, { translate: "5px -3px" },
                      { translate: "-3px 2px" }, { translate: "0 0" }], { duration: 380, easing: "ease-out" });
      }
      setTimeout(kerOmTerug, TERUG_NA);
    }

    /* Terug: hij schiet van achter de ruit naar voren, met een klein golfje. */
    function kerOmTerug() {
      logo.classList.remove("ontploft");
      logo.classList.add("terug");
      if (!rustig) {
        var b = logo.offsetWidth / 100;
        setTimeout(function () { effect("vis-golf klein", 46 * b, 50 * b, 26 * b, 700); }, 380);
      }
      setTimeout(function () { logo.classList.remove("terug"); ontploft = false; }, 1150);
    }

    logo.addEventListener("pointerdown", aai);
    logo.addEventListener("contextmenu", function (e) { e.preventDefault(); });
    /* iPhone trekt zich niet altijd iets aan van touch-action: manipulation,
       en zoomt dan bij snel tikken in en uit (dubbeltik). Het einde van elke
       tik afvangen houdt dat tegen; het aaien zelf loopt via pointerdown. */
    logo.addEventListener("touchend", function (e) { e.preventDefault(); }, { passive: false });
  }
})();

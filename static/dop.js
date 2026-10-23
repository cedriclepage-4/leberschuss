/* Kroonkurken (de "dop" van een speler).
 *
 * Elke speler heeft een eigen kroonkurk als avatar. Een ontwerp is een klein
 * object met keuzes (kleur, rand, patroon, embleem, afwerking); dit bestand
 * tekent daar een SVG van.
 *
 * LET OP: dop.py in de hoofdmap is een letterlijke vertaling van dit bestand.
 * De server tekent daarmee de afbeeldingen die je overal op de site ziet; de
 * editor gebruikt dit script om meteen te tonen wat je kiest. Pas je hier iets
 * aan, pas dan ook dop.py aan: test_dop.py controleert dat beide exact
 * dezelfde SVG maken.
 */
(function (wereld) {
  "use strict";

  /* ---------------------------------------------------------- keuzes -- */
  var KLEUREN = [
    ["wit", "#f8fafc", "Wit"], ["creme", "#f1e4c3", "Crème"], ["zilver", "#c0c6cc", "Zilver"],
    ["goud", "#d4a72c", "Goud"], ["brons", "#b06d34", "Brons"], ["geel", "#facc15", "Geel"],
    ["oranje", "#f97316", "Oranje"], ["rood", "#dc2626", "Rood"], ["bordeaux", "#7f1d1d", "Bordeaux"],
    ["roze", "#ec4899", "Roze"], ["paars", "#7c3aed", "Paars"], ["blauw", "#1d4ed8", "Blauw"],
    ["hemel", "#38bdf8", "Hemelsblauw"], ["turkoois", "#14b8a6", "Turkoois"], ["groen", "#16a34a", "Groen"],
    ["olijf", "#65702c", "Olijf"], ["bruin", "#6b4226", "Bruin"], ["zwart", "#18181b", "Zwart"]
  ];
  var RANDEN = [["klassiek", "Klassiek"], ["fijn", "Fijn"], ["golf", "Golf"], ["kartel", "Kartel"], ["glad", "Glad"]];
  var PATRONEN = [["geen", "Geen"], ["strepen", "Strepen"], ["ringen", "Ringen"], ["stralen", "Stralen"],
    ["stippen", "Stippen"], ["ruit", "Dambord"], ["half", "Half"], ["band", "Band"], ["kruis", "Kruis"],
    ["golven", "Golven"]];
  var EMBLEMEN = [["geen", "Geen"], ["ster", "Ster"], ["hart", "Hart"], ["bliksem", "Bliksem"],
    ["kroon", "Kroon"], ["vis", "Vis"], ["klaver", "Klaver"], ["vlam", "Vlam"], ["diamant", "Diamant"],
    ["maan", "Maan"], ["pint", "Pint"], ["nummer", "Nummer"]];
  var AFWERKINGEN = [["mat", "Mat"], ["glans", "Glans"], ["metaal", "Metaal"], ["glitter", "Glitter"]];

  /* Iedereen begint met deze: een gewone, zilveren kroonkurk. */
  var BASIS = { kleur: "zilver", rand: "klassiek", randkleur: "zelfde", patroon: "geen",
    patroonkleur: "wit", embleem: "geen", embleemkleur: "zwart", nummer: 7, afwerking: "metaal" };

  function sleutels(lijst) { return lijst.map(function (x) { return x[0]; }); }
  var TOEGELATEN = {
    kleur: sleutels(KLEUREN), rand: sleutels(RANDEN), randkleur: ["zelfde"].concat(sleutels(KLEUREN)),
    patroon: sleutels(PATRONEN), patroonkleur: sleutels(KLEUREN), embleem: sleutels(EMBLEMEN),
    embleemkleur: sleutels(KLEUREN), afwerking: sleutels(AFWERKINGEN)
  };
  var HEX = {};
  KLEUREN.forEach(function (k) { HEX[k[0]] = k[1]; });

  /* Alles wat niet klopt, wordt de basiswaarde. Zo kan een kapot of oud
     ontwerp nooit een fout geven. */
  function normaliseer(o) {
    var uit = {};
    o = (o && typeof o === "object") ? o : {};
    Object.keys(BASIS).forEach(function (k) {
      if (k === "nummer") {
        var n = parseInt(o.nummer, 10);
        uit.nummer = (isNaN(n) || n < 0 || n > 99) ? BASIS.nummer : n;
      } else {
        uit[k] = TOEGELATEN[k].indexOf(o[k]) >= 0 ? o[k] : BASIS[k];
      }
    });
    return uit;
  }

  /* ------------------------------------------------------- hulpjes -- */
  /* Getallen altijd op dezelfde manier afronden, zodat Python en JavaScript
     letter voor letter hetzelfde schrijven. */
  function n(x) {
    var r = Math.floor(x * 100 + 0.5) / 100;
    return r === 0 ? "0" : String(r);
  }
  function hex2(v) {
    var s = Math.max(0, Math.min(255, Math.floor(v + 0.5))).toString(16);
    return s.length < 2 ? "0" + s : s;
  }
  /* Twee kleuren mengen: t = 0 geeft a, t = 1 geeft b. */
  function meng(a, b, t) {
    var uit = "#";
    for (var i = 1; i < 7; i += 2) {
      var x = parseInt(a.substr(i, 2), 16), y = parseInt(b.substr(i, 2), 16);
      uit += hex2(x + (y - x) * t);
    }
    return uit;
  }
  /* Is een kleur licht? Dan krijgt wat erop staat een donkere rand. */
  function helder(c) {
    var r = parseInt(c.substr(1, 2), 16), g = parseInt(c.substr(3, 2), 16), b = parseInt(c.substr(5, 2), 16);
    return (r * 299 + g * 587 + b * 114) / 1000 > 150;
  }

  /* ------------------------------------------------------- de rand -- */
  /* De gekartelde rand rond het midden; de klassieke kroonkurk heeft er 21. */
  function randPad(stijl) {
    var R = 48.5, d = "";
    if (stijl === "glad") {
      return "M2 50a48 48 0 1 0 96 0a48 48 0 1 0-96 0z";
    }
    if (stijl === "kartel") {
      var t = 24, a = 3.4;
      for (var i = 0; i < t * 2; i++) {
        var hk = i * Math.PI / t, r = (i % 2 === 0) ? R : R - a;
        d += (i ? "L" : "M") + n(50 + r * Math.cos(hk)) + " " + n(50 + r * Math.sin(hk));
      }
      return d + "z";
    }
    var tanden = stijl === "fijn" ? 36 : stijl === "golf" ? 10 : 21;
    var amp = stijl === "fijn" ? 1.8 : stijl === "golf" ? 3.2 : 3;
    var stap = stijl === "golf" ? 12 : 6, aantal = tanden * stap;
    for (var j = 0; j < aantal; j++) {
      var h = j * 2 * Math.PI / aantal;
      var rr = R - amp * (1 - Math.cos(tanden * h)) / 2;
      d += (j ? "L" : "M") + n(50 + rr * Math.cos(h)) + " " + n(50 + rr * Math.sin(h));
    }
    return d + "z";
  }

  /* ---------------------------------------------------- patronen -- */
  function patroon(soort, kleur) {
    var s = "", i, j;
    if (soort === "strepen") {
      s = '<g transform="rotate(-35 50 50)">';
      for (i = -10; i < 110; i += 11) s += '<rect x="' + i + '" y="-10" width="5" height="120"/>';
      return '<g fill="' + kleur + '">' + s + "</g></g>";
    }
    if (soort === "ringen") {
      return '<g fill="none" stroke="' + kleur + '" stroke-width="3.6">' +
        '<circle cx="50" cy="50" r="31"/><circle cx="50" cy="50" r="23"/><circle cx="50" cy="50" r="15"/></g>';
    }
    if (soort === "stralen") {
      var d = "";
      for (i = 0; i < 12; i++) {
        var a1 = (i * 30 - 7.5) * Math.PI / 180, a2 = (i * 30 + 7.5) * Math.PI / 180;
        d += "M50 50L" + n(50 + 45 * Math.cos(a1)) + " " + n(50 + 45 * Math.sin(a1)) +
             "L" + n(50 + 45 * Math.cos(a2)) + " " + n(50 + 45 * Math.sin(a2)) + "z";
      }
      return '<path fill="' + kleur + '" d="' + d + '"/>';
    }
    if (soort === "stippen") {
      for (j = 0; j < 10; j++) {
        var y = 8 + j * 9.2;
        for (i = 0; i < 11; i++) {
          var x = 5 + i * 10 + (j % 2) * 5;
          if ((x - 50) * (x - 50) + (y - 50) * (y - 50) < 41 * 41) {
            s += '<circle cx="' + n(x) + '" cy="' + n(y) + '" r="2.4"/>';
          }
        }
      }
      return '<g fill="' + kleur + '">' + s + "</g>";
    }
    if (soort === "ruit") {
      for (j = 0; j < 11; j++) {
        for (i = 0; i < 11; i++) {
          if ((i + j) % 2 === 0) s += '<rect x="' + (i * 10 - 5) + '" y="' + (j * 10 - 5) + '" width="10" height="10"/>';
        }
      }
      return '<g fill="' + kleur + '" transform="rotate(45 50 50)">' + s + "</g>";
    }
    if (soort === "half") {
      return '<rect fill="' + kleur + '" x="50" y="-20" width="70" height="140" transform="rotate(35 50 50)"/>';
    }
    if (soort === "band") {
      return '<g fill="' + kleur + '" transform="rotate(-20 50 50)"><rect x="-10" y="41" width="120" height="18"/>' +
        '<rect x="-10" y="35" width="120" height="2.6"/><rect x="-10" y="62.4" width="120" height="2.6"/></g>';
    }
    if (soort === "kruis") {
      return '<g fill="' + kleur + '"><rect x="43" y="-5" width="14" height="110"/><rect x="-5" y="43" width="110" height="14"/></g>';
    }
    if (soort === "golven") {
      for (j = 0; j < 4; j++) {
        var yy = 23 + j * 18;
        s += "M0 " + yy;
        for (i = 0; i < 5; i++) s += "q5 -5 10 0t10 0";
      }
      return '<path fill="none" stroke="' + kleur + '" stroke-width="3.4" stroke-linecap="round" d="' + s + '"/>';
    }
    return "";
  }

  /* ---------------------------------------------------- emblemen -- */
  /* Getekend in een vak van 24 x 24. */
  var VORMEN = {
    hart: '<path d="M12 21s-7.5-4.6-9.6-9.3C.9 8.3 3 4.5 6.6 4.5c2.2 0 3.6 1.3 5.4 3.4 1.8-2.1 3.2-3.4 5.4-3.4 3.6 0 5.7 3.8 4.2 7.2C19.5 16.4 12 21 12 21z"/>',
    bliksem: '<path d="M14 1.5 4 13.5h6.5L8.5 22.5 20 9.5h-6.5L16 1.5z"/>',
    kroon: '<path d="M2.5 7.5 7.5 12 12 4l4.5 8 5-4.5-2 12h-15z"/><path d="M4.5 21h15v1.8h-15z"/>',
    klaver: '<circle cx="12" cy="7.4" r="4.3"/><circle cx="16.6" cy="12" r="4.3"/><circle cx="12" cy="16.6" r="4.3"/><circle cx="7.4" cy="12" r="4.3"/>',
    vlam: '<path d="M12 1.5c1.2 4.2 6.5 6.4 6.5 12.5a6.5 6.5 0 0 1-13 0c0-3.4 2-5.6 3.2-7.6.2 2.2 1.2 3.4 2.4 3.6-.4-3.2-.6-5.6.9-8.5z"/>',
    diamant: '<path d="M6.5 3h11l4.5 6.2L12 21.5 2 9.2z"/>',
    maan: '<path d="M15.5 2.5a9.5 9.5 0 1 0 6 16.4A8 8 0 1 1 15.5 2.5z"/>',
    pint: '<path d="M4.5 4h11.5v16a1.5 1.5 0 0 1-1.5 1.5h-8.5a1.5 1.5 0 0 1-1.5-1.5z"/><path fill="none" stroke="currentColor" stroke-width="2.2" d="M16 8.2h2.4a1.9 1.9 0 0 1 1.9 1.9v4.4a1.9 1.9 0 0 1-1.9 1.9H16"/>'
  };
  function ster() {
    var d = "";
    for (var i = 0; i < 10; i++) {
      var h = (-90 + i * 36) * Math.PI / 180, r = (i % 2 === 0) ? 11.5 : 4.8;
      d += (i ? "L" : "M") + n(12 + r * Math.cos(h)) + " " + n(12.6 + r * Math.sin(h));
    }
    return '<path d="' + d + 'z"/>';
  }

  function embleem(o, cx, cy, schaal, basiskleur, id) {
    if (o.embleem === "geen") return "";
    var kleur = HEX[o.embleemkleur];
    var rand = helder(kleur) ? meng(kleur, "#000000", 0.62) : meng(kleur, "#ffffff", 0.6);
    if (o.embleem === "nummer") {
      var grootte = 30 * schaal / 1.55;
      return '<text x="' + n(cx) + '" y="' + n(cy + grootte * 0.36) + '" text-anchor="middle" font-size="' + n(grootte) +
        '" font-family="Arial Black,Arial,Helvetica,sans-serif" font-weight="900" fill="' + kleur +
        '" stroke="' + rand + '" stroke-width="' + n(2.6 * schaal / 1.55) + '" stroke-linejoin="round" paint-order="stroke">' + o.nummer + "</text>";
    }
    var binnen;
    if (o.embleem === "ster") binnen = ster();
    else if (o.embleem === "vis") {
      binnen = '<g transform="translate(-9.3 -7.9) scale(.4)"><path d="M24 50C33 39 52 36.5 62 43c3.5 2.2 5.5 4.8 5.5 7s-2 4.8-5.5 7C52 63.5 33 61 24 50z"/>' +
        '<path d="M61 50 76.5 36.5c3-2.3 4.5-1.3 3.3 1.7L73.2 50l6.6 11.8c1.2 3-.3 4-3.3 1.7z"/>' +
        '<circle cx="36.5" cy="48.2" r="4.6" fill="' + basiskleur + '" stroke="none"/></g>';
    } else binnen = VORMEN[o.embleem];
    return '<g transform="translate(' + n(cx - 12 * schaal) + " " + n(cy - 12 * schaal) + ") scale(" + n(schaal) +
      ')" color="' + kleur + '" fill="' + kleur + '" stroke="' + rand + '" stroke-width="' + n(2.4 / schaal) +
      '" stroke-linejoin="round" paint-order="stroke">' + binnen + "</g>";
  }

  /* Vaste glinsters (een eenvoudige rekenreeks, zodat ze overal op dezelfde
     plek staan). */
  var GLINSTERS = (function () {
    var z = 12345, uit = [];
    function volgende() { z = (z * 16807) % 2147483647; return z / 2147483647; }
    for (var i = 0; i < 46; i++) uit.push([12 + volgende() * 76, 12 + volgende() * 76, 0.35 + volgende() * 0.6]);
    return uit;
  })();

  /* ------------------------------------------------------- tekenen -- */
  /* Geeft [defs, inhoud] van één dop. p is een voorvoegsel voor de id's,
     zodat meerdere doppen op één pagina elkaar niet storen. */
  function onderdelen(o, p, emb) {
    var kleur = HEX[o.kleur];
    var rk = o.randkleur === "zelfde" ? kleur : HEX[o.randkleur];
    var defs = '<radialGradient id="' + p + 'r"><stop offset=".78" stop-color="' + meng(rk, "#ffffff", 0.18) +
      '"/><stop offset=".9" stop-color="' + rk + '"/><stop offset="1" stop-color="' + meng(rk, "#000000", 0.38) + '"/></radialGradient>' +
      '<radialGradient id="' + p + 's" cx=".42" cy=".38" r=".62"><stop offset=".55" stop-color="#000" stop-opacity="0"/>' +
      '<stop offset="1" stop-color="#000" stop-opacity=".22"/></radialGradient>' +
      '<clipPath id="' + p + 'c"><circle cx="50" cy="50" r="38.5"/></clipPath>';
    var rand = randPad(o.rand);
    var uit = '<path d="' + rand + '" fill="' + meng(rk, "#000000", 0.2) + '"/>' +
      '<path d="' + rand + '" fill="url(#' + p + 'r)"/>';

    uit += '<g clip-path="url(#' + p + 'c)"><circle cx="50" cy="50" r="38.5" fill="' + kleur + '"/>';
    if (o.patroon !== "geen") uit += patroon(o.patroon, HEX[o.patroonkleur]);
    uit += emb;
    uit += '<circle cx="50" cy="50" r="38.5" fill="url(#' + p + 's)"/>';

    if (o.afwerking === "glans") {
      uit += '<ellipse cx="38" cy="33" rx="23" ry="13" transform="rotate(-32 38 33)" fill="#fff" opacity=".26"/>' +
        '<circle cx="29" cy="28" r="3.2" fill="#fff" opacity=".55"/>';
    } else if (o.afwerking === "metaal") {
      defs += '<linearGradient id="' + p + 'm" x1="0" y1="0" x2="1" y2="1">' +
        '<stop offset="0" stop-color="#fff" stop-opacity=".45"/><stop offset=".3" stop-color="#fff" stop-opacity="0"/>' +
        '<stop offset=".52" stop-color="#fff" stop-opacity=".3"/><stop offset=".7" stop-color="#000" stop-opacity=".12"/>' +
        '<stop offset="1" stop-color="#fff" stop-opacity=".2"/></linearGradient>';
      uit += '<circle cx="50" cy="50" r="38.5" fill="url(#' + p + 'm)"/>';
    } else if (o.afwerking === "glitter") {
      GLINSTERS.forEach(function (g) {
        uit += '<circle cx="' + n(g[0]) + '" cy="' + n(g[1]) + '" r="' + n(g[2]) + '" fill="#fff" opacity=".75"/>';
      });
    }
    uit += "</g>";
    // de opstaande rand van het bovenvlak
    uit += '<circle cx="50" cy="50" r="38.5" fill="none" stroke="' + meng(rk, "#000000", 0.32) + '" stroke-width="1.6"/>' +
      '<circle cx="50" cy="50" r="37.2" fill="none" stroke="' + meng(kleur, "#ffffff", 0.3) + '" stroke-width=".6" opacity=".7"/>';
    if (o.afwerking === "metaal") {
      uit += '<path d="' + rand + '" fill="url(#' + p + 'm)" opacity=".7"/>';
    }
    return [defs, uit];
  }

  var KOP = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">';

  /* De dop van één speler. */
  function svg(ontwerp, p) {
    var o = normaliseer(ontwerp);
    p = p || "d";
    var delen = onderdelen(o, p, embleem(o, 50, 50, 1.55, HEX[o.kleur], p));
    return KOP + "<defs>" + delen[0] + "</defs>" + delen[1] + "</svg>";
  }

  /* Een teamdop: links de helft van speler 1, rechts die van speler 2, met
     een lijn in het midden. De emblemen schuiven naar het midden van hun
     helft, zodat ze niet doormidden gesneden worden. */
  function teamSvg(o1, o2, p) {
    var a = normaliseer(o1), b = normaliseer(o2);
    p = p || "t";
    var d1 = onderdelen(a, p + "a", embleem(a, 30, 50, 0.92, HEX[a.kleur], p + "a"));
    var d2 = onderdelen(b, p + "b", embleem(b, 70, 50, 0.92, HEX[b.kleur], p + "b"));
    return KOP + "<defs>" + d1[0] + d2[0] +
      '<clipPath id="' + p + 'l"><rect x="0" y="0" width="50" height="100"/></clipPath>' +
      '<clipPath id="' + p + 'h"><rect x="50" y="0" width="50" height="100"/></clipPath></defs>' +
      '<g clip-path="url(#' + p + 'l)">' + d1[1] + "</g>" +
      '<g clip-path="url(#' + p + 'h)">' + d2[1] + "</g>" +
      '<path d="M50 .8V99.2" stroke="#0b0b0b" stroke-width="2.4"/><path d="M50 12V88" stroke="#fff" stroke-opacity=".35" stroke-width=".6"/></svg>';
  }

  /* Een willekeurig ontwerp (de dobbelsteen in de editor). */
  function willekeurig() {
    function kies(l) { return l[Math.floor(Math.random() * l.length)]; }
    var o = {};
    Object.keys(TOEGELATEN).forEach(function (k) { o[k] = kies(TOEGELATEN[k]); });
    o.nummer = Math.floor(Math.random() * 100);
    if (o.patroonkleur === o.kleur) o.patroonkleur = o.kleur === "wit" ? "zwart" : "wit";
    if (o.embleemkleur === o.kleur) o.embleemkleur = o.kleur === "zwart" ? "wit" : "zwart";
    return o;
  }

  /* Zien twee ontwerpen er hetzelfde uit? Keuzes die niet getoond worden,
     tellen niet mee. (Zelfde regel als zelfde() in dop.py.) */
  function zelfde(a, b) {
    a = normaliseer(a); b = normaliseer(b);
    return Object.keys(BASIS).every(function (k) {
      if (k === "patroonkleur" && a.patroon === "geen") return true;
      if (k === "embleemkleur" && a.embleem === "geen") return true;
      if (k === "nummer" && a.embleem !== "nummer") return true;
      return a[k] === b[k];
    });
  }

  var Dop = {
    KLEUREN: KLEUREN, RANDEN: RANDEN, PATRONEN: PATRONEN, EMBLEMEN: EMBLEMEN, AFWERKINGEN: AFWERKINGEN,
    BASIS: BASIS, normaliseer: normaliseer, zelfde: zelfde, svg: svg, teamSvg: teamSvg, willekeurig: willekeurig, hex: HEX
  };
  if (typeof module !== "undefined" && module.exports) module.exports = Dop;
  else wereld.Dop = Dop;
})(this);

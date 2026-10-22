/* Beamerscherm van een toernooi.
 *
 * De pagina laadt één keer. Daarna halen we om de zoveel seconden de nieuwe
 * gegevens op (/toernooi/<id>/beamer.json). Is er iets veranderd, dan tonen
 * we dat met een animatie:
 *
 *   Stand     eerst lichten de teams op die net speelden (winst of verlies),
 *             daarna schuift elke rij naar zijn nieuwe plaats.
 *   Knockout  de winnaar van een wedstrijd vliegt naar zijn plaats in de
 *             volgende ronde, de verliezer valt af. Wie een halve finale
 *             verliest, vliegt naar de kleine finale. Na de finale volgt
 *             een feestscherm voor de kampioen.
 *
 * Het scherm volgt de fase van het toernooi (stand → knockout), tenzij je
 * zelf op een van de knoppen klikt. Met ?demo=1 verzint het zelf uitslagen.
 */
(function () {
  "use strict";

  var cfg = window.BEAMER || {};
  var rustig = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var data = JSON.parse(document.getElementById("beamer-gegevens").textContent);
  var weergave = data.fase;
  var handmatig = false;
  var bezig = false, wachtrij = null;

  var paneelStand = document.querySelector('[data-paneel="stand"]');
  var paneelKo = document.querySelector('[data-paneel="knockout"]');
  var feed = document.querySelector("[data-feed]");
  var faseLabel = document.querySelector("[data-fase-label]");
  var melding = document.querySelector("[data-melding]");

  /* ------------------------------------------------------------ hulp -- */
  function el(tag, klasse, tekst) {
    var e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (tekst != null) e.textContent = tekst;
    return e;
  }
  function wacht(ms) {
    return new Promise(function (ok) { setTimeout(ok, rustig ? Math.min(ms, 50) : ms); });
  }
  function foto(team, klasse) {
    if (team && team.foto) {
      var img = el("img", "bm-foto " + (klasse || ""));
      img.src = team.foto; img.alt = "";
      return img;
    }
    return el("span", "bm-foto bm-letter " + (klasse || ""),
              team && team.naam ? team.naam.charAt(0).toUpperCase() : "?");
  }
  function telOp(e, van, naar, duur) {
    if (rustig || van === naar) { e.textContent = naar; return; }
    var start = null;
    function stap(t) {
      if (start === null) start = t;
      var p = Math.min(1, (t - start) / duur);
      e.textContent = Math.round(van + (naar - van) * (1 - Math.pow(1 - p, 3)));
      if (p < 1) requestAnimationFrame(stap);
    }
    requestAnimationFrame(stap);
  }
  function opnieuw(e, klasse) {          // een CSS-animatie opnieuw laten afspelen
    e.classList.remove(klasse);
    void e.getBoundingClientRect();      // werkt ook voor SVG (offsetWidth niet)
    e.classList.add(klasse);
  }

  /* =========================================================== STAND == */
  var rijen = {};             // team-id → rij-element
  var rijHoogte = 60;

  function vormBollen(houder, vorm) {
    houder.textContent = "";
    vorm.forEach(function (v) { houder.appendChild(el("span", "bm-bol bm-bol-" + v, v)); });
  }

  function vulRij(rij, r) {
    rij.querySelector(".bm-pos").textContent = r.positie;
    rij.querySelector(".bm-g").textContent = r.gespeeld;
    rij.querySelector(".bm-w").textContent = r.winst;
    rij.querySelector(".bm-v").textContent = r.verlies;
    rij.querySelector(".bm-ptn").textContent = r.punten;
    vormBollen(rij.querySelector(".bm-vorm"), r.vorm);
    rij.classList.toggle("door", !!r.doorstoot);
    rij.classList.toggle("onbeslist", !!r.onbeslist);
    var pil = rij.querySelector(".bm-extra");
    pil.textContent = r.onbeslist ? "shootout" : (r.gedeeld ? "gedeelde plaats" : "");
    pil.hidden = !pil.textContent;
  }

  function maakRij(r) {
    var rij = el("div", "bm-rij");
    rij.appendChild(el("span", "bm-pos"));
    var team = el("span", "bm-team");
    team.appendChild(foto(r));
    var namen = el("span", "bm-namen");
    namen.appendChild(el("strong", "bm-naam", r.naam));
    namen.appendChild(el("small", "bm-spelers", r.spelers));
    team.appendChild(namen);
    var extra = el("span", "bm-extra"); extra.hidden = true;
    team.appendChild(extra);
    team.appendChild(el("span", "bm-uitslag"));     // "Winst +3" / "Verlies"
    rij.appendChild(team);
    ["bm-g", "bm-w", "bm-v"].forEach(function (k) { rij.appendChild(el("span", "bm-getal " + k)); });
    var ptn = el("span", "bm-ptn-vak"); ptn.appendChild(el("span", "bm-ptn"));
    rij.appendChild(ptn);
    rij.appendChild(el("span", "bm-vorm"));
    rij.appendChild(el("span", "bm-beweging"));     // ▲ 2 / ▼ 1
    vulRij(rij, r);
    return rij;
  }

  /* ---------------------------------------------------------------
     De rijen hebben een vaste minimumhoogte, zodat de namen ook achteraan
     de zaal leesbaar blijven. Passen niet alle teams op het scherm, dan
     kijken we door een "venster": een camera die traag de stand afschuift
     en bij een nieuwe uitslag naar de teams gaat die net speelden.
     --------------------------------------------------------------- */
  var MIN_RIJ_VH = 8.2;       // rij nooit lager dan dit (≈ 10 rijen in beeld)
  var camera = { offset: 0, max: 0, venster: 0, pauze: false, timer: null, generatie: 0 };

  function bouwStand(d) {
    paneelStand.textContent = "";
    rijen = {};
    if (!d.stand.length) {
      paneelStand.appendChild(el("p", "bm-leeg", "De stand verschijnt zodra het toernooi geloot is."));
      return;
    }
    var kop = el("div", "bm-rij bm-rij-kop");
    ["#", "Team", "G", "W", "V", "Ptn", "Vorm"].forEach(function (t, i) {
      kop.appendChild(el("span", ["bm-pos", "bm-team", "bm-getal", "bm-getal", "bm-getal", "bm-ptn-vak", "bm-vorm"][i], t));
    });
    paneelStand.appendChild(kop);
    var venster = el("div", "bm-venster");
    var lijst = el("div", "bm-rijen");
    venster.appendChild(lijst);
    paneelStand.appendChild(venster);
    var streep = el("div", "bm-streep");
    streep.appendChild(el("span", null, "Knockoutgrens"));
    lijst.appendChild(streep);
    d.stand.forEach(function (r) {
      var rij = maakRij(r);
      rijen[r.id] = rij;
      lijst.appendChild(rij);
    });
    camera.offset = 0;
    plaatsRijen(d, false);
    zetCamera(0, 0);
    planAutoScroll(9000);
  }

  /* Elke rij staat absoluut op zijn plaats; verhuizen = de transform wijzigen,
     de CSS-overgang doet de rest. */
  function plaatsRijen(d, animeer) {
    var lijst = paneelStand.querySelector(".bm-rijen");
    var venster = paneelStand.querySelector(".bm-venster");
    if (!lijst || !venster) return;
    var hoogte = venster.clientHeight;
    var minimum = window.innerHeight * MIN_RIJ_VH / 100;
    rijHoogte = Math.max(minimum, Math.min(window.innerHeight * .1, hoogte / Math.max(1, d.stand.length)));
    paneelStand.style.setProperty("--h", rijHoogte + "px");
    lijst.style.height = (rijHoogte * d.stand.length) + "px";
    camera.venster = hoogte;
    camera.max = Math.max(0, rijHoogte * d.stand.length - hoogte);
    d.stand.forEach(function (r, i) {
      var rij = rijen[r.id];
      if (!rij) return;
      rij.classList.toggle("glijdt", !!animeer);
      rij.style.transform = "translateY(" + (i * rijHoogte) + "px)";
    });
    var streep = lijst.querySelector(".bm-streep");
    var toon = d.status === "bracket" && d.cut < d.stand.length;
    streep.hidden = !toon;
    streep.style.transform = "translateY(" + (d.cut * rijHoogte) + "px)";
    if (camera.offset > camera.max) zetCamera(camera.max, 0);
    else randen();
  }

  /* -------------------------------------------------------- camera -- */
  function randen() {                       // vervaagde rand = er is nog meer
    var venster = paneelStand.querySelector(".bm-venster");
    if (!venster) return;
    venster.classList.toggle("meer-boven", camera.offset > 2);
    venster.classList.toggle("meer-onder", camera.offset < camera.max - 2);
  }

  function zetCamera(offset, duur) {
    var lijst = paneelStand.querySelector(".bm-rijen");
    if (!lijst) return Promise.resolve();
    offset = Math.max(0, Math.min(camera.max, offset));
    if (rustig) duur = 0;
    camera.offset = offset;
    lijst.style.transition = duur ? "transform " + duur + "ms cubic-bezier(.45, 0, .25, 1)" : "none";
    lijst.style.transform = "translateY(" + (-offset) + "px)";
    randen();
    return new Promise(function (ok) { setTimeout(ok, duur); });
  }

  /* Zorg dat de rijen van..tot (posities, vanaf 0) in beeld staan. */
  function kijkNaar(van, tot, duur) {
    if (camera.max <= 0) return Promise.resolve();
    var boven = van * rijHoogte, onder = (tot + 1) * rijHoogte;
    var marge = rijHoogte * .5;
    if (boven >= camera.offset + marge * .5 && onder <= camera.offset + camera.venster - marge * .5) {
      return Promise.resolve();             // staat al in beeld
    }
    var doel = (boven + onder) / 2 - camera.venster / 2;
    return zetCamera(doel, duur == null ? 1100 : duur);
  }

  /* Hoeveel rijen passen er tegelijk in beeld? */
  function zichtbareRijen() {
    return Math.max(1, Math.floor(camera.venster / rijHoogte));
  }

  /* Deel posities op in groepjes die samen in beeld passen. */
  function groepeer(posities) {
    var lijst = posities.slice().sort(function (a, b) { return a - b; });
    var groepen = [], per = zichtbareRijen();
    lijst.forEach(function (p) {
      var laatste = groepen[groepen.length - 1];
      if (laatste && p - laatste[0] < per - 1) laatste.push(p);
      else groepen.push([p]);
    });
    return groepen;
  }

  /* Traag op en neer schuiven: even bovenaan, traag naar onder, even
     onderaan, en vlot terug naar boven. Stopt zodra er iets gebeurt. */
  function planAutoScroll(ms) {
    clearTimeout(camera.timer);
    var generatie = ++camera.generatie;
    camera.timer = setTimeout(function () { autoScroll(generatie); }, ms);
  }
  function autoScroll(generatie) {
    if (generatie !== camera.generatie) return;
    if (camera.pauze || weergave !== "stand" || camera.max <= 0 || document.hidden) {
      planAutoScroll(3000);
      return;
    }
    var naarOnder = camera.offset < camera.max - 1;
    var duur = naarOnder ? Math.max(5000, (camera.max - camera.offset) / 30 * 1000) : 2600;
    zetCamera(naarOnder ? camera.max : 0, duur).then(function () {
      if (generatie !== camera.generatie) return;
      planAutoScroll(naarOnder ? 6000 : 9000);
    });
  }

  function zelfdeTeams(a, b) {
    if (a.stand.length !== b.stand.length) return false;
    var ids = {};
    a.stand.forEach(function (r) { ids[r.id] = true; });
    return b.stand.every(function (r) { return ids[r.id]; });
  }

  var standRonde = 0;          // zodat een late opruimbeurt een nieuwe reeks niet wist
  async function werkStandBij(oud, nieuw) {
    standRonde++;
    if (!rijen || !Object.keys(rijen).length || !zelfdeTeams(oud, nieuw)) {
      bouwStand(nieuw);
      return;
    }
    var vorig = {}, nieuwRij = {}, nieuwePlek = {};
    oud.stand.forEach(function (r) { vorig[r.id] = r; });
    nieuw.stand.forEach(function (r, i) { nieuwRij[r.id] = r; nieuwePlek[r.id] = i; });
    var gespeeld = nieuw.stand.filter(function (r) { return vorig[r.id].gespeeld !== r.gespeeld; });

    /* Even geen automatisch schuiven: de camera volgt nu de uitslagen. */
    camera.pauze = true;
    camera.generatie++;

    /* De wachtrij: één wedstrijd tegelijk, in de volgorde waarin ze gespeeld
       werden. Winnaar en verliezer van hetzelfde duel horen samen. */
    var gespeeldId = {};
    gespeeld.forEach(function (r) { gespeeldId[r.id] = r; });
    var oudeFeed = {};
    (oud.laatste || []).forEach(function (w) { oudeFeed[w.id] = true; });
    var duels = (nieuw.laatste || []).filter(function (w) { return !oudeFeed[w.id]; }).reverse()
      .map(function (w) {
        return [w.team1, w.team2].filter(function (t) { return t && gespeeldId[t.id]; })
          .map(function (t) { return gespeeldId[t.id]; });
      }).filter(function (d) { return d.length; });
    var getoond = {};
    duels.forEach(function (d) { d.forEach(function (r) { getoond[r.id] = true; }); });
    gespeeld.forEach(function (r) { if (!getoond[r.id]) duels.push([r]); });

    /* Geen uitslagen, wel een andere stand (bv. een beslissing bij gelijke
       stand)? Dan gewoon iedereen naar zijn nieuwe plaats. */
    if (!duels.length) {
      nieuw.stand.forEach(function (r) { vulRij(rijen[r.id], r); });
      plaatsRijen(nieuw, true);
      camera.pauze = false;
      planAutoScroll(7000);
      return;
    }

    /* Elke uitslag is een scène van ± 5 seconden. Zijn er veel tegelijk
       binnengekomen, dan gaat het tempo wat omhoog. */
    var scene = duels.length <= 6 ? 5000 : Math.max(2600, Math.round(30000 / duels.length));
    var huidig = {};                     // de tussenstand: per team de cijfers tot nu toe
    oud.stand.forEach(function (r) { huidig[r.id] = r; });
    var volgorde = oud.stand.map(function (r) { return r.id; });

    function ruimOp() {
      Object.keys(rijen).forEach(function (id) {
        var rij = rijen[id];
        rij.classList.remove("flits-winst", "flits-verlies");
        rij.querySelector(".bm-uitslag").className = "bm-uitslag";
        rij.querySelector(".bm-beweging").className = "bm-beweging";
      });
    }
    function toonUitslag(r) {
      var o = huidig[r.id], rij = rijen[r.id];
      var won = r.winst > o.winst;
      var label = rij.querySelector(".bm-uitslag");
      label.textContent = won ? "Winst +" + (r.punten - o.punten) : "Verlies";
      label.className = "bm-uitslag " + (won ? "is-winst" : "is-verlies");
      opnieuw(rij, won ? "flits-winst" : "flits-verlies");
      telOp(rij.querySelector(".bm-ptn"), o.punten, r.punten, 900);
      rij.querySelector(".bm-g").textContent = r.gespeeld;
      rij.querySelector(".bm-w").textContent = r.winst;
      rij.querySelector(".bm-v").textContent = r.verlies;
      vormBollen(rij.querySelector(".bm-vorm"), r.vorm);
      var laatste = rij.querySelector(".bm-vorm .bm-bol:last-child");
      if (laatste) laatste.classList.add("nieuw");
    }

    for (var k = 0; k < duels.length; k++) {
      var start = performance.now();
      var duel = duels[k], slot = k === duels.length - 1;
      ruimOp();

      /* 1. De camera naar het duel, de uitslag licht op, lichtslag op de winnaar. */
      var plek = {};
      volgorde.forEach(function (id, i) { plek[id] = i; });
      var winnaar = duel.filter(function (r) { return r.winst > huidig[r.id].winst; })[0] || duel[0];
      var plaatsen = duel.map(function (r) { return plek[r.id]; });
      var van = Math.min.apply(null, plaatsen), tot = Math.max.apply(null, plaatsen);
      if (tot - van >= zichtbareRijen()) van = tot = plek[winnaar.id];
      await kijkNaar(van, tot);
      duel.forEach(toonUitslag);
      lichtslag(rijen[winnaar.id], GOUD, 1);
      duel.forEach(function (r) { huidig[r.id] = r; });
      await wacht(1900);

      /* 2. Deze teams naar hun nieuwe plaats. Na de laatste uitslag is dat
            precies de officiële stand; daarvoor een tussenstand op punten. */
      var nieuweVolgorde = slot ? nieuw.stand.map(function (r) { return r.id; })
        : volgorde.slice().sort(function (a, b) {
            return huidig[b].punten - huidig[a].punten || nieuwePlek[a] - nieuwePlek[b];
          });
      var tussen = {
        status: nieuw.status, cut: nieuw.cut,
        stand: nieuweVolgorde.map(function (id, i) {
          if (slot) return nieuwRij[id];
          var r = Object.assign({}, huidig[id]);
          r.positie = i + 1; r.doorstoot = i < nieuw.cut;
          r.gedeeld = false; r.onbeslist = false;
          return r;
        })
      };
      tussen.stand.forEach(function (r, i) {
        var rij = rijen[r.id], pijl = rij.querySelector(".bm-beweging");
        var verschil = plek[r.id] - i;
        rij.querySelector(".bm-pos").textContent = r.positie;
        rij.classList.toggle("door", !!r.doorstoot);
        if (slot) vulRij(rij, r);
        pijl.className = "bm-beweging";
        if (verschil) {
          pijl.textContent = (verschil > 0 ? "▲ " : "▼ ") + Math.abs(verschil);
          pijl.className = "bm-beweging " + (verschil > 0 ? "omhoog" : "omlaag");
        }
      });
      plaatsRijen(tussen, true);
      volgorde = nieuweVolgorde;
      await wacht(1200);

      /* 3. De camera volgt de winnaar naar zijn nieuwe plaats. */
      await kijkNaar(nieuweVolgorde.indexOf(winnaar.id), nieuweVolgorde.indexOf(winnaar.id), 900);
      await wacht(Math.max(400, scene - (performance.now() - start)));
    }

    var deze = ++standRonde;
    setTimeout(function () { if (deze === standRonde) ruimOp(); }, rustig ? 0 : 3000);
    camera.pauze = false;
    planAutoScroll(7000);
  }

  /* ======================================================== KNOCKOUT == */
  var kaarten = {};           // game-id → kaart-element
  var BEKER = '<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M20 8h24v14a12 12 0 0 1-24 0z"/>' +
              '<path class="oor" d="M20 13h-7v3a9 9 0 0 0 9 9M44 13h7v3a9 9 0 0 1-9 9"/>' +
              '<path d="M29 33h6v10h-6z"/><path d="M22 47h20l2 9H20z"/></svg>';

  function vulKant(kant, team) {
    kant.textContent = "";
    kant.dataset.team = team ? team.id : "";
    if (!team) {
      kant.classList.add("leeg");
      kant.appendChild(el("span", "bm-kant-naam", "nog te bepalen"));
      return;
    }
    kant.classList.remove("leeg");
    kant.appendChild(foto(team, "klein"));
    var namen = el("span", "bm-kant-namen");
    namen.appendChild(el("strong", "bm-kant-naam", team.naam));
    namen.appendChild(el("small", null, team.spelers));
    kant.appendChild(namen);
  }

  function zetUitslag(kaart, g) {
    kaart.classList.toggle("gespeeld", g.gespeeld);
    kaart.querySelectorAll(".bm-kant").forEach(function (kant) {
      var id = parseInt(kant.dataset.team, 10);
      kant.classList.toggle("winnaar", g.gespeeld && id === g.winnaar);
      kant.classList.toggle("verliezer", g.gespeeld && !!id && id !== g.winnaar);
    });
  }

  function maakKaart(g, extraKlasse) {
    var kaart = el("div", "bm-game " + (extraKlasse || ""));
    kaart.dataset.id = g.id;
    [1, 2].forEach(function (slot) {
      var kant = el("div", "bm-kant");
      kant.dataset.slot = slot;
      vulKant(kant, slot === 1 ? g.team1 : g.team2);
      kaart.appendChild(kant);
    });
    zetUitslag(kaart, g);
    kaarten[g.id] = kaart;
    return kaart;
  }

  function bouwBracket(d) {
    paneelKo.textContent = "";
    kaarten = {};
    var ko = d.knockout;
    if (!ko.kolommen.length) {
      paneelKo.appendChild(el("p", "bm-leeg",
        "Het knockoutschema verschijnt zodra de bracketfase gespeeld is."));
      return;
    }
    var rij = el("div", "bm-kolommen");
    ko.kolommen.forEach(function (kolom, i) {
      var laatste = i === ko.kolommen.length - 1;
      var k = el("div", "bm-kolom" + (laatste ? " bm-kolom-finale" : ""));
      k.appendChild(el("div", "bm-kolom-titel", kolom.titel));
      var vak = el("div", "bm-kolom-vak");
      kolom.games.forEach(function (g) { vak.appendChild(maakKaart(g, laatste ? "bm-finale" : "")); });
      if (laatste && ko.kleine_finale) {
        var klein = el("div", "bm-klein");
        klein.appendChild(el("div", "bm-kolom-titel", "Kleine finale"));
        klein.appendChild(maakKaart(ko.kleine_finale, "bm-kleine-finale"));
        vak.appendChild(klein);
      }
      k.appendChild(vak);
      rij.appendChild(k);
    });
    /* De kampioen, helemaal rechts. */
    var kamp = el("div", "bm-kolom bm-kolom-kampioen");
    kamp.appendChild(el("div", "bm-kolom-titel", "Kampioen"));
    var kvak = el("div", "bm-kolom-vak");
    var kaart = el("div", "bm-kampioen" + (ko.kampioen ? " heeft" : ""));
    kaart.innerHTML = BEKER;
    kaart.appendChild(el("strong", null, ko.kampioen ? ko.kampioen.naam : "?"));
    if (ko.kampioen) kaart.appendChild(el("small", null, ko.kampioen.spelers));
    kvak.appendChild(kaart);
    kamp.appendChild(kvak);
    rij.appendChild(kamp);

    paneelKo.appendChild(rij);
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "bm-lijnen");
    paneelKo.appendChild(svg);
    requestAnimationFrame(tekenLijnen);
  }

  /* Verbindingslijnen tussen de rondes, berekend uit de echte posities. */
  function tekenLijnen() {
    var svg = paneelKo.querySelector(".bm-lijnen");
    if (!svg) return;
    var basis = paneelKo.getBoundingClientRect();
    svg.setAttribute("viewBox", "0 0 " + basis.width + " " + basis.height);
    svg.textContent = "";
    var alle = [];
    data.knockout.kolommen.forEach(function (k) { alle = alle.concat(k.games); });
    alle.forEach(function (g) {
      var van = kaarten[g.id];
      var naar = g.volgende && kaarten[g.volgende];
      if (!van) return;
      var a = van.getBoundingClientRect();
      var x1 = a.right - basis.left, y1 = a.top + a.height / 2 - basis.top;
      var x2, y2;
      if (naar) {
        var kant = naar.querySelector('.bm-kant[data-slot="' + g.slot + '"]');
        var b = kant.getBoundingClientRect();
        x2 = b.left - basis.left; y2 = b.top + b.height / 2 - basis.top;
      } else {                                   // de finale → de kampioen
        var kamp = paneelKo.querySelector(".bm-kampioen");
        if (!kamp) return;
        var c = kamp.getBoundingClientRect();
        x2 = c.left - basis.left; y2 = c.top + c.height / 2 - basis.top;
      }
      var mx = x1 + (x2 - x1) / 2;
      var pad = document.createElementNS("http://www.w3.org/2000/svg", "path");
      pad.setAttribute("d", "M" + x1 + " " + y1 + " H" + mx + " V" + y2 + " H" + x2);
      pad.setAttribute("class", "bm-lijn" + (g.gespeeld ? " actief" : ""));
      svg.appendChild(pad);
    });
  }

  /* Een naamkaartje vliegt van de ene plek naar de andere. */
  function vlieg(bron, doel, team, soort) {
    return new Promise(function (klaar) {
      if (!bron || !doel || rustig) { vulKant(doel, team); klaar(); return; }
      var a = bron.getBoundingClientRect(), b = doel.getBoundingClientRect();
      var kaartje = el("div", "bm-vlieger " + (soort || ""));
      kaartje.appendChild(el("strong", null, team.naam));
      kaartje.style.left = a.left + "px"; kaartje.style.top = a.top + "px";
      kaartje.style.width = a.width + "px"; kaartje.style.height = a.height + "px";
      document.body.appendChild(kaartje);
      var dx = b.left - a.left, dy = b.top - a.top;
      var anim = kaartje.animate([
        { transform: "translate(0, 0) scale(1)", opacity: 1 },
        { transform: "translate(" + dx * .5 + "px, " + (dy * .5 - 50) + "px) scale(1.12)", opacity: 1, offset: .5 },
        { transform: "translate(" + dx + "px, " + dy + "px) scale(1)", opacity: 1 }
      ], { duration: 1500, easing: "cubic-bezier(.45, 0, .3, 1)" });
      anim.onfinish = function () {
        kaartje.remove();
        vulKant(doel, team);
        opnieuw(doel, "aangekomen");
        klaar();
      };
    });
  }

  /* --------------------------------------------- kampioen: de sprong -- */
  /* Het kaartje van de winnaar van de finale springt in een hoge boog naar
     de beker, duikt erin en verdwijnt. Daar: een schokgolf, vonken, de beker
     veert op met de naam erin, en de zwaarste lichtslag van de avond. */
  function sprongInBeker(bron, beker, team) {
    return new Promise(function (klaar) {
      function vulBeker() {
        beker.classList.add("heeft");
        beker.querySelector("strong").textContent = team.naam;
        var klein = beker.querySelector("small") || beker.appendChild(el("small"));
        klein.textContent = team.spelers || "";
      }
      if (!bron || rustig) { vulBeker(); klaar(); return; }
      var a = bron.getBoundingClientRect(), b = beker.getBoundingClientRect();
      /* Het doek voor de vonken nu al klaarzetten (tijdens de sprong), zodat
         er op het moment van de ontploffing niets meer aangemaakt moet worden. */
      var vonkDoek = maakVonkDoek(b.left + b.width / 2, b.top + b.height * .42);
      var kaartje = el("div", "bm-vlieger kampioen");
      kaartje.appendChild(el("strong", null, team.naam));
      kaartje.style.left = a.left + "px"; kaartje.style.top = a.top + "px";
      kaartje.style.width = a.width + "px"; kaartje.style.height = a.height + "px";
      document.body.appendChild(kaartje);
      var dx = (b.left + b.width / 2) - (a.left + a.width / 2);
      var dy = (b.top + b.height * .42) - (a.top + a.height / 2);
      var hoogte = Math.max(110, window.innerHeight * .26);
      /* Eén vloeiende boog (een Bézier-curve): omhoog uit de finale, over de
         top, en van bovenaf de beker in. Elke beeldje opnieuw berekend, zodat
         er nergens een knik of schok in zit. */
      var p1 = [dx * .25, -hoogte * 1.25], p2 = [dx * .8, dy - hoogte * 1.35], p3 = [dx, dy];
      function boog(u, as) {
        var v = 1 - u;
        return 3 * v * v * u * p1[as] + 3 * v * u * u * p2[as] + u * u * u * p3[as];
      }
      function zachtInUit(x) { return .5 - .5 * Math.cos(Math.PI * x); }
      function glad(x) { x = Math.max(0, Math.min(1, x)); return x * x * (3 - 2 * x); }
      var DUUR = 2400, begin = null;
      kaartje.style.willChange = "transform, opacity";
      var anim = { onfinish: null };
      requestAnimationFrame(function stap(nu) {
        if (begin === null) begin = nu;
        var t = Math.min(1, (nu - begin) / DUUR), u = zachtInUit(t);
        /* groeit traag tot boven op de boog, krimpt dan zacht de beker in */
        var schaal = (1 + .16 * Math.sin(Math.PI * glad(t / .7))) * (1 - .8 * glad((t - .55) / .45));
        var draai = 7 * Math.sin(Math.PI * t) * glad(t / .5);
        kaartje.style.transform = "translate3d(" + boog(u, 0) + "px, " + boog(u, 1) + "px, 0) scale(" +
                                  schaal + ") rotate(" + draai + "deg)";
        kaartje.style.opacity = 1 - glad((t - .82) / .18);
        if (t < 1) requestAnimationFrame(stap);
        else if (anim.onfinish) anim.onfinish();
      });
      anim.onfinish = function () {
        kaartje.remove();
        vulBeker();
        opnieuw(beker, "plof");
        ontploffing(b.left + b.width / 2, b.top + b.height * .42, vonkDoek);
        lichtslag(beker, GOUD, 1.7);
        setTimeout(klaar, 1500);           // even laten nagloeien, dan het feestscherm
      };
    });
  }

  /* Een klein doek rond het punt van de ontploffing (niet het hele scherm):
     minder om elke keer te wissen en te tekenen. Onzichtbaar tot de knal. */
  function maakVonkDoek(x, y) {
    var H = window.innerHeight;
    var B = Math.round(H * 1.0), Ho = Math.round(H * 1.25);
    var doek = el("canvas", "bm-vonken");
    var dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    doek.width = Math.round(B * dpr); doek.height = Math.round(Ho * dpr);
    doek.style.width = B + "px"; doek.style.height = Ho + "px";
    doek.style.left = Math.round(x - B / 2) + "px"; doek.style.top = Math.round(y - Ho * .4) + "px";
    doek.style.visibility = "hidden";
    document.body.appendChild(doek);
    var ctx = doek.getContext && doek.getContext("2d");
    if (ctx) ctx.scale(dpr, dpr);
    return { doek: doek, ctx: ctx, B: B, H: Ho, cx: B / 2, cy: Ho * .4 };
  }

  /* Een schokgolf en een bos vonken op dit punt van het scherm. */
  function ontploffing(x, y, v) {
    var ring = el("div", "bm-schokgolf");
    ring.style.left = x + "px"; ring.style.top = y + "px";
    document.body.appendChild(ring);
    setTimeout(function () { ring.remove(); }, 1200);

    v = v || maakVonkDoek(x, y);
    if (!v.ctx) { v.doek.remove(); return; }
    v.doek.style.visibility = "visible";
    var ctx = v.ctx, schaal = window.innerHeight / 900;
    var kleuren = ["#fde68a", "#fbbf24", "#f59e0b", "#ffffff", "#fff7d6"];
    /* per kleur een groepje: zo hoeft het doek maar 5 keer van kleur te wisselen */
    var groepen = kleuren.map(function () { return []; });
    for (var i = 0; i < 120; i++) {
      var hoek = Math.random() * Math.PI * 2, snel = (2 + Math.random() * 8) * schaal;
      groepen[i % kleuren.length].push({ x: v.cx, y: v.cy, vx: Math.cos(hoek) * snel,
        vy: Math.sin(hoek) * snel - 2.5 * schaal, r: (1.5 + Math.random() * 3.2) * schaal,
        leeft: 0, duur: 50 + Math.random() * 40 });
    }
    var vorige = null;
    requestAnimationFrame(function frame(nu) {
      /* tempo los van de beeldsnelheid: ook bij een trager scherm even snel */
      var stap = vorige === null ? 1 : Math.min(3, (nu - vorige) / 16.7);
      vorige = nu;
      ctx.clearRect(0, 0, v.B, v.H);
      var over = 0;
      groepen.forEach(function (groep, k) {
        ctx.fillStyle = kleuren[k];
        groep.forEach(function (s) {
          if (s.leeft >= s.duur) return;
          s.vy += .18 * schaal * stap; s.vx *= Math.pow(.975, stap); s.vy *= Math.pow(.975, stap);
          s.x += s.vx * stap; s.y += s.vy * stap; s.leeft += stap;
          var rest = Math.max(0, 1 - s.leeft / s.duur);
          if (!rest) return;
          over++;
          ctx.globalAlpha = rest;
          ctx.fillRect(s.x - s.r, s.y - s.r, s.r * 2 * (.4 + .6 * rest), s.r * 2 * (.4 + .6 * rest));
        });
      });
      if (over) requestAnimationFrame(frame);
      else v.doek.remove();
    });
  }

  function alleGames(d) {
    var lijst = [];
    d.knockout.kolommen.forEach(function (k) { lijst = lijst.concat(k.games); });
    if (d.knockout.kleine_finale) lijst.push(d.knockout.kleine_finale);
    return lijst;
  }

  async function werkBracketBij(oud, nieuw) {
    var oudeIds = alleGames(oud).map(function (g) { return g.id; }).sort().join();
    var nieuweIds = alleGames(nieuw).map(function (g) { return g.id; }).sort().join();
    if (!Object.keys(kaarten).length || oudeIds !== nieuweIds) {
      bouwBracket(nieuw);
      if (!oud.knockout.kampioen && nieuw.knockout.kampioen) await feest(nieuw.knockout.kampioen);
      return;
    }
    var vorig = {};
    alleGames(oud).forEach(function (g) { vorig[g.id] = g; });
    /* Eerst de vroegste rondes, de finale als laatste. */
    var nieuweUitslagen = alleGames(nieuw).filter(function (g) {
      return g.gespeeld && !vorig[g.id].gespeeld;
    }).sort(function (a, b) {
      var ra = a.ronde === 3 ? 2.5 : a.ronde, rb = b.ronde === 3 ? 2.5 : b.ronde;
      return rb - ra || a.positie - b.positie;
    });

    for (var i = 0; i < nieuweUitslagen.length; i++) {
      var g = nieuweUitslagen[i], begin = performance.now();
      var kaart = kaarten[g.id];
      if (!kaart) continue;
      /* Teams die intussen pas binnenkwamen, eerst invullen. */
      vulKant(kaart.querySelector('[data-slot="1"]'), g.team1);
      vulKant(kaart.querySelector('[data-slot="2"]'), g.team2);
      zetUitslag(kaart, g);
      opnieuw(kaart, "net-gespeeld");
      var winnaar = g.winnaar === (g.team1 && g.team1.id) ? g.team1 : g.team2;
      var verliezer = winnaar === g.team1 ? g.team2 : g.team1;
      var bronW = kaart.querySelector('.bm-kant[data-team="' + winnaar.id + '"]');
      /* Lichtslag op de winnaar; bij de finale wat feller. Pas als de bundels
         bij de winnaar zijn, vertrekt hij naar de volgende ronde, en het licht
         reist met hem mee. */
      lichtslag(bronW || kaart, GOUD, g.ronde === 2 ? 1.35 : 1);
      await wacht(2200);
      if (g.volgende && kaarten[g.volgende]) {
        lichtVolg(kaarten[g.volgende].querySelector('[data-slot="' + g.slot + '"]'), 2600);
      }
      var bronV = verliezer && kaart.querySelector('.bm-kant[data-team="' + verliezer.id + '"]');
      var reizen = [];
      if (g.volgende && kaarten[g.volgende]) {
        reizen.push(vlieg(bronW, kaarten[g.volgende].querySelector('[data-slot="' + g.slot + '"]'),
                          winnaar, "winnaar"));
      }
      /* De finale: de winnaar springt in de beker. */
      var beker = paneelKo.querySelector(".bm-kampioen");
      if (g.ronde === 2 && beker && nieuw.knockout.kampioen) {
        lichtVolg(beker, 3800);
        reizen.push(sprongInBeker(bronW, beker, winnaar));
      }
      if (g.ronde === 4 && nieuw.knockout.kleine_finale && verliezer) {
        var kf = kaarten[nieuw.knockout.kleine_finale.id];
        if (kf) reizen.push(vlieg(bronV, kf.querySelector('[data-slot="' + g.positie + '"]'),
                                  verliezer, "verliezer"));
      }
      await Promise.all(reizen);
      /* Ook hier een scène per wedstrijd: ± 5,5 seconden (wat korter als er
         heel veel tegelijk binnenkwamen). */
      await wacht(Math.max(500, (nieuweUitslagen.length <= 6 ? 5500 : 4200) - (performance.now() - begin)));
    }
    data = nieuw;              // zodat de lijnen de nieuwe toestand volgen
    bouwBracket(nieuw);
    if (!oud.knockout.kampioen && nieuw.knockout.kampioen) await feest(nieuw.knockout.kampioen);
  }

  /* ---------------------------------------------------- feestscherm -- */
  var feestBlok = document.querySelector("[data-feest]");
  function feest(team) {
    return new Promise(function (klaar) {
      feestBlok.querySelector("[data-feest-naam]").textContent = team.naam;
      feestBlok.querySelector("[data-feest-spelers]").textContent = team.spelers || "";
      feestBlok.hidden = false;
      opnieuw(feestBlok, "toon");
      lichtslag(feestBlok.querySelector("[data-feest-naam]"), GOUD, 1.25);
      var stop = confetti(feestBlok.querySelector(".bm-confetti"));
      setTimeout(function () {
        feestBlok.classList.add("weg");
        setTimeout(function () {
          stop();
          feestBlok.hidden = true;
          feestBlok.classList.remove("toon", "weg");
          klaar();
        }, 900);
      }, rustig ? 6000 : 11000);
    });
  }

  function confetti(doek) {
    if (rustig || !doek.getContext) return function () {};
    var ctx = doek.getContext("2d");
    var B = doek.width = window.innerWidth, H = doek.height = window.innerHeight;
    var kleuren = ["#f59e0b", "#fcd34d", "#ffffff", "#d97706", "#fde68a", "#94a3b8"];
    var stukjes = [];
    function salvo(x) {
      for (var i = 0; i < 140; i++) {
        var hoek = -Math.PI / 2 + (Math.random() - .5) * 1.3;
        var v = 9 + Math.random() * 13;
        stukjes.push({ x: x, y: H + 10, vx: Math.cos(hoek) * v, vy: Math.sin(hoek) * v,
                       r: 4 + Math.random() * 6, hoek: Math.random() * 6, draai: (Math.random() - .5) * .3,
                       kleur: kleuren[i % kleuren.length], vorm: Math.random() < .3 });
      }
    }
    salvo(B * .2); salvo(B * .8);
    var tweede = setTimeout(function () { salvo(B * .5); }, 900);
    var actief = true;
    (function frame() {
      if (!actief) return;
      ctx.clearRect(0, 0, B, H);
      stukjes.forEach(function (s) {
        s.vy += .28; s.vx *= .99; s.x += s.vx; s.y += s.vy; s.hoek += s.draai;
        ctx.save(); ctx.translate(s.x, s.y); ctx.rotate(s.hoek);
        ctx.fillStyle = s.kleur;
        if (s.vorm) { ctx.beginPath(); ctx.arc(0, 0, s.r * .5, 0, 7); ctx.fill(); }
        else ctx.fillRect(-s.r / 2, -s.r / 4, s.r, s.r / 2);
        ctx.restore();
      });
      stukjes = stukjes.filter(function (s) { return s.y < H + 40; });
      requestAnimationFrame(frame);
    })();
    return function () { actief = false; clearTimeout(tweede); ctx.clearRect(0, 0, B, H); };
  }

  /* ============================================== LAATSTE UITSLAGEN == */
  function maakFeedItem(u) {
    var li = el("li", "bm-feed-item");
    li.dataset.id = u.id;
    li.appendChild(el("span", "bm-feed-label", u.label));
    var w = u.winnaar === (u.team1 && u.team1.id) ? u.team1 : u.team2;
    var v = w === u.team1 ? u.team2 : u.team1;
    var regel = el("span", "bm-feed-regel");
    regel.appendChild(el("strong", "bm-feed-winnaar", w ? w.naam : "?"));
    regel.appendChild(el("span", "bm-feed-tegen", "wint van"));
    regel.appendChild(el("span", "bm-feed-verliezer", v ? v.naam : "?"));
    li.appendChild(regel);
    return li;
  }
  function werkFeedBij(d, animeer) {
    var bestaand = {};
    feed.querySelectorAll(".bm-feed-item").forEach(function (li) { bestaand[li.dataset.id] = li; });
    var nieuw = document.createDocumentFragment();
    d.laatste.forEach(function (u) {
      var li = bestaand[u.id] || maakFeedItem(u);
      if (!bestaand[u.id] && animeer) li.classList.add("nieuw");
      nieuw.appendChild(li);
    });
    feed.textContent = "";
    feed.appendChild(nieuw);
    if (!d.laatste.length) feed.appendChild(el("li", "bm-feed-leeg", "Nog geen uitslagen."));
  }

  /* ======================================================= WEERGAVE == */
  var knoppen = document.querySelectorAll("[data-weergave]");
  function toon(wat) {
    weergave = wat;
    paneelStand.hidden = wat !== "stand";
    paneelKo.hidden = wat !== "knockout";
    knoppen.forEach(function (k) { k.setAttribute("aria-selected", k.dataset.weergave === wat); });
    if (wat === "stand") bouwStand(data); else bouwBracket(data);
  }
  knoppen.forEach(function (k) {
    k.addEventListener("click", function () { handmatig = true; toon(k.dataset.weergave); });
  });

  async function grooteMelding(tekst) {
    melding.textContent = tekst;
    melding.hidden = false;
    opnieuw(melding, "toon");
    await wacht(2600);
    melding.hidden = true;
  }

  /* ========================================================= OPHALEN == */
  async function verwerk(nieuw) {
    if (bezig) { wachtrij = nieuw; return; }
    bezig = true;
    try {
      var oud = data;
      faseLabel.textContent = nieuw.status_label;
      werkFeedBij(nieuw, true);
      /* Een animatie mag het scherm nooit laten vastlopen: duurt het langer
         dan een minuut, dan bouwen we gewoon opnieuw op met de nieuwe stand. */
      var bijgewerkt = await Promise.race([
        (weergave === "stand" ? werkStandBij(oud, nieuw) : werkBracketBij(oud, nieuw))
          .then(function () { return true; }),
        new Promise(function (ok) { setTimeout(function () { ok(false); }, 60000); })
      ]);
      data = nieuw;
      if (!bijgewerkt) toon(weergave);
      /* De bracketfase is net voorbij: na de laatste stand naar het schema. */
      if (!handmatig && nieuw.fase !== weergave) {
        await wacht(2500);
        if (nieuw.fase === "knockout") await grooteMelding("Knockout");
        toon(nieuw.fase);
      }
    } catch (e) {
      data = nieuw;                         // bij een fout: gewoon opnieuw opbouwen
      try { toon(weergave); } catch (e2) {}
    } finally {
      bezig = false;
    }
    if (wachtrij) { var volgende = wachtrij; wachtrij = null; verwerk(volgende); }
  }

  /* De ring naast "Live" loopt leeg tot de volgende keer dat we kijken. */
  var ring = document.querySelector(".bm-ring");
  var ringAnimatie = null;
  function startRing(seconden) {
    if (!ring || !ring.animate) return;
    if (ringAnimatie) ringAnimatie.cancel();
    ringAnimatie = ring.animate([{ strokeDashoffset: 0 }, { strokeDashoffset: 94.25 }],
                                { duration: seconden * 1000, easing: "linear", fill: "forwards" });
  }

  var interval = cfg.demo ? Math.min(cfg.elke || 20, 8) : (cfg.elke || 20);
  /* Vast ritme: de volgende keer wordt meteen ingepland, wat er nu ook
     gebeurt. Loopt er nog een animatie, dan wacht de nieuwe stand in de rij
     (zie verwerk). Enkel de demo wacht op zijn eigen animaties, omdat hij
     verder bouwt op wat er op het scherm staat. */
  async function haal() {
    if (!cfg.demo) setTimeout(haal, interval * 1000);
    startRing(interval);
    try {
      if (cfg.demo) {
        await verwerk(demoStap(data));
      } else {
        var antwoord = await fetch(cfg.bron, { cache: "no-store" });
        if (!antwoord.ok) throw new Error(antwoord.status);
        var nieuw = await antwoord.json();
        document.body.classList.remove("offline");
        var laatst = wachtrij || data;
        if (nieuw && nieuw.versie !== laatst.versie) verwerk(nieuw);
      }
    } catch (e) {
      document.body.classList.add("offline");       // zichtbaar aan de ring
    }
    if (cfg.demo) setTimeout(haal, interval * 1000);
  }

  /* ============================================================ DEMO == */
  /* Verzint telkens één uitslag (of een paar), om de animaties te tonen. */
  function demoStap(d) {
    var n = JSON.parse(JSON.stringify(d));
    n.versie = "demo" + Math.random();
    var games = alleGames(n).filter(function (g) { return !g.gespeeld && g.team1 && g.team2; });
    if (n.fase === "knockout" && games.length) {
      games.sort(function (a, b) {
        var ra = a.ronde === 3 ? 2.5 : a.ronde, rb = b.ronde === 3 ? 2.5 : b.ronde;
        return rb - ra || a.positie - b.positie;
      });
      var ronde = games[0].ronde;
      games.filter(function (g) { return g.ronde === ronde; }).slice(0, 2).forEach(function (g) {
        g.gespeeld = true;
        g.winnaar = Math.random() < .5 ? g.team1.id : g.team2.id;
        var w = g.winnaar === g.team1.id ? g.team1 : g.team2, v = w === g.team1 ? g.team2 : g.team1;
        alleGames(n).forEach(function (h) {
          if (h.id === g.volgende) h["team" + g.slot] = w;
        });
        if (g.ronde === 4 && n.knockout.kleine_finale) n.knockout.kleine_finale["team" + g.positie] = v;
        if (g.ronde === 2) n.knockout.kampioen = w;
        n.laatste.unshift({ id: Math.random(), label: "Demo", team1: g.team1, team2: g.team2, winnaar: g.winnaar });
      });
      n.laatste = n.laatste.slice(0, 7);
      return n;
    }
    if (n.fase === "knockout") return d;    // alles gespeeld: niets meer te verzinnen
    if (n.stand.length < 2) return n;
    /* Bracketfase: twee willekeurige paren spelen. */
    var pool = n.stand.slice().sort(function () { return Math.random() - .5; });
    for (var p = 0; p + 1 < Math.min(4, pool.length); p += 2) {
      var a = pool[p], b = pool[p + 1];
      var winA = Math.random() < .5;
      [[a, winA], [b, !winA]].forEach(function (x) {
        var r = x[0];
        r.gespeeld += 1;
        if (x[1]) { r.winst += 1; r.punten += 3; } else r.verlies += 1;
        r.vorm = r.vorm.concat([x[1] ? "W" : "V"]).slice(-5);
      });
      n.laatste.unshift({ id: Math.random(), label: "Demo", team1: a, team2: b, winnaar: (winA ? a : b).id });
    }
    n.laatste = n.laatste.slice(0, 7);
    n.stand.sort(function (x, y) { return y.punten - x.punten || y.winst - x.winst || x.naam.localeCompare(y.naam); });
    n.stand.forEach(function (r, i) { r.positie = i + 1; r.doorstoot = i < n.cut; });
    return n;
  }

  /* ============================================ VOLLEDIG SCHERM & CO == */
  var volKnop = document.querySelector("[data-volledig]");
  function wisselVolledig() {
    if (document.fullscreenElement) document.exitFullscreen();
    else if (document.documentElement.requestFullscreen) document.documentElement.requestFullscreen().catch(function () {});
  }
  volKnop.addEventListener("click", wisselVolledig);
  document.addEventListener("fullscreenchange", function () {
    document.body.classList.toggle("volledig", !!document.fullscreenElement);
    volKnop.textContent = document.fullscreenElement ? "Venster" : "Volledig scherm";
    setTimeout(herschik, 200);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "f" || e.key === "F") wisselVolledig();
    if (e.key === "s" || e.key === "S") { handmatig = true; toon("stand"); }
    if (e.key === "k" || e.key === "K") { handmatig = true; toon("knockout"); }
  });

  /* De muisaanwijzer verdwijnt na een paar seconden stilstand. */
  var muisTimer;
  document.addEventListener("mousemove", function () {
    document.body.classList.remove("geen-muis");
    clearTimeout(muisTimer);
    muisTimer = setTimeout(function () { document.body.classList.add("geen-muis"); }, 3000);
  });

  /* Het scherm van de laptop mag niet in slaap vallen. */
  async function wakker() {
    try { if (navigator.wakeLock) await navigator.wakeLock.request("screen"); } catch (e) {}
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) wakker(); });
  wakker();

  function herschik() {
    if (weergave === "stand") plaatsRijen(data, false); else tekenLijnen();
    if (rook) rook.maat(.08, .93);
  }
  var herschikTimer;
  window.addEventListener("resize", function () {
    clearTimeout(herschikTimer);
    herschikTimer = setTimeout(herschik, 120);
  });

  /* ---------------------------------------------------------- rook -- */
  var rook = (!rustig && window.Rook) ? window.Rook(document.querySelector(".bm-rook"), { sterkte: .75 }) : null;
  var GOUD = [1, .74, .28];
  /* Eén lichtslag in de zaal, gericht op dit element (zie rook.js). */
  function plekVan(element) {
    var doek = document.querySelector(".bm-rook").getBoundingClientRect();
    var r = element.getBoundingClientRect();
    return [(r.left + r.width / 2 - doek.left) / doek.width,
            1 - (r.top + r.height / 2 - doek.top) / doek.height];
  }
  function lichtVolg(element, ms) {
    if (!rook || !element) return;
    var p = plekVan(element);
    rook.volg(p[0], p[1], ms);
  }
  function lichtslag(element, kleur, kracht) {
    if (!rook || !element) return;
    var doek = document.querySelector(".bm-rook").getBoundingClientRect();
    var r = element.getBoundingClientRect();
    rook.lichtbeat((r.left + r.width / 2 - doek.left) / doek.width,
                   1 - (r.top + r.height / 2 - doek.top) / doek.height, kleur, kracht);
  }
  if (rook) {
    rook.maat(.08, .93);                 // het licht komt van het logo linksboven
    (function lus(t) {
      if (!document.hidden) rook.teken(t);
      requestAnimationFrame(lus);
    })(performance.now());
  }

  /* ---------------------------------------------------------- start -- */
  werkFeedBij(data, false);
  toon(weergave);
  startRing(interval);
  setTimeout(haal, interval * 1000);
})();

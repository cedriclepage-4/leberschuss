/* Editor voor de beamergeluiden (/admin/beamergeluiden).
 *
 * Per gebeurtenis een kaart: bestand opladen, de golfvorm met twee grepen
 * (start en einde) om een stuk uit te knippen, en de overige instellingen.
 * Alles wat je wijzigt, geldt meteen in de proefweergave (ook zonder op te
 * slaan): het beamerscherm in het kader speelt zijn geluiden af via de
 * Geluid-motor van deze pagina.
 */
(function () {
  "use strict";

  var editor = document.querySelector("[data-editor]");
  if (!editor || !window.Geluid) return;
  var basis = editor.dataset.basis;
  var proefAdres = editor.dataset.proef;
  var scherm = editor.querySelector("[data-proef-scherm]");
  var sceneTekst = editor.querySelector("[data-scene]");
  var begin = JSON.parse(document.getElementById("geluiden-gegevens").textContent);
  var kaarten = {};                     // sleutel → { el, geluid, gewijzigd, ... }

  function post(adres, gegevens) {
    var opties = { method: "POST", headers: {} };
    if (gegevens instanceof FormData) opties.body = gegevens;
    else { opties.body = JSON.stringify(gegevens || {}); opties.headers["Content-Type"] = "application/json"; }
    return fetch(adres, opties).then(function (a) {
      return a.json().catch(function () { return { ok: false, fout: "Er ging iets mis (" + a.status + ")." }; });
    });
  }
  function sec(x) { return (Math.round(x * 100) / 100).toFixed(2); }

  /* ------------------------------------------------------- kaart -- */
  function Kaart(el) {
    var k = this;
    k.el = el;
    k.sleutel = el.dataset.sleutel;
    k.geluid = null;                    // de instellingen zoals op het scherm
    k.gewijzigd = false;
    k.golf = el.querySelector("[data-golf]");
    k.doek = k.golf.querySelector("canvas");
    k.speelkop = el.querySelector("[data-speelkop]");
    k.status = el.querySelector("[data-status]");
    k.velden = {};
    el.querySelectorAll("[data-veld]").forEach(function (v) { k.velden[v.dataset.veld] = v; });

    el.querySelector("[data-bestand]").addEventListener("change", function () {
      if (this.files[0]) k.laadOp(this.files[0]);
      this.value = "";
    });
    Object.keys(k.velden).forEach(function (naam) {
      k.velden[naam].addEventListener("input", function () { k.leesVelden(); });
    });
    el.querySelector('[data-actie="fragment"]').addEventListener("click", function () {
      Geluid.speel(k.sleutel, { direct: true, altijd: true });
    });
    el.querySelector('[data-actie="actie"]').addEventListener("click", function () { speelScene(k.sleutel); });
    el.querySelector('[data-actie="opslaan"]').addEventListener("click", function () { k.bewaar(); });
    el.querySelector('[data-actie="wissen"]').addEventListener("click", function () { k.wis(); });
    k.grepen();
  }

  Kaart.prototype.duur = function () {
    var b = Geluid.buffer(this.sleutel);
    return b ? b.duration : 0;
  };

  /* Eind 0 betekent "tot het einde"; op het scherm tonen we dan de lengte. */
  Kaart.prototype.eind = function () {
    var g = this.geluid;
    return g && g.eind > g.start ? Math.min(g.eind, this.duur()) : this.duur();
  };

  Kaart.prototype.toon = function (geluid) {
    var k = this;
    k.geluid = geluid;
    k.el.classList.toggle("heeft-geluid", !!geluid);
    k.el.querySelectorAll("[data-heeft]").forEach(function (x) { x.hidden = !geluid; });
    k.el.querySelector("[data-naam]").textContent = geluid ? geluid.naam : "Nog geen geluid.";
    k.el.querySelector("[data-kies-tekst]").textContent = geluid ? "Ander bestand" : "Kies een bestand";
    k.golf.hidden = !geluid;
    if (!geluid) { Geluid.zet(k.sleutel, null); return; }
    Geluid.zet(k.sleutel, geluid);
    Geluid.laadUrl(k.sleutel, geluid.url).then(function () {
      k.vulVelden();
      k.teken();
    }).catch(function () {
      k.meld("Dit bestand kan de browser niet afspelen.", "fout");
    });
    k.vulVelden();
  };

  Kaart.prototype.vulVelden = function () {
    var g = this.geluid, v = this.velden, duur = this.duur();
    if (!g) return;
    v.aan.checked = g.aan !== false;
    v.start.value = sec(g.start || 0);
    v.eind.value = sec(this.eind());
    v.start.max = v.eind.max = duur ? sec(duur) : "";
    v.vertraging.value = g.vertraging || 0;
    v.uitfaden.value = g.uitfaden || 0;
    v.volume.value = g.volume == null ? 1 : g.volume;
    this.zetGrepen();
  };

  Kaart.prototype.leesVelden = function () {
    var g = this.geluid, v = this.velden, duur = this.duur() || 3600;
    if (!g) return;
    var start = Math.max(0, Math.min(duur, parseFloat(v.start.value) || 0));
    var eind = Math.max(0, Math.min(duur, parseFloat(v.eind.value) || duur));
    g.start = start;
    g.eind = eind > start ? eind : 0;
    g.vertraging = Math.max(0, parseInt(v.vertraging.value, 10) || 0);
    g.uitfaden = Math.max(0, parseInt(v.uitfaden.value, 10) || 0);
    g.volume = parseFloat(v.volume.value);
    g.aan = v.aan.checked;
    Geluid.zet(this.sleutel, g);
    this.zetGrepen();
    this.markeer(true);
  };

  Kaart.prototype.markeer = function (gewijzigd) {
    this.gewijzigd = gewijzigd;
    this.el.classList.toggle("gewijzigd", gewijzigd);
    if (gewijzigd) this.meld("Niet opgeslagen", "let-op");
  };

  Kaart.prototype.meld = function (tekst, soort) {
    this.status.textContent = tekst || "";
    this.status.className = "gl-status klein-tekst" + (soort ? " " + soort : "");
  };

  /* ------------------------------------------------- golfvorm -- */
  Kaart.prototype.teken = function () {
    var buf = Geluid.buffer(this.sleutel);
    var doek = this.doek;
    var dpr = window.devicePixelRatio || 1;
    var B = doek.clientWidth, H = doek.clientHeight;
    if (!buf || !B) return;
    doek.width = Math.round(B * dpr); doek.height = Math.round(H * dpr);
    var ctx = doek.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, B, H);
    var kanalen = [];
    for (var c = 0; c < Math.min(2, buf.numberOfChannels); c++) kanalen.push(buf.getChannelData(c));
    var per = Math.max(1, Math.floor(buf.length / B));
    var midden = H / 2;
    ctx.fillStyle = getComputedStyle(this.golf).getPropertyValue("--golf").trim() || "#1e293b";
    for (var x = 0; x < B; x++) {
      var hoog = 0, laag = 0, van = x * per;
      for (var i = van; i < van + per && i < buf.length; i += Math.max(1, per >> 6)) {
        var s = 0;
        for (var k = 0; k < kanalen.length; k++) s += kanalen[k][i];
        s /= kanalen.length;
        if (s > hoog) hoog = s;
        if (s < laag) laag = s;
      }
      var y1 = midden - hoog * midden * .95, y2 = midden - laag * midden * .95;
      ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
    }
    this.zetGrepen();
  };

  Kaart.prototype.zetGrepen = function () {
    var duur = this.duur();
    if (!duur || !this.geluid) return;
    var start = (this.geluid.start || 0) / duur * 100, eind = this.eind() / duur * 100;
    this.golf.style.setProperty("--start", start + "%");
    this.golf.style.setProperty("--eind", eind + "%");
  };

  /* Slepen: de dichtstbijzijnde greep volgt de muis (of de vinger). */
  Kaart.prototype.grepen = function () {
    var k = this, welke = null;
    function tijd(e) {
      var r = k.golf.getBoundingClientRect();
      return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * k.duur();
    }
    k.golf.addEventListener("pointerdown", function (e) {
      if (!k.geluid || !k.duur()) return;
      var t = tijd(e);
      welke = e.target.dataset.greep ||
        (Math.abs(t - (k.geluid.start || 0)) <= Math.abs(t - k.eind()) ? "start" : "eind");
      k.golf.setPointerCapture(e.pointerId);
      verplaats(e);
      e.preventDefault();
    });
    function verplaats(e) {
      if (!welke) return;
      var t = tijd(e), v = k.velden;
      if (welke === "start") v.start.value = sec(Math.min(t, k.eind() - .01));
      else v.eind.value = sec(Math.max(t, (k.geluid.start || 0) + .01));
      k.leesVelden();
    }
    k.golf.addEventListener("pointermove", verplaats);
    k.golf.addEventListener("pointerup", function () { welke = null; });
    k.golf.addEventListener("pointercancel", function () { welke = null; });
  };

  /* De speelkop loopt mee met elk geluid dat afspeelt, ook als het
     proefscherm het afspeelt. Zo zie je wanneer het precies vertrekt. */
  Kaart.prototype.loop = function (r) {
    var k = this, duur = k.duur();
    if (!duur) return;
    var generatie = k.generatie = (k.generatie || 0) + 1;
    k.speelkop.hidden = false;
    (function stap() {
      if (generatie !== k.generatie) return;
      var nu = Geluid.nu() - r.wanneer;
      if (nu > r.duur) { k.speelkop.hidden = true; return; }
      k.speelkop.style.left = ((r.start + Math.max(0, nu)) / duur * 100) + "%";
      k.speelkop.classList.toggle("wacht", nu < 0);
      requestAnimationFrame(stap);
    })();
  };

  /* ----------------------------------------------------- opslaan -- */
  Kaart.prototype.laadOp = function (bestand) {
    var k = this, data = new FormData();
    data.append("bestand", bestand);
    k.meld("Bezig met opladen…");
    post(basis + "/" + k.sleutel + "/bestand", data).then(function (a) {
      if (!a.ok) { k.meld(a.fout, "fout"); return; }
      k.toon(a.geluid);
      k.markeer(false);
      k.meld("Opgeladen. Sleep de grepen om het stuk te kiezen.", "ok");
    }).catch(function () { k.meld("Opladen lukte niet.", "fout"); });
  };

  Kaart.prototype.bewaar = function () {
    var k = this;
    k.leesVelden();
    post(basis + "/" + k.sleutel, k.geluid).then(function (a) {
      if (!a.ok) { k.meld(a.fout, "fout"); return; }
      k.geluid = a.geluid;
      Geluid.zet(k.sleutel, a.geluid);
      k.vulVelden();
      k.markeer(false);
      k.meld("Opgeslagen", "ok");
    }).catch(function () { k.meld("Opslaan lukte niet.", "fout"); });
  };

  Kaart.prototype.wis = function () {
    var k = this;
    if (!confirm("Het geluid van deze gebeurtenis verwijderen?")) return;
    post(basis + "/" + k.sleutel + "/wissen").then(function (a) {
      if (!a.ok) { k.meld(a.fout, "fout"); return; }
      Geluid.stop(k.sleutel);
      k.toon(null);
      k.markeer(false);
      k.meld("");
    });
  };

  /* -------------------------------------------------- proefscherm -- */
  var sceneNamen = {};
  editor.querySelectorAll(".gl-kaart").forEach(function (el) {
    sceneNamen[el.dataset.sleutel] = el.querySelector("h3").textContent;
  });
  function speelScene(scene) {
    Geluid.ontgrendel();
    Geluid.stopAlles();
    sceneTekst.innerHTML = "";
    sceneTekst.appendChild(document.createTextNode("Nu: "));
    var b = document.createElement("strong");
    b.textContent = scene === "alles" ? "heel het toernooi" : sceneNamen[scene];
    sceneTekst.appendChild(b);
    sceneTekst.appendChild(document.createTextNode(" …"));
    scherm.src = proefAdres + "?scene=" + encodeURIComponent(scene) + "&t=" + Date.now();
    scherm.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  window.addEventListener("message", function (e) {
    if (e.origin !== location.origin || !e.data || e.data.beamerProef !== "klaar") return;
    sceneTekst.textContent = "Klaar. Pas iets aan en kijk opnieuw.";
  });
  editor.querySelector("[data-alles]").addEventListener("click", function () { speelScene("alles"); });

  var volumeTimer;
  editor.querySelector("[data-hoofdvolume]").addEventListener("input", function () {
    var v = parseFloat(this.value);
    Geluid.volume(v);
    clearTimeout(volumeTimer);
    volumeTimer = setTimeout(function () { post(basis + "/volume", { volume: v }); }, 400);
  });

  /* De speelkop bij elk geluid dat start, van waar het ook komt. */
  var speel = Geluid.speel;
  Geluid.speel = function (sleutel, opties) {
    var r = speel(sleutel, opties);
    if (r && kaarten[sleutel]) kaarten[sleutel].loop(r);
    return r;
  };

  /* -------------------------------------------------------- start -- */
  Geluid.volume(begin.volume);
  editor.querySelectorAll(".gl-kaart").forEach(function (el) {
    var k = new Kaart(el);
    kaarten[k.sleutel] = k;
    k.toon(begin.geluiden[k.sleutel] || null);
  });
  var tekenTimer;
  window.addEventListener("resize", function () {
    clearTimeout(tekenTimer);
    tekenTimer = setTimeout(function () {
      Object.keys(kaarten).forEach(function (s) { kaarten[s].teken(); });
    }, 150);
  });
  window.addEventListener("beforeunload", function (e) {
    var open = Object.keys(kaarten).some(function (s) { return kaarten[s].gewijzigd; });
    if (open) { e.preventDefault(); e.returnValue = ""; }
  });
})();

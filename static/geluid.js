/* Geluiden bij het beamerscherm.
 *
 * Per gebeurtenis (sleutel) één geluid: een bestand, en het stuk dat we eruit
 * spelen (start/eind, in seconden), een vertraging na de gebeurtenis, een
 * volume en een uitfade. We spelen af via de Web Audio API: zo begint het
 * geluid op de milliseconde en kan je er netjes een stuk uit knippen.
 *
 * Browsers laten pas geluid toe na een klik of toets op de pagina. Daarom
 * proberen we bij elke klik of toets de geluidsmotor wakker te maken.
 *
 *   Geluid.laad(geluiden, volume)   alles ophalen (geluiden: sleutel → instellingen + url)
 *   Geluid.speel(sleutel, opties)   afspelen (opties.direct: zonder vertraging)
 *   Geluid.zet(sleutel, inst)       instellingen wijzigen zonder op te slaan (editor)
 */
(function () {
  "use strict";

  var Ctx = window.AudioContext || window.webkitAudioContext;
  var ctx = null, hoofd = null;
  var instellingen = {}, buffers = {}, adressen = {}, spelend = {};
  var volume = 1, stil = false;
  var luisteraars = [];

  /* "Geluid uit" op de beamer geldt niet voor de editor zelf. */
  if (!window.GELUID_EDITOR) {
    try { stil = localStorage.getItem("beamer-geluid-stil") === "1"; } catch (e) {}
  }

  function context() {
    if (!ctx && Ctx) {
      ctx = new Ctx();
      hoofd = ctx.createGain();
      hoofd.connect(ctx.destination);
      zetHoofd();
      ctx.onstatechange = meld;
    }
    return ctx;
  }
  function zetHoofd() {
    if (hoofd) hoofd.gain.value = stil ? 0 : volume;
  }
  function meld() { luisteraars.forEach(function (f) { try { f(); } catch (e) {} }); }

  function ontgrendel() {
    var c = context();
    if (c && c.state === "suspended") c.resume().then(meld, function () {});
  }
  document.addEventListener("pointerdown", ontgrendel, true);
  document.addEventListener("keydown", ontgrendel, true);

  /* Eén bestand ophalen en ontcijferen; geeft de AudioBuffer terug. */
  function laadUrl(sleutel, url) {
    var c = context();
    if (!c || !url) return Promise.resolve(null);
    if (adressen[sleutel] === url && buffers[sleutel]) return Promise.resolve(buffers[sleutel]);
    adressen[sleutel] = url;
    return fetch(url).then(function (a) {
      if (!a.ok) throw new Error(a.status);
      return a.arrayBuffer();
    }).then(function (data) {
      return new Promise(function (ok, nok) { c.decodeAudioData(data, ok, nok); });
    }).then(function (buffer) {
      if (adressen[sleutel] === url) buffers[sleutel] = buffer;
      return buffer;
    });
  }

  function laad(geluiden, vol) {
    if (vol != null) zetVolume(vol);
    var klaar = Object.keys(geluiden || {}).map(function (sleutel) {
      instellingen[sleutel] = geluiden[sleutel];
      return laadUrl(sleutel, geluiden[sleutel].url).catch(function () { return null; });
    });
    return Promise.all(klaar);
  }

  function zet(sleutel, inst) {
    if (!inst) { delete instellingen[sleutel]; delete buffers[sleutel]; delete adressen[sleutel]; return; }
    instellingen[sleutel] = Object.assign({}, instellingen[sleutel], inst);
  }

  function stop(sleutel) {
    var bron = spelend[sleutel];
    if (bron) { try { bron.stop(); } catch (e) {} delete spelend[sleutel]; }
  }
  function stopAlles() { Object.keys(spelend).forEach(stop); }

  /* Het stuk dat we spelen: [start, duur] binnen het bestand. */
  function fragment(sleutel) {
    var s = instellingen[sleutel], buf = buffers[sleutel];
    if (!s || !buf) return null;
    var start = Math.max(0, Math.min(+s.start || 0, buf.duration));
    var eind = +s.eind > start ? Math.min(+s.eind, buf.duration) : buf.duration;
    return { start: start, duur: Math.max(0, eind - start) };
  }

  function speel(sleutel, opties) {
    opties = opties || {};
    var s = instellingen[sleutel], buf = buffers[sleutel], c = context();
    if (!c || !s || !buf || (s.aan === false && !opties.altijd)) return null;
    if (stil && !opties.altijd) return null;
    if (c.state === "suspended") c.resume().catch(function () {});
    var f = fragment(sleutel);
    if (!f || f.duur <= 0) return null;
    stop(sleutel);

    var wanneer = c.currentTime + .01 + (opties.direct ? 0 : (+s.vertraging || 0) / 1000);
    var vol = s.volume == null ? 1 : +s.volume;
    var gain = c.createGain();
    gain.connect(hoofd);
    /* Heel korte infade (geen klik), en de uitfade aan het einde. */
    var aan = Math.min(.012, f.duur / 4);
    var uit = Math.min((+s.uitfaden || 0) / 1000, f.duur - aan);
    gain.gain.setValueAtTime(0, wanneer);
    gain.gain.linearRampToValueAtTime(vol, wanneer + aan);
    if (uit > 0) {
      gain.gain.setValueAtTime(vol, wanneer + f.duur - uit);
      gain.gain.linearRampToValueAtTime(0, wanneer + f.duur);
    }
    var bron = c.createBufferSource();
    bron.buffer = buf;
    bron.connect(gain);
    bron.start(wanneer, f.start, f.duur);
    spelend[sleutel] = bron;
    bron.onended = function () {
      if (spelend[sleutel] === bron) delete spelend[sleutel];
      gain.disconnect();
    };
    if (window.BEAMER_GELUID_LOG) console.log("[geluid]", sleutel, "over", Math.round((wanneer - c.currentTime) * 1000), "ms");
    return { wanneer: wanneer, start: f.start, duur: f.duur };
  }

  function zetVolume(v) { volume = Math.max(0, Math.min(1, +v || 0)); zetHoofd(); }
  function zetStil(ja) {
    stil = !!ja;
    try { localStorage.setItem("beamer-geluid-stil", stil ? "1" : "0"); } catch (e) {}
    if (stil) stopAlles();
    zetHoofd();
    meld();
  }

  window.Geluid = {
    laad: laad,
    laadUrl: laadUrl,
    zet: zet,
    speel: speel,
    stop: stop,
    stopAlles: stopAlles,
    fragment: fragment,
    buffer: function (sleutel) { return buffers[sleutel] || null; },
    heeft: function (sleutel) { return !!(instellingen[sleutel] && buffers[sleutel]); },
    aantal: function () { return Object.keys(instellingen).length; },
    volume: zetVolume,
    stil: function (ja) { if (ja === undefined) return stil; zetStil(ja); },
    nu: function () { return ctx ? ctx.currentTime : 0; },
    geblokkeerd: function () { return !ctx || ctx.state !== "running"; },
    ontgrendel: ontgrendel,
    bijWijziging: function (f) { luisteraars.push(f); }
  };
})();

/* Het grote scherm na het melden van een uitslag, zoals bij Strava of je
 * bankapp: het hele scherm kleurt, er springt een groot icoon tevoorschijn,
 * en bij winst volgt er confetti. Tik ergens (of druk op een toets) om verder
 * te gaan; na een paar seconden verdwijnt het vanzelf.
 */
(function () {
  "use strict";
  var scherm = document.querySelector(".viering");
  if (!scherm) return;
  var rustig = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var gewonnen = scherm.classList.contains("viering-winst");
  document.documentElement.classList.add("viering-open");

  requestAnimationFrame(function () {
    requestAnimationFrame(function () { scherm.classList.add("toon"); });
  });
  try { scherm.focus({ preventScroll: true }); } catch (e) {}
  if (navigator.vibrate && !rustig) {
    try { navigator.vibrate(gewonnen ? [40, 60, 40, 60, 120] : 60); } catch (e) {}
  }

  var gesloten = false;
  function sluit() {
    if (gesloten) return;
    gesloten = true;
    scherm.classList.add("weg");
    setTimeout(function () {
      scherm.remove();
      document.documentElement.classList.remove("viering-open");
    }, 450);
  }
  /* Even wachten voor je kan wegtikken: anders sluit een dubbele tik op de
     meldknop het scherm meteen weer. */
  setTimeout(function () {
    scherm.addEventListener("click", sluit);
    document.addEventListener("keydown", sluit);
  }, 600);
  setTimeout(sluit, gewonnen ? 5200 : 4200);

  /* ------------------------------------------------------- confetti -- */
  if (!gewonnen || rustig) return;
  var doek = scherm.querySelector(".viering-confetti");
  var ctx = doek.getContext && doek.getContext("2d");
  if (!ctx) return;
  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  var B = window.innerWidth, H = window.innerHeight;
  doek.width = B * dpr; doek.height = H * dpr;
  ctx.scale(dpr, dpr);
  var kleuren = ["#fcd34d", "#f59e0b", "#ffffff", "#fde68a", "#0f172a", "#bbf7d0"];
  var stukjes = [];
  function knal(x, y, aantal, kracht) {
    for (var i = 0; i < aantal; i++) {
      var hoek = Math.random() * Math.PI * 2;
      var v = kracht * (.35 + Math.random() * .65);
      stukjes.push({ x: x, y: y, vx: Math.cos(hoek) * v, vy: Math.sin(hoek) * v - kracht * .35,
                     r: 5 + Math.random() * 7, hoek: Math.random() * 6,
                     draai: (Math.random() - .5) * .35, kleur: kleuren[i % kleuren.length],
                     rond: Math.random() < .3, leeft: 0 });
    }
  }
  /* Eerste knal vanuit het icoon, net als het tevoorschijn springt. */
  setTimeout(function () { knal(B / 2, H * .38, 130, Math.min(B, H) * .035); }, 380);
  setTimeout(function () { knal(B * .15, H * .9, 50, Math.min(B, H) * .03); knal(B * .85, H * .9, 50, Math.min(B, H) * .03); }, 900);
  (function frame() {
    if (gesloten) return;
    ctx.clearRect(0, 0, B, H);
    stukjes.forEach(function (s) {
      s.vy += .32; s.vx *= .985; s.vy *= .99;
      s.x += s.vx; s.y += s.vy; s.hoek += s.draai; s.leeft++;
      ctx.save();
      ctx.translate(s.x, s.y); ctx.rotate(s.hoek);
      ctx.globalAlpha = Math.max(0, 1 - s.leeft / 220);
      ctx.fillStyle = s.kleur;
      if (s.rond) { ctx.beginPath(); ctx.arc(0, 0, s.r * .45, 0, 7); ctx.fill(); }
      else ctx.fillRect(-s.r / 2, -s.r / 4, s.r, s.r / 2.2);
      ctx.restore();
    });
    stukjes = stukjes.filter(function (s) { return s.y < H + 40 && s.leeft < 220; });
    requestAnimationFrame(frame);
  })();
})();

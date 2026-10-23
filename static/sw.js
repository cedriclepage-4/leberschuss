/* Service worker van Leberschuss Tonzent.
 *
 * De site draait op de laptop in het jeugdhuis en de gegevens veranderen de hele
 * avond door. Daarom: pagina's ALTIJD eerst van het netwerk halen, zodat je nooit
 * een oude stand ziet. Enkel als het netwerk wegvalt, tonen we wat we hebben.
 * Vaste bestanden (stijl, script, iconen) komen wel eerst uit de cache.
 */
/* __VERSIE__ wordt door de server ingevuld met een code die uit de inhoud van
 * style.css, app.js en de iconen berekend is. Verandert er één van, dan krijgt
 * de cache een nieuwe naam en wordt de oude opgeruimd — bezoekers zien je
 * aanpassingen dus meteen na een update, zonder iets te moeten wissen. */
const VERSIE = "leberschuss-__VERSIE__";
const VASTE_BESTANDEN = [
  "/static/style.css",
  "/static/app.js",
  "/static/logo.svg",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/manifest.webmanifest",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(VERSIE).then((c) => c.addAll(VASTE_BESTANDEN)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((sleutels) => Promise.all(sleutels.filter((k) => k !== VERSIE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const verzoek = e.request;
  // Alleen gewone GET-verzoeken naar deze site; formulieren nooit onderscheppen.
  if (verzoek.method !== "GET" || new URL(verzoek.url).origin !== self.location.origin) return;

  const isVastBestand = new URL(verzoek.url).pathname.startsWith("/static/");

  if (isVastBestand) {
    // Cache first: deze bestanden veranderen zelden (en hebben een versiecode
    // in hun adres). Lukt het netwerk niet, dan liever de vorige versie dan
    // niets: zonder stijl valt de hele pagina uit elkaar.
    e.respondWith(
      caches.match(verzoek).then((gevonden) =>
        gevonden || fetch(verzoek).then((antwoord) => {
          const kopie = antwoord.clone();
          if (antwoord.ok) caches.open(VERSIE).then((c) => c.put(verzoek, kopie));
          return antwoord;
        }).catch(() =>
          caches.match(verzoek, { ignoreSearch: true }).then((oud) => oud || Response.error())
        )
      )
    );
    return;
  }

  // Coupons: NOOIT uit de cache. Anders kan een herhaalde scan zonder netwerk
  // een oud "Geldig!"-scherm tonen en worden de pintjes twee keer gegeven.
  if (new URL(verzoek.url).pathname.startsWith("/coupon/")) {
    e.respondWith(
      fetch(verzoek).catch(() => new Response(
        '<!DOCTYPE html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
        '<body style="margin:0;font-family:sans-serif;background:#b91c1c;color:#fff;display:flex;' +
        'min-height:100vh;align-items:center;justify-content:center;text-align:center;padding:24px">' +
        '<div><h1 style="font-size:2.4rem;margin:0 0 12px">Geen verbinding</h1>' +
        '<p style="font-size:1.15rem;max-width:26rem">Deze coupon is <strong>NIET</strong> ingelost. ' +
        'Geef nog geen pintjes: scan opnieuw zodra er weer internet is.</p></div>',
        { status: 503, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } }))
    );
    return;
  }

  // Network first: standen en uitslagen moeten actueel zijn.
  e.respondWith(
    fetch(verzoek)
      .then((antwoord) => {
        const kopie = antwoord.clone();
        if (antwoord.ok) caches.open(VERSIE).then((c) => c.put(verzoek, kopie));
        return antwoord;
      })
      .catch(() => caches.match(verzoek).then((gevonden) => gevonden || caches.match("/")))
  );
});

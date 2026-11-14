# -*- coding: utf-8 -*-
"""Bezoekersstatistieken: wat telt mee, wie mag kijken, en klopt het tellen?

    python test_bezoekers.py

Deze test raakt shuss.db NIET aan: hij werkt in een tijdelijke map.
"""

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

WERKMAP = tempfile.mkdtemp(prefix="shuss_bezoek_")
BRON = os.path.dirname(os.path.abspath(__file__))
for naam in ("app.py", "database.py", "elo.py", "tournament.py", "league.py", "dop.py",
             "samenvoegen.py", "kansen.py", "bezoekers.py", "geo.py"):
    shutil.copy(os.path.join(BRON, naam), WERKMAP)
shutil.copytree(os.path.join(BRON, "templates"), os.path.join(WERKMAP, "templates"))
shutil.copytree(os.path.join(BRON, "static"), os.path.join(WERKMAP, "static"),
                ignore=shutil.ignore_patterns("uploads"))
sys.path.insert(0, WERKMAP)
os.chdir(WERKMAP)

import app as shuss                                              # noqa: E402
import bezoekers as bz                                           # noqa: E402
import geo                                                       # noqa: E402
from database import verbind                                     # noqa: E402

DB = os.path.join(WERKMAP, "shuss.db")
ECHTE_GEO = os.path.join(BRON, "geo", "dbip-city-lite.mmdb")
FOUTEN = []
CHROME = ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/130.0 Mobile Safari/537.36")


def check(voorwaarde, tekst):
    print(("  ✔ " if voorwaarde else "  ✘ ") + tekst)
    if not voorwaarde:
        FOUTEN.append(tekst)


conn = verbind(DB)
conn.execute("INSERT INTO players (name, nickname, role) VALUES ('Baas', '', 'eigenaar')")
conn.execute("INSERT INTO players (name, nickname, role) VALUES ('Orga', '', 'admin')")
conn.execute("INSERT INTO players (name, nickname) VALUES ('Gewoon', '')")
conn.commit()
ids = [r[0] for r in conn.execute("SELECT id FROM players ORDER BY name")]
BAAS, GEWOON, ORGA = ids   # alfabetisch: Baas, Gewoon, Orga


def aantal():
    bz.schrijf_weg(conn)
    return conn.execute("SELECT COUNT(*) FROM bezoeken").fetchone()[0]


print("\n== Bron herkennen ==")
check(bz.bron_van("https://www.google.com/", CHROME, "shuss.be") == "Google", "Google")
check(bz.bron_van("https://l.instagram.com/?u=x", CHROME, "shuss.be") == "Instagram", "Instagram-link")
check(bz.bron_van("", CHROME + " Instagram 300.0", "shuss.be") == "Instagram",
      "Instagram-app zonder afzender")
check(bz.bron_van("https://shuss.be/toernooien", CHROME, "shuss.be") is None,
      "klik binnen de site telt niet als bron")
check(bz.bron_van("https://shuss.be/x", CHROME, "www.shuss.be") is None, "www. maakt niet uit")
check(bz.bron_van("", CHROME, "shuss.be") == bz.DIRECT, "zonder afzender: rechtstreeks")
check(bz.bron_van("https://jeugdhuis.be/agenda", CHROME, "shuss.be") == "jeugdhuis.be",
      "onbekende site: het domein")
check(bz.is_bot("Googlebot/2.1") and bz.is_bot("WhatsApp/2.23.1") and bz.is_bot(""),
      "bots en linkvoorbeelden herkend")
check(not bz.is_bot(CHROME), "gewone browser is geen bot")

print("\n== Wat telt mee ==")
gast = shuss.app.test_client()
gast.environ_base["HTTP_USER_AGENT"] = CHROME
voor = aantal()
r = gast.get("/", headers={"Sec-Fetch-Dest": "document"})
check(r.status_code == 200, "thuispagina opent")
check(bz.COOKIE in (r.headers.get("Set-Cookie") or ""), "nieuwe bezoeker krijgt een cookie")
check(aantal() == voor + 1, "gewone pagina telt mee")
code = conn.execute("SELECT bezoeker FROM bezoeken ORDER BY id DESC").fetchone()[0]
gast.get("/toernooien")
check(conn.execute("SELECT bezoeker FROM bezoeken ORDER BY id DESC").fetchone()[0] == code
      if aantal() == voor + 2 else False, "tweede pagina: zelfde bezoeker via de cookie")
voor = aantal()
gast.get("/", headers={"Sec-Fetch-Dest": "empty"})
check(aantal() == voor, "fetch() op de achtergrond (tv-verversen) telt niet")
gast.get("/", headers={"Sec-Purpose": "prefetch"})
check(aantal() == voor, "prefetch telt niet")
gast.get("/static/style.css")
check(aantal() == voor, "stijlblad telt niet")
bot = shuss.app.test_client()
bot.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 (compatible; Googlebot/2.1)"
r = bot.get("/")
check(aantal() == voor and bz.COOKIE not in (r.headers.get("Set-Cookie") or ""),
      "bot telt niet en krijgt geen cookie")
ruw = conn.execute("SELECT * FROM bezoeken").fetchall()
check(all(len(r["bezoeker"]) == 20 for r in ruw), "geen IP-adres, enkel een code")
koekjes = [r["met_cookie"] for r in conn.execute(
    "SELECT met_cookie FROM bezoeken WHERE bezoeker = ? ORDER BY id", (code,))]
check(koekjes[:2] == [0, 1], f"eerste pagina zonder cookie, daarna met ({koekjes})")

print("\n== Plaats ==")
check(geo.plaats("127.0.0.1") is None and geo.plaats("192.168.1.5") is None
      and geo.plaats("geen adres") is None, "eigen netwerk of onzin: geen plaats")
if os.path.exists(ECHTE_GEO):
    geo.BESTAND = ECHTE_GEO
    geo._laatst_gekeken = 0
    check(geo.plaats("212.71.0.1") == "Sint-Niklaas (Vlaanderen)", f"Belgisch adres ({geo.plaats('212.71.0.1')})")
    check(geo.plaats("193.191.1.1") == "Brussel", f"Brussel, in het Nederlands ({geo.plaats('193.191.1.1')})")
    check((geo.plaats("90.0.0.1") or "").endswith("(Frankrijk)"), f"buitenland ({geo.plaats('90.0.0.1')})")
    voor = aantal()
    gast.get("/", environ_base={"REMOTE_ADDR": "212.71.0.1", "HTTP_USER_AGENT": CHROME})
    check(aantal() == voor + 1, "bezoek met een echt IP-adres telt")
    rij = conn.execute("SELECT * FROM bezoeken ORDER BY id DESC").fetchone()
    check(rij["plaats"] == "Sint-Niklaas (Vlaanderen)", f"de gemeente wordt bewaard ({rij['plaats']})")
    check("212.71" not in " ".join(str(v) for v in tuple(rij)), "het IP-adres zelf niet")
    geo.BESTAND = os.path.join(WERKMAP, "geo", "bestaat-niet.mmdb")
    geo._laatst_gekeken = 0
    check(geo.plaats("212.71.0.1") is None, "zonder databank: gewoon geen plaats")
else:
    print("  (plaatsdatabank niet gevonden: python geo_bijwerken.py; opzoeken overgeslagen)")

print("\n== Toegang ==")
check(gast.get("/admin/bezoekers").status_code == 302, "zonder login: doorgestuurd")
orga = shuss.app.test_client()
with orga.session_transaction() as s:
    s["speler_id"] = ORGA
check(orga.get("/admin/bezoekers").status_code == 302, "een gewone organisator: doorgestuurd")
check("Bezoekers" not in orga.get("/admin").get_data(as_text=True).split("admin-nav")[1][:1500],
      "organisator ziet het tabblad niet")
baas = shuss.app.test_client()
baas.environ_base["HTTP_USER_AGENT"] = CHROME
with baas.session_transaction() as s:
    s["speler_id"] = BAAS
voor = aantal()
r = baas.get("/admin/bezoekers")
check(r.status_code == 200, "de eigenaar ziet de pagina")
check(aantal() == voor, "het organisatiepaneel zelf telt niet mee")

print("\n== Tellen ==")
conn.execute("DELETE FROM bezoeken")
conn.execute("DELETE FROM bezoekers")
nu = datetime(2026, 11, 11, 20, 30)
gisteren = nu - timedelta(days=1)


def hit(t, bezoeker, speler=None, bron=None, plaats=None):
    conn.execute("INSERT INTO bezoeken (moment, bezoeker, speler_id, bron, plaats) "
                 "VALUES (?, ?, ?, ?, ?)", (t.strftime(bz.TIJD), bezoeker, speler, bron, plaats))
    conn.execute("INSERT INTO bezoekers (id, eerst, laatst) VALUES (?, ?, ?) "
                 "ON CONFLICT(id) DO UPDATE SET laatst = MAX(laatst, excluded.laatst)",
                 (bezoeker, t.strftime(bz.TIJD), t.strftime(bz.TIJD)))


# a: twee bezoeken gisteren (via WhatsApp, later rechtstreeks), ingelogd
hit(gisteren.replace(hour=19, minute=0), "a" * 20, GEWOON, "WhatsApp")
hit(gisteren.replace(hour=19, minute=10), "a" * 20, GEWOON)
hit(gisteren.replace(hour=21, minute=0), "a" * 20, GEWOON, bz.DIRECT)
# b: één bezoek net nu, anoniem, via Google
hit(nu - timedelta(minutes=2), "b" * 20, None, "Google")
hit(nu - timedelta(minutes=1), "b" * 20)
# c: lang geleden (buiten 30 dagen)
hit(nu - timedelta(days=40), "c" * 20, None, "Google")
conn.commit()

c = bz.overzicht(conn, nu)
check(c["online"]["bezoekers"] == 1 and c["online"]["ingelogd"] == 0 and c["online"]["spelers"] == [], "nu online: 1, niet ingelogd")
p24, p7, p30 = c["perioden"]
# Gisteren 21u valt nog binnen de laatste 24 uur (nu is het 20u30).
check((p24["bezoekers"], p24["bezoeken"], p24["paginas"]) == (2, 2, 3),
      f"laatste 24 uur: 2 bezoekers, 2 bezoeken, 3 pagina's ({p24})")
check((p7["bezoekers"], p7["bezoeken"], p7["paginas"]) == (2, 3, 5),
      f"laatste 7 dagen: 2 bezoekers, 3 bezoeken, 5 pagina's ({p7})")
bronnen = {b["naam"]: b["aantal"] for b in c["bronnen"]}
check(bronnen == {"WhatsApp": 1, bz.DIRECT: 1, "Google": 1}, f"bronnen per bezoek ({bronnen})")
check(c["wie"]["ingelogd"] == 1 and c["wie"]["anoniem"] == 1, "1 ingelogd, 1 anoniem")
check(c["actief"] and c["actief"][0]["speler_id"] == GEWOON
      and c["actief"][0]["bezoeken"] == 2 and c["actief"][0]["dagen"] == 1,
      "meest actief: Gewoon, 2 bezoeken op 1 dag")
drukte = {(r["dag"], u["uur"]): u["aantal"] for r in c["drukte"] for u in r["uren"]}
check(drukte[(bz.DAGNAMEN[gisteren.weekday()], 19)] == 1
      and drukte[(bz.DAGNAMEN[gisteren.weekday()], 21)] == 1, "drukke momenten per uur")
check(sum(e["bezoekers"] for e in c["grafieken"]["7d"]) == 2, "dagengrafiek telt 2 bezoekers")

print("\n== Onderhoud ==")
bz.onderhoud(conn, nu)
conn.commit()
dag = conn.execute("SELECT * FROM bezoeken_dag WHERE datum = ?",
                   (gisteren.strftime("%Y-%m-%d"),)).fetchone()
check(dag and (dag["paginas"], dag["bezoekers"], dag["bezoeken"], dag["ingelogd"]) == (3, 1, 2, 1),
      "dagtotaal van gisteren klopt")
check(conn.execute("SELECT COUNT(*) FROM bezoeken WHERE bezoeker = ?", ("c" * 20,)).fetchone()[0] == 0,
      "paginabezoeken ouder dan een maand zijn gewist")
check(conn.execute("SELECT COUNT(*) FROM bezoeken_dag").fetchone()[0] == 2,
      "maar hun dagtotaal blijft")
check(conn.execute("SELECT COUNT(*) FROM bezoeken WHERE bezoeker = ?", ("a" * 20,)).fetchone()[0] == 3,
      "recente paginabezoeken blijven")
c = bz.overzicht(conn, nu)
check(len(c["maanden"]) == 2, "per maand: twee maanden")
# Vandaag is nog niet samengevat, maar telt in de maand al wel mee: gisteren 3
# pagina's (2 bezoeken) + vandaag 2 pagina's (1 bezoek).
deze = c["maanden"][0]
check((deze["paginas"], deze["bezoeken"], deze["dagen"]) == (5, 3, 2),
      f"per maand: de lopende dag telt meteen mee ({deze})")
check(deze["paginas"] == c["perioden"][1]["paginas"],
      "per maand: zelfde aantal pagina's als de laatste 7 dagen")
check(all("hoogte_p" in e for e in c["grafieken"]["30d"]), "grafiek kent ook paginahoogtes")
check(sum(u["paginas"] for r in c["drukte"] for u in r["uren"]) == 5,
      "drukke momenten telt ook pagina's")

print("\n== Wie is online, en wanneer was een speler online ==")
conn.execute("DELETE FROM bezoeken")
# Gewoon: gisteren 19u00-19u40 (3 pagina's), en nu bezig sinds 20u10.
for minuut in (0, 20, 40):
    hit(gisteren.replace(hour=19, minute=minuut), "a" * 20, GEWOON)
hit(nu.replace(minute=10), "a" * 20, GEWOON, plaats="Lokeren (Vlaanderen)")
hit(nu - timedelta(minutes=1), "a" * 20, GEWOON, plaats="Lokeren (Vlaanderen)")
# Een anonieme bezoeker is ook online, maar telt niet als ingelogde speler.
hit(nu - timedelta(minutes=2), "b" * 20, plaats="Brussel")
conn.commit()
c = bz.overzicht(conn, nu)
check(c["online"]["bezoekers"] == 2 and c["online"]["ingelogd"] == 1, "2 online, 1 ingelogd")
o = c["online"]["spelers"]
check(len(o) == 1 and o[0]["speler_id"] == GEWOON and o[0]["sinds"][11:16] == "20:10"
      and o[0]["minuten"] == 20, f"Gewoon is online sinds 20u10, 20 minuten ({o})")
check(o[0]["plaats"] == "Lokeren (Vlaanderen)", "plaats van wie online is")
check(dict(c["online"]["plaatsen"]) == {"Lokeren (Vlaanderen)": 1, "Brussel": 1},
      f"plaatsen van alle toestellen online ({c['online']['plaatsen']})")
check({p["naam"]: p["aantal"] for p in c["waar"]["plaatsen"]}
      == {"Lokeren (Vlaanderen)": 1, "Brussel": 1}, f"waar: per gemeente ({c['waar']})")
check(dict(c["waar"]["gewesten"]) == {"Vlaanderen": 1, "Brussel": 1}, "waar: per gewest")
check(c["waar"]["onbekend"] == 0, "waar: geen toestellen zonder plaats")
check(c["actief"][0]["minuten"] == 40 + 19, f"totale tijd: 40 + 19 minuten ({c['actief'][0]})")
h = bz.historiek(conn, GEWOON, nu)
check(h["nu_online"] and h["aantal"] == 2 and h["dagen_actief"] == 2, "historiek: 2 bezoeken op 2 dagen")
check([(s["van"], s["tot"], s["paginas"]) for d in h["lijst"] for s in d["sessies"]]
      == [("20:10", "20:29", 2), ("19:00", "19:40", 3)], f"historiek: nieuwste eerst ({h['lijst']})")
check(sum(u["aantal"] for u in h["uren"]) == 2, "historiek: uren tellen")
check(h["plaatsen"] == [("Lokeren (Vlaanderen)", 1)], f"historiek: plaatsen ({h['plaatsen']})")
check(h["lijst"][0]["sessies"][0]["plaats"] == "Lokeren (Vlaanderen)", "historiek: plaats per bezoek")
check(len(h["dagen"]) == bz.RUW_BEWAREN_DAGEN, "historiek: een balk per dag")
check(bz.historiek(conn, BAAS, nu)["aantal"] == 0, "historiek van iemand die nooit online was: leeg")

r = baas.get(f"/admin/bezoekers/speler/{GEWOON}")
check(r.status_code == 200 and "Historiek" in r.get_data(as_text=True), "de eigenaar ziet de historiek")
check(baas.get("/admin/bezoekers/speler/99999").status_code == 404, "onbekende speler: 404")
check(orga.get(f"/admin/bezoekers/speler/{GEWOON}").status_code == 302,
      "een gewone organisator ziet de historiek niet")
historiek_url = f"/admin/bezoekers/speler/{GEWOON}"
check(historiek_url in baas.get(f"/speler/{GEWOON}").get_data(as_text=True),
      "de eigenaar ziet op het profiel een knop naar de historiek")
check(historiek_url not in orga.get(f"/speler/{GEWOON}").get_data(as_text=True),
      "een organisator ziet die knop niet")
r = baas.get(historiek_url, headers={"Referer": f"http://localhost/speler/{GEWOON}"})
check("terug-knop" in r.get_data(as_text=True), "de historiek heeft een terugknop")

print("\n== Automaten ==")
conn.execute("DELETE FROM bezoeken")
conn.execute("DELETE FROM bezoekers")
conn.execute("DELETE FROM bezoeken_dag")
nu = datetime(2026, 11, 14, 20, 0)
vandaag = nu.replace(hour=0, minute=1)


def hit2(t, bezoeker, speler=None, bron=None, met_cookie=None):
    hit(t, bezoeker, speler, bron)
    conn.execute("UPDATE bezoeken SET met_cookie = ? WHERE id = last_insert_rowid()",
                 (met_cookie,))


# Monitor: elke 143 seconden een pagina, nooit een cookie terug.
for i in range(300):
    hit2(vandaag + timedelta(seconds=143 * i), "m" * 20, bron=bz.DIRECT, met_cookie=0)
# Scraper van vóór we het cookie bijhielden: 30 pagina's rechtstreeks op 2 minuten.
for i in range(30):
    hit2(nu.replace(hour=14) + timedelta(seconds=4 * i), "s" * 20, bron=bz.DIRECT)
# Mens: binnen via Instagram, daarna klikken, met cookie.
hit2(nu.replace(hour=13), "h" * 20, bron="Instagram", met_cookie=0)
for i in range(1, 30):
    hit2(nu.replace(hour=13) + timedelta(seconds=20 * i), "h" * 20, met_cookie=1)
# Mens op een toernooiavond: ververst de stand 25 keer (rechtstreeks), mét cookie.
for i in range(25):
    hit2(nu.replace(hour=19) + timedelta(minutes=2 * i), "r" * 20, bron=bz.DIRECT, met_cookie=1)
# Speler, oude bezoeken zonder cookie-info: 25 keer rechtstreeks, maar ingelogd.
for i in range(25):
    hit2(nu.replace(hour=16) + timedelta(minutes=i), "p" * 20, GEWOON, bz.DIRECT)
# Kort anoniem bezoek zonder cookie-info: 3 pagina's.
for i in range(3):
    hit2(nu.replace(hour=11) + timedelta(minutes=i), "k" * 20, bron=bz.DIRECT)
conn.commit()

ruw = bz._rijen_ruw(conn, "2000-01-01 00:00:00", bz._tijd(nu))
check(bz.automaten(ruw) == {"m" * 20, "s" * 20},
      f"monitor en scraper herkend, mensen niet ({sorted(b[0] for b in bz.automaten(ruw))})")
c = bz.overzicht(conn, nu)
check(c["perioden"][0]["bezoekers"] == 4, f"enkel de 4 mensen geteld ({c['perioden'][0]})")
check(c["perioden"][0]["paginas"] == 30 + 25 + 25 + 3, "pagina's van automaten tellen niet")
check(c["automaat"] == {"toestellen": 2, "paginas": 330}, f"weggelaten automaten ({c['automaat']})")
check(c["wie"]["nieuw"] == 4, f"automaten tellen niet als nieuwe bezoekers ({c['wie']})")
check(sum(e["paginas"] for e in c["grafieken"]["24u"]) == 83, "grafiek zonder automaten")

# Een dagtotaal dat al met automaten opgeslagen was, wordt opnieuw berekend.
gisteren = (nu - timedelta(days=1)).strftime("%Y-%m-%d")
for i in range(30):
    hit2(nu - timedelta(days=1, seconds=-4 * i), "t" * 20, bron=bz.DIRECT)
hit2(nu - timedelta(days=1), "u" * 20, bron="Google", met_cookie=0)
conn.execute("INSERT INTO bezoeken_dag (datum, paginas, bezoekers, bezoeken, ingelogd, nieuw) "
             "VALUES (?, 31, 2, 2, 0, 2)", (gisteren,))
conn.commit()
bz.onderhoud(conn, nu)
conn.commit()
dag = conn.execute("SELECT * FROM bezoeken_dag WHERE datum = ?", (gisteren,)).fetchone()
check(dag and (dag["paginas"], dag["bezoekers"], dag["nieuw"]) == (1, 1, 1),
      f"oud dagtotaal opnieuw berekend zonder automaat ({dict(dag) if dag else None})")

print("\n" + "=" * 62)
if FOUTEN:
    print(f"❌ {len(FOUTEN)} fout(en):")
    for f in FOUTEN:
        print("  -", f)
else:
    print("✅ Bezoekersstatistieken kloppen.")
print("=" * 62)
conn.close()
os.chdir(BRON)
shutil.rmtree(WERKMAP, ignore_errors=True)
sys.exit(1 if FOUTEN else 0)

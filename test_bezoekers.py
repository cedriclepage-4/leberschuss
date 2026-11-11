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
             "samenvoegen.py", "kansen.py", "bezoekers.py"):
    shutil.copy(os.path.join(BRON, naam), WERKMAP)
shutil.copytree(os.path.join(BRON, "templates"), os.path.join(WERKMAP, "templates"))
shutil.copytree(os.path.join(BRON, "static"), os.path.join(WERKMAP, "static"),
                ignore=shutil.ignore_patterns("uploads"))
sys.path.insert(0, WERKMAP)
os.chdir(WERKMAP)

import app as shuss                                              # noqa: E402
import bezoekers as bz                                           # noqa: E402
from database import verbind                                     # noqa: E402

DB = os.path.join(WERKMAP, "shuss.db")
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


def hit(t, bezoeker, speler=None, bron=None):
    conn.execute("INSERT INTO bezoeken (moment, bezoeker, speler_id, bron) VALUES (?, ?, ?, ?)",
                 (t.strftime(bz.TIJD), bezoeker, speler, bron))
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
check(c["online"] == {"bezoekers": 1, "ingelogd": 0}, "nu online: 1, niet ingelogd")
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

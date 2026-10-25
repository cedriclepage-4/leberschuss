# -*- coding: utf-8 -*-
"""Beamergeluiden: opladen, knippen, bewaren en tonen op het beamerscherm.

    python test_geluiden.py

Deze test raakt shuss.db NIET aan: hij werkt in een tijdelijke map.
"""

import io
import json
import os
import shutil
import sys
import tempfile

WERKMAP = tempfile.mkdtemp(prefix="shuss_geluid_")
BRON = os.path.dirname(os.path.abspath(__file__))
for naam in ("app.py", "database.py", "elo.py", "tournament.py", "league.py", "dop.py",
             "samenvoegen.py"):
    shutil.copy(os.path.join(BRON, naam), WERKMAP)
shutil.copytree(os.path.join(BRON, "templates"), os.path.join(WERKMAP, "templates"))
shutil.copytree(os.path.join(BRON, "static"), os.path.join(WERKMAP, "static"),
                ignore=shutil.ignore_patterns("uploads"))
sys.path.insert(0, WERKMAP)
os.chdir(WERKMAP)

import app as shuss                                              # noqa: E402
from database import verbind                                     # noqa: E402

DB = os.path.join(WERKMAP, "shuss.db")
FOUTEN = []


def check(voorwaarde, tekst):
    print(("  ✔ " if voorwaarde else "  ✘ ") + tekst)
    if not voorwaarde:
        FOUTEN.append(tekst)


conn = verbind(DB)
conn.execute("INSERT INTO players (name, nickname, role) VALUES ('Orga', '', 'eigenaar')")
conn.execute("INSERT INTO players (name, nickname) VALUES ('Gewoon', '')")
conn.execute("INSERT INTO tournaments (name, date, status) VALUES ('Beker', '2026-10-30', 'bracket')")
conn.commit()

klant = shuss.app.test_client()
with klant.session_transaction() as s:
    s["speler_id"] = 1


def oplaad(sleutel, naam, inhoud=b"ID3" + b"\0" * 64):
    return klant.post(f"/admin/beamergeluiden/{sleutel}/bestand",
                      data={"bestand": (io.BytesIO(inhoud), naam)},
                      content_type="multipart/form-data")


print("\n== Toegang ==")
gast = shuss.app.test_client()
check(gast.get("/admin/beamergeluiden").status_code == 302, "zonder login: doorgestuurd")
speler = shuss.app.test_client()
with speler.session_transaction() as s:
    s["speler_id"] = 2
check(speler.post("/admin/beamergeluiden/beker", json={}).status_code == 302,
      "een gewone speler kan niets bewaren")
check(klant.get("/admin/beamergeluiden").status_code == 200, "de editor opent voor een organisator")
check(klant.get("/admin/beamergeluiden/proef").status_code == 200, "de proefweergave opent")
check(klant.get("/admin/beamergeluiden/proef?scene=beker").status_code == 200,
      "de proefweergave opent met een scène")

print("\n== Opladen ==")
check(oplaad("onbekend", "knal.mp3").status_code == 404, "onbekende gebeurtenis: 404")
check(oplaad("beker", "knal.exe").status_code == 400, "verkeerde extensie geweigerd")
check(oplaad("beker", "knal.mp3", b"\0" * (shuss.MAX_GELUID + 1)).status_code == 400,
      "te groot bestand geweigerd")
a = oplaad("beker", "knal.mp3")
check(a.status_code == 200 and a.get_json()["ok"], "mp3 opgeladen")
eerste = a.get_json()["geluid"]["bestand"]
check(shuss._GELUID_RE.match(eerste) is not None, f"veilige bestandsnaam ({eerste})")
check(os.path.exists(os.path.join(shuss.UPLOAD_MAP, eerste)), "bestand staat op de schijf")
check(klant.get(f"/geluid/{eerste}").status_code == 200, "het geluid is af te spelen")
check(klant.get("/geluid/..%2Fshuss.db").status_code == 404, "geen andere bestanden via /geluid/")
check(klant.get("/geluid/avatar_p1_0123abcd.png").status_code == 404, "geen avatars via /geluid/")

print("\n== Knippen en bewaren ==")
a = klant.post("/admin/beamergeluiden/beker",
               json={"start": -4, "eind": 99999, "vertraging": 50000, "volume": 7,
                     "uitfaden": 300, "aan": True})
g = a.get_json()["geluid"]
check(g["start"] == 0 and g["eind"] == 3600, "start en einde begrensd")
check(g["vertraging"] == 10000 and g["volume"] == 1, "vertraging en volume begrensd")
a = klant.post("/admin/beamergeluiden/beker", json={"start": 2.5, "eind": 1, "volume": "nan"})
g = a.get_json()["geluid"]
check(g["start"] == 2.5 and g["eind"] == 0, "einde vóór start wordt 'tot het einde'")
check(g["volume"] == 1, "onzin als volume wordt de standaard")
check(klant.post("/admin/beamergeluiden/kampioen", json={}).status_code == 400,
      "instellingen zonder geluid: geweigerd")
klant.post("/admin/beamergeluiden/beker", json={"start": 0.4, "eind": 1.6, "vertraging": 120,
                                                 "volume": 0.7, "uitfaden": 200, "aan": True})

a = oplaad("beker", "knal2.wav")
tweede = a.get_json()["geluid"]["bestand"]
check(not os.path.exists(os.path.join(shuss.UPLOAD_MAP, eerste)), "het oude bestand is weg")
check(a.get_json()["geluid"]["start"] == 0 and a.get_json()["geluid"]["vertraging"] == 120,
      "nieuw bestand: knippen opnieuw, de rest blijft")
klant.post("/admin/beamergeluiden/beker", json={"start": 0.4, "eind": 1.6, "vertraging": 120,
                                                 "volume": 0.7, "uitfaden": 200, "aan": True})
klant.post("/admin/beamergeluiden/volume", json={"volume": 0.5})

print("\n== Op het beamerscherm ==")
pagina = klant.get("/toernooi/1/beamer").get_data(as_text=True)
check("geluid.js" in pagina and "data-geluid" in pagina, "geluidsmotor en knop op het scherm")
check(tweede in pagina and '"vertraging": 120' in pagina, "de instellingen staan erin")
check("volume: 0.5" in pagina, "het hoofdvolume staat erin")
check(gast.get("/toernooi/1/beamer").status_code == 200, "ook zonder login (zoals voorheen)")

print("\n== Wissen ==")
check(klant.post("/admin/beamergeluiden/beker/wissen").get_json()["ok"], "geluid gewist")
check(not os.path.exists(os.path.join(shuss.UPLOAD_MAP, tweede)), "bestand van de schijf")
pagina = klant.get("/toernooi/1/beamer").get_data(as_text=True)
check("geluid.js" not in pagina and "data-geluid" not in pagina, "zonder geluiden: geen knop")
ruw = json.loads(shuss.instelling(conn, "beamer_geluiden", "{}"))
check(ruw == {}, "niets meer bewaard")

conn.close()
print("\n" + "=" * 62)
if FOUTEN:
    print(f"❌ {len(FOUTEN)} probleem(en):")
    for f in FOUTEN:
        print("  -", f)
else:
    print("✅ Beamergeluiden kloppen.")
print("=" * 62)
os.chdir(BRON)
shutil.rmtree(WERKMAP, ignore_errors=True)
sys.exit(1 if FOUTEN else 0)

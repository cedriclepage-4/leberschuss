# -*- coding: utf-8 -*-
"""VAR-review: een organisator stuurt een foto + beslissing door naar de beamer.

    python test_var.py

Deze test raakt shuss.db NIET aan: hij werkt in een tijdelijke map.
"""

import io
import json
import os
import shutil
import sys
import tempfile

WERKMAP = tempfile.mkdtemp(prefix="shuss_var_")
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
conn.execute("INSERT INTO teams (name, player1_id, status) VALUES ('De Dopjes', 1, 'actief')")
conn.execute("INSERT INTO teams (name, player1_id, status) VALUES ('Lijnrechters', 2, 'actief')")
conn.execute("INSERT INTO tournaments (name, date, status) VALUES ('Beker', '2026-10-30', 'bracket')")
conn.execute("INSERT INTO tournament_locations (tournament_id, name) VALUES (1, 'Tafel 2')")
conn.execute("INSERT INTO games (team1_id, team2_id, tournament_id, fase, ronde, location_id, "
             "scheduled_at) VALUES (1, 2, 1, 'bracket', 1, 1, '2026-10-30T20:00')")
conn.execute("INSERT INTO games (team1_id, team2_id, scheduled_at) VALUES (1, 2, '2026-10-01T20:00')")
conn.commit()

klant = shuss.app.test_client()
with klant.session_transaction() as s:
    s["speler_id"] = 1
JPG = b"\xff\xd8\xff\xe0" + b"\0" * 256


def stuur(c, game_id=1, beslissing="2", naam="var.jpg", inhoud=JPG, json_=True):
    data = {"beslissing": beslissing}
    if naam:
        data["foto"] = (io.BytesIO(inhoud), naam)
    return c.post(f"/admin/wedstrijden/{game_id}/var", data=data,
                  content_type="multipart/form-data",
                  headers={"X-Var": "1"} if json_ else {})


print("\n== Toegang ==")
gast = shuss.app.test_client()
check(stuur(gast).status_code == 302, "zonder login: doorgestuurd")
speler = shuss.app.test_client()
with speler.session_transaction() as s:
    s["speler_id"] = 2
check(stuur(speler).status_code == 302, "een gewone speler kan geen VAR doorsturen")
check(gast.get("/toernooi/1/var.json").get_json() == {"var": None}, "nog geen review")
pagina = klant.get("/admin/toernooi/1").get_data(as_text=True)
check("/admin/wedstrijden/1/var" in pagina, "de VAR-knop staat bij de openstaande wedstrijd")

print("\n== Foute invoer ==")
check(stuur(klant, beslissing="4").status_code == 400, "onbekende beslissing geweigerd")
check(stuur(klant, naam=None).status_code == 400, "zonder foto geweigerd")
check(stuur(klant, naam="var.heic").status_code == 400, "onbekend bestandstype geweigerd")
check(stuur(klant, game_id=2).status_code == 404, "een leaguewedstrijd heeft geen VAR")
check(stuur(klant, game_id=99).status_code == 404, "onbestaande wedstrijd")
check(not [n for n in os.listdir(shuss.UPLOAD_MAP) if n.startswith("var_")],
      "niets opgeslagen bij foute invoer")

print("\n== Doorsturen ==")
r = stuur(klant, beslissing="adje")
check(r.status_code == 200 and r.get_json()["ok"], "review doorgestuurd")
v = gast.get("/toernooi/1/var.json").get_json()["var"]
check(v is not None and v["beslissing"] == "adje" and v["label"] == "Adje!", "beamer ziet de beslissing")
check(v["team1"] == "De Dopjes" and v["team2"] == "Lijnrechters" and v["tafel"] == "Tafel 2",
      "beamer ziet de wedstrijd en de tafel")
foto = gast.get(v["foto"])
check(foto.status_code == 200 and foto.data == JPG, "de foto is op te halen")
foto.close()
check(gast.get("/var/../shuss.db").status_code == 404, "geen andere bestanden via /var/")
check(gast.get("/var/avatar_p1_00000000.jpg").status_code == 404, "geen avatars via /var/")

eerste = v["foto"].rsplit("/", 1)[-1]
r = stuur(klant, beslissing="uit", naam="var.png", json_=False)
check(r.status_code == 302 and "#openstaand" in r.headers["Location"],
      "zonder JavaScript: terug naar het beheer")
v2 = gast.get("/toernooi/1/var.json").get_json()["var"]
check(v2["id"] != v["id"] and v2["beslissing"] == "uit", "een nieuwe review vervangt de vorige")
check(not os.path.exists(os.path.join(shuss.UPLOAD_MAP, eerste)), "de vorige foto is opgeruimd")

print("\n== Te oud ==")
ruw = json.loads(shuss.instelling(conn, "var_toernooi_1"))
ruw["tijd"] -= shuss.VAR_VERS + 1
shuss.zet_instelling(conn, "var_toernooi_1", json.dumps(ruw))
conn.commit()
check(gast.get("/toernooi/1/var.json").get_json() == {"var": None},
      "een oude review komt niet meer op de beamer")
shuss.zet_instelling(conn, "var_toernooi_1", "kapot{")
conn.commit()
check(gast.get("/toernooi/1/var.json").get_json() == {"var": None}, "kapotte instelling: geen fout")

print("\n== Beamer ==")
pagina = gast.get("/toernooi/1/beamer").get_data(as_text=True)
check("/toernooi/1/var.json" in pagina and "data-var" in pagina, "de beamer kent de VAR-bron")
check("elke: 8" in pagina, "de beamer ververst standaard elke 8 seconden")
check(klant.get("/admin/beamergeluiden/proef?scene=var").status_code == 200,
      "de geluidseditor toont de VAR-scène")

conn.close()
print("\n" + "=" * 62)
if FOUTEN:
    print(f"❌ {len(FOUTEN)} probleem(en):")
    for f in FOUTEN:
        print("  -", f)
else:
    print("✅ VAR-review klopt.")
print("=" * 62)
os.chdir(BRON)
shutil.rmtree(WERKMAP, ignore_errors=True)
sys.exit(1 if FOUTEN else 0)

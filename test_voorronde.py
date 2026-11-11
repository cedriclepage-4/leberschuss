# -*- coding: utf-8 -*-
"""Test van een knockout met voorronde (bv. 12 teams door i.p.v. 8 of 16).

    python test_voorronde.py

Gecontroleerd:

  1. Opbouw      — voor elk aantal van 4 t/m 32: de juiste vrijgelote top, de
                   juiste voorrondeaffiches (5–12, 6–11, ...) en elk team
                   precies één keer in het schema.
  2. Uitspelen   — wint telkens de hoogst geplaatste, dan speelt 1 tegen 2 in
                   de finale en zitten de beste 4 in de halve finales.
  3. Volledig    — 17 teams, 12 door: bracketfase, voorronde, kwartfinale, ...
                   tot de finale, met kansberekening, toernooipagina en beamer.
  4. Wissen      — een voorrondeuitslag wissen haalt de winnaar weer uit de
                   kwartfinale; het vrijgelote team blijft staan.

Deze test raakt shuss.db NIET aan: hij werkt in een tijdelijke map.
"""

import json
import os
import random
import shutil
import sys
import tempfile

WERKMAP = tempfile.mkdtemp(prefix="schuss_voorronde_")
BRON = os.path.dirname(os.path.abspath(__file__))
for naam in ("app.py", "database.py", "elo.py", "tournament.py", "kansen.py"):
    shutil.copy(os.path.join(BRON, naam), WERKMAP)
shutil.copytree(os.path.join(BRON, "templates"), os.path.join(WERKMAP, "templates"))
shutil.copytree(os.path.join(BRON, "static"), os.path.join(WERKMAP, "static"),
                ignore=shutil.ignore_patterns("uploads"))
sys.path.insert(0, WERKMAP)
os.chdir(WERKMAP)

import app as schuss                                             # noqa: E402
import kansen                                                    # noqa: E402
import tournament as tm                                          # noqa: E402
from database import init_db, verbind                            # noqa: E402
from elo import KLEINE_FINALE, fase_omschrijving                 # noqa: E402

FOUTEN = []
kansen.REKENTIJD = 0.3
kansen.MIN_AFLOOPJES = 1500
kansen.AFLOOPJES_VOORAF = 800


def meld(voorwaarde, tekst):
    print(("  ok    " if voorwaarde else "  FOUT  ") + tekst)
    if not voorwaarde:
        FOUTEN.append(tekst)


def bouw(n_teams, ronden, ko, tafels=4, pad=None):
    pad = pad or os.path.join(WERKMAP, f"v_{n_teams}_{ko}_{random.random()}.db")
    init_db(pad)
    db = verbind(pad)
    for i in range(2 * n_teams):
        db.execute("INSERT INTO players (name, nickname) VALUES (?, '')", (f"S{i + 1:03d}",))
    for t in range(n_teams):
        db.execute("INSERT INTO teams (name, player1_id, player2_id, status) "
                   "VALUES (?, ?, ?, 'actief')", (f"T{t + 1:02d}", 2 * t + 1, 2 * t + 2))
    db.execute("""INSERT INTO tournaments (name, date, start_tijd, bracket_ronden,
                  ko_teams, potten, slot_minuten)
                  VALUES ('Test', '2026-03-07', '19:00', ?, ?, 4, 20)""", (ronden, ko))
    for t in range(1, n_teams + 1):
        db.execute("INSERT INTO tournament_teams (tournament_id, team_id) VALUES (1, ?)", (t,))
    for i in range(tafels):
        db.execute("INSERT INTO tournament_locations (tournament_id, name) VALUES (1, ?)",
                   (f"Tafel {i + 1}",))
    db.commit()
    return db


def ko_games(db):
    return db.execute("SELECT * FROM games WHERE tournament_id = 1 AND fase = 'knockout' "
                      "ORDER BY ronde DESC, positie").fetchall()


# ---------------------------------------------------------------------------
print("1. Opbouw van het schema, 4 t/m 32 teams")
meld(not tm.geldig_ko_aantal(3) and tm.geldig_ko_aantal(2) and tm.geldig_ko_aantal(12),
     "2 en 12 mogen, 3 niet")
meld(tm.voorronde_info(12) == (4, 8, 8), "12: top 4 vrij, 8 in de voorronde, kwartfinale")
meld(tm.voorronde_info(8) == (8, 0, 8), "8: geen voorronde")
meld(fase_omschrijving("knockout", 12) == "Voorronde", "ronde 12 heet 'Voorronde'")
meld(fase_omschrijving("knockout", 8) == "Kwartfinale", "ronde 8 blijft 'Kwartfinale'")

alles_ok = {"opbouw": True, "uitspelen": True}
for n in range(4, 33):
    db = bouw(n, 1, n)
    t = tm.toernooi(db, 1)
    geplaatst = list(range(1, n + 1))                  # team-id = seed
    tm.maak_knockout(db, t, geplaatst)
    games = ko_games(db)
    vrij, voor, volle = tm.voorronde_info(n)
    voorronde = [g for g in games if g["ronde"] == n and voor]
    volle_ronde = [g for g in games if g["ronde"] == volle]
    # Elk team één keer in het startschema: voorronde + vaste plaatsen in de volle ronde.
    gevoed = {(g["volgende_game_id"], g["volgende_slot"]) for g in voorronde}
    in_schema = [x for g in voorronde for x in (g["team1_id"], g["team2_id"])]
    for g in volle_ronde:
        for slot in (1, 2):
            if (g["id"], slot) not in gevoed:
                in_schema.append(g[f"team{slot}_id"])
    ok = sorted(in_schema) == geplaatst
    ok &= len(voorronde) == voor // 2
    # Voorronde: seed s tegen seed (vrij + 1 + n) - s.
    ok &= all(g["team1_id"] + g["team2_id"] == vrij + 1 + n for g in voorronde)
    # Vrijgelote teams = precies de top.
    vrije = sorted(x for x in in_schema if x not in
                   {y for g in voorronde for y in (g["team1_id"], g["team2_id"])})
    ok &= vrije == list(range(1, vrij + 1))
    # Elke voorronde mondt uit in een vak dat nog leeg is.
    ok &= all(db.execute("SELECT team%d_id FROM games WHERE id = ?" % g["volgende_slot"],
                         (g["volgende_game_id"],)).fetchone()[0] is None for g in voorronde)
    # Geen gaten op de kalender: voorronde vóór de volle ronde.
    if voorronde:
        ok &= max(g["scheduled_at"] for g in voorronde) < min(g["scheduled_at"]
                                                              for g in volle_ronde)
    if not ok:
        alles_ok["opbouw"] = False
        print(f"     opbouw met {n} teams klopt niet")

    # 2. Uitspelen: de hoogst geplaatste (laagste id) wint altijd.
    db.execute("UPDATE tournaments SET status = 'knockout' WHERE id = 1")
    db.commit()
    for _ in range(10):
        open_ = [g for g in ko_games(db) if g["status"] == "gepland"
                 and g["team1_id"] and g["team2_id"]]
        if not open_:
            break
        for g in open_:
            w = min(g["team1_id"], g["team2_id"])
            db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, "
                       "played_at = scheduled_at WHERE id = ?", (w, g["id"]))
        db.commit()
        tm.evalueer(db, 1)
    finale = db.execute("SELECT * FROM games WHERE tournament_id = 1 AND fase = 'knockout' "
                        "AND ronde = 2").fetchone()
    halve = db.execute("SELECT * FROM games WHERE tournament_id = 1 AND fase = 'knockout' "
                       "AND ronde = 4").fetchall()
    ok = {finale["team1_id"], finale["team2_id"]} == {1, 2} and finale["winner_team_id"] == 1
    ok &= {x for g in halve for x in (g["team1_id"], g["team2_id"])} == {1, 2, 3, 4}
    ok &= tm.toernooi(db, 1)["status"] == "afgelopen"
    if not ok:
        alles_ok["uitspelen"] = False
        print(f"     uitspelen met {n} teams klopt niet")
    db.close()
meld(alles_ok["opbouw"], "opbouw klopt voor elk aantal van 4 t/m 32")
meld(alles_ok["uitspelen"], "met de favoriet als winnaar: 1–2 in de finale, top 4 in de halve")

# ---------------------------------------------------------------------------
print("3. Volledig toernooi: 17 teams, 12 door")
# Op de database van de app zelf, zodat ook de pagina's getest kunnen worden.
schuss.DB_PATH = os.path.join(WERKMAP, "volledig.db")
db = bouw(17, 4, 12, pad=schuss.DB_PATH)
meld(tm.controleer(db, 1) is None, "de opzet is geldig")
ok, boodschap = tm.genereer(db, 1, rng=random.Random(11))
meld(ok, f"geloot: {boodschap}")
rng = random.Random(5)

u = kansen.bereken(db, 1)
meld(u is not None, "kansen worden berekend tijdens de bracketfase")
if u:
    grootten = [r["grootte"] for r in u["rondes"]]
    meld(grootten == [12, 8, 4, 2, 1], f"rondes in de kansen: {grootten}")
    meld(u["rondes"][0]["label"] == "Knockout", "eerste kanskolom heet 'Knockout'")
    for gr, verwacht in ((12, 12), (8, 8), (4, 4), (2, 2), (1, 1)):
        som = sum(k[gr] for k in u["kansen"].values())
        meld(abs(som - verwacht) < 1e-9, f"kansen sluiten: {verwacht} plaatsen voor ronde {gr}")

for _ in range(20):
    open_ = db.execute("SELECT * FROM games WHERE tournament_id = 1 AND status = 'gepland' "
                       "AND fase IN ('bracket', 'shootout') AND team1_id IS NOT NULL "
                       "ORDER BY scheduled_at, id").fetchall()
    if not open_:
        break
    for g in open_:
        db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, played_at = ? "
                   "WHERE id = ?", (rng.choice([g["team1_id"], g["team2_id"]]),
                                    g["scheduled_at"], g["id"]))
    db.commit()
    schuss.herbereken_alles(db)
    tm.evalueer(db, 1)

meld(tm.toernooi(db, 1)["status"] == "knockout", "na de bracketfase: knockout")
geordend, _ = tm.stand(db, 1)
seed = {r["team_id"]: i for i, r in enumerate(geordend, start=1)}
games = ko_games(db)
voor = [g for g in games if g["ronde"] == 12]
kwart = [g for g in games if g["ronde"] == 8]
meld(len(voor) == 4 and len(kwart) == 4, "4 voorrondewedstrijden en 4 kwartfinales")
meld(sorted(tuple(sorted((seed[g["team1_id"]], seed[g["team2_id"]]))) for g in voor)
     == [(5, 12), (6, 11), (7, 10), (8, 9)], "voorronde: 5–12, 6–11, 7–10, 8–9")
meld(sorted(seed[g["team1_id"]] for g in kwart) == [1, 2, 3, 4] or
     sorted(seed[x] for g in kwart for x in (g["team1_id"], g["team2_id"]) if x) == [1, 2, 3, 4],
     "de top 4 staat al in de kwartfinale")
kwart1 = next(g for g in kwart if seed.get(g["team1_id"]) == 1 or seed.get(g["team2_id"]) == 1)
v89 = next(g for g in voor if {seed[g["team1_id"]], seed[g["team2_id"]]} == {8, 9})
meld(v89["volgende_game_id"] == kwart1["id"], "de winnaar van 8–9 speelt tegen nummer 1")

u = kansen.bereken(db, 1)
meld(u is not None, "kansen tijdens de knockout")
if u:
    for gr in (12, 8, 4, 2, 1):
        som = sum(k[gr] for k in u["kansen"].values())
        meld(abs(som - gr) < 1e-9, f"knockout: {gr} plaatsen voor ronde {gr} (som {som:.3f})")
    top = geordend[0]["team_id"]
    meld(u["kansen"][top][12] == 1.0 and u["kansen"][top][8] == 1.0,
         "nummer 1 zit zeker in de knockout én de kwartfinale")
    s9 = geordend[8]["team_id"]
    meld(u["kansen"][s9][12] == 1.0 and 0 < u["kansen"][s9][8] < 1,
         "nummer 9 zit in de knockout, de kwartfinale is nog onzeker")
    meld(abs(sum(k[1] for k in kansen.bereken_vooraf(db, 1)["kansen"].values()) - 1) < 1e-9,
         "de voorspelling vooraf werkt ook met 12")

# Weergave: toernooipagina en beamer.
klant = schuss.app.test_client()
html = klant.get("/toernooi/1").get_data(as_text=True)
meld("Voorronde" in html, "de knockouttab toont een kolom 'Voorronde'")
meld("bracket-cel-voorronde" in html, "de voorrondewedstrijden hebben hun eigen opmaak")
meld("vrijgeloot" in html, "in de stand staat wie vrijgeloot is")
data = json.loads(klant.get("/toernooi/1/beamer.json").get_data(as_text=True))
kol = data["knockout"]["kolommen"]
meld([k["titel"] for k in kol] == ["Voorronde", "Kwartfinale", "Halve finale", "Finale"],
     f"beamer: kolommen {[k['titel'] for k in kol]}")
meld(kol[0]["voorronde"] and kol[0]["plaatsen"] == 8, "beamer: voorronde met 8 vakken")
meld(data["vrij"] == 4, "beamer: de top 4 is vrijgeloot")

# 4. Een voorrondeuitslag invoeren en weer wissen.
print("4. Wissen")
g = voor[0]
w = g["team1_id"]
db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, played_at = scheduled_at "
           "WHERE id = ?", (w, g["id"]))
db.commit()
tm.evalueer(db, 1)
kw = db.execute("SELECT * FROM games WHERE id = ?", (g["volgende_game_id"],)).fetchone()
vast = kw["team2_id"] if g["volgende_slot"] == 1 else kw["team1_id"]
meld(w in (kw["team1_id"], kw["team2_id"]) and vast is not None,
     "de winnaar schuift naar de kwartfinale, naast het vrijgelote team")
meld(tm.mag_wissen(db, g) is None, "de voorrondeuitslag mag nog gewist worden")
db.execute("UPDATE games SET status = 'gepland', winner_team_id = NULL, played_at = NULL "
           "WHERE id = ?", (g["id"],))
db.commit()
tm.herstel_na_wissen(db, 1, "knockout", g)
tm.evalueer(db, 1)
kw = db.execute("SELECT * FROM games WHERE id = ?", (g["volgende_game_id"],)).fetchone()
meld(w not in (kw["team1_id"], kw["team2_id"]), "na wissen is de winnaar weer weg")
meld(vast in (kw["team1_id"], kw["team2_id"]), "het vrijgelote team blijft staan")

# Uitspelen tot het einde.
for _ in range(10):
    open_ = [g for g in ko_games(db) if g["status"] == "gepland"
             and g["team1_id"] and g["team2_id"]]
    if not open_:
        break
    for g in open_:
        db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, "
                   "played_at = scheduled_at WHERE id = ?",
                   (rng.choice([g["team1_id"], g["team2_id"]]), g["id"]))
    db.commit()
    schuss.herbereken_alles(db)
    tm.evalueer(db, 1)
meld(tm.toernooi(db, 1)["status"] == "afgelopen", "het toernooi raakt tot het einde")
u = kansen.bereken(db, 1)
meld(u and all(k[gr] in (0.0, 1.0) for k in u["kansen"].values() for gr in (12, 8, 4, 2, 1)),
     "na afloop zijn alle kansen 0% of 100%")
meld(klant.get("/toernooi/1").status_code == 200, "de toernooipagina werkt na afloop")

print("\n" + "=" * 60)
if FOUTEN:
    print(f"❌ {len(FOUTEN)} test(s) gefaald:")
    for f in FOUTEN:
        print("   -", f)
    sys.exit(1)
print("✅ Alle tests geslaagd.")
print("=" * 60)

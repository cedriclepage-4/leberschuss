# -*- coding: utf-8 -*-
"""Test van de kansberekening (kansen.py).

    python test_kansen.py

Gecontroleerd:

  1. Sterkte     — twee teams van 1000 met sterkere spelers zijn géén 50-50.
  2. Sluitend    — de kansen tellen op: exact `ko_teams` tickets, één kampioen,
                   en verder geraken is nooit waarschijnlijker dan een ronde eerder.
  3. Feiten      — een afgelopen toernooi geeft enkel 0% en 100%, en die kloppen
                   met wat er echt gebeurde.
  4. Knockout    — wie zijn knockoutwedstrijd verloor staat op 0%, wie won is
                   zeker van de volgende ronde.
  5. Logisch     — een duidelijk sterker team heeft een grotere titelkans.
  6. Vooraf      — de voorspelling van vóór de aftrap gebruikt de ratings van bij
                   de aftrap, vult een sluitend schema in (één kampioen, ...) en
                   verandert niet meer naarmate er gespeeld wordt.

Deze test raakt shuss.db NIET aan: hij werkt in een tijdelijke map.
"""

import os
import random
import shutil
import sys
import tempfile

WERKMAP = tempfile.mkdtemp(prefix="schuss_kansen_")
BRON = os.path.dirname(os.path.abspath(__file__))
for naam in ("app.py", "database.py", "elo.py", "tournament.py", "kansen.py"):
    shutil.copy(os.path.join(BRON, naam), WERKMAP)
sys.path.insert(0, WERKMAP)
os.chdir(WERKMAP)

import app as schuss                                             # noqa: E402
import kansen                                                    # noqa: E402
import tournament as tm                                          # noqa: E402
from database import init_db, verbind                            # noqa: E402

FOUTEN = []
kansen.REKENTIJD = 0.3
kansen.MIN_AFLOOPJES = 1500
kansen.AFLOOPJES_VOORAF = 800


def meld(voorwaarde, tekst):
    print(("  ok    " if voorwaarde else "  FOUT  ") + tekst)
    if not voorwaarde:
        FOUTEN.append(tekst)


def bouw(n_teams, ronden, ko, team_elos, speler_elos=None):
    pad = os.path.join(WERKMAP, f"k_{n_teams}_{ronden}_{ko}_{random.random()}.db")
    init_db(pad)
    db = verbind(pad)
    for i in range(2 * n_teams):
        elo = speler_elos[i] if speler_elos else 1000
        db.execute("INSERT INTO players (name, nickname, elo) VALUES (?, '', ?)",
                   (f"S{i + 1:03d}", elo))
    for t in range(n_teams):
        db.execute("INSERT INTO teams (name, player1_id, player2_id, status, elo) "
                   "VALUES (?, ?, ?, 'actief', ?)",
                   (f"T{t + 1:02d}", 2 * t + 1, 2 * t + 2, team_elos[t]))
    db.execute("""INSERT INTO tournaments (name, date, start_tijd, bracket_ronden,
                  ko_teams, potten, slot_minuten)
                  VALUES ('Test', '2026-03-07', '19:00', ?, ?, 4, 20)""", (ronden, ko))
    for t in range(1, n_teams + 1):
        db.execute("INSERT INTO tournament_teams (tournament_id, team_id) VALUES (1, ?)", (t,))
    for i in range(4):
        db.execute("INSERT INTO tournament_locations (tournament_id, name) VALUES (1, ?)",
                   (f"Tafel {i + 1}",))
    db.commit()
    ok, boodschap = tm.genereer(db, 1, rng=random.Random(7))
    assert ok, boodschap
    return db


def speel(db, fase, rng, maximum=None):
    """Speel openstaande wedstrijden van een fase (hoogstens `maximum`)."""
    games = db.execute("SELECT * FROM games WHERE tournament_id = 1 AND fase = ? AND "
                       "status = 'gepland' AND team1_id IS NOT NULL AND team2_id IS NOT NULL "
                       "ORDER BY COALESCE(ronde, 0) DESC, scheduled_at, id",
                       (fase,)).fetchall()
    if fase == "bracket":
        games = sorted(games, key=lambda g: (g["scheduled_at"], g["id"]))
    for g in games[:maximum]:
        db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, played_at = ? "
                   "WHERE id = ?", (rng.choice([g["team1_id"], g["team2_id"]]),
                                    g["scheduled_at"], g["id"]))
    db.commit()
    schuss.herbereken_alles(db)
    tm.evalueer(db, 1)


def sluitend(u, cut, tekst):
    som_ko = sum(k[cut] for k in u["kansen"].values())
    som_kamp = sum(k[1] for k in u["kansen"].values())
    meld(abs(som_ko - cut) < 1e-9, f"{tekst}: {cut} knockouttickets (som {som_ko:.3f})")
    meld(abs(som_kamp - 1) < 1e-9, f"{tekst}: één kampioen (som {som_kamp:.3f})")
    grootten = [r["grootte"] for r in u["rondes"]]
    meld(all(k[a] >= k[b] for k in u["kansen"].values() for a, b in zip(grootten, grootten[1:])),
         f"{tekst}: verder geraken is nooit waarschijnlijker dan de ronde ervoor")


# ---------------------------------------------------------------------------
print("1. Sterkte van een kant")
db = bouw(8, 3, 4, [1000] * 8, [1200, 1200] + [1000] * 14)
sterkte = kansen.sterktes(db, 1)
p = kansen.winstkans(sterkte, 1, 2)
meld(p > 0.6, f"team 1 (spelers van 1200) tegen team 2 (spelers van 1000): {p:.0%} en geen 50%")
meld(abs(kansen.winstkans(sterkte, 2, 3) - 0.5) < 1e-9, "twee identieke teams: 50%")

print("2. Vóór de eerste wedstrijd")
# Verschillen in sterkte komen, net als op de echte site, uit eerdere
# wedstrijden: herbereken_alles bouwt elke rating op vanaf 1000. Team k
# (1..5) klopt in de league (6 - k) * 3 keer team 11 - k.
db = bouw(10, 4, 4, [1000] * 10)
for k in range(1, 6):
    for i in range((6 - k) * 3):
        db.execute("INSERT INTO games (team1_id, team2_id, fase, scheduled_at, played_at, "
                   "status, winner_team_id) VALUES (?, ?, 'liga', ?, ?, 'gespeeld', ?)",
                   (k, 11 - k, f"2026-01-{k:02d}T20:{i:02d}", f"2026-01-{k:02d}T20:{i:02d}", k))
db.commit()
schuss.herbereken_alles(db)
u = kansen.bereken(db, 1)
sluitend(u, 4, "begin")
meld(u["kansen"][1][1] > u["kansen"][10][1] * 3,
     f"sterkste team heeft een veel grotere titelkans ({u['kansen'][1][1]:.0%} tegen "
     f"{u['kansen'][10][1]:.1%})")
meld(u["ranglijst"][0] == 1, "het sterkste team staat bovenaan de ranglijst")
meld(kansen.bereken(db, 1) is u, "tweede keer: uit de bewaarde berekening")
sterkte_aftrap = kansen.sterktes(db, 1)
voorspeld_aftrap = kansen.voorspellingen(kansen.bereken_vooraf(db, 1))
labels = sorted(f["label"] for f in voorspeld_aftrap.values())
meld(labels.count("Kampioen") == 1 and labels.count("Finale") == 1
     and labels.count("Halve finale") == 2 and labels.count("Bracketfase") == 6,
     f"voorspeld schema sluit: 1 kampioen, 1 finalist, 2 halvefinalisten, 6 uit ({labels})")
meld(voorspeld_aftrap[1]["label"] == "Kampioen", "het sterkste team is de voorspelde kampioen")

print("3. Halverwege de bracketfase")
rng = random.Random(3)
speel(db, "bracket", rng, maximum=10)
u2 = kansen.bereken(db, 1)
meld(u2 is not u, "na een uitslag wordt opnieuw gerekend")
sluitend(u2, 4, "halverwege")

print("4. Bracketfase uitgespeeld, knockout begonnen")
speel(db, "bracket", rng)
while tm._open_shootouts(db, 1):
    speel(db, "shootout", rng)
t = tm.toernooi(db, 1)
meld(t["status"] == "knockout", f"toernooi staat in de knockout ({t['status']})")
u3 = kansen.bereken(db, 1)
sluitend(u3, 4, "knockout")
in_ko = {x for g in tm.games_van(db, 1, "knockout") if g["ronde"] == 4
         for x in (g["team1_id"], g["team2_id"])}
meld(all((u3["kansen"][x][4] == 1.0) == (x in in_ko) for x in u3["kansen"]),
     "precies de knockoutteams staan op 100% voor de knockout, de rest op 0%")
halve = [g for g in tm.games_van(db, 1, "knockout") if g["ronde"] == 4]
speel(db, "knockout", rng, maximum=1)
g = db.execute("SELECT * FROM games WHERE id = ?", (halve[0]["id"],)).fetchone()
u4 = kansen.bereken(db, 1)
verliezer = g["team1_id"] if g["winner_team_id"] == g["team2_id"] else g["team2_id"]
meld(u4["kansen"][g["winner_team_id"]][2] == 1.0, "winnaar halve finale: 100% finale")
meld(u4["kansen"][verliezer][2] == 0.0 and u4["kansen"][verliezer][1] == 0.0,
     "verliezer halve finale: 0% finale en 0% kampioen")
stap = kansen.volgende_stap(u4, g["winner_team_id"])
meld(stap and stap["grootte"] == 1, "volgende stap van de finalist is de titel")
meld(kansen.volgende_stap(u4, verliezer) is None, "uitgeschakeld: geen volgende stap")

print("4b. Vooraf blijft vooraf")
vooraf_sterkte = kansen.sterktes_vooraf(db, 1)
meld(all(abs(vooraf_sterkte[x] - sterkte_aftrap[x]) < 1e-6 for x in sterkte_aftrap),
     "sterktes vooraf = de sterktes van bij de aftrap, ook al zijn de ratings intussen veranderd")
meld(any(abs(kansen.sterktes(db, 1)[x] - sterkte_aftrap[x]) > 1 for x in sterkte_aftrap),
     "(en die ratings zijn wel degelijk veranderd)")
kansen._cache_vooraf.clear()
meld(kansen.voorspellingen(kansen.bereken_vooraf(db, 1)) == voorspeld_aftrap,
     "opnieuw berekend geeft de voorspelling exact dezelfde uitkomst")

print("5. Afgelopen toernooi")
while tm.toernooi(db, 1)["status"] != "afgelopen":
    speel(db, "knockout", rng)
u5 = kansen.bereken(db, 1)
meld(u5["afloopjes"] == 1, "niets meer te spelen: één afloopje volstaat")
meld(all(k in (0.0, 1.0) for per in u5["kansen"].values() for k in per.values()),
     "enkel feiten: 0% of 100%")
finale = db.execute("SELECT winner_team_id FROM games WHERE tournament_id = 1 AND "
                    "fase = 'knockout' AND ronde = 2").fetchone()[0]
meld(u5["kansen"][finale][1] == 1.0 and u5["ranglijst"][0] == finale,
     "de echte winnaar staat op 100% en bovenaan")
meld(kansen.eindfase(u5, finale)["label"] == "Kampioen", "eindfase van de winnaar: Kampioen")
eind = sorted(kansen.eindfase(u5, x)["label"] for x in u5["kansen"])
meld(eind.count("Finale") == 1 and eind.count("Halve finale") == 2 and eind.count("Bracketfase") == 6,
     f"eindfases sluiten ({eind})")

print()
if FOUTEN:
    print(f"{len(FOUTEN)} fout(en).")
    sys.exit(1)
print("Alles in orde.")

# -*- coding: utf-8 -*-
"""Een team dat later komt: wedstrijden opschuiven in plaats van forfaits.

    python test_laatkomer.py

Controleert `later_inplannen()`:

  1. er verdwijnt niets en er komt niets bij: dezelfde affiches, dezelfde id's;
  2. wat gespeeld is en wat nu op tafel ligt (zonder de laatkomer) blijft staan;
  3. de laatkomer speelt niet in de speelbeurten die hij mist;
  4. niemand staat twee keer in dezelfde speelbeurt, nooit meer wedstrijden dan
     tafels, nooit twee wedstrijden op dezelfde tafel;
  5. de speelrondes per team lopen netjes op in de volgorde van de kalender;
  6. het toernooi kan gewoon uitgespeeld worden;
  7. een wedstrijd waarvoor al een uitslag gemeld is, verschuift niet.

Deze test raakt shuss.db NIET aan: hij werkt in een tijdelijke map.
"""

import collections
import os
import random
import shutil
import sys
import tempfile

WERKMAP = tempfile.mkdtemp(prefix="schuss_laat_")
BRON = os.path.dirname(os.path.abspath(__file__))
for naam in ("app.py", "database.py", "elo.py", "tournament.py"):
    shutil.copy(os.path.join(BRON, naam), WERKMAP)
shutil.copytree(os.path.join(BRON, "templates"), os.path.join(WERKMAP, "templates"))
shutil.copytree(os.path.join(BRON, "static"), os.path.join(WERKMAP, "static"),
                ignore=shutil.ignore_patterns("uploads"))
sys.path.insert(0, WERKMAP)
os.chdir(WERKMAP)

import app as schuss                                             # noqa: E402
import tournament as tm                                          # noqa: E402
from database import init_db, verbind                            # noqa: E402

FOUTEN = []
TEL = collections.Counter()


def meld(voorwaarde, tekst):
    if not voorwaarde:
        FOUTEN.append(tekst)


def bouw(n, ronden, ko, tafels, zaad):
    pad = os.path.join(WERKMAP, f"l{n}_{ronden}_{tafels}_{zaad}.db")
    if os.path.exists(pad):
        os.remove(pad)
    init_db(pad)
    db = verbind(pad)
    for i in range(1, 2 * n + 1):
        db.execute("INSERT INTO players (name, nickname) VALUES (?, '')", (f"S{i:03d}",))
    for t in range(n):
        db.execute("INSERT INTO teams (name, player1_id, player2_id, status, elo) "
                   "VALUES (?, ?, ?, 'actief', 1000)",
                   (f"T{t + 1:02d}", 2 * t + 1, 2 * t + 2))
    db.execute("""INSERT INTO tournaments (name, date, start_tijd, bracket_ronden,
                  ko_teams, potten, slot_minuten)
                  VALUES ('Test', '2026-09-05', '20:00', ?, ?, 4, 20)""", (ronden, ko))
    for t in range(1, n + 1):
        db.execute("INSERT INTO tournament_teams (tournament_id, team_id) VALUES (1, ?)", (t,))
    for i in range(tafels):
        db.execute("INSERT INTO tournament_locations (tournament_id, name) "
                   "VALUES (1, ?)", (f"Tafel {i + 1}",))
    db.commit()
    ok, _ = tm.genereer(db, 1, rng=random.Random(zaad))
    if not ok:
        db.close()
        os.remove(pad)
        return None, None
    return db, pad


def bracket(db):
    return {g["id"]: dict(g) for g in db.execute(
        "SELECT * FROM games WHERE tournament_id = 1 AND fase = 'bracket'")}


def open_tijden(db):
    return sorted({g["scheduled_at"] for g in db.execute(
        "SELECT scheduled_at FROM games WHERE tournament_id = 1 AND fase = 'bracket' "
        "AND status = 'gepland'")})


def speel_slots(db, aantal, rng):
    for slot in open_tijden(db)[:aantal]:
        for g in db.execute("SELECT * FROM games WHERE tournament_id = 1 AND "
                            "fase = 'bracket' AND scheduled_at = ? AND status = 'gepland'",
                            (slot,)).fetchall():
            db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, "
                       "played_at = ? WHERE id = ?",
                       (rng.choice([g["team1_id"], g["team2_id"]]), g["scheduled_at"], g["id"]))
    db.commit()
    schuss.herbereken_alles(db)
    tm.evalueer(db, 1)


def speel_uit(db, rng):
    for _ in range(60):
        rijen = db.execute("SELECT * FROM games WHERE tournament_id = 1 AND "
                           "status = 'gepland' AND team1_id IS NOT NULL "
                           "ORDER BY scheduled_at, id").fetchall()
        if not rijen:
            return
        for g in rijen:
            db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, "
                       "played_at = ? WHERE id = ?",
                       (rng.choice([g["team1_id"], g["team2_id"]]),
                        g["scheduled_at"] or "2026-09-05T23:00", g["id"]))
        db.commit()
        schuss.herbereken_alles(db)
        tm.evalueer(db, 1)


def controleer(db, voor, laat, beurten, tafels, etiket):
    na = bracket(db)
    meld(set(voor) == set(na), f"{etiket}: er verdwenen of kwamen wedstrijden bij")
    for gid, g in voor.items():
        h = na.get(gid)
        if not h:
            continue
        meld((g["team1_id"], g["team2_id"]) == (h["team1_id"], h["team2_id"]),
             f"{etiket}: affiche van wedstrijd {gid} veranderde")
        if g["status"] == "gespeeld":
            meld(g["scheduled_at"] == h["scheduled_at"] and h["status"] == "gespeeld",
                 f"{etiket}: gespeelde wedstrijd {gid} werd verschoven")

    # Wat nu op tafel lag (zonder de laatkomer) blijft exact staan.
    huidige = min((g["scheduled_at"] for g in voor.values() if g["status"] == "gepland"))
    for gid, g in voor.items():
        if (g["status"] == "gepland" and g["scheduled_at"] == huidige
                and laat not in (g["team1_id"], g["team2_id"])):
            meld(na[gid]["scheduled_at"] == huidige
                 and na[gid]["location_id"] == g["location_id"],
                 f"{etiket}: lopende wedstrijd {gid} werd verschoven")

    # De laatkomer speelt niet in de speelbeurten die hij mist.
    tijden = open_tijden(db)
    meld(tijden[0] == huidige, f"{etiket}: het programma begint niet meer bij de huidige beurt")
    eigen = sorted(g["scheduled_at"] for g in na.values()
                   if g["status"] == "gepland" and laat in (g["team1_id"], g["team2_id"]))
    if eigen:
        ervoor = tijden.index(eigen[0])
        anderen_erna = any(g["scheduled_at"] >= eigen[0] and laat not in (g["team1_id"], g["team2_id"])
                           for g in na.values() if g["status"] == "gepland")
        # Zijn alle andere wedstrijden al voorbij, dan wacht men gewoon op hen.
        meld(ervoor >= beurten or not anderen_erna,
             f"{etiket}: laatkomer speelt al in speelbeurt {ervoor + 1} (mist er {beurten})")
        TEL["na elkaar (laatkomer)"] += sum(
            1 for x, y in zip(tijden, tijden[1:]) if x in eigen and y in eigen)

    per_slot = collections.defaultdict(list)
    tafels_per_slot = collections.defaultdict(list)
    for g in na.values():
        per_slot[g["scheduled_at"]] += [g["team1_id"], g["team2_id"]]
        tafels_per_slot[g["scheduled_at"]].append(g["location_id"])
    for slot, teams in per_slot.items():
        meld(len(set(teams)) == len(teams), f"{etiket}: een team speelt dubbel om {slot}")
        meld(len(teams) // 2 <= tafels, f"{etiket}: meer wedstrijden dan tafels om {slot}")
        locs = tafels_per_slot[slot]
        meld(None not in locs and len(set(locs)) == len(locs),
             f"{etiket}: tafels dubbel of leeg om {slot}: {locs}")

    # Speelrondes per team stijgen in kalendervolgorde.
    vorige = {}
    for g in sorted(na.values(), key=lambda g: (g["scheduled_at"], g["id"])):
        for kant in (g["team1_id"], g["team2_id"]):
            meld(g["ronde"] > vorige.get(kant, 0),
                 f"{etiket}: speelronde van team {kant} daalt")
            vorige[kant] = g["ronde"]
    return len(tijden)


print("=" * 62)
print("Een team dat later komt")
print("=" * 62)

print("\n== 1. Verschillende avonden, verschillende momenten ==")
vormen = [(14, 5, 8, 7), (12, 4, 4, 6), (15, 4, 8, 5), (16, 5, 8, 8),
          (13, 4, 4, 4), (20, 4, 8, 7), (9, 4, 4, 4), (10, 3, 4, 3)]
langer = collections.Counter()
boven = collections.Counter()
for n, ronden, ko, tafels in vormen:
    for zaad in (1, 2):
        for na_ronde in (0, 1):
            for beurten in (1, 2, 3):
                db, pad = bouw(n, ronden, ko, tafels, zaad)
                if db is None:
                    continue
                rng = random.Random(zaad * 17 + n + beurten)
                speel_slots(db, na_ronde, rng)
                laat = rng.randrange(1, n + 1)
                voor = bracket(db)
                beurten_voor = len(open_tijden(db))
                open_games = [g for g in voor.values() if g["status"] == "gepland"]
                per = collections.Counter(x for g in open_games for x in (g["team1_id"], g["team2_id"]))
                # Ondergrens: de laatkomer moet wachten en daarna al zijn wedstrijden spelen;
                # en meer wedstrijden dan tafels gaan niet in één beurt.
                ondergrens = max(beurten + per[laat], -(-len(open_games) // tafels), max(per.values()))
                etiket = f"n={n} r={ronden} t={tafels} zaad={zaad} na={na_ronde} mist={beurten}"
                ok, boodschap = tm.later_inplannen(db, 1, laat, beurten, rng=random.Random(zaad))
                meld(ok, f"{etiket}: geweigerd — {boodschap}")
                if ok:
                    beurten_na = controleer(db, voor, laat, beurten, tafels, etiket)
                    langer[beurten_na - beurten_voor] += 1
                    boven[beurten_na - ondergrens] += 1
                    meld(beurten_na <= ondergrens + 1,
                         f"{etiket}: {beurten_na} speelbeurten, terwijl {ondergrens} kon")
                    speel_uit(db, rng)
                    meld(tm.toernooi(db, 1)["status"] in ("knockout", "afgelopen"),
                         f"{etiket}: toernooi raakt niet af")
                    TEL["geslaagd"] += 1
                db.close()
                os.remove(pad)
print(f"  {TEL['geslaagd']} keer opgeschoven en uitgespeeld")
print("  extra speelbeurten: " + ", ".join(f"+{k}: {v}x" for k, v in sorted(langer.items())))
print("  boven de ondergrens: " + ", ".join(f"+{k}: {v}x" for k, v in sorted(boven.items())))

print("\n== 2. Twee keer na elkaar (eerst 1 beurt, dan nog eens 2) ==")
for zaad in (1, 2, 3):
    db, pad = bouw(14, 5, 8, 7, zaad)
    rng = random.Random(zaad)
    voor = bracket(db)
    ok1, b1 = tm.later_inplannen(db, 1, 5, 1, rng=random.Random(zaad))
    meld(ok1, f"eerste keer (zaad {zaad}): {b1}")
    tussen = bracket(db)
    ok2, b2 = tm.later_inplannen(db, 1, 5, 2, rng=random.Random(zaad + 1))
    meld(ok2, f"tweede keer (zaad {zaad}): {b2}")
    if ok1 and ok2:
        controleer(db, tussen, 5, 2, 7, f"tweede keer (zaad {zaad})")
        meld(set(voor) == set(bracket(db)), f"tweede keer (zaad {zaad}): wedstrijden veranderd")
        speel_uit(db, rng)
        meld(tm.toernooi(db, 1)["status"] in ("knockout", "afgelopen"),
             f"tweede keer (zaad {zaad}): toernooi raakt niet af")
    db.close()
    os.remove(pad)
print("  drie avonden doorgerekend")

print("\n== 3. Weigeringen ==")
db, pad = bouw(14, 5, 8, 7, 1)
# Een uitslag gemeld voor een latere wedstrijd van de laatkomer: niets mag veranderen.
g = db.execute("SELECT * FROM games WHERE tournament_id = 1 AND fase = 'bracket' "
               "AND (team1_id = 3 OR team2_id = 3) ORDER BY scheduled_at DESC").fetchone()
speler = db.execute("SELECT player1_id FROM teams WHERE id = 3").fetchone()[0]
db.execute("INSERT INTO game_reports (game_id, player_id, team_id, winner_team_id) "
           "VALUES (?, ?, 3, 3)", (g["id"], speler))
db.commit()
voor = bracket(db)
ok, bericht = tm.later_inplannen(db, 1, 3, 2)
meld(not ok and "gemeld" in bericht, f"melding: niet geweigerd of onduidelijk — {bericht}")
meld(voor == bracket(db), "melding: er veranderde toch iets")
ok, bericht = tm.later_inplannen(db, 1, 4, 0)
meld(not ok, "0 speelbeurten werd aanvaard")
db.execute("UPDATE tournaments SET status = 'opzet' WHERE id = 1")
db.commit()
ok, bericht = tm.later_inplannen(db, 1, 4, 1)
meld(not ok, "buiten de bracketfase werd aanvaard")
db.close()
os.remove(pad)
print("  weigeringen gecontroleerd")

print("\n== 4. Via het organisatiepaneel ==")
db, pad = bouw(14, 5, 8, 7, 1)
db.close()
shutil.move(pad, schuss.DB_PATH)                 # de database die de site zelf gebruikt
db = verbind(schuss.DB_PATH)
db.execute("UPDATE players SET role = 'eigenaar' WHERE id = 1")
db.commit()
schuss.app.config["TESTING"] = True
klant = schuss.app.test_client()
with klant.session_transaction() as s:
    s["speler_id"] = 1
pagina = klant.get("/admin/toernooi/1").get_data(as_text=True)
meld("Komt later…" in pagina and "/teams/6/later" in pagina,
     "organisatiepaneel toont de knop 'Komt later' niet")
voor = bracket(db)
antwoord = klant.post("/admin/toernooi/1/teams/6/later", data={"beurten": "2"},
                      follow_redirects=True)
tekst = antwoord.get_data(as_text=True)
meld(antwoord.status_code == 200 and "niemand krijgt een forfait" in tekst,
     f"opschuiven via het paneel: {antwoord.status_code}, geen bevestiging getoond")
db.close()
db = verbind(schuss.DB_PATH)
controleer(db, voor, 6, 2, 7, "via het paneel")
antwoord = klant.post("/admin/toernooi/1/teams/6/later", data={"beurten": "x"},
                      follow_redirects=True)
meld("minstens 1" in antwoord.get_data(as_text=True), "ongeldige invoer niet netjes geweigerd")
db.close()
print("  pagina en knop gecontroleerd")

print("\n" + "=" * 62)
if FOUTEN:
    print(f"❌ {len(FOUTEN)} probleem(en):")
    for f in FOUTEN[:20]:
        print("  -", f)
else:
    print("✅ Alles klopt: niets geschrapt, niemand dubbel, laatkomer pas later aan de beurt.")
print("=" * 62)
shutil.rmtree(WERKMAP, ignore_errors=True)
sys.exit(1 if FOUTEN else 0)

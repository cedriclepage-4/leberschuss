# -*- coding: utf-8 -*-
"""Een team dat niet komt opdagen: schrappen en de rest opnieuw loten.

    python test_terugtrekken.py

Een forfaitzege is geen echte zege. Wie tegen de afwezige ploeg geloot was kreeg
drie punten cadeau, en dat beslist vaak over de laatste plaats in de knockout.
Deze test controleert of `terugtrekken()` dat rechtzet:

  1. de afwezige ploeg verdwijnt volledig, forfaits incluis;
  2. elk overblijvend team speelt exact even veel wedstrijden;
  3. niemand treft twee keer dezelfde tegenstander;
  4. wat al gespeeld is blijft staan, en de ronde die bezig is ook;
  5. het toernooi kan gewoon uitgespeeld worden.

Deze test raakt shuss.db NIET aan: hij werkt in een tijdelijke map.
"""

import collections
import os
import random
import shutil
import sys
import tempfile

WERKMAP = tempfile.mkdtemp(prefix="schuss_forfait_")
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
    pad = os.path.join(WERKMAP, f"f{n}_{ronden}_{tafels}_{zaad}.db")
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
    ok, boodschap = tm.genereer(db, 1, rng=random.Random(zaad))
    if not ok:
        db.close()
        os.remove(pad)
        return None, None
    return db, pad


def speel_slots(db, aantal, weg, rng):
    """Speel de eerste `aantal` speelrondes; tegen `weg` wordt forfait ingevuld."""
    slots = sorted({g["scheduled_at"] for g in db.execute(
        "SELECT scheduled_at FROM games WHERE tournament_id = 1 AND fase = 'bracket'")})
    for slot in slots[:aantal]:
        for g in db.execute("SELECT * FROM games WHERE tournament_id = 1 AND "
                            "fase = 'bracket' AND scheduled_at = ?", (slot,)).fetchall():
            if weg in (g["team1_id"], g["team2_id"]):
                w = g["team2_id"] if g["team1_id"] == weg else g["team1_id"]
            else:
                w = rng.choice([g["team1_id"], g["team2_id"]])
            db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, "
                       "played_at = ? WHERE id = ?", (w, g["scheduled_at"], g["id"]))
    db.commit()
    schuss.herbereken_alles(db)
    tm.evalueer(db, 1)
    return len(slots)


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


def controleer(db, n, weg, ronden, etiket):
    t = tm.toernooi(db, 1)
    doel = t["bracket_ronden"]
    per_team = collections.Counter()
    paren = collections.Counter()
    per_slot = collections.defaultdict(list)
    for g in db.execute("SELECT * FROM games WHERE tournament_id = 1 AND fase = 'bracket'"):
        per_team[g["team1_id"]] += 1
        per_team[g["team2_id"]] += 1
        paren[tuple(sorted((g["team1_id"], g["team2_id"])))] += 1
        per_slot[g["scheduled_at"]] += [g["team1_id"], g["team2_id"]]

    meld(per_team[weg] == 0,
         f"{etiket}: de teruggetrokken ploeg heeft nog {per_team[weg]} wedstrijden")
    blijvers = [t_ for t_ in range(1, n + 1) if t_ != weg]
    aantallen = {per_team[t_] for t_ in blijvers}
    meld(aantallen == {doel},
         f"{etiket}: niet iedereen speelt even veel — {sorted(aantallen)} (doel {doel})")
    meld(abs(doel - ronden) <= 1,
         f"{etiket}: {doel} wedstrijden per team wijkt te ver af van de {ronden} "
         f"die aangekondigd waren")
    dubbel = [p for p, v in paren.items() if v > 1]
    meld(not dubbel, f"{etiket}: affiche komt twee keer voor: {dubbel[:3]}")
    for slot, teams in per_slot.items():
        meld(len(set(teams)) == len(teams),
             f"{etiket}: een team speelt twee wedstrijden tegelijk om {slot}")
    stand, _ = tm.stand(db, 1)
    meld(len(stand) == n - 1, f"{etiket}: {len(stand)} teams in de stand i.p.v. {n - 1}")
    meld(weg not in {r["team_id"] for r in stand},
         f"{etiket}: de teruggetrokken ploeg staat nog in de stand")
    return doel


print("=" * 62)
print("Een team dat niet komt opdagen")
print("=" * 62)

print("\n== 1. Jouw avond: 14 teams, 7 tafels, 5 ronden ==")
for na_ronde in (0, 1, 2, 3, 4):
    for zaad in (1, 2, 3):
        db, pad = bouw(14, 5, 8, 7, zaad)
        if db is None:
            continue
        rng = random.Random(zaad * 13 + na_ronde)
        weg = rng.randrange(1, 15)
        speel_slots(db, na_ronde, weg, rng)
        # wat lag vast vóór het terugtrekken?
        vast_voor = {r["id"] for r in db.execute(
            "SELECT id FROM games WHERE tournament_id = 1 AND status = 'gespeeld'")}
        # De wedstrijden die nu op tafel liggen: die mogen NOOIT verdwijnen.
        open_slots = sorted({r["scheduled_at"] for r in db.execute(
            "SELECT scheduled_at FROM games WHERE tournament_id = 1 AND "
            "fase = 'bracket' AND status = 'gepland'")})
        lopend = {r["id"] for r in db.execute(
            "SELECT id FROM games WHERE tournament_id = 1 AND fase = 'bracket' "
            "AND status = 'gepland' AND scheduled_at = ? AND team1_id != ? AND team2_id != ?",
            (open_slots[0] if open_slots else "", weg, weg))}
        ok, boodschap = tm.terugtrekken(db, 1, weg, rng=random.Random(zaad))
        if not ok:
            # Weigeren mag — maar enkel in de laatste ronde, en dan moet er
            # ook echt niets veranderd zijn.
            meld(na_ronde >= 4,
                 f"na ronde {na_ronde} (zaad {zaad}): geweigerd terwijl het nog kon — "
                 f"{boodschap}")
            meld("te laat" in boodschap,
                 f"na ronde {na_ronde} (zaad {zaad}): onduidelijke weigering — {boodschap}")
            nu = {r["id"] for r in db.execute("SELECT id FROM games WHERE tournament_id = 1")}
            meld(lopend <= nu,
                 f"na ronde {na_ronde} (zaad {zaad}): er verdween een wedstrijd ondanks "
                 f"de weigering")
            TEL["geweigerd (laatste ronde)"] += 1
        if ok:
            nu = {r["id"] for r in db.execute("SELECT id FROM games WHERE tournament_id = 1")}
            meld(lopend <= nu,
                 f"na ronde {na_ronde} (zaad {zaad}): een lopende wedstrijd werd afgebroken")
            schuss.herbereken_alles(db)
            tm.evalueer(db, 1)
            nog = {r["id"] for r in db.execute(
                "SELECT id FROM games WHERE tournament_id = 1 AND status = 'gespeeld'")}
            weg_games = {r["id"] for r in db.execute(
                "SELECT id FROM games WHERE tournament_id = 1")}
            verdwenen = (vast_voor - nog) - (vast_voor - weg_games)
            doel = controleer(db, 14, weg, 5, f"na ronde {na_ronde} (zaad {zaad})")
            TEL[f"doel {doel}"] += 1
            speel_uit(db, rng)
            meld(tm.toernooi(db, 1)["status"] in ("knockout", "afgelopen"),
                 f"na ronde {na_ronde} (zaad {zaad}): toernooi raakt niet af")
            TEL["geslaagd"] += 1
        db.close()
        os.remove(pad)
print(f"  {TEL['geslaagd']} keer teruggetrokken en uitgespeeld")
print("  nieuw aantal wedstrijden per team: "
      + ", ".join(f"{k} ({v}x)" for k, v in sorted(TEL.items()) if k.startswith("doel")))

print("\n== 2. Andere toernooivormen ==")
vormen = [(12, 4, 4, 6), (15, 4, 8, 5), (16, 5, 8, 8), (20, 4, 8, 7),
          (9, 4, 4, 4), (24, 4, 8, 6), (8, 3, 4, 4), (25, 4, 8, 7)]
gelukt = mislukt = 0
for n, ronden, ko, tafels in vormen:
    for zaad in (1, 2):
        db, pad = bouw(n, ronden, ko, tafels, zaad)
        if db is None:
            continue
        rng = random.Random(zaad * 7 + n)
        weg = rng.randrange(1, n + 1)
        speel_slots(db, 2, weg, rng)
        ok, boodschap = tm.terugtrekken(db, 1, weg, rng=random.Random(zaad))
        if not ok:
            mislukt += 1
            FOUTEN.append(f"n={n} r={ronden}: {boodschap}")
        else:
            gelukt += 1
            schuss.herbereken_alles(db)
            tm.evalueer(db, 1)
            doel = controleer(db, n, weg, ronden, f"n={n} r={ronden} zaad={zaad}")
            speel_uit(db, rng)
            meld(tm.toernooi(db, 1)["status"] in ("knockout", "afgelopen"),
                 f"n={n} r={ronden} zaad={zaad}: toernooi raakt niet af")
        db.close()
        os.remove(pad)
print(f"  {gelukt} geslaagd, {mislukt} geweigerd")

print("\n== 3. Elke aangeboden keuze moet ook echt werken ==")
gecontroleerd = 0
for n, ronden, ko, tafels in [(14, 5, 8, 7), (15, 4, 8, 7), (16, 5, 8, 8), (13, 4, 4, 6),
                              (20, 4, 8, 7), (12, 5, 4, 6)]:
    for zaad in (1, 2):
        db, pad = bouw(n, ronden, ko, tafels, zaad)
        if db is None:
            continue
        rng = random.Random(zaad * 5 + n)
        weg = rng.randrange(1, n + 1)
        speel_slots(db, 2, weg, rng)
        opties = tm.terugtrek_opties(db, 1, weg)
        meld(opties, f"n={n} r={ronden}: helemaal geen opties aangeboden")
        meld(sum(1 for o in opties if o["standaard"]) == 1,
             f"n={n} r={ronden}: niet precies één standaardkeuze")
        db.close(); os.remove(pad)
        for optie in opties:
            db, pad = bouw(n, ronden, ko, tafels, zaad)
            rng2 = random.Random(zaad * 5 + n)
            speel_slots(db, 2, weg, rng2)
            ok, bericht = tm.terugtrekken(db, 1, weg, doel=optie["doel"],
                                          rng=random.Random(zaad))
            meld(ok, f"n={n} r={ronden} doel={optie['doel']}: geweigerd — {bericht}")
            if ok:
                schuss.herbereken_alles(db); tm.evalueer(db, 1)
                gehaald = tm.toernooi(db, 1)["bracket_ronden"]
                meld(gehaald == optie["doel"],
                     f"n={n} doel={optie['doel']}: bracket_ronden werd {gehaald}")
                per_team = collections.Counter()
                for g in db.execute("SELECT * FROM games WHERE tournament_id = 1 "
                                    "AND fase = 'bracket'"):
                    per_team[g["team1_id"]] += 1
                    per_team[g["team2_id"]] += 1
                blijvers = [x for x in range(1, n + 1) if x != weg]
                meld({per_team[x] for x in blijvers} == {optie["doel"]},
                     f"n={n} doel={optie['doel']}: ongelijk aantal wedstrijden")
                speel_uit(db, rng2)
                meld(tm.toernooi(db, 1)["status"] in ("knockout", "afgelopen"),
                     f"n={n} doel={optie['doel']}: toernooi raakt niet af")
                gecontroleerd += 1
            db.close(); os.remove(pad)
# een onmogelijke keuze moet netjes geweigerd worden
db, pad = bouw(14, 5, 8, 7, 1)
rng = random.Random(1); speel_slots(db, 2, 4, rng)
ok, bericht = tm.terugtrekken(db, 1, 4, doel=5)
meld(not ok, "13 teams x 5 wedstrijden werd tóch aanvaard")
meld("kan hier niet" in bericht, f"onduidelijke weigering: {bericht}")
db.close(); os.remove(pad)
print(f"  {gecontroleerd} keuzes doorgerekend en uitgespeeld")

print("\n== 4. Twee ploegen die niet opdagen ==")
for zaad in (1, 2, 3):
    db, pad = bouw(14, 5, 8, 7, zaad)
    if db is None:
        continue
    rng = random.Random(zaad * 31)
    speel_slots(db, 1, 3, rng)
    ok1, b1 = tm.terugtrekken(db, 1, 3, rng=random.Random(zaad))
    schuss.herbereken_alles(db); tm.evalueer(db, 1)
    ok2, b2 = tm.terugtrekken(db, 1, 9, rng=random.Random(zaad + 1))
    schuss.herbereken_alles(db); tm.evalueer(db, 1)
    meld(ok1 and ok2, f"twee terugtrekkingen (zaad {zaad}): {b1} / {b2}")
    if ok1 and ok2:
        per_team = collections.Counter()
        for g in db.execute("SELECT * FROM games WHERE tournament_id = 1 AND fase = 'bracket'"):
            per_team[g["team1_id"]] += 1
            per_team[g["team2_id"]] += 1
        blijvers = [t for t in range(1, 15) if t not in (3, 9)]
        meld(len({per_team[t] for t in blijvers}) == 1,
             f"twee terugtrekkingen (zaad {zaad}): ongelijk aantal wedstrijden")
        meld(per_team[3] == 0 and per_team[9] == 0,
             f"twee terugtrekkingen (zaad {zaad}): er staan nog wedstrijden van de afwezigen")
        speel_uit(db, rng)
        meld(tm.toernooi(db, 1)["status"] in ("knockout", "afgelopen"),
             f"twee terugtrekkingen (zaad {zaad}): toernooi raakt niet af")
    db.close()
    os.remove(pad)
print("  drie avonden met twee afwezige ploegen doorgerekend")


def vrijgegeven(db):
    """Open wedstrijden die de site nu al laat spelen (niets vroegers meer open)."""
    return {g["id"]: dict(g) for g in db.execute(
        "SELECT * FROM games WHERE tournament_id = 1 AND fase = 'bracket' "
        "AND status = 'gepland'").fetchall() if tm.eerdere_wedstrijd(db, g) is None}


def half_gespeelde_beurt(db):
    """Speel de eerste speelbeurt, op één wedstrijd na: die is nog bezig. Wie
    klaar is, mag dan al aan zijn volgende wedstrijd beginnen."""
    slot = min(r["scheduled_at"] for r in db.execute(
        "SELECT scheduled_at FROM games WHERE tournament_id = 1 AND status = 'gepland'"))
    rijen = db.execute("SELECT * FROM games WHERE tournament_id = 1 AND fase = 'bracket' "
                       "AND scheduled_at = ? ORDER BY id", (slot,)).fetchall()
    for g in rijen[:-1]:
        db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, played_at = ? "
                   "WHERE id = ?", (g["team1_id"], slot, g["id"]))
    db.commit()
    schuss.herbereken_alles(db)
    tm.evalueer(db, 1)


print("\n== 5. Wedstrijden die al vrijgegeven zijn, blijven staan ==")
# Is de vorige speelbeurt elders nog bezig, dan staan teams die klaar zijn al
# aan hun volgende wedstrijd — ook al staat die in een latere speelbeurt.
# Die mag niet geschrapt, verplaatst of opnieuw op slot gezet worden.
bekeken = 0
for n, ronden, ko, tafels in [(12, 5, 8, 4), (14, 5, 8, 7), (16, 4, 8, 5), (13, 4, 4, 4)]:
    for zaad in range(1, 7):
        db, pad = bouw(n, ronden, ko, tafels, zaad)
        if db is None:
            continue
        half_gespeelde_beurt(db)
        vrij = vrijgegeven(db)
        bezig = {x for g in vrij.values() for x in (g["team1_id"], g["team2_id"])}
        kandidaten = [x for x in range(1, n + 1) if x not in bezig]
        weg = random.Random(zaad).choice(kandidaten or list(range(1, n + 1)))
        etiket = f"n={n} t={tafels} zaad={zaad}"
        ok, boodschap = tm.terugtrekken(db, 1, weg, rng=random.Random(zaad))
        if ok:
            for gid, g in vrij.items():
                if weg in (g["team1_id"], g["team2_id"]):
                    continue
                h = db.execute("SELECT * FROM games WHERE id = ?", (gid,)).fetchone()
                meld(h is not None and (h["team1_id"], h["team2_id"]) == (g["team1_id"], g["team2_id"]),
                     f"{etiket}: vrijgegeven wedstrijd {gid} werd geschrapt of herloot")
                if h is None:
                    continue
                # (wie nog op een vrije tafel wachtte, mag er intussen eentje krijgen)
                meld(h["scheduled_at"] == g["scheduled_at"] and (
                         g["location_id"] is None or h["location_id"] == g["location_id"]),
                     f"{etiket}: vrijgegeven wedstrijd {gid} kreeg een ander moment of andere tafel")
                meld(tm.eerdere_wedstrijd(db, h) is None,
                     f"{etiket}: vrijgegeven wedstrijd {gid} staat nu weer op slot")
            controleer(db, n, weg, ronden, etiket)
            speel_uit(db, random.Random(zaad))
            meld(tm.toernooi(db, 1)["status"] in ("knockout", "afgelopen"),
                 f"{etiket}: toernooi raakt niet af")
            bekeken += 1
        else:
            meld("te laat" in boodschap, f"{etiket}: onverwachte weigering — {boodschap}")
        db.close()
        os.remove(pad)
print(f"  {bekeken} avonden met een half gespeelde speelbeurt doorgerekend")

print("\n== 6. Na het terugtrekken: elke speelbare wedstrijd een eigen tafel ==")
bekeken = 0
for n, ronden, ko, tafels in [(12, 5, 8, 3), (14, 5, 8, 4), (16, 4, 8, 5), (13, 4, 4, 4)]:
    for zaad in (1, 2, 3):
        db, pad = bouw(n, ronden, ko, tafels, zaad)
        if db is None:
            continue
        rng = random.Random(zaad * 11 + n)
        for _ in range(rng.randrange(2, 3 * tafels)):     # door elkaar uitspelen
            nu = [g for g in vrijgegeven(db).values() if g["location_id"] is not None]
            if not nu:
                break
            g = rng.choice(nu)
            db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, played_at = ? "
                       "WHERE id = ?", (g["team1_id"], g["scheduled_at"], g["id"]))
            db.commit()
            tm.evalueer(db, 1)
        voor = {gid: g["location_id"] for gid, g in vrijgegeven(db).items()
                if g["location_id"] is not None}
        weg = rng.randrange(1, n + 1)
        etiket = f"tafels n={n} t={tafels} zaad={zaad}"
        ok, boodschap = tm.terugtrekken(db, 1, weg, rng=random.Random(zaad))
        meld(not db.in_transaction, f"{etiket}: de transactie bleef open staan")
        if ok:
            nu = list(vrijgegeven(db).values())
            met = [g for g in nu if g["location_id"] is not None]
            meld(len({g["location_id"] for g in met}) == len(met),
                 f"{etiket}: twee speelbare wedstrijden op dezelfde tafel")
            for g in nu:
                if g["id"] in voor:
                    meld(g["location_id"] == voor[g["id"]],
                         f"{etiket}: speelbare wedstrijd {g['id']} moest van tafel veranderen")
            if any(g["location_id"] is None for g in nu):
                meld(len(met) == tafels,
                     f"{etiket}: een wedstrijd wacht op een tafel terwijl er nog eentje vrij is")
            bekeken += 1
        db.close()
        os.remove(pad)
print(f"  {bekeken} terugtrekkingen midden in een rommelige avond nagekeken")

print("\n== 7. Geen uitslag tussendoor tijdens het herloten ==")
import sqlite3                                                  # noqa: E402
db, pad = bouw(14, 5, 8, 7, 1)
speel_slots(db, 1, 4, random.Random(1))
geblokkeerd = []
origineel = tm._aanvulparen


def met_indringer(*args, **kwargs):
    ander = sqlite3.connect(pad, timeout=0)
    try:
        ander.execute("UPDATE games SET status = 'gespeeld' WHERE status = 'gepland'")
        ander.commit()
        geblokkeerd.append(False)
    except sqlite3.OperationalError:
        geblokkeerd.append(True)
    finally:
        ander.close()
    return origineel(*args, **kwargs)


tm._aanvulparen = met_indringer
try:
    ok, bericht = tm.terugtrekken(db, 1, 4, rng=random.Random(1))
finally:
    tm._aanvulparen = origineel
meld(ok, f"schrijfslot: terugtrekken mislukte — {bericht}")
meld(geblokkeerd and all(geblokkeerd),
     f"schrijfslot: een andere schrijver kwam ertussen ({geblokkeerd})")
meld(not db.in_transaction, "schrijfslot: de transactie bleef open staan")
voor = db.execute("SELECT COUNT(*) FROM games").fetchone()[0]
ok, _ = tm.terugtrekken(db, 1, 4)                              # al teruggetrokken: weigering
meld(not ok and not db.in_transaction, "schrijfslot: na een weigering bleef het slot hangen")
meld(voor == db.execute("SELECT COUNT(*) FROM games").fetchone()[0],
     "schrijfslot: een weigering veranderde toch iets")
db.close()
os.remove(pad)
print("  schrijfslot gecontroleerd")

print("\n" + "=" * 62)
if FOUTEN:
    print(f"❌ {len(FOUTEN)} probleem(en):")
    for f in FOUTEN[:20]:
        print("  -", f)
else:
    print("✅ Alles klopt: forfaits weg, iedereen even veel wedstrijden, geen herhalingen.")
print("=" * 62)
shutil.rmtree(WERKMAP, ignore_errors=True)
sys.exit(1 if FOUTEN else 0)

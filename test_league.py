# -*- coding: utf-8 -*-
"""Leaguelogica: inschrijven, uitdagen en de wachtrij.

    python test_league.py

Wat hier gecontroleerd wordt:

  1. Inschrijven — een team staat meteen in het klassement, met nul wedstrijden.
  2. Uitdagen — aanvaarden plant de wedstrijd, weigeren niet, en je kan geen
     twee wedstrijden tegelijk hebben.
  3. De wachtrij — er wordt niet meteen gekoppeld bij twee teams, nooit meer
     wedstrijden dan er tafels zijn, en dezelfde affiche keert niet terug zolang
     er een alternatief is.
  4. Een hele avond, van 4 tot 25 teams.

Deze test raakt shuss.db NIET aan: hij werkt in een tijdelijke map.
"""

import collections
import os
import random
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

WERKMAP = tempfile.mkdtemp(prefix="schuss_league_")
BRON = os.path.dirname(os.path.abspath(__file__))
for naam in ("app.py", "database.py", "elo.py", "tournament.py", "league.py"):
    shutil.copy(os.path.join(BRON, naam), WERKMAP)
shutil.copytree(os.path.join(BRON, "templates"), os.path.join(WERKMAP, "templates"))
shutil.copytree(os.path.join(BRON, "static"), os.path.join(WERKMAP, "static"),
                ignore=shutil.ignore_patterns("uploads"))
sys.path.insert(0, WERKMAP)
os.chdir(WERKMAP)

import app as schuss                                             # noqa: E402
import league as lg                                              # noqa: E402
from database import init_db, verbind                            # noqa: E402

FOUTEN = []


def check(voorwaarde, tekst):
    print(("  ✔ " if voorwaarde else "  ✘ ") + tekst)
    if not voorwaarde:
        FOUTEN.append(tekst)


def bouw(n_teams, tafels=2, zaad=1, elo_spreiding=80):
    pad = os.path.join(WERKMAP, f"l{n_teams}_{tafels}_{zaad}.db")
    if os.path.exists(pad):
        os.remove(pad)
    init_db(pad)
    db = verbind(pad)
    r = random.Random(zaad)
    for i in range(1, 2 * n_teams + 1):
        db.execute("INSERT INTO players (name, nickname) VALUES (?, '')", (f"S{i:03d}",))
    for t in range(n_teams):
        db.execute("INSERT INTO teams (name, player1_id, player2_id, status, elo) "
                   "VALUES (?, ?, ?, 'actief', ?)",
                   (f"T{t + 1:02d}", 2 * t + 1, 2 * t + 2,
                    round(r.gauss(1000, elo_spreiding), 1)))
    db.execute("INSERT INTO seasons (name, start_date, end_date) "
               "VALUES ('Seizoen', '2026-01-01', '2026-12-31')")
    db.execute("INSERT INTO matchdays (season_id, title, date, tafels) "
               "VALUES (1, 'Speeldag 1', '2026-03-06', ?)", (tafels,))
    db.commit()
    return db, pad


# ------------------------------------------------------------ inschrijven --
print("\n== 1. Inschrijven voor een seizoen ==")
db, pad = bouw(6)
ok, boodschap = lg.inschrijven(db, 1, 1)
check(ok, f"team schrijft zich in: {boodschap}")
check(len(lg.ingeschreven(db, 1)) == 1, "staat in de lijst van ingeschreven teams")
ok, _ = lg.inschrijven(db, 1, 1)
check(not ok, "twee keer inschrijven kan niet")
for t in range(2, 7):
    lg.inschrijven(db, 1, t)
check(len(lg.ingeschreven(db, 1)) == 6, "alle zes de teams zijn ingeschreven")
ok, _ = lg.uitschrijven(db, 1, 6)
check(ok, "uitschrijven kan zolang er niets gespeeld is")
lg.inschrijven(db, 1, 6)
db.close(); os.remove(pad)


# --------------------------------------------------------------- uitdagen --
print("\n== 2. Uitdagen ==")
db, pad = bouw(6)
for t in range(1, 7):
    lg.inschrijven(db, 1, t)

ok, _ = lg.uitdagen(db, 1, 1, 1)
check(not ok, "jezelf uitdagen kan niet")
ok, boodschap = lg.uitdagen(db, 1, 1, 2)
check(ok, f"team 1 daagt team 2 uit: {boodschap}")
ok, _ = lg.uitdagen(db, 1, 2, 1)
check(not ok, "een tweede uitdaging tussen dezelfde teams kan niet")
check(len(lg.uitdagingen(db, 1)) == 1, "één openstaande uitdaging")

uid = lg.uitdagingen(db, 1)[0]["id"]
ok, boodschap = lg.beantwoord_uitdaging(db, uid, aanvaard=False)
check(ok and not db.execute("SELECT COUNT(*) FROM games").fetchone()[0],
      "weigeren maakt geen wedstrijd aan")

lg.uitdagen(db, 1, 1, 2)
uid = lg.uitdagingen(db, 1)[0]["id"]
ok, boodschap = lg.beantwoord_uitdaging(db, uid, aanvaard=True)
game = db.execute("SELECT * FROM games").fetchone()
check(ok and game is not None, f"aanvaarden plant de wedstrijd: {boodschap}")
check({game["team1_id"], game["team2_id"]} == {1, 2}, "met de juiste twee teams")
check(game["matchday_id"] == 1 and game["fase"] == "liga",
      "op de juiste speeldag, als leaguewedstrijd")

ok, _ = lg.uitdagen(db, 1, 1, 3)
check(not ok, "een team met een lopende wedstrijd kan niet uitgedaagd worden")
ok, _ = lg.aansluiten(db, 1, 1)
check(not ok, "en kan ook niet in de wachtrij")
db.close(); os.remove(pad)


# --------------------------------------------------------------- wachtrij --
print("\n== 3. De wachtrij koppelt niet te vroeg ==")
db, pad = bouw(8, tafels=3)
for t in range(1, 9):
    lg.inschrijven(db, 1, t)
nu = datetime(2026, 3, 6, 20, 0)

lg.aansluiten(db, 1, 1, nu=nu)
lg.koppel_wachtrij(db, 1, nu=nu)
check(db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0,
      "met één wachtend team gebeurt er niets")

lg.aansluiten(db, 1, 2, nu=nu)
lg.koppel_wachtrij(db, 1, nu=nu)
check(db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0,
      "met twee wachtende teams wordt er nog NIET gekoppeld")

lg.koppel_wachtrij(db, 1, nu=nu + timedelta(minutes=lg.GEDULD_MINUTEN + 1))
check(db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1,
      f"maar na {lg.GEDULD_MINUTEN} minuten wachten wél")
db.close(); os.remove(pad)

db, pad = bouw(8, tafels=3)
for t in range(1, 9):
    lg.inschrijven(db, 1, t)
for t in range(1, 5):
    lg.aansluiten(db, 1, t, nu=nu)
lg.koppel_wachtrij(db, 1, nu=nu)
check(db.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 2,
      f"vanaf {lg.POEL} wachtende teams wordt er meteen gekoppeld (2 wedstrijden)")

print("\n== 4. Nooit meer wedstrijden dan tafels ==")
db, pad = bouw(10, tafels=2)
for t in range(1, 11):
    lg.inschrijven(db, 1, t)
for t in range(1, 11):
    lg.aansluiten(db, 1, t, nu=nu)
lg.koppel_wachtrij(db, 1, nu=nu)
lopend = db.execute("SELECT COUNT(*) FROM games WHERE status = 'gepland'").fetchone()[0]
check(lopend == 2, f"met 2 tafels lopen er 2 wedstrijden (gekregen: {lopend})")
check(len(lg.wachtrij(db, 1)) == 6, "de andere zes blijven netjes wachten")
lg.koppel_wachtrij(db, 1, nu=nu + timedelta(minutes=30))
check(db.execute("SELECT COUNT(*) FROM games WHERE status = 'gepland'").fetchone()[0] == 2,
      "zolang de tafels bezet zijn komt er niets bij")
db.close(); os.remove(pad)


print("\n== 5. Dezelfde affiche keert niet terug zolang er keuze is ==")
db, pad = bouw(8, tafels=4)
for t in range(1, 9):
    lg.inschrijven(db, 1, t)
paren = collections.Counter()
klok = nu
for ronde in range(6):
    for t in range(1, 9):
        lg.aansluiten(db, 1, t, nu=klok)
    lg.koppel_wachtrij(db, 1, nu=klok)
    for g in db.execute("SELECT * FROM games WHERE status = 'gepland'").fetchall():
        paren[frozenset((g["team1_id"], g["team2_id"]))] += 1
        db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, "
                   "played_at = ? WHERE id = ?",
                   (g["team1_id"], klok.strftime(lg.TIJDFORMAAT), g["id"]))
    db.commit()
    klok += timedelta(minutes=20)
herhaald = sum(v - 1 for v in paren.values() if v > 1)
check(herhaald == 0, f"24 wedstrijden met 8 teams: geen enkele herhaling "
                     f"(gekregen: {herhaald})")
check(len(paren) == sum(paren.values()), "elke affiche is uniek")
db.close(); os.remove(pad)


print("\n== 6. Een herhaling alleen als het niet anders kan ==")
# Vier teams wachten. T01 speelde vanavond al tegen T02, maar nog niet tegen
# T03. Dan hoort T03 (of T04) de tegenstander te zijn, niet T02 — ook al is T02
# qua Aura veel dichter.
db, pad = bouw(4, tafels=1, zaad=3)
db.execute("UPDATE teams SET elo = 1000 WHERE id = 1")
db.execute("UPDATE teams SET elo = 1001 WHERE id = 2")   # bijna gelijk: verleidelijk
db.execute("UPDATE teams SET elo = 1200 WHERE id = 3")   # ver weg qua Aura
db.execute("UPDATE teams SET elo = 1250 WHERE id = 4")
for t in range(1, 5):
    lg.inschrijven(db, 1, t)
db.execute("INSERT INTO games (team1_id, team2_id, matchday_id, fase, scheduled_at, "
           "played_at, status, winner_team_id) VALUES (1, 2, 1, 'liga', ?, ?, "
           "'gespeeld', 1)", ("2026-03-06T20:00", "2026-03-06T20:18"))
db.commit()
for t in range(1, 5):
    lg.aansluiten(db, 1, t, nu=nu)
lg.koppel_wachtrij(db, 1, nu=nu)
nieuw = db.execute("SELECT * FROM games WHERE status = 'gepland'").fetchone()
paar = {nieuw["team1_id"], nieuw["team2_id"]} if nieuw else set()
check(paar and paar != {1, 2},
      f"kiest een nieuwe tegenstander boven een herhaling (gekregen: {sorted(paar)})")

# Maar als élke tegenstander al gespeeld is, mág het wel.
db.execute("DELETE FROM games WHERE status = 'gepland'")
for a, b in [(1, 3), (1, 4)]:
    db.execute("INSERT INTO games (team1_id, team2_id, matchday_id, fase, scheduled_at, "
               "played_at, status, winner_team_id) VALUES (?, ?, 1, 'liga', ?, ?, "
               "'gespeeld', ?)", (a, b, "2026-03-06T20:20", "2026-03-06T20:38", a))
db.commit()
for t in range(1, 5):
    lg.aansluiten(db, 1, t, nu=nu)
lg.koppel_wachtrij(db, 1, nu=nu)
check(db.execute("SELECT COUNT(*) FROM games WHERE status = 'gepland'").fetchone()[0] == 1,
      "als alles al gespeeld is, komt er tóch een wedstrijd (liever dan stilvallen)")
db.close(); os.remove(pad)


print("\n== 7. Een hele avond, van klein tot druk ==")
def avond(n_teams, tafels, zaad, minuten=180, duur=18):
    db, pad = bouw(n_teams, tafels=tafels, zaad=zaad)
    for t in range(1, n_teams + 1):
        lg.inschrijven(db, 1, t)
    r = random.Random(zaad)
    start = datetime(2026, 3, 6, 20, 0)
    aankomst = {t: start + timedelta(minutes=int(r.triangular(0, 60, 10)))
                for t in range(1, n_teams + 1)}
    einde = {}
    for stap in range(minuten):
        klok = start + timedelta(minutes=stap)
        for t, moment in aankomst.items():
            if moment == klok:
                lg.aansluiten(db, 1, t, nu=klok)
        for g in db.execute("SELECT * FROM games WHERE status = 'gepland'").fetchall():
            if g["id"] not in einde:
                einde[g["id"]] = klok + timedelta(minutes=duur)
            elif einde[g["id"]] <= klok:
                db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, "
                           "played_at = ? WHERE id = ?",
                           (g["team1_id"], klok.strftime(lg.TIJDFORMAAT), g["id"]))
                db.commit()
                for t in (g["team1_id"], g["team2_id"]):
                    lg.aansluiten(db, 1, t, nu=klok + timedelta(minutes=r.randint(0, 8)))
        lg.koppel_wachtrij(db, 1, nu=klok)
    rijen = db.execute("SELECT * FROM games").fetchall()
    paren = collections.Counter(frozenset((g["team1_id"], g["team2_id"])) for g in rijen)
    per_team = collections.Counter()
    for g in rijen:
        per_team[g["team1_id"]] += 1
        per_team[g["team2_id"]] += 1
    tegelijk = collections.Counter(g["scheduled_at"] for g in rijen)
    db.close()
    os.remove(pad)
    return {"matches": len(rijen), "herhaald": sum(v - 1 for v in paren.values() if v > 1),
            "spreiding": (min(per_team.values(), default=0),
                          max(per_team.values(), default=0)),
            "max_tegelijk": max(tegelijk.values(), default=0)}

print(f"  {'teams':>6} {'tafels':>7} {'matches':>8} {'herhaald':>9} {'per team':>10}")
for n, tafels in [(4, 1), (5, 2), (8, 2), (8, 3), (12, 3), (16, 4), (25, 5)]:
    uit = avond(n, tafels, zaad=n)
    print(f"  {n:>6} {tafels:>7} {uit['matches']:>8} {uit['herhaald']:>9} "
          f"{str(uit['spreiding'][0]) + '-' + str(uit['spreiding'][1]):>10}")
    check(uit["matches"] > 0, f"{n} teams: er worden wedstrijden gemaakt")
    check(uit["spreiding"][0] >= 1, f"{n} teams: iedereen speelt minstens één keer")
    # Bij weinig teams is herhalen onvermijdelijk (4 teams = 3 mogelijke
    # affiches). Vanaf 8 teams mag het hooguit sporadisch.
    if n >= 8:
        grens = max(1, uit["matches"] // 10)
        check(uit["herhaald"] <= grens,
              f"{n} teams: hoogstens {grens} herhaling(en) op {uit['matches']} "
              f"wedstrijden (gekregen: {uit['herhaald']})")

print("\n" + "=" * 60)
if FOUTEN:
    print(f"❌ {len(FOUTEN)} probleem(en):")
    for f in FOUTEN:
        print("  -", f)
else:
    print("✅ De leaguelogica klopt.")
print("=" * 60)
shutil.rmtree(WERKMAP, ignore_errors=True)
sys.exit(1 if FOUTEN else 0)

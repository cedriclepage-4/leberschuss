# -*- coding: utf-8 -*-
"""Dubbele accounts en teams samenvoegen.

    python test_samenvoegen.py

Het geval uit de praktijk: Noah en Sam speelden als "Oop & Goop". Noah vergat
zijn wachtwoord, maakte een tweede account ("Noa Van den Broek") en een tweede
team "Oop & Goop 2" met dezelfde Sam, speelde er al mee en schreef het in voor
het volgende toernooi. Na het samenvoegen moet gelden:

  1. er is nog één Noah en één Oop & Goop; het tweede account en team zijn weg;
  2. elke wedstrijd, melding, statistiek en inschrijving van beide is er nog;
  3. de Aura is exact wat ze zou zijn als alles meteen op het eerste account
     gespeeld was;
  4. Noah logt in met het wachtwoord van zijn tweede account;
  5. wat niet kan (tegen jezelf gespeeld, samen in één team, beide in een geloot
     toernooi) wordt geweigerd zonder iets te veranderen;
  6. via de organisatiepagina: eerst een voorbeeld, dan pas samenvoegen, met
     een back-up vooraf.

Deze test raakt shuss.db NIET aan: hij werkt in een tijdelijke map.
"""

import os
import shutil
import sys
import tempfile

WERKMAP = tempfile.mkdtemp(prefix="schuss_samen_")
BRON = os.path.dirname(os.path.abspath(__file__))
for naam in ("app.py", "database.py", "elo.py", "tournament.py", "league.py", "dop.py",
             "samenvoegen.py"):
    shutil.copy(os.path.join(BRON, naam), WERKMAP)
shutil.copytree(os.path.join(BRON, "templates"), os.path.join(WERKMAP, "templates"))
shutil.copytree(os.path.join(BRON, "static"), os.path.join(WERKMAP, "static"),
                ignore=shutil.ignore_patterns("uploads"))
sys.path.insert(0, WERKMAP)
os.chdir(WERKMAP)

import app as schuss                                             # noqa: E402
import samenvoegen as sv                                         # noqa: E402
from database import init_db, verbind                            # noqa: E402
from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402

FOUTEN = []


def check(voorwaarde, tekst):
    print(("  ✓ " if voorwaarde else "  ✗ ") + tekst)
    if not voorwaarde:
        FOUTEN.append(tekst)


NOAH, SAM, NOAH2, ANNA, BERT, CAS, DIRK, ORG = 2401, 8259, 5555, 3001, 3002, 3003, 3004, 1111
OG, OG2, AB, CD = 1, 2, 3, 4


def bouw(pad, op_tweede_account=True):
    """De situatie van Noah. Met op_tweede_account=False speelde alles meteen
    op het eerste account en team: de referentie voor de Aura."""
    if os.path.exists(pad):
        os.remove(pad)
    init_db(pad)
    db = verbind(pad)
    spelers = [(NOAH, "Noah Van den Broeck", "Goop", "oud"), (SAM, "Sam Van Meensel", "", None),
               (ANNA, "Anna A", "", "x"), (BERT, "Bert B", "", "x"),
               (CAS, "Cas C", "", "x"), (DIRK, "Dirk D", "", "x"),
               (ORG, "Org Anisator", "", "x")]
    if op_tweede_account:
        spelers.append((NOAH2, "Noa Van den Broek", "", "nieuw"))
    for pid, naam, bijnaam, ww in spelers:
        db.execute("INSERT INTO players (id, name, nickname, password_hash, created_at) "
                   "VALUES (?, ?, ?, ?, ?)",
                   (pid, naam, bijnaam, generate_password_hash(ww) if ww else None,
                    "2026-09-01 12:00:00" if pid == NOAH2 else "2026-08-25 12:00:00"))
    db.execute("UPDATE players SET role = 'eigenaar' WHERE id = ?", (ORG,))
    teams = [(OG, "Oop & Goop", NOAH, SAM), (AB, "Anna & Bert", ANNA, BERT),
             (CD, "Cas & Dirk", CAS, DIRK)]
    if op_tweede_account:
        teams.append((OG2, "Oop & Goop 2", NOAH2, SAM))
    for tid, naam, a, b in teams:
        db.execute("INSERT INTO teams (id, name, player1_id, player2_id, status) "
                   "VALUES (?, ?, ?, ?, 'actief')", (tid, naam, a, b))
    nieuw_team = OG2 if op_tweede_account else OG
    nieuw_noah = NOAH2 if op_tweede_account else NOAH

    # Een afgelopen toernooi met het oude team.
    db.execute("INSERT INTO tournaments (id, name, date, status) "
               "VALUES (1, 'Zomer', '2026-08-30', 'afgelopen')")
    for t in (OG, AB, CD):
        db.execute("INSERT INTO tournament_teams (tournament_id, team_id) VALUES (1, ?)", (t,))
    wedstrijden = [  # (id, team1, team2, winnaar, moment, toernooi, fase)
        (1, OG, AB, OG, "2026-08-30T20:00", 1, "bracket"),
        (2, CD, OG, CD, "2026-08-30T20:20", 1, "bracket"),
        (3, AB, CD, AB, "2026-08-30T20:40", 1, "bracket"),
        # Daarna speelde Noah op zijn nieuwe account (liga, buiten een seizoen).
        (4, nieuw_team, AB, nieuw_team, "2026-09-10T20:00", None, "liga"),
        (5, CD, nieuw_team, nieuw_team, "2026-09-10T20:30", None, "liga"),
        (6, AB, CD, CD, "2026-09-10T21:00", None, "liga"),
    ]
    for gid, t1, t2, w, moment, tn, fase in wedstrijden:
        db.execute("INSERT INTO games (id, team1_id, team2_id, winner_team_id, scheduled_at, "
                   "played_at, status, tournament_id, fase, ronde) "
                   "VALUES (?, ?, ?, ?, ?, ?, 'gespeeld', ?, ?, 1)",
                   (gid, t1, t2, w, moment, moment, tn, fase))
    db.execute("INSERT INTO stat_types (id, name) VALUES (1, 'saves')")
    db.execute("INSERT INTO game_stats (game_id, player_id, stat_type_id, value) "
               "VALUES (4, ?, 1, 3)", (nieuw_noah,))
    db.execute("INSERT INTO game_reports (game_id, team_id, player_id, winner_team_id) "
               "VALUES (5, ?, ?, ?)", (nieuw_team, nieuw_noah, nieuw_team))
    # Het nieuwe toernooi: het tweede team is ingeschreven, Sam moet nog bevestigen.
    db.execute("INSERT INTO tournaments (id, name, date, status) "
               "VALUES (2, 'Herfst', '2026-11-14', 'opzet')")
    db.execute("INSERT INTO tournament_teams (tournament_id, team_id, ingeschreven_door, "
               "te_bevestigen) VALUES (2, ?, ?, ?)", (nieuw_team, nieuw_noah, SAM))
    db.execute("INSERT INTO tournament_teams (tournament_id, team_id) VALUES (2, ?)", (AB,))
    db.commit()
    schuss.herbereken_alles(db)
    return db


def foto(db):
    """Alles wat telt, om voor en na te vergelijken."""
    return {
        "spelers": {r["id"]: round(r["elo"], 6) for r in db.execute("SELECT id, elo FROM players")},
        "teams": {r["id"]: round(r["elo"], 6) for r in db.execute("SELECT id, elo FROM teams")},
        "games": db.execute("SELECT id, team1_id, team2_id, winner_team_id FROM games "
                            "ORDER BY id").fetchall(),
        "inschrijvingen": db.execute("SELECT tournament_id, team_id, ingeschreven_door, "
                                     "te_bevestigen FROM tournament_teams ORDER BY id").fetchall(),
        "stats": db.execute("SELECT game_id, player_id, value FROM game_stats").fetchall(),
        "meldingen": db.execute("SELECT game_id, team_id, player_id, winner_team_id "
                                "FROM game_reports").fetchall(),
    }


# ------------------------------------------------------------- het geval --
print("== Noah en Oop & Goop 2 samenvoegen ==")
db = bouw(os.path.join(WERKMAP, "noah.db"))
ref = bouw(os.path.join(WERKMAP, "referentie.db"), op_tweede_account=False)

plan = sv.plan_spelers(db, NOAH2, NOAH)
check(plan["bezwaren"] == [], f"geen bezwaren (gekregen: {plan['bezwaren']})")
check([(p["bron"]["id"], p["doel"]["id"]) for p in plan["team_samenvoegingen"]] == [(OG2, OG)],
      "Oop & Goop 2 gaat mee op in Oop & Goop")
check(plan["wedstrijden"] == 2, "het voorbeeld telt de 2 wedstrijden van het tweede account")

r = sv.spelers_samenvoegen(db, NOAH2, NOAH, wachtwoord_van_bron=True,
                           teamnamen={OG: "Oop & Goop"})
schuss.herbereken_alles(db)
check(db.execute("SELECT 1 FROM players WHERE id = ?", (NOAH2,)).fetchone() is None,
      "het tweede account is weg")
check(db.execute("SELECT 1 FROM teams WHERE id = ?", (OG2,)).fetchone() is None,
      "het tweede team is weg")
check(r["teams"] == ["Oop & Goop"], "het team heet nog Oop & Goop")
noah = db.execute("SELECT * FROM players WHERE id = ?", (NOAH,)).fetchone()
check(noah["name"] == "Noah Van den Broeck" and noah["nickname"] == "Goop",
      "naam en bijnaam van het eerste account blijven")
check(check_password_hash(noah["password_hash"], "nieuw"),
      "Noah logt in met het wachtwoord van zijn tweede account")
check(not check_password_hash(noah["password_hash"], "oud"), "het vergeten wachtwoord werkt niet meer")
check(noah["created_at"] == "2026-08-25 12:00:00", "aanmaakdatum = de oudste")

na, verwacht = foto(db), foto(ref)
check(na["games"] == verwacht["games"], "alle 6 wedstrijden staan op Oop & Goop, met de juiste winnaar")
check(na["spelers"] == verwacht["spelers"],
      "spelers-Aura is exact alsof alles op het eerste account gespeeld werd")
check(na["teams"] == verwacht["teams"], "team-Aura idem")
check([tuple(x) for x in na["inschrijvingen"]] == [tuple(x) for x in verwacht["inschrijvingen"]],
      "inschrijving voor Herfst staat op Oop & Goop, ingeschreven door Noah, Sam bevestigt nog")
check([tuple(x) for x in na["stats"]] == [tuple(x) for x in verwacht["stats"]],
      "statistieken verhuisd naar Noah")
check([tuple(x) for x in na["meldingen"]] == [tuple(x) for x in verwacht["meldingen"]],
      "meldingen verhuisd naar Noah en Oop & Goop")
check(db.execute("PRAGMA foreign_key_check").fetchall() == [], "geen gebroken verwijzingen")
db.close()

# ---------------------------------------- tweede team blijft naam houden --
print("\n== Andere keuzes ==")
db = bouw(os.path.join(WERKMAP, "keuzes.db"))
sv.spelers_samenvoegen(db, NOAH2, NOAH, wachtwoord_van_bron=False,
                       naam="Noa Van den Broek", teamnamen={OG: "Oop & Goop 2"})
db.commit()
noah = db.execute("SELECT * FROM players WHERE id = ?", (NOAH,)).fetchone()
check(noah["name"] == "Noa Van den Broek", "naam van het tweede account gekozen")
check(check_password_hash(noah["password_hash"], "oud"), "oud wachtwoord gekozen")
check(db.execute("SELECT name FROM teams WHERE id = ?", (OG,)).fetchone()[0] == "Oop & Goop 2",
      "teamnaam van het tweede team gekozen (id van het eerste blijft)")
db.close()

# --------------------------------------------------------- bezwaren --
print("\n== Wat niet mag ==")


def weigert(db, functie, *args, tekst):
    voor = foto(db)
    try:
        functie(db, *args)
        gelukt = True
    except sv.SamenvoegFout:
        gelukt = False
    db.rollback()
    check(not gelukt and foto(db) == voor, tekst)


db = bouw(os.path.join(WERKMAP, "tegen.db"))
db.execute("INSERT INTO games (team1_id, team2_id, winner_team_id, scheduled_at, played_at, "
           "status) VALUES (?, ?, ?, '2026-09-11T20:00', '2026-09-11T20:00', 'gespeeld')",
           (OG, OG2, OG))
db.commit()
weigert(db, sv.spelers_samenvoegen, NOAH2, NOAH,
        tekst="Oop & Goop speelde tegen Oop & Goop 2: geweigerd, niets veranderd")
weigert(db, sv.teams_samenvoegen, OG2, OG, tekst="idem bij teams samenvoegen")
weigert(db, sv.spelers_samenvoegen, SAM, NOAH,
        tekst="Noah en Sam zitten samen in een team: geweigerd")
weigert(db, sv.teams_samenvoegen, AB, OG, tekst="teams zonder dezelfde spelers: geweigerd")
db.close()

db = bouw(os.path.join(WERKMAP, "geloot.db"))
db.execute("INSERT INTO tournaments (id, name, date, status) VALUES (3, 'Lopend', '2026-10-01', 'bracket')")
db.execute("INSERT INTO tournament_teams (tournament_id, team_id) VALUES (3, ?)", (OG,))
db.execute("INSERT INTO tournament_teams (tournament_id, team_id) VALUES (3, ?)", (OG2,))
db.commit()
weigert(db, sv.spelers_samenvoegen, NOAH2, NOAH,
        tekst="beide teams in een geloot toernooi: geweigerd")
db.close()

db = bouw(os.path.join(WERKMAP, "opzet.db"))
# Beide teams ingeschreven voor Herfst (vóór de loting): de vroegste blijft.
db.execute("INSERT INTO tournament_teams (tournament_id, team_id) VALUES (2, ?)", (OG,))
db.commit()
vroegste = db.execute("SELECT id FROM tournament_teams WHERE tournament_id = 2 AND team_id = ?",
                      (OG2,)).fetchone()[0]
sv.spelers_samenvoegen(db, NOAH2, NOAH)
db.commit()
rijen = db.execute("SELECT id, team_id FROM tournament_teams WHERE tournament_id = 2 "
                   "AND team_id = ?", (OG,)).fetchall()
check(len(rijen) == 1 and rijen[0]["id"] == vroegste,
      "beide ingeschreven vóór de loting: één inschrijving, de vroegste plaats blijft")
db.close()

# ------------------------------------------ onvolledig tweede team --
print("\n== Tweede team met enkel een uitnodiging (Sam aanvaardde nooit) ==")


def bouw_onvolledig(pad):
    """Zoals in het echt: "Noah" maakte een nieuw account en een team "Oop & Goop 2"
    met enkel zichzelf, en nodigde Sam uit. Sam logt nooit in, dus aanvaardt nooit."""
    db = bouw(pad)
    db.execute("DELETE FROM games WHERE id IN (4, 5)")
    db.execute("UPDATE teams SET player2_id = NULL, status = 'onvolledig' WHERE id = ?", (OG2,))
    db.execute("INSERT INTO team_invitations (team_id, player_id) VALUES (?, ?)", (OG2, SAM))
    db.execute("UPDATE players SET name = 'Noah' WHERE id = ?", (NOAH2,))
    db.commit()
    return db


db = bouw_onvolledig(os.path.join(WERKMAP, "onvolledig.db"))
try:
    schuss.herbereken_alles(db)
    gelukt = True
except Exception as fout:
    gelukt = False
    print("   ", repr(fout))
check(gelukt, "herberekenen crasht niet op een team met één speler")
check(db.execute("SELECT elo FROM teams WHERE id = ?", (OG2,)).fetchone()[0]
      == db.execute("SELECT elo FROM players WHERE id = ?", (NOAH2,)).fetchone()[0],
      "een onvolledig team heeft de Aura van zijn enige speler")
paren = {(p["doel"]["id"], p["bron"]["id"]) for p in sv.mogelijke_dubbels(db, schuss.naam_lijkt_op)}
check((NOAH, NOAH2) in paren, "“Noah” en “Noah Van den Broeck” met dezelfde (uitgenodigde) "
                              "ploegmaat worden voorgesteld")
check(not ({(ANNA, BERT), (BERT, ANNA)} & paren), "gewone ploegmaten worden niet voorgesteld")

plan = sv.plan_spelers(db, NOAH2, NOAH)
check([(p["bron"]["id"], p["doel"]["id"]) for p in plan["team_samenvoegingen"]] == [(OG2, OG)],
      "het onvolledige Oop & Goop 2 gaat mee op in Oop & Goop")
plan_omgekeerd = sv.plan_spelers(db, NOAH, NOAH2)
check([(p["bron"]["id"], p["doel"]["id"]) for p in plan_omgekeerd["team_samenvoegingen"]]
      == [(OG2, OG)], "ook omgekeerd blijft het volledige team, niet het onvolledige")
sv.spelers_samenvoegen(db, NOAH2, NOAH)
schuss.herbereken_alles(db)
check(db.execute("SELECT COUNT(*) FROM teams WHERE id = ?", (OG2,)).fetchone()[0] == 0
      and db.execute("SELECT COUNT(*) FROM team_invitations").fetchone()[0] == 0,
      "het onvolledige team en zijn uitnodiging zijn weg")
check(db.execute("SELECT team_id FROM tournament_teams WHERE tournament_id = 2 AND "
                 "ingeschreven_door = ?", (NOAH,)).fetchone()[0] == OG,
      "de inschrijving voor Herfst staat op Oop & Goop")
check(db.execute("PRAGMA foreign_key_check").fetchall() == [], "geen gebroken verwijzingen")
db.close()

# Het geval van "nath": een los dubbel account samenvoegen terwijl er ergens
# anders een onvolledig team bestaat. Dat crashte bij het herberekenen.
db = bouw_onvolledig(os.path.join(WERKMAP, "nath.db"))
db.execute("INSERT INTO players (id, name, nickname) VALUES (4109, 'Ana A', 'ana')")
db.commit()
try:
    sv.spelers_samenvoegen(db, 4109, ANNA)
    schuss.herbereken_alles(db)
    gelukt = True
except Exception as fout:
    gelukt = False
    print("   ", repr(fout))
check(gelukt, "los account samenvoegen terwijl er een onvolledig team bestaat")
db.close()

# ------------------------------------------------------- via de pagina --
print("\n== Via de organisatiepagina ==")
db = bouw(schuss.DB_PATH)
db.close()
schuss.app.config["TESTING"] = True
klant = schuss.app.test_client()
with klant.session_transaction() as s:
    s["speler_id"] = ORG

pagina = klant.get("/admin/spelers")
html = pagina.get_data(as_text=True)
check(pagina.status_code == 200, "/admin/spelers laadt")
check("Noa Van den Broek" in html and "zelfde ploegmaat" in html,
      "het dubbele account wordt voorgesteld, met de gedeelde ploegmaat")
check("Oop &amp; Goop 2" in html, "het dubbele team staat in de lijst")

voorbeeld = klant.get(f"/admin/samenvoegen/spelers?bron={NOAH2}&doel={NOAH}")
html = voorbeeld.get_data(as_text=True)
check(voorbeeld.status_code == 200 and "Teams die mee samengaan" in html,
      "het voorbeeld toont welke teams mee samengaan")
check(klant.get(f"/admin/samenvoegen/teams?bron={OG2}&doel={OG}").status_code == 200,
      "het voorbeeld voor teams laadt")

klant.post("/admin/samenvoegen/spelers", data={"bron": NOAH2, "doel": NOAH,
                                               "bevestiging": "fout"})
db = verbind(schuss.DB_PATH)
check(db.execute("SELECT 1 FROM players WHERE id = ?", (NOAH2,)).fetchone() is not None,
      "zonder het juiste bevestigingswoord verandert er niets")
db.close()

backups_voor = len(schuss.backup_lijst())
antwoord = klant.post("/admin/samenvoegen/spelers",
                      data={"bron": NOAH2, "doel": NOAH, "bevestiging": "samenvoegen",
                            "naam": "Noah Van den Broeck", "wachtwoord": "bron",
                            f"teamnaam_{OG}": "Oop & Goop"})
check(antwoord.status_code == 302, "samenvoegen stuurt terug naar de lijst")
db = verbind(schuss.DB_PATH)
check(db.execute("SELECT COUNT(*) FROM players WHERE id IN (?, ?)", (NOAH, NOAH2)).fetchone()[0] == 1
      and db.execute("SELECT COUNT(*) FROM teams WHERE name LIKE 'Oop%'").fetchone()[0] == 1,
      "één Noah en één Oop & Goop over")
check(len(schuss.backup_lijst()) == backups_voor + 1, "er werd vooraf een back-up gemaakt")
check(round(db.execute("SELECT elo FROM players WHERE id = ?", (NOAH,)).fetchone()[0], 6)
      == verwacht["spelers"][NOAH], "Aura herberekend")
db.close()

inloggen = schuss.app.test_client().post("/inloggen", data={"naam": "Noah Van den Broeck",
                                                            "wachtwoord": "nieuw"})
check(inloggen.status_code == 302, "Noah kan inloggen met zijn nieuwe wachtwoord")

with klant.session_transaction() as s:
    s["speler_id"] = ORG
check(klant.get(f"/admin/samenvoegen/spelers?bron={ORG}&doel={ANNA}").status_code == 200
      and "kan (nog) niet" in klant.get(f"/admin/samenvoegen/spelers?bron={ORG}&doel={ANNA}")
      .get_data(as_text=True), "het account van de eigenaar kan niet verdwijnen")

ref.close()
print("\n" + "=" * 62)
if FOUTEN:
    print(f"❌ {len(FOUTEN)} probleem(en):")
    for f in FOUTEN:
        print("  -", f)
else:
    print("✅ Samenvoegen klopt: één account, één team, volledige historiek.")
print("=" * 62)
os.chdir(BRON)
shutil.rmtree(WERKMAP, ignore_errors=True)
sys.exit(1 if FOUTEN else 0)

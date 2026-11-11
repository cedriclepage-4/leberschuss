# -*- coding: utf-8 -*-
"""Twee uitslagen tegelijk: wordt de knockout maar één keer aangemaakt?

    python test_gelijktijdig.py

Op het einde van de bracketfase eindigen er vaak meerdere tafels tegelijk, of
bevestigen beide teams op hetzelfde moment. Zagen twee verzoeken allebei
"bracketfase gespeeld, nog geen knockout", dan bouwden ze allebei een
knockoutschema: dubbel zoveel wedstrijden.

Zo'n botsing gebeurt in het echt maar af en toe. Om ze elke keer te krijgen,
houdt deze test het eerste verzoek even tegen vlak vóór het de knockout bouwt,
terwijl een tweede verzoek (eigen thread, eigen verbinding — zoals bij waitress)
hetzelfde probeert. Daarnaast: houdt het slot ook een ánder proces tegen
(gunicorn met meerdere werkprocessen)?

Deze test raakt shuss.db NIET aan: hij werkt in een tijdelijke map.
"""

import multiprocessing as mp
import os
import random
import shutil
import sys
import tempfile
import threading
import time

BRON = os.path.dirname(os.path.abspath(__file__))
FOUTEN = []


def check(voorwaarde, tekst):
    print(("  ✔ " if voorwaarde else "  ✘ ") + tekst)
    if not voorwaarde:
        FOUTEN.append(tekst)


def houd_slot_vast(werkmap, pad, klaar, seconden):
    """Ander proces: neem het evaluatieslot en hou het even vast."""
    sys.path.insert(0, werkmap)
    import tournament as tm
    from database import verbind
    db = verbind(pad)
    with tm._evaluatieslot(db):
        klaar.set()
        time.sleep(seconden)


def main():
    werkmap = tempfile.mkdtemp(prefix="shuss_test_")
    for naam in ("app.py", "database.py", "elo.py", "tournament.py", "kansen.py",
                 "league.py", "dop.py", "samenvoegen.py", "bezoekers.py"):
        shutil.copy(os.path.join(BRON, naam), werkmap)
    shutil.copytree(os.path.join(BRON, "templates"), os.path.join(werkmap, "templates"))
    shutil.copytree(os.path.join(BRON, "static"), os.path.join(werkmap, "static"),
                    ignore=shutil.ignore_patterns("uploads"))
    sys.path.insert(0, werkmap)
    os.chdir(werkmap)

    import app as shuss
    import tournament as tm
    from database import verbind

    pad = os.path.join(werkmap, "shuss.db")
    conn = verbind(pad)
    for i in range(1, 25):
        conn.execute("INSERT INTO players (name, nickname) VALUES (?, '')", (f"Speler {i:02d}",))
    for t in range(12):
        conn.execute("INSERT INTO teams (name, player1_id, player2_id, status) "
                     "VALUES (?, ?, ?, 'actief')", (f"Team {chr(65 + t)}", 2 * t + 1, 2 * t + 2))
    conn.execute("INSERT INTO tournaments (name, date, start_tijd, bracket_ronden, ko_teams, "
                 "potten) VALUES ('Test', '2026-03-07', '19:00', 3, 8, 4)")
    for t in range(1, 13):
        conn.execute("INSERT INTO tournament_teams (tournament_id, team_id) VALUES (1, ?)", (t,))
    for naam in ("Tafel 1", "Tafel 2", "Tafel 3"):
        conn.execute("INSERT INTO tournament_locations (tournament_id, name) VALUES (1, ?)", (naam,))
    conn.commit()
    ok, boodschap = tm.genereer(conn, 1, rng=random.Random(5))
    assert ok, boodschap

    # Alle bracketwedstrijden gespeeld, maar nog niet geëvalueerd: precies de
    # toestand vlak na de laatste uitslag. Team 1 wint telkens.
    conn.execute("UPDATE games SET status = 'gespeeld', winner_team_id = team1_id, "
                 "played_at = scheduled_at WHERE tournament_id = 1")
    conn.commit()
    shuss.herbereken_alles(conn)
    _, beslissend = tm.stand(conn, 1)
    if beslissend:
        print("  (deze loting geeft shootouts; die eerst spelen)")
        tm.evalueer(conn, 1)
        conn.execute("UPDATE games SET status = 'gespeeld', winner_team_id = team1_id "
                     "WHERE tournament_id = 1 AND fase = 'shootout'")
        conn.commit()

    # ------------------------------------------- twee verzoeken tegelijk --
    print("\n== Twee verzoeken evalueren tegelijk ==")
    echte_knockout = tm.maak_knockout
    eerste_wacht = threading.Event()
    ga_verder = threading.Event()

    def trage_knockout(db, t, geplaatst):
        # Het eerste verzoek blijft hier hangen tot het tweede zijn kans gehad heeft.
        if not eerste_wacht.is_set():
            eerste_wacht.set()
            ga_verder.wait(10)
        return echte_knockout(db, t, geplaatst)

    tm.maak_knockout = trage_knockout
    try:
        def verzoek():
            tm.evalueer(verbind(pad), 1)

        a = threading.Thread(target=verzoek)
        a.start()
        check(eerste_wacht.wait(10), "het eerste verzoek staat op het punt de knockout te bouwen")
        b = threading.Thread(target=verzoek)
        b.start()
        b.join(1.5)
        check(b.is_alive(), "het tweede verzoek wacht tot het eerste klaar is")
        ga_verder.set()
        a.join(20)
        b.join(20)
        check(not a.is_alive() and not b.is_alive(), "beide verzoeken raken klaar (geen deadlock)")
    finally:
        tm.maak_knockout = echte_knockout

    ko = conn.execute("SELECT COUNT(*) FROM games WHERE tournament_id = 1 "
                      "AND fase = 'knockout'").fetchone()[0]
    check(ko == 8, f"precies één knockoutschema: 8 wedstrijden (kregen er {ko})")

    # ------------------------------------------ binnen hetzelfde verzoek --
    print("\n== Evalueer roept zichzelf opnieuw op ==")
    klaar = threading.Event()

    def genest():
        eigen = verbind(pad)
        with tm._evaluatieslot(eigen):
            tm.evalueer_alles(eigen)
        eigen.close()
        klaar.set()

    threading.Thread(target=genest, daemon=True).start()
    check(klaar.wait(10), "evalueren terwijl je het slot zelf al hebt, loopt niet vast")

    # ------------------------------------------------ over processen heen --
    print("\n== Een ander proces houdt het slot vast ==")
    vast = mp.Event()
    proces = mp.Process(target=houd_slot_vast, args=(werkmap, pad, vast, 2.0))
    proces.start()
    check(vast.wait(30), "het andere proces heeft het slot")
    begin = time.perf_counter()
    tm.evalueer(conn, 1)
    gewacht = time.perf_counter() - begin
    proces.join(30)
    check(gewacht > 1.0, f"evalueer wachtte op het andere proces ({gewacht:.1f} s)")

    conn.close()
    os.chdir(BRON)
    shutil.rmtree(werkmap, ignore_errors=True)
    print()
    if FOUTEN:
        print(f"MISLUKT: {len(FOUTEN)} controle(s).")
        sys.exit(1)
    print("Alles in orde.")


if __name__ == "__main__":
    main()

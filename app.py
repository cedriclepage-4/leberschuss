# -*- coding: utf-8 -*-
"""
Leberschuss Tonzent — webapplicatie voor de schusscompetitie van jeugdhuis Tonzent.

Starten:
    pip install flask
    python app.py
Daarna surfen naar http://localhost:5000

Spelers maken zelf een account aan, vormen zelf teams (met uitnodigingen) en
melden zelf hun uitslagen; organisatoren beheren seizoenen, speeldagen en
wedstrijden.
"""

import difflib
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import unicodedata
from urllib.parse import quote, urlparse
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import wraps
from itertools import groupby

# ---------------------------------------------------------------- tijdzone --
# Hostingservers staan bijna altijd op UTC; dan zouden alle uren twee uur
# verkeerd staan (zowel in Python als in de datetime('now','localtime') van
# SQLite). Daarom zetten we de tijdzone van het proces zelf goed, vóór er ook
# maar iets met tijd gebeurt. Elders in de wereld? Zet TIJDZONE in de omgeving.
TIJDZONE = os.environ.get("TIJDZONE", "Europe/Brussels")
os.environ["TZ"] = TIJDZONE
if hasattr(time, "tzset"):          # Linux en macOS; op Windows niet nodig
    time.tzset()

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

import league as liga_motor
import tournament as toernooi_motor
import dop as kroonkurk
import samenvoegen
from database import (DB_PATH, ROL_ADMIN, ROL_EIGENAAR, ROL_SPELER, init_db,
                      verbind, vrij_spelernummer)
from elo import (START_ELO, fase_factor, fase_omschrijving, proces_wedstrijd)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TEMPLATE_MAP = os.path.join(BASE_DIR, "templates")
STATIC_MAP = os.path.join(BASE_DIR, "static")
if not os.path.isdir(TEMPLATE_MAP):
    TEMPLATE_MAP = BASE_DIR
if not os.path.isdir(STATIC_MAP):
    STATIC_MAP = BASE_DIR
UPLOAD_MAP = os.path.join(STATIC_MAP, "uploads")

BACKUP_MAP = os.path.join(BASE_DIR, "backups")

app = Flask(__name__, template_folder=TEMPLATE_MAP, static_folder=None)
# Ruim genoeg voor een databaseback-up; profielfoto's worden apart beperkt tot
# 3 MB in bewaar_avatar(), zodat niemand een reuzenfoto kan uploaden.
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024
MAX_AVATAR = 3 * 1024 * 1024

_SLEUTELBESTAND = os.path.join(BASE_DIR, ".secret_key")
if os.path.exists(_SLEUTELBESTAND):
    with open(_SLEUTELBESTAND) as f:
        app.secret_key = f.read().strip()
else:
    app.secret_key = secrets.token_hex(32)
    with open(_SLEUTELBESTAND, "w") as f:
        f.write(app.secret_key)

# ------------------------------------------------------------- achter nginx --
# Draait de site op een echte server, dan zit er meestal een webserver vóór die
# de https-verbinding afhandelt. Twee dingen moeten dan kloppen:
#
#   ACHTER_PROXY=1  → vertrouw de X-Forwarded-headers van die webserver, zodat
#                     we het échte IP-adres van de bezoeker zien (voor het
#                     claimlogboek) en weten dat de verbinding https is.
#   HTTPS=1         → de sessiecookie mag enkel over https verstuurd worden.
#
# Zet ACHTER_PROXY nooit aan zonder zo'n webserver ervoor: dan kan iedereen
# zelf een X-Forwarded-For meesturen en zijn de IP's in het logboek verzonnen.
app.config["SESSION_COOKIE_HTTPONLY"] = True     # niet leesbaar voor javascript
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"    # niet meesturen vanaf andere sites
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("HTTPS") == "1"
# Ingelogd blijven op je toestel, ook als je de site of de app sluit: de
# sessiecookie blijft een jaar geldig en elk bezoek schuift die termijn op.
# Wie een jaar niet langskomt, of op "Uitloggen" tikt, moet opnieuw inloggen.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=365)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True

if os.environ.get("ACHTER_PROXY") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

MAANDEN = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
           "augustus", "september", "oktober", "november", "december"]

_TOEGELATEN_STATIC = {"style.css", "app.js", "logo.svg", "logo.png", "dop.js", "dop3d.js",
                      "manifest.webmanifest", "icon-192.png", "icon-512.png",
                      "icon-maskable-512.png", "apple-touch-icon.png", "thuis.js",
                      "rook.js", "beamer.js", "viering.js", "jsQR.js",
                      # lettertypes (Barlow), zelf gehost zodat ze ook offline werken
                      "barlow-latin-400-normal.woff2", "barlow-latin-500-normal.woff2",
                      "barlow-latin-600-normal.woff2", "barlow-latin-700-normal.woff2",
                      "barlow-condensed-latin-600-normal.woff2",
                      "barlow-condensed-latin-700-normal.woff2",
                      "barlow-condensed-latin-800-normal.woff2"}
_AVATAR_EXT = {"png", "jpg", "jpeg", "webp", "gif"}
_AVATAR_RE = re.compile(r"^avatar_[pt]\d+_[0-9a-f]{8}\.(png|jpg|jpeg|webp|gif)$")

# SQL-fragment: weergavenaam = bijnaam als die er is, anders de accountnaam.
WEERGAVE = "COALESCE(NULLIF(p.nickname, ''), p.name)"


# ---------------------------------------------------------------- database --

def get_db():
    if "db" not in g:
        g.db = verbind(DB_PATH)
    return g.db


@app.teardown_appcontext
def sluit_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.before_request
def controleer_sessie():
    """Een sessie van een speler die niet meer bestaat (bv. na het vernieuwen van
    de spelersnummers) wordt netjes opgeruimd in plaats van fouten te geven."""
    speler_id = session.get("speler_id")
    if speler_id and request.endpoint not in (None, "statisch", "media"):
        bestaat = get_db().execute("SELECT 1 FROM players WHERE id = ?",
                                   (speler_id,)).fetchone()
        if not bestaat:
            session.pop("speler_id", None)
        elif not session.permanent:
            # Ook wie al ingelogd was vóór deze regel, blijft voortaan ingelogd.
            session.permanent = True


def instelling(db, sleutel, standaard=None):
    rij = db.execute("SELECT value FROM settings WHERE key = ?", (sleutel,)).fetchone()
    return rij["value"] if rij else standaard


def zet_instelling(db, sleutel, waarde):
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (sleutel, str(waarde)))


# ------------------------------------------------------------------- auth --
#
# Er is geen algemeen adminwachtwoord: je bent organisator omdat je eigen account
# die rol heeft. Het eerste account dat wordt aangemaakt is meteen eigenaar.
#
#   speler    — gewone speler, geen organisatierechten
#   admin     — organisator: mag alles behalve andermans organisatorrol afnemen
#   eigenaar  — hoofdorganisator (hoogstens één): mag dat wél, en kan het
#               eigenaarschap doorgeven. Kan door niemand afgezet of gewist worden.

ROLLEN = (ROL_SPELER, ROL_ADMIN, ROL_EIGENAAR)
ORGANISATOREN = (ROL_ADMIN, ROL_EIGENAAR)

ROL_LABEL = {ROL_SPELER: "speler", ROL_ADMIN: "organisator",
             ROL_EIGENAAR: "eigenaar"}


def huidige_speler():
    """De ingelogde speler (of None). Wordt per verzoek één keer opgehaald."""
    if "ik" not in g:
        speler_id = session.get("speler_id")
        g.ik = get_db().execute("SELECT * FROM players WHERE id = ?",
                                (speler_id,)).fetchone() if speler_id else None
    return g.ik


def mijn_rol():
    ik = huidige_speler()
    return ik["role"] if ik else ROL_SPELER


def is_organisator():
    return mijn_rol() in ORGANISATOREN


def is_eigenaar():
    return mijn_rol() == ROL_EIGENAAR


def de_eigenaar(db):
    return db.execute("SELECT * FROM players WHERE role = ?",
                      (ROL_EIGENAAR,)).fetchone()


# Vóór de loting zien spelers niet wie er ingeschreven is, en het aantal teams
# pas vanaf dit aantal: een bijna leeg toernooi schrikt anderen af.
INSCHRIJVINGEN_ZICHTBAAR_VANAF = 5


@app.context_processor
def rollen_in_templates():
    """Zo kunnen de sjablonen `ik`, `is_organisator` en `is_eigenaar` gebruiken."""
    return {"ik": huidige_speler(), "is_organisator": is_organisator(),
            "is_eigenaar": is_eigenaar(), "ZICHTBAAR_VANAF": INSCHRIJVINGEN_ZICHTBAAR_VANAF}


def login_vereist(f):
    """Organisatorrechten vereist (organisator of eigenaar)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("speler_id"):
            flash("Log eerst in met je eigen account om bij het "
                  "organisatiepaneel te kunnen.", "fout")
            return redirect(url_for("inloggen", volgende=request.path))
        if not is_organisator():
            flash("Je hebt hier geen toegang: enkel organisatoren kunnen bij het "
                  "organisatiepaneel. Vraag een organisator om je die rol te geven.",
                  "fout")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper


def eigenaar_vereist(f):
    """Enkel de eigenaar (hoofdorganisator)."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not is_eigenaar():
            flash("Enkel de eigenaar kan dat doen.", "fout")
            return redirect(url_for("admin_spelers"))
        return f(*args, **kwargs)
    return wrapper


def speler_vereist(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("speler_id"):
            flash("Log eerst in met je spelersaccount.", "fout")
            return redirect(url_for("inloggen"))
        return f(*args, **kwargs)
    return wrapper


# ------------------------------------------------------------------- hulp --

def weergave(speler):
    """De naam waarmee één speler op de site verschijnt: bijnaam, anders echte naam."""
    return (speler["nickname"] or "").strip() or speler["name"]


def weergavenamen(db):
    """{speler_id: bijnaam of naam} voor alle spelers."""
    return {r["id"]: r["naam"] for r in db.execute(
        f"SELECT p.id, {WEERGAVE} AS naam FROM players p")}


def teamnamen(db):
    return {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM teams")}


def herbereken_alles(db):
    """
    Herbereken ALLE ratings door elke gespeelde wedstrijd chronologisch opnieuw
    af te spelen. Zo blijven de ratings altijd consistent, ook na het
    verwijderen of corrigeren van een resultaat of het wijzigen van K-factoren.

    Er worden twee soorten ratings berekend:

    * de **permanente Aura** (`players.elo` / `teams.elo`): álle wedstrijden
      tellen mee, league én toernooi. Toernooiwedstrijden wegen zwaarder naarmate
      een team verder geraakt (zie `elo.fase_factor`). Shootouts tellen niet mee:
      die beslissen enkel wie doorstoot.
    * de **seizoens-Aura** (`season_ratings`): per seizoen apart, iedereen start
      er opnieuw op 1000, en enkel ligawedstrijden van dat seizoen tellen mee.
    """
    k_s = float(instelling(db, "k_speler", "32"))
    k_t = float(instelling(db, "k_team", "32"))

    team_leden = {r["id"]: (r["player1_id"], r["player2_id"])
                  for r in db.execute("SELECT id, player1_id, player2_id FROM teams")}

    db.execute("DELETE FROM rating_history")
    db.execute("DELETE FROM season_ratings")

    # ---------------------------------------------------------- permanent --
    speler_elos = {r["id"]: START_ELO for r in db.execute("SELECT id FROM players")}
    team_elos = {}

    wedstrijden = db.execute(
        "SELECT * FROM games WHERE status = 'gespeeld' AND team1_id IS NOT NULL "
        "AND team2_id IS NOT NULL ORDER BY played_at, id"
    ).fetchall()

    for w in wedstrijden:
        t1, t2 = w["team1_id"], w["team2_id"]
        if t1 not in team_leden or t2 not in team_leden:
            continue
        if w["fase"] == "shootout":
            continue          # beslissingswedstrijd: raakt de ratings niet aan
        for t in (t1, t2):
            if t not in team_elos:
                a, b = team_leden[t]
                team_elos[t] = (speler_elos[a] + speler_elos[b]) / 2.0

        s1 = {pid: speler_elos[pid] for pid in team_leden[t1]}
        s2 = {pid: speler_elos[pid] for pid in team_leden[t2]}
        winnaar = 1 if w["winner_team_id"] == t1 else 2
        f = fase_factor(w["fase"], w["ronde"])
        # In de knockout betaalt de verliezer maar een deel: zo kost ver
        # geraken nooit Aura. Zie de uitleg bij KO_VERLIES in elo.py.
        f_verlies = fase_factor(w["fase"], w["ronde"], verloren=True)
        deel = (f_verlies / f) if f else 1.0

        nieuwe, nieuw_t1, nieuw_t2 = proces_wedstrijd(s1, s2, team_elos[t1],
                                                      team_elos[t2], winnaar,
                                                      k_s * f, k_t * f, deel)

        for pid, na in nieuwe.items():
            db.execute("INSERT INTO rating_history (game_id, entity_type, entity_id, "
                       "elo_voor, elo_na, scope) VALUES (?, 'speler', ?, ?, ?, 'permanent')",
                       (w["id"], pid, speler_elos[pid], na))
        db.execute("INSERT INTO rating_history (game_id, entity_type, entity_id, "
                   "elo_voor, elo_na, scope) VALUES (?, 'team', ?, ?, ?, 'permanent')",
                   (w["id"], t1, team_elos[t1], nieuw_t1))
        db.execute("INSERT INTO rating_history (game_id, entity_type, entity_id, "
                   "elo_voor, elo_na, scope) VALUES (?, 'team', ?, ?, ?, 'permanent')",
                   (w["id"], t2, team_elos[t2], nieuw_t2))

        speler_elos.update(nieuwe)
        team_elos[t1], team_elos[t2] = nieuw_t1, nieuw_t2

    for pid, e in speler_elos.items():
        db.execute("UPDATE players SET elo = ? WHERE id = ?", (e, pid))
    for tid, leden in team_leden.items():
        if tid not in team_elos:
            # Nog niet gespeeld: het gemiddelde van de spelers. Een onvolledig
            # team (tweede speler nog niet aanvaard) krijgt de Aura van zijn
            # enige speler, net zoals bij het aanmaken.
            aanwezig = [speler_elos[p] for p in leden if p is not None]
            team_elos[tid] = sum(aanwezig) / len(aanwezig)
        db.execute("UPDATE teams SET elo = ? WHERE id = ?", (team_elos[tid], tid))

    # ------------------------------------------------------- per seizoen --
    for s in db.execute("SELECT id FROM seasons").fetchall():
        _herbereken_seizoen(db, s["id"], team_leden, k_s, k_t)

    db.commit()


def _herbereken_seizoen(db, seizoen_id, team_leden, k_s, k_t):
    """Seizoens-Aura: iedereen start op 1000, enkel ligawedstrijden tellen mee."""
    sp, tm = {}, {}
    telling = defaultdict(lambda: {"gespeeld": 0, "winst": 0})

    wedstrijden = db.execute("""
        SELECT g.* FROM games g
        JOIN matchdays md ON md.id = g.matchday_id
        WHERE md.season_id = ? AND g.status = 'gespeeld' AND g.fase = 'liga'
          AND g.team1_id IS NOT NULL AND g.team2_id IS NOT NULL
        ORDER BY g.played_at, g.id
    """, (seizoen_id,)).fetchall()

    for w in wedstrijden:
        t1, t2 = w["team1_id"], w["team2_id"]
        if t1 not in team_leden or t2 not in team_leden:
            continue
        for pid in team_leden[t1] + team_leden[t2]:
            sp.setdefault(pid, START_ELO)
        for t in (t1, t2):
            if t not in tm:
                a, b = team_leden[t]
                tm[t] = (sp[a] + sp[b]) / 2.0

        s1 = {pid: sp[pid] for pid in team_leden[t1]}
        s2 = {pid: sp[pid] for pid in team_leden[t2]}
        winnaar = 1 if w["winner_team_id"] == t1 else 2
        nieuwe, nieuw_t1, nieuw_t2 = proces_wedstrijd(s1, s2, tm[t1], tm[t2],
                                                      winnaar, k_s, k_t)

        for pid, na in nieuwe.items():
            db.execute("INSERT INTO rating_history (game_id, entity_type, entity_id, "
                       "elo_voor, elo_na, scope, season_id) "
                       "VALUES (?, 'speler', ?, ?, ?, 'seizoen', ?)",
                       (w["id"], pid, sp[pid], na, seizoen_id))
        for tid_, voor, na in ((t1, tm[t1], nieuw_t1), (t2, tm[t2], nieuw_t2)):
            db.execute("INSERT INTO rating_history (game_id, entity_type, entity_id, "
                       "elo_voor, elo_na, scope, season_id) "
                       "VALUES (?, 'team', ?, ?, ?, 'seizoen', ?)",
                       (w["id"], tid_, voor, na, seizoen_id))

        for tid_, leden in ((t1, team_leden[t1]), (t2, team_leden[t2])):
            gewonnen = (w["winner_team_id"] == tid_)
            telling[("team", tid_)]["gespeeld"] += 1
            telling[("team", tid_)]["winst"] += gewonnen
            for pid in leden:
                telling[("speler", pid)]["gespeeld"] += 1
                telling[("speler", pid)]["winst"] += gewonnen

        sp.update(nieuwe)
        tm[t1], tm[t2] = nieuw_t1, nieuw_t2

    for pid, e in sp.items():
        d = telling[("speler", pid)]
        db.execute("INSERT INTO season_ratings (season_id, entity_type, entity_id, "
                   "elo, gespeeld, winst) VALUES (?, 'speler', ?, ?, ?, ?)",
                   (seizoen_id, pid, e, d["gespeeld"], d["winst"]))
    for tid_, e in tm.items():
        d = telling[("team", tid_)]
        db.execute("INSERT INTO season_ratings (season_id, entity_type, entity_id, "
                   "elo, gespeeld, winst) VALUES (?, 'team', ?, ?, ?, ?)",
                   (seizoen_id, tid_, e, d["gespeeld"], d["winst"]))


def na_resultaat(db, game_id=None):
    """Alles bijwerken na een nieuw of gewijzigd resultaat."""
    herbereken_alles(db)
    return toernooi_motor.evalueer_alles(db)


def seizoen_klassement(db, seizoen_id):
    """Klassement binnen één seizoen (seizoens-Aura, iedereen startte op 1000)."""
    # Net als bij de teams: wie ingeschreven is hoort erbij te staan, ook vóór
    # zijn eerste wedstrijd. De spelers van een ingeschreven team doen mee.
    spelers = {r["pid"]: dict(r) for r in db.execute(f"""
        SELECT sr.*, {WEERGAVE} AS naam, p.avatar, p.id AS pid
        FROM season_ratings sr
        JOIN players p ON p.id = sr.entity_id
        WHERE sr.season_id = ? AND sr.entity_type = 'speler'
    """, (seizoen_id,))}
    for p in db.execute(f"""
        SELECT p.id AS pid, {WEERGAVE} AS naam, p.avatar
        FROM season_teams st
        JOIN teams t ON t.id = st.team_id
        JOIN players p ON p.id = t.player1_id OR p.id = t.player2_id
        WHERE st.season_id = ?
    """, (seizoen_id,)):
        spelers.setdefault(p["pid"], {"pid": p["pid"], "naam": p["naam"],
                                      "avatar": p["avatar"], "elo": START_ELO,
                                      "gespeeld": 0, "winst": 0})
    spelers = sorted(spelers.values(), key=lambda r: (-r["elo"], r["naam"]))
    # Ingeschreven teams horen in het klassement te staan zodra ze zich
    # inschrijven — ook vóór hun eerste wedstrijd. Wie nog niets speelde staat
    # gewoon op de startwaarde met nul wedstrijden.
    teams = {r["tid"]: dict(r) for r in db.execute("""
        SELECT sr.*, t.name AS naam, t.avatar, t.id AS tid
        FROM season_ratings sr
        JOIN teams t ON t.id = sr.entity_id
        WHERE sr.season_id = ? AND sr.entity_type = 'team'
    """, (seizoen_id,))}
    for t in db.execute("""
        SELECT t.id AS tid, t.name AS naam, t.avatar
        FROM season_teams st JOIN teams t ON t.id = st.team_id
        WHERE st.season_id = ?
    """, (seizoen_id,)):
        teams.setdefault(t["tid"], {"tid": t["tid"], "naam": t["naam"],
                                    "avatar": t["avatar"], "elo": START_ELO,
                                    "gespeeld": 0, "winst": 0})
    gesorteerd = sorted(teams.values(), key=lambda r: (-r["elo"], r["naam"]))
    return spelers, gesorteerd


def huidig_seizoen(db):
    vandaag = date.today().isoformat()
    return db.execute("SELECT * FROM seasons WHERE start_date <= ? AND end_date >= ? "
                      "ORDER BY start_date DESC LIMIT 1", (vandaag, vandaag)).fetchone()


def speel_statistieken(db):
    """Gespeeld/gewonnen per speler en per team, berekend uit de wedstrijden.

    Shootouts tellen niet mee: dat zijn beslissingswedstrijden, geen echte
    partijen. Ze blijven wel zichtbaar in de wedstrijdhistoriek.
    """
    rijen = db.execute("""
        SELECT g.winner_team_id, g.team1_id, g.team2_id,
               t1.player1_id AS a1, t1.player2_id AS a2,
               t2.player1_id AS b1, t2.player2_id AS b2
        FROM games g
        JOIN teams t1 ON t1.id = g.team1_id
        JOIN teams t2 ON t2.id = g.team2_id
        WHERE g.status = 'gespeeld' AND g.fase != 'shootout'
    """).fetchall()
    spelers = defaultdict(lambda: {"gespeeld": 0, "winst": 0})
    teams = defaultdict(lambda: {"gespeeld": 0, "winst": 0})
    for r in rijen:
        for tid, pids in ((r["team1_id"], (r["a1"], r["a2"])),
                          (r["team2_id"], (r["b1"], r["b2"]))):
            gewonnen = (tid == r["winner_team_id"])
            teams[tid]["gespeeld"] += 1
            teams[tid]["winst"] += gewonnen
            for pid in pids:
                spelers[pid]["gespeeld"] += 1
                spelers[pid]["winst"] += gewonnen
    return spelers, teams


def wedstrijden_deze_maand(db):
    """Aantal wedstrijden (gepland + gespeeld) per speler in de huidige maand."""
    prefix = datetime.now().strftime("%Y-%m")
    rijen = db.execute("""
        SELECT COALESCE(g.played_at, g.scheduled_at) AS moment,
               t1.player1_id AS a1, t1.player2_id AS a2,
               t2.player1_id AS b1, t2.player2_id AS b2
        FROM games g
        JOIN teams t1 ON t1.id = g.team1_id
        JOIN teams t2 ON t2.id = g.team2_id
        WHERE g.fase != 'shootout'
    """).fetchall()
    telling = defaultdict(int)
    for r in rijen:
        if (r["moment"] or "").startswith(prefix):
            for pid in (r["a1"], r["a2"], r["b1"], r["b2"]):
                telling[pid] += 1
    return telling


def alle_rangen(db):
    return db.execute("SELECT * FROM ranks ORDER BY min_elo DESC").fetchall()


def rang_voor(elo, rangen):
    for r in rangen:
        if r["min_elo"] <= elo <= r["max_elo"]:
            return r
    return None


def rangverdeling(db):
    """Hoeveel actieve spelers in elke rang zitten (voor het staafdiagram)."""
    rangen = alle_rangen(db)
    if not rangen:
        return []
    elos = [r["elo"] for r in db.execute("SELECT elo FROM players WHERE active = 1")]
    totaal = len(elos)
    verdeling = []
    zonder = 0
    for e in elos:
        if rang_voor(e, rangen) is None:
            zonder += 1
    for r in sorted(rangen, key=lambda x: -x["min_elo"]):
        aantal = sum(1 for e in elos if rang_voor(e, rangen) is not None
                     and rang_voor(e, rangen)["id"] == r["id"])
        verdeling.append({"naam": r["name"], "kleur": r["color"], "aantal": aantal,
                          "pct": (100.0 * aantal / totaal) if totaal else 0})
    if zonder:
        verdeling.append({"naam": "Zonder rang", "kleur": "#cbd5e1", "aantal": zonder,
                          "pct": (100.0 * zonder / totaal) if totaal else 0})
    return verdeling


def elo_verloop(db, entity_id, entity_type="speler"):
    """Punten voor de Aura-grafiek: na elke wedstrijd de nieuwe Aura + positie.

    Werkt zowel voor een speler als voor een team: de plaats in het klassement
    wordt telkens berekend binnen de eigen soort (spelers onder spelers, teams
    onder teams), net zoals de twee klassementen op de startpagina.

    Klein verschil tussen de twee, dat het teamklassement ook maakt: elke speler
    staat in de ranglijst, maar een team pas zodra het samen gespeeld heeft. Een
    duo dat enkel op papier bestaat, telt dus niet mee voor de plaats.
    """
    is_team = entity_type == "team"
    tabel = "teams" if is_team else "players"
    huidige = {r["id"]: START_ELO for r in db.execute(f"SELECT id FROM {tabel}")}
    gespeeld = set()                       # wie al aan de bak is geweest
    rijen = db.execute("""
        SELECT rh.game_id, rh.entity_id, rh.elo_na, g.played_at
        FROM rating_history rh
        JOIN games g ON g.id = rh.game_id
        WHERE rh.entity_type = ? AND rh.scope = 'permanent'
        ORDER BY g.played_at, g.id
    """, (entity_type,)).fetchall()

    punten = []
    for _game_id, groep in groupby(rijen, key=lambda r: r["game_id"]):
        groep = list(groep)
        deed_mee = False
        moment = None
        for r in groep:
            if r["entity_id"] in huidige:
                huidige[r["entity_id"]] = r["elo_na"]
                gespeeld.add(r["entity_id"])
            if r["entity_id"] == entity_id:
                deed_mee = True
                moment = r["played_at"]
        if deed_mee:
            eigen = huidige[entity_id]
            rang = 1 + sum(1 for wie, v in huidige.items() if v > eigen
                           and (not is_team or wie in gespeeld))
            punten.append({"datum": filter_datum(moment), "elo": round(eigen),
                           "rang": rang})
    return punten


def actieve_wedstrijd(db, speler_id):
    """De eerstvolgende wedstrijd van deze speler, voor de balk onderaan.

    Zo hoeft niemand te zoeken waar hij moet zijn: één tik brengt hem naar het
    formulier om de uitslag te melden.
    """
    g_ = db.execute("""
        SELECT g.id, g.scheduled_at, g.tournament_id, g.team1_id, g.team2_id,
               tn.name AS toernooi, loc.name AS locatie,
               t1.player1_id AS a1, t1.player2_id AS a2,
               t2.player1_id AS b1, t2.player2_id AS b2
        FROM games g
        JOIN teams t1 ON t1.id = g.team1_id
        JOIN teams t2 ON t2.id = g.team2_id
        LEFT JOIN tournaments tn ON tn.id = g.tournament_id
        LEFT JOIN tournament_locations loc ON loc.id = g.location_id
        WHERE g.status = 'gepland'
          AND ? IN (t1.player1_id, t1.player2_id, t2.player1_id, t2.player2_id)
        ORDER BY g.scheduled_at, g.id LIMIT 1
    """, (speler_id,)).fetchone()
    if not g_:
        return None
    # Een leaguewedstrijd tonen we niet zolang dat gedeelte dicht staat.
    if g_["tournament_id"] is None and not league_toegankelijk():
        return None

    eigen = g_["team1_id"] if speler_id in (g_["a1"], g_["a2"]) else g_["team2_id"]
    tegen = g_["team2_id"] if eigen == g_["team1_id"] else g_["team1_id"]
    # Enkel de naam van dié tegenstander ophalen: deze functie draait bij élke
    # paginaweergave van een ingelogde speler.
    naam_rij = db.execute("SELECT name FROM teams WHERE id = ?", (tegen,)).fetchone()
    gemeld = db.execute("SELECT 1 FROM game_reports WHERE game_id = ? AND team_id = ?",
                        (g_["id"], eigen)).fetchone() is not None
    if g_["tournament_id"]:
        doel = url_for("toernooi_detail", toernooi_id=g_["tournament_id"])
    else:
        doel = url_for("speler_profiel", speler_id=speler_id) + "#melden"
    return {"tegenstander": naam_rij["name"] if naam_rij else "?",
            "locatie": g_["locatie"],
            "wanneer": g_["scheduled_at"], "toernooi": g_["toernooi"],
            "gemeld": gemeld, "url": doel, "game_id": g_["id"]}


def wedstrijd_context(g_):
    """Waar hoort deze wedstrijd bij? Geeft label, soort en link terug."""
    if g_["toernooi"]:
        return {"soort": "toernooi", "naam": g_["toernooi"],
                "detail": fase_omschrijving(g_["fase"], g_["ronde"]),
                "url": url_for("toernooi_detail", toernooi_id=g_["toernooi_id"]),
                "locatie": g_["locatie"]}
    if g_["seizoen"]:
        # Staat de league uit, dan is de seizoenspagina er niet voor spelers:
        # de naam blijft staan, de link valt weg.
        return {"soort": "liga", "naam": g_["seizoen"], "detail": g_["speeldag"],
                "url": (url_for("seizoen_detail", seizoen_id=g_["seizoen_id"])
                        if league_toegankelijk() else None),
                "locatie": None}
    return {"soort": "liga", "naam": "Vriendschappelijk", "detail": None,
            "url": None, "locatie": None}


def speler_wedstrijd_meldingen(db, speler_id, toernooi_id=None, alleen_liga=False,
                               alleen_toernooi=False):
    """Geplande wedstrijden van deze speler, met de meldstatus van beide teams."""
    items = []
    filter_sql = ""
    args = [speler_id]
    if toernooi_id is not None:
        filter_sql = "AND g.tournament_id = ?"
        args.append(toernooi_id)
    elif alleen_liga:
        filter_sql = "AND g.tournament_id IS NULL"
    elif alleen_toernooi:
        filter_sql = "AND g.tournament_id IS NOT NULL"
    for g_ in db.execute(f"""
        SELECT g.*, t1.player1_id AS a1, t1.player2_id AS a2,
               t2.player1_id AS b1, t2.player2_id AS b2,
               md.title AS speeldag, s.name AS seizoen, s.id AS seizoen_id,
               tn.name AS toernooi, tn.id AS toernooi_id, loc.name AS locatie
        FROM games g
        JOIN teams t1 ON t1.id = g.team1_id
        JOIN teams t2 ON t2.id = g.team2_id
        LEFT JOIN matchdays md ON md.id = g.matchday_id
        LEFT JOIN seasons s ON s.id = md.season_id
        LEFT JOIN tournaments tn ON tn.id = g.tournament_id
        LEFT JOIN tournament_locations loc ON loc.id = g.location_id
        WHERE g.status = 'gepland'
          AND ? IN (t1.player1_id, t1.player2_id, t2.player1_id, t2.player2_id)
          {filter_sql}
        ORDER BY g.scheduled_at
    """, args).fetchall():
        eigen_team = g_["team1_id"] if speler_id in (g_["a1"], g_["a2"]) else g_["team2_id"]
        tegen_team = g_["team2_id"] if eigen_team == g_["team1_id"] else g_["team1_id"]
        meldingen = {r["team_id"]: r for r in db.execute(
            "SELECT * FROM game_reports WHERE game_id = ?", (g_["id"],))}
        eigen = meldingen.get(eigen_team)
        tegen = meldingen.get(tegen_team)
        conflict = bool(eigen and tegen
                        and eigen["winner_team_id"] != tegen["winner_team_id"])
        lid_ids = ((g_["a1"], g_["a2"]) if eigen_team == g_["team1_id"]
                   else (g_["b1"], g_["b2"]))
        leden = db.execute(f"SELECT p.id, {WEERGAVE} AS name FROM players p "
                           "WHERE p.id IN (?, ?)", lid_ids).fetchall()
        waarden = {pid: {} for pid in lid_ids}
        for r in db.execute("SELECT player_id, stat_type_id, value FROM game_stats "
                            "WHERE game_id = ? AND player_id IN (?, ?)",
                            (g_["id"], *lid_ids)):
            waarden[r["player_id"]][r["stat_type_id"]] = r["value"]
        # Nog een vroegere wedstrijd open? Dan tonen we deze wel, maar op slot.
        vorige = toernooi_motor.eerdere_wedstrijd(db, g_)
        items.append({"game": g_, "eigen_team": eigen_team, "tegen_team": tegen_team,
                      "eigen": eigen, "tegen": tegen, "conflict": conflict,
                      "leden": leden, "waarden": waarden,
                      "context": wedstrijd_context(g_),
                      "wacht_op": vorige,
                      "heeft_waarden": any(waarden.values())})
    return items


def seizoen_historiek(db, entity_id, entity_type="speler"):
    """Per seizoen: eindplaats in het seizoensklassement, cijfers en Aura.

    Werkt zowel voor een speler als voor een team; de plaats is telkens die
    binnen het klassement van zijn eigen soort.
    """
    uit = []
    for r in db.execute("""
        SELECT s.id, s.name, s.start_date, s.end_date,
               sr.elo, sr.gespeeld, sr.winst,
               (SELECT COUNT(*) FROM season_ratings x
                 WHERE x.season_id = sr.season_id AND x.entity_type = sr.entity_type
                   AND x.elo > sr.elo) + 1 AS plaats,
               (SELECT COUNT(*) FROM season_ratings x
                 WHERE x.season_id = sr.season_id
                   AND x.entity_type = sr.entity_type) AS aantal
        FROM season_ratings sr
        JOIN seasons s ON s.id = sr.season_id
        WHERE sr.entity_type = ? AND sr.entity_id = ?
        ORDER BY s.start_date DESC
    """, (entity_type, entity_id)):
        d = dict(r)
        d["status"] = seizoen_status(r)
        d["verlies"] = r["gespeeld"] - r["winst"]
        uit.append(d)
    return uit


def toernooi_historiek(db, speler_id=None, team_id=None):
    """Per toernooi: met welk team, hoever geraakt en de eindstand.

    Geef ofwel een speler mee (dan tellen alle teams waarin hij speelde),
    ofwel één team.
    """
    from elo import KO_LABEL

    if team_id is not None:
        rijen = db.execute("""
            SELECT tn.id, tn.name, tn.date, tn.status, tn.ko_teams,
                   tt.team_id, t.name AS team_naam
            FROM tournaments tn
            JOIN tournament_teams tt ON tt.tournament_id = tn.id
            JOIN teams t ON t.id = tt.team_id
            WHERE tt.team_id = ? AND tn.status != 'opzet'
            ORDER BY tn.date DESC, tn.id DESC
        """, (team_id,))
    else:
        rijen = db.execute("""
            SELECT tn.id, tn.name, tn.date, tn.status, tn.ko_teams,
                   tt.team_id, t.name AS team_naam
            FROM tournaments tn
            JOIN tournament_teams tt ON tt.tournament_id = tn.id
            JOIN teams t ON t.id = tt.team_id
            WHERE (t.player1_id = ? OR t.player2_id = ?) AND tn.status != 'opzet'
            ORDER BY tn.date DESC, tn.id DESC
        """, (speler_id, speler_id))

    uit = []
    for r in rijen:
        tid, team_id = r["id"], r["team_id"]

        cijfers = db.execute("""
            SELECT COUNT(*) AS gespeeld,
                   COALESCE(SUM(winner_team_id = ?), 0) AS winst
            FROM games WHERE tournament_id = ? AND status = 'gespeeld'
              AND fase != 'shootout' AND (team1_id = ? OR team2_id = ?)
        """, (team_id, tid, team_id, team_id)).fetchone()

        positie = aantal = None
        if r["status"] != "opzet":
            geordend, _ = toernooi_motor.stand(db, tid)
            aantal = len(geordend)
            positie = next((x["positie"] for x in geordend
                            if x["team_id"] == team_id), None)

        ko = db.execute("""
            SELECT * FROM games WHERE tournament_id = ? AND fase = 'knockout'
              AND (team1_id = ? OR team2_id = ?) ORDER BY ronde
        """, (tid, team_id, team_id)).fetchall()
        gespeelde_ko = [g for g in ko if g["status"] == "gespeeld"]
        wacht_nog = any(g["status"] == "gepland" for g in ko)

        klasse, label = "", ""
        if r["status"] == "opzet":
            label = "Nog niet geloot"
        elif gespeelde_ko or wacht_nog:
            verst = min(g["ronde"] for g in (gespeelde_ko or ko))
            beslissend = next((g for g in gespeelde_ko if g["ronde"] == verst), None)
            gewonnen = bool(beslissend and beslissend["winner_team_id"] == team_id)
            if wacht_nog:
                volgende = min(g["ronde"] for g in ko if g["status"] == "gepland")
                label = f"Nog in de running · {KO_LABEL.get(volgende, 'knockout').lower()}"
                klasse = "loopt"
            elif verst == 2 and gewonnen:
                label = "Toernooiwinnaar"
                klasse = "goud"
            elif verst == 2:
                label = "Finalist"
                klasse = "zilver"
            elif verst == 3 and gewonnen:          # kleine finale gewonnen
                label = "Derde plaats"
                klasse = "brons"
            elif verst == 3:
                label = "Vierde plaats"
            else:
                label = f"Uitgeschakeld in de {KO_LABEL.get(verst, 'knockout').lower()}"
                klasse = "brons" if verst == 4 else ""
        elif r["status"] == "afgelopen":
            label = ("Net naast de knockout" if positie and positie <= r["ko_teams"] + 2
                     else "Niet naar de knockout")
        else:
            label = "Bracketfase bezig"
            klasse = "loopt"

        uit.append({"id": tid, "naam": r["name"], "datum": r["date"],
                    "status": r["status"], "team_id": team_id,
                    "team_naam": r["team_naam"], "positie": positie,
                    "aantal": aantal, "ko_teams": r["ko_teams"],
                    "gespeeld": cijfers["gespeeld"], "winst": cijfers["winst"],
                    "verlies": cijfers["gespeeld"] - cijfers["winst"],
                    "label": label, "klasse": klasse})
    return uit


def liga_records(db):
    """Vaste "recordboek"-statistieken, berekend uit alle gespeelde wedstrijden."""
    naam_van = {r["id"]: r["naam"] for r in db.execute(
        f"SELECT p.id, {WEERGAVE} AS naam FROM players p WHERE p.active = 1")}
    rijen = db.execute("""
        SELECT g.winner_team_id, g.team1_id, g.team2_id,
               t1.player1_id AS a1, t1.player2_id AS a2,
               t2.player1_id AS b1, t2.player2_id AS b2
        FROM games g
        JOIN teams t1 ON t1.id = g.team1_id
        JOIN teams t2 ON t2.id = g.team2_id
        WHERE g.status = 'gespeeld' AND g.fase != 'shootout'
        ORDER BY g.played_at, g.id
    """).fetchall()
    if not rijen:
        return []

    sp = defaultdict(lambda: {"g": 0, "w": 0, "reeks": 0, "beste_reeks": 0})
    team_tellen = defaultdict(int)
    for r in rijen:
        team_tellen[r["team1_id"]] += 1
        team_tellen[r["team2_id"]] += 1
        won1 = r["winner_team_id"] == r["team1_id"]
        winnaars = (r["a1"], r["a2"]) if won1 else (r["b1"], r["b2"])
        verliezers = (r["b1"], r["b2"]) if won1 else (r["a1"], r["a2"])
        for pid in winnaars:
            s = sp[pid]
            s["g"] += 1
            s["w"] += 1
            s["reeks"] += 1
            s["beste_reeks"] = max(s["beste_reeks"], s["reeks"])
        for pid in verliezers:
            s = sp[pid]
            s["g"] += 1
            s["reeks"] = 0

    def top(sleutel, filter_fn=None):
        items = [(pid, d) for pid, d in sp.items()
                 if pid in naam_van and (filter_fn is None or filter_fn(d))]
        if not items:
            return None, []
        beste = max(sleutel(d) for _, d in items)
        if beste <= 0:
            return None, []
        return beste, [pid for pid, d in items if sleutel(d) == beste]

    def speler_links(pids):
        return [{"naam": naam_van[p],
                 "url": url_for("speler_profiel", speler_id=p)} for p in pids[:4]]

    records = []

    beste, pids = top(lambda d: d["beste_reeks"])
    if beste:
        records.append({"emoji": "🔥", "titel": "Langste winstreeks",
                        "waarde": f"{beste} op rij", "houders": speler_links(pids)})

    beste, pids = top(lambda d: d["reeks"])
    if beste and beste >= 2:
        records.append({"emoji": "⚡", "titel": "Actuele winstreeks",
                        "waarde": f"{beste} op rij", "houders": speler_links(pids)})

    beste, pids = top(lambda d: d["g"])
    if beste:
        records.append({"emoji": "🎮", "titel": "Meeste wedstrijden",
                        "waarde": f"{beste} gespeeld", "houders": speler_links(pids)})

    beste, pids = top(lambda d: d["w"])
    if beste:
        records.append({"emoji": "🏆", "titel": "Meeste overwinningen",
                        "waarde": f"{beste} gewonnen", "houders": speler_links(pids)})

    beste, pids = top(lambda d: round(100.0 * d["w"] / d["g"], 1),
                      filter_fn=lambda d: d["g"] >= 5)
    if beste:
        records.append({"emoji": "💯", "titel": "Beste winstpercentage",
                        "waarde": f"{beste:.0f}% (min. 5 wedstrijden)",
                        "houders": speler_links(pids)})

    piek = db.execute("""
        SELECT rh.entity_id AS pid, MAX(rh.elo_na) AS v
        FROM rating_history rh
        JOIN players p ON p.id = rh.entity_id AND p.active = 1
        WHERE rh.entity_type = 'speler' AND rh.scope = 'permanent'
    """).fetchone()
    if piek and piek["pid"] is not None:
        records.append({"emoji": "🚀", "titel": "Hoogste Aura ooit",
                        "waarde": f"{piek['v']:.0f}",
                        "houders": speler_links([piek["pid"]])})

    sprong = db.execute("""
        SELECT rh.entity_id AS pid, MAX(rh.elo_na - rh.elo_voor) AS v
        FROM rating_history rh
        JOIN players p ON p.id = rh.entity_id AND p.active = 1
        WHERE rh.entity_type = 'speler' AND rh.scope = 'permanent'
    """).fetchone()
    if sprong and sprong["pid"] is not None and sprong["v"] and sprong["v"] > 0:
        records.append({"emoji": "📈", "titel": "Grootste Aura-sprong",
                        "waarde": f"+{sprong['v']:.0f} in één wedstrijd",
                        "houders": speler_links([sprong["pid"]])})

    if team_tellen:
        namen = teamnamen(db)
        beste = max(team_tellen.values())
        houders = [{"naam": namen[t], "url": url_for("team_profiel", team_id=t)}
                   for t, n in team_tellen.items() if n == beste][:3]
        records.append({"emoji": "👯", "titel": "Onafscheidelijk duo",
                        "waarde": f"{beste} wedstrijden samen", "houders": houders})

    return records


def seizoen_stats(db, seizoen_id):
    """Ereborden van één seizoen: overwinningen en Aura-klim, spelers en teams."""
    rijen = db.execute("""
        SELECT g.id, g.winner_team_id, g.team1_id, g.team2_id,
               t1.player1_id AS a1, t1.player2_id AS a2,
               t2.player1_id AS b1, t2.player2_id AS b2
        FROM games g
        JOIN matchdays md ON md.id = g.matchday_id
        JOIN teams t1 ON t1.id = g.team1_id
        JOIN teams t2 ON t2.id = g.team2_id
        WHERE md.season_id = ? AND g.status = 'gespeeld'
    """, (seizoen_id,)).fetchall()
    if not rijen:
        return None

    sp = defaultdict(lambda: {"g": 0, "w": 0})
    tm = defaultdict(lambda: {"g": 0, "w": 0})
    for r in rijen:
        for tid, pids in ((r["team1_id"], (r["a1"], r["a2"])),
                          (r["team2_id"], (r["b1"], r["b2"]))):
            gewonnen = (tid == r["winner_team_id"])
            tm[tid]["g"] += 1
            tm[tid]["w"] += gewonnen
            for pid in pids:
                sp[pid]["g"] += 1
                sp[pid]["w"] += gewonnen

    klim = {"speler": defaultdict(float), "team": defaultdict(float)}
    for r in db.execute("""
        SELECT rh.entity_type, rh.entity_id,
               SUM(rh.elo_na - rh.elo_voor) AS d
        FROM rating_history rh
        WHERE rh.scope = 'seizoen' AND rh.season_id = ?
        GROUP BY rh.entity_type, rh.entity_id
    """, (seizoen_id,)):
        klim[r["entity_type"]][r["entity_id"]] = r["d"]

    namen_sp = weergavenamen(db)
    namen_tm = teamnamen(db)

    spelers_winst = [{"naam": namen_sp[pid],
                      "url": url_for("speler_profiel", speler_id=pid),
                      "waarde": f"{d['w']} gewonnen ({d['g']} gespeeld)"}
                     for pid, d in sorted(sp.items(),
                                          key=lambda kv: (-kv[1]["w"], kv[1]["g"]))[:5]
                     if pid in namen_sp]
    spelers_klim = [{"naam": namen_sp[pid],
                     "url": url_for("speler_profiel", speler_id=pid),
                     "waarde": f"{'+' if d >= 0 else ''}{d:.0f} Aura"}
                    for pid, d in sorted(klim["speler"].items(), key=lambda kv: -kv[1])[:5]
                    if pid in namen_sp]
    teams_winst = [{"naam": namen_tm[tid],
                    "url": url_for("team_profiel", team_id=tid),
                    "waarde": f"{d['w']} gewonnen ({d['g']} gespeeld)"}
                   for tid, d in sorted(tm.items(), key=lambda kv: -kv[1]["w"])[:3]
                   if tid in namen_tm]
    teams_klim = [{"naam": namen_tm[tid],
                   "url": url_for("team_profiel", team_id=tid),
                   "waarde": f"{'+' if d >= 0 else ''}{d:.0f} Aura"}
                  for tid, d in sorted(klim["team"].items(), key=lambda kv: -kv[1])[:3]
                  if tid in namen_tm]

    return {"aantal": len(rijen), "spelers_winst": spelers_winst,
            "spelers_klim": spelers_klim, "teams_winst": teams_winst,
            "teams_klim": teams_klim}


def wis_avatar(bestandsnaam):
    """Verwijder een profielfoto van de schijf (als het er echt eentje is)."""
    if bestandsnaam and _AVATAR_RE.match(bestandsnaam):
        try:
            os.remove(os.path.join(UPLOAD_MAP, bestandsnaam))
        except OSError:
            pass


def bewaar_avatar(bestand, prefix, oud=None):
    """Sla een geüploade profielfoto op; geeft de bestandsnaam terug (of None)."""
    if not bestand or not bestand.filename or "." not in bestand.filename:
        return None
    ext = bestand.filename.rsplit(".", 1)[-1].lower()
    if ext not in _AVATAR_EXT:
        return None
    # Grootte zelf nakijken: de algemene uploadlimiet staat hoog voor back-ups.
    bestand.stream.seek(0, os.SEEK_END)
    if bestand.stream.tell() > MAX_AVATAR:
        bestand.stream.seek(0)
        return None
    bestand.stream.seek(0)
    os.makedirs(UPLOAD_MAP, exist_ok=True)
    naam = f"avatar_{prefix}_{secrets.token_hex(4)}.{ext}"
    bestand.save(os.path.join(UPLOAD_MAP, naam))
    if oud and _AVATAR_RE.match(oud):
        try:
            os.remove(os.path.join(UPLOAD_MAP, oud))
        except OSError:
            pass
    return naam


# ---------------------------------------------------------------- filters --

@app.template_filter("elo")
def filter_elo(waarde):
    return f"{waarde:.0f}"


@app.template_filter("kracht")
def filter_kracht(waarde):
    """Toernooikracht: de Aura die je vanavond won of verloor, mét teken.

    Twee cijfers na de komma: dit staat er enkel als het cijfer de volgorde
    bepaalt, en dan mogen twee ploegen die net niet gelijk staan er niet gelijk
    uitzien. De `or 0.0` houdt een lelijke "-0,00" weg.
    """
    afgerond = round(waarde or 0.0, 2) or 0.0
    return f"{afgerond:+.2f}".replace(".", ",")


@app.template_filter("datum")
def filter_datum(iso):
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso
    return f"{d.day} {MAANDEN[d.month - 1]} {d.year}, {d:%H:%M}"


@app.template_filter("uur")
def filter_uur(iso):
    """Enkel het uur — genoeg voor de balk met je eerstvolgende wedstrijd."""
    if not iso:
        return "—"
    try:
        return f"{datetime.fromisoformat(iso):%H:%M}"
    except (ValueError, TypeError):
        return iso


@app.template_filter("dag")
def filter_dag(iso):
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return iso
    return f"{d.day} {MAANDEN[d.month - 1]} {d.year}"


@app.template_filter("maandlabel")
def filter_maandlabel(iso):
    try:
        d = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return ""
    return f"{MAANDEN[d.month - 1].capitalize()} {d.year}"


@app.template_filter("fase")
def filter_fase(game):
    """Leesbaar label van de fase van een wedstrijd."""
    try:
        return fase_omschrijving(game["fase"], game["ronde"])
    except (TypeError, KeyError, IndexError):
        return "Wedstrijd"


# ------------------------------------------------------- league aan of uit --
#
# De site kan draaien met enkel het toernooigedeelte. Eén vinkje bij
# Organisatie → Instellingen zet het leaguegedeelte (klassement, wedstrijden,
# statistieken, seizoenen) aan of uit voor de spelers. Organisatoren houden
# altijd toegang, zodat je alles rustig kan klaarzetten voor je opengaat.

def league_zichtbaar():
    """Mag de league getoond worden aan gewone bezoekers en spelers?"""
    return instelling(get_db(), "league_actief", "0") == "1"


def league_toegankelijk():
    """Mag ík de leaguepagina's zien? Organisatoren ook als de league uit staat."""
    return league_zichtbaar() or is_organisator()


def league_vereist(f):
    """Leaguepagina: onzichtbaar voor spelers zolang het gedeelte uit staat."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not league_toegankelijk():
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper


# Waar hoort een detailpagina onder? Die bestemming is het vangnet van de
# terugknop: op een gsm is de knop van de browser lastig te vinden, en wie via
# een QR-code of een gedeelde link binnenkomt heeft helemaal geen geschiedenis.
# Met JavaScript gaat de knop gewoon één stap terug; zonder (of bij een directe
# link) belandt hij hier.
_TERUG_REGELS = [
    (re.compile(r"^/seizoen/\d+"), "seizoenen", "Alle seizoenen"),
    (re.compile(r"^/toernooi/\d+"), "toernooien", "Alle toernooien"),
    (re.compile(r"^/speler/\d+"), "index", "Klassement"),
    (re.compile(r"^/team/\d+"), "index", "Klassement"),
    (re.compile(r"^/profiel/dop$"), "mijn_profiel", "Je profiel"),
    (re.compile(r"^/claimen/\d+"), "claimen", "Kies je naam"),
    (re.compile(r"^/admin/seizoen/\d+"), "admin_seizoenen", "Alle seizoenen"),
    (re.compile(r"^/admin/toernooi/\d+"), "admin_toernooien", "Alle toernooien"),
]


# Hoe heet de pagina waar je vandaan komt? Kort en herkenbaar; de knop moet in
# één oogopslag duidelijk maken waar hij je heen brengt.
_PAGINA_NAMEN = [
    (re.compile(r"^/$"), "Klassement"),
    (re.compile(r"^/statistieken"), "Statistieken"),
    (re.compile(r"^/seizoenen"), "Alle seizoenen"),
    (re.compile(r"^/seizoen/\d+"), "Seizoen"),
    (re.compile(r"^/toernooien"), "Alle toernooien"),
    (re.compile(r"^/toernooi/\d+/loting"), "Loting"),
    (re.compile(r"^/toernooi/\d+"), "Toernooi"),
    (re.compile(r"^/speler/\d+"), "Spelersprofiel"),
    (re.compile(r"^/team/\d+"), "Teamprofiel"),
    (re.compile(r"^/claimen"), "Accounts opeisen"),
    (re.compile(r"^/inloggen"), "Inloggen"),
    (re.compile(r"^/registreren"), "Registreren"),
    (re.compile(r"^/admin/seizoen/\d+"), "Seizoen beheren"),
    (re.compile(r"^/admin/seizoenen"), "Alle seizoenen"),
    (re.compile(r"^/admin/toernooi/\d+"), "Toernooi beheren"),
    (re.compile(r"^/admin/toernooien"), "Alle toernooien"),
    (re.compile(r"^/admin/spelers"), "Spelers & teams"),
    (re.compile(r"^/admin/klassement"), "Rangen & statistieken"),
    (re.compile(r"^/admin/instellingen"), "Instellingen"),
    (re.compile(r"^/admin"), "Organisatie"),
]


def _vorige_pagina(huidig_pad):
    """Het pad van de vorige pagina op deze site, of None.

    Enkel een verwijzer van onze eigen site telt, en nooit de pagina zelf: na
    het opslaan van een formulier wijst de verwijzer namelijk naar de pagina
    waar je al staat.
    """
    verwijzer = request.referrer if request else None
    if not verwijzer:
        return None
    stuk = urlparse(verwijzer)
    if stuk.netloc and stuk.netloc != urlparse(request.host_url).netloc:
        return None                       # van een andere site: negeren
    pad = stuk.path + (f"?{stuk.query}" if stuk.query else "")
    if not pad.startswith("/") or pad.startswith("//"):
        return None
    return None if stuk.path == huidig_pad else pad


def _terugknop(pad):
    """De terugknop voor deze pagina, of None op een hoofdpagina.

    De knop brengt je naar de pagina waar je vandaan kwam, en zegt dat er ook
    bij. Kwam je hier rechtstreeks binnen (QR-code, gedeelde link, nieuw
    tabblad), dan valt hij terug op de overzichtspagina waar deze pagina onder
    hoort — anders zou de knop nergens heen kunnen.
    """
    for patroon, endpoint, label in _TERUG_REGELS:
        if patroon.match(pad):
            vorige = _vorige_pagina(pad)
            if vorige:
                for herken, naam in _PAGINA_NAMEN:
                    if herken.match(vorige):
                        return {"url": vorige, "label": naam}
                return {"url": vorige, "label": "Vorige pagina"}
            try:
                return {"url": url_for(endpoint), "label": label}
            except Exception:
                return None
    return None


@app.context_processor
def extra_context():
    naam = "logo.png" if os.path.exists(os.path.join(STATIC_MAP, "logo.png")) else "logo.svg"
    pad = request.path if request else "/"
    # Drie delen: Home (globaal klassement en statistieken), League (seizoenen)
    # en Toernooi. Een pagina die bij géén van drieën hoort — een profiel,
    # inloggen, het organisatiepaneel — laat alle drie de knoppen dof en toont
    # geen subnavigatie: anders lijkt het alsof je nog in dat deel zit.
    if pad.startswith("/toernooi"):
        sectie = "toernooi"
    elif pad.startswith("/seizoen"):
        sectie = "liga"
    elif pad == "/" or pad.startswith("/statistieken"):
        sectie = "home"
    else:
        sectie = None
    # De balk met je eerstvolgende wedstrijd: enkel voor wie ingelogd is, en niet
    # bij statische bestanden (die hoeven geen databasevraag).
    nu_spelen = None
    if session.get("speler_id") and request.endpoint not in (
            None, "statisch", "media", "service_worker", "manifest",
            "dop_speler_svg", "dop_team_svg"):
        try:
            nu_spelen = actieve_wedstrijd(get_db(), session["speler_id"])
        except Exception:
            nu_spelen = None            # nooit een pagina laten crashen hierdoor
    te_bevestigen = None
    if session.get("speler_id") and request.endpoint not in (
            None, "statisch", "media", "service_worker", "manifest",
            "dop_speler_svg", "dop_team_svg"):
        try:
            te_bevestigen = _te_bevestigen(get_db(), session["speler_id"])
        except Exception:
            te_bevestigen = None

    return {"logo_bestand": naam, "sectie": sectie, "terug": _terugknop(pad),
            "league_zichtbaar": league_zichtbaar(),
            "league_toegankelijk": league_toegankelijk(),
            "claim_venster_open": claim_open(),
            "nu_spelen": nu_spelen,
            "te_bevestigen": te_bevestigen,
            # Het grote scherm na het melden van een uitslag: één keer tonen.
            "viering": session.pop("viering", None) if request.endpoint not in (
                None, "statisch", "media", "service_worker", "manifest") else None}


def seizoen_status(s):
    vandaag = date.today().isoformat()
    if s["end_date"] < vandaag:
        return "afgelopen"
    if s["start_date"] > vandaag:
        return "gepland"
    return "bezig"


# ----------------------------------------------------------------- static --

@app.route("/static/<path:bestand>")
def statisch(bestand):
    if os.path.basename(bestand) not in _TOEGELATEN_STATIC:
        abort(404)
    antwoord = send_from_directory(STATIC_MAP, os.path.basename(bestand))
    # Met een versiecode in het adres (?v=...) verandert de inhoud nooit meer:
    # de gsm mag het bestand dan een jaar bijhouden zonder telkens te vragen.
    # Minder verzoeken = minder kans dat de stijl op een slechte verbinding
    # wegvalt. Een nieuwe versie krijgt vanzelf een nieuw adres.
    if request.args.get("v"):
        antwoord.cache_control.no_cache = None
        antwoord.cache_control.public = True
        antwoord.cache_control.max_age = 365 * 24 * 3600
        antwoord.cache_control.immutable = True
    return antwoord


@app.url_defaults
def _versie_in_statische_adressen(endpoint, waarden):
    """Elk adres naar een vast bestand krijgt de versiecode erbij."""
    if endpoint == "statisch" and "v" not in waarden:
        waarden["v"] = _static_versie()


def _static_versie():
    """Een korte code die verandert zodra style.css of app.js wijzigt.

    De service worker bewaart die vaste bestanden en haalt ze daarna uit zijn
    eigen cache. Zonder deze code zou een bezoeker na een update nog altijd de
    óude stijl zien: de cache wordt namelijk pas opgeruimd als de naam ervan
    verandert. Door de inhoud van de bestanden te hashen gebeurt dat vanzelf,
    en hoeven we bij elke aanpassing niets handmatig op te hogen.
    """
    # Onthouden zolang geen enkel bestand wijzigde: dit wordt bij elke pagina
    # gevraagd en mag dus niet telkens alle bestanden inlezen.
    tijden = tuple(_mtime(os.path.join(STATIC_MAP, n)) for n in sorted(_TOEGELATEN_STATIC))
    if _VERSIE_GEHEUGEN.get("tijden") == tijden:
        return _VERSIE_GEHEUGEN["code"]
    stempel = hashlib.sha1()
    for naam in sorted(_TOEGELATEN_STATIC):
        pad = os.path.join(STATIC_MAP, naam)
        try:
            with open(pad, "rb") as f:
                stempel.update(f.read())
        except OSError:
            continue
    _VERSIE_GEHEUGEN.update(tijden=tijden, code=stempel.hexdigest()[:12])
    return _VERSIE_GEHEUGEN["code"]


_VERSIE_GEHEUGEN = {}


def _mtime(pad):
    try:
        return os.path.getmtime(pad)
    except OSError:
        return None


@app.route("/sw.js")
def service_worker():
    """De service worker moet vanaf de hoofdmap geserveerd worden, anders mag hij
    enkel /static/ beheren en werkt de app niet offline."""
    with open(os.path.join(STATIC_MAP, "sw.js"), encoding="utf-8") as f:
        inhoud = f.read()
    # De cachenaam meegeven, zodat een update de oude bestanden weggooit.
    inhoud = inhoud.replace("__VERSIE__", _static_versie())
    antwoord = app.response_class(inhoud, mimetype="application/javascript")
    antwoord.headers["Service-Worker-Allowed"] = "/"
    antwoord.headers["Cache-Control"] = "no-cache"
    return antwoord


@app.route("/manifest.webmanifest")
def manifest():
    """Het manifest maakt de site installeerbaar als app op de gsm.

    We serveren het via een route (en niet als vast bestand), omdat de
    snelkoppelingen afhangen van de instellingen: staat de league uit, dan
    hebben snelkoppelingen naar seizoenen geen zin. De startpagina is altijd de
    thuispagina — die staat los van league én toernooi.
    """
    with open(os.path.join(STATIC_MAP, "manifest.webmanifest"), encoding="utf-8") as f:
        gegevens = json.load(f)
    gegevens["start_url"] = "/"
    gegevens["shortcuts"] = [s for s in gegevens.get("shortcuts", [])
                             if league_zichtbaar() or not s["url"].startswith("/seizoen")]
    antwoord = app.response_class(json.dumps(gegevens, ensure_ascii=False, indent=2),
                                  mimetype="application/manifest+json")
    antwoord.headers["Cache-Control"] = "no-cache"
    return antwoord


# ------------------------------------------------------------- kroonkurken --
#
# Elke speler heeft een kroonkurk (een "dop") als avatar, zelf te ontwerpen.
# Een team toont een dop die in het midden gesplitst is: links de helft van de
# ene speler, rechts die van de andere. De server tekent ze als SVG (dop.py);
# in de pagina's staan ze als gewone afbeelding met een versiecode in het
# adres, zodat de gsm ze voor altijd mag bijhouden tot het ontwerp verandert.

def _dop_gegevens():
    """Alle ontwerpen en teamsamenstellingen, één keer per verzoek opgehaald.
    Beide tabellen zijn klein; zo kost een lijst van 30 teams geen 30 vragen."""
    if "doppen" not in g:
        db = get_db()
        g.doppen = {r["id"]: r["dop"] or "" for r in db.execute("SELECT id, dop FROM players")}
        g.team_spelers = {r["id"]: (r["player1_id"], r["player2_id"])
                          for r in db.execute("SELECT id, player1_id, player2_id FROM teams")}
    return g.doppen, g.team_spelers


def _dop_versie(*teksten):
    return hashlib.sha1("|".join(teksten).encode()).hexdigest()[:10]


def dop_speler(speler_id):
    """Adres van de dop van een speler (voor in een <img>)."""
    doppen, _ = _dop_gegevens()
    return url_for("dop_speler_svg", speler_id=speler_id or 0,
                   v=_dop_versie(doppen.get(speler_id, "")))


def dop_team(team_id):
    """Adres van de gesplitste dop van een team."""
    doppen, teams = _dop_gegevens()
    p1, p2 = teams.get(team_id, (None, None))
    return url_for("dop_team_svg", team_id=team_id or 0,
                   v=_dop_versie(doppen.get(p1, ""), doppen.get(p2, "")))


def kroonkurk_hex(ontwerp):
    """De hoofdkleur van een ontwerp (voor de gloed achter de dop)."""
    return kroonkurk.HEX[kroonkurk.normaliseer(ontwerp)["kleur"]]


def dop_kleur(speler_id):
    doppen, _ = _dop_gegevens()
    return kroonkurk_hex(doppen.get(speler_id))


def dop_3d(speler_id=None, team_id=None):
    """De data-attributen voor de 3D-dop (static/dop3d.js): afbeelding, randkleur,
    randvorm en afwerking. Bij een team twee waarden: links en rechts."""
    doppen, teams = _dop_gegevens()
    if team_id is not None:
        p1, p2 = teams.get(team_id, (None, None))
        ontwerpen = [kroonkurk.normaliseer(doppen.get(p1)), kroonkurk.normaliseer(doppen.get(p2))]
        adres = dop_team(team_id)
    else:
        ontwerpen = [kroonkurk.normaliseer(doppen.get(speler_id))]
        adres = dop_speler(speler_id)
    rand = ",".join(kroonkurk.randkleur_hex(o) for o in ontwerpen)
    vorm = ",".join(o["rand"] for o in ontwerpen)
    afw = ",".join(o["afwerking"] for o in ontwerpen)
    return Markup(f'data-dop="{adres}" data-rand="{rand}" data-randvorm="{vorm}" data-afwerking="{afw}"')


app.jinja_env.globals.update(dop_speler=dop_speler, dop_team=dop_team, dop_3d=dop_3d,
                             kroonkurk_hex=kroonkurk_hex, dop_kleur=dop_kleur)


def _plaats_in_klassement(rijen, eigen_id, stats):
    """Plaats in het klassement (op Aura), onder wie al minstens één keer speelde.
    Geeft (plaats, aantal) of (None, aantal)."""
    lijst = sorted((r for r in rijen if stats.get(r["id"], {}).get("gespeeld")),
                   key=lambda r: -r["elo"])
    for i, r in enumerate(lijst):
        if r["id"] == eigen_id:
            return i + 1, len(lijst)
    return None, len(lijst)


def _svg_antwoord(inhoud):
    antwoord = app.response_class(inhoud, mimetype="image/svg+xml")
    if request.args.get("v"):
        antwoord.cache_control.public = True
        antwoord.cache_control.max_age = 365 * 24 * 3600
        antwoord.cache_control.immutable = True
    else:
        antwoord.headers["Cache-Control"] = "no-cache"
    # Een SVG mag nooit scripts uitvoeren, ook niet als iemand hem rechtstreeks opent.
    antwoord.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return antwoord


@app.route("/dop/s/<int:speler_id>.svg")
def dop_speler_svg(speler_id):
    doppen, _ = _dop_gegevens()
    return _svg_antwoord(kroonkurk.svg(doppen.get(speler_id)))


@app.route("/dop/t/<int:team_id>.svg")
def dop_team_svg(team_id):
    doppen, teams = _dop_gegevens()
    p1, p2 = teams.get(team_id, (None, None))
    return _svg_antwoord(kroonkurk.team_svg(doppen.get(p1), doppen.get(p2)))


@app.route("/profiel")
@speler_vereist
def mijn_profiel():
    """Je eigen profiel (bv. als vangnet voor de terugknop in de dop-editor)."""
    return redirect(url_for("speler_profiel", speler_id=session["speler_id"]))


@app.route("/profiel/dop", methods=["GET", "POST"])
@speler_vereist
def dop_ontwerpen():
    """De editor voor je eigen kroonkurk."""
    db = get_db()
    speler_id = session["speler_id"]
    speler = db.execute(f"SELECT id, {WEERGAVE} AS naam, dop FROM players p WHERE id = ?",
                        (speler_id,)).fetchone()
    if request.method == "POST":
        try:
            ontwerp = json.loads(request.form.get("dop") or "{}")
        except ValueError:
            ontwerp = {}
        # De editor bewaart elke wijziging meteen (zonder knop), dus geen
        # melding of doorverwijzing: enkel een kort antwoord.
        db.execute("UPDATE players SET dop = ? WHERE id = ?", (kroonkurk.als_tekst(ontwerp), speler_id))
        db.commit()
        return jsonify({"ok": True})
    bewaard = kroonkurk.bewaarde_lijst(db.execute(
        "SELECT dop_ontwerpen FROM players WHERE id = ?", (speler_id,)).fetchone()[0])
    return render_template("dop.html", speler=speler, ontwerp=kroonkurk.normaliseer(speler["dop"]),
                           basis=kroonkurk.BASIS, bewaard=bewaard, max_bewaard=kroonkurk.MAX_BEWAARD)


@app.route("/profiel/dop/ontwerpen", methods=["POST"])
@speler_vereist
def dop_ontwerpen_bewaren():
    """De (hoogstens drie) bewaarde ontwerpen bijwerken. De editor stuurt telkens
    de volledige lijst; we bewaren ze meteen, los van de knop "Bewaren"."""
    try:
        lijst = json.loads(request.form.get("ontwerpen") or "[]")
    except ValueError:
        return jsonify({"ok": False}), 400
    lijst = kroonkurk.bewaarde_lijst(lijst)
    db = get_db()
    db.execute("UPDATE players SET dop_ontwerpen = ? WHERE id = ?",
               (json.dumps(lijst, separators=(",", ":"), sort_keys=True), session["speler_id"]))
    db.commit()
    return jsonify({"ok": True, "ontwerpen": lijst})


@app.route("/profiel/dop/kies/<int:plek>", methods=["POST"])
@speler_vereist
def dop_kiezen(plek):
    """Met één tik op je profiel wisselen naar een van je bewaarde ontwerpen."""
    db = get_db()
    speler_id = session["speler_id"]
    bewaard = kroonkurk.bewaarde_lijst(db.execute(
        "SELECT dop_ontwerpen FROM players WHERE id = ?", (speler_id,)).fetchone()[0])
    if 0 <= plek < len(bewaard):
        db.execute("UPDATE players SET dop = ? WHERE id = ?", (kroonkurk.als_tekst(bewaard[plek]), speler_id))
        db.commit()
    return redirect(url_for("speler_profiel", speler_id=speler_id))


@app.route("/media/<bestand>")
def media(bestand):
    if not _AVATAR_RE.match(bestand):
        abort(404)
    return send_from_directory(UPLOAD_MAP, bestand)


# --------------------------------------------------------- publieke pagina's

@app.route("/")
def index():
    """Thuispagina: het globale klassement over álles wat ooit gespeeld is.

    Dit staat los van league én toernooi — de permanente Aura telt alle
    wedstrijden mee — en is dus ook zichtbaar wanneer het leaguegedeelte
    verborgen is.
    """
    db = get_db()
    sp_stats, tm_stats = speel_statistieken(db)
    rangen = alle_rangen(db)

    spelers = []
    for p in db.execute(f"SELECT p.*, {WEERGAVE} AS naam FROM players p "
                        "WHERE p.active = 1 ORDER BY p.elo DESC, naam"):
        s = sp_stats.get(p["id"], {"gespeeld": 0, "winst": 0})
        spelers.append({
            "id": p["id"], "naam": p["naam"], "elo": p["elo"], "avatar": p["avatar"],
            "gespeeld": s["gespeeld"], "winst": s["winst"],
            "verlies": s["gespeeld"] - s["winst"],
            "pct": (100.0 * s["winst"] / s["gespeeld"]) if s["gespeeld"] else None,
            "rang": rang_voor(p["elo"], rangen),
        })

    teams = []
    for t in db.execute("SELECT * FROM teams WHERE status = 'actief' ORDER BY elo DESC"):
        s = tm_stats.get(t["id"])
        if not s:
            continue  # enkel teams die al effectief samen gespeeld hebben
        teams.append({
            "id": t["id"], "naam": t["name"], "elo": t["elo"], "avatar": t["avatar"],
            "gespeeld": s["gespeeld"], "winst": s["winst"],
            "verlies": s["gespeeld"] - s["winst"],
            "pct": 100.0 * s["winst"] / s["gespeeld"],
        })
    teams.sort(key=lambda x: -x["elo"])

    aantal_gespeeld = db.execute(
        "SELECT COUNT(*) AS n FROM games WHERE status = 'gespeeld' "
        "AND fase != 'shootout'").fetchone()["n"]

    # Is er een toernooi bezig? Dat is op zo'n avond het eerste wat men zoekt.
    lopend = db.execute("""
        SELECT * FROM tournaments WHERE status IN ('bracket', 'knockout')
        ORDER BY date DESC LIMIT 1
    """).fetchone()
    if lopend is None:
        lopend = db.execute("""
            SELECT * FROM tournaments WHERE status = 'opzet' AND date >= ?
            ORDER BY date LIMIT 1
        """, (date.today().isoformat(),)).fetchone()

    # Is er vandaag een speeldag? Dan hoort die bovenaan te staan met een knop
    # erbij: op zo'n avond wil je niet drie keer doorklikken om te kunnen spelen.
    speeldag_nu = klaar = zoekt = speelt = None
    if league_toegankelijk():
        speeldag_nu, is_vandaag = liga_motor.speeldag_vandaag(db)
        if speeldag_nu is not None:
            klaar, zoekt, speelt, _ = liga_motor.mijn_speeldagstatus(
                db, speeldag_nu["id"], speeldag_nu["seizoen_id"],
                mijn_teams(db, session.get("speler_id")))
            speeldag_nu = dict(speeldag_nu, vandaag=is_vandaag)

    return render_template("index.html", spelers=spelers, teams=teams,
                           aantal_spelers=len(spelers), aantal_teams=len(teams),
                           speeldag_nu=speeldag_nu, sd_klaar=klaar,
                           sd_zoekt=zoekt, sd_speelt=speelt,
                           aantal_gespeeld=aantal_gespeeld, lopend=lopend,
                           lopend_label=(TOERNOOI_STATUS.get(lopend["status"], ("", ""))
                                         if lopend else None))


@app.route("/statistieken")
def statistieken():
    """Records en statistiekenborden over álle wedstrijden — league én toernooi."""
    db = get_db()
    types = db.execute("SELECT * FROM stat_types WHERE active = 1 ORDER BY name").fetchall()

    borden = []
    for st in types:
        rijen = db.execute(f"""
            SELECT p.id AS speler_id, {WEERGAVE} AS naam, SUM(gs.value) AS totaal,
                   COUNT(DISTINCT gs.game_id) AS n
            FROM game_stats gs
            JOIN players p ON p.id = gs.player_id
            WHERE gs.stat_type_id = ?
            GROUP BY gs.player_id
        """, (st["id"],)).fetchall()
        totaal = sorted(rijen, key=lambda r: -r["totaal"])[:10]
        gemiddeld = sorted(rijen, key=lambda r: -(r["totaal"] / r["n"]))[:10]
        borden.append({"type": st, "totaal": totaal, "gemiddeld": gemiddeld})

    return render_template("statistieken.html", borden=borden,
                           records=liga_records(db), verdeling=rangverdeling(db))


@app.route("/seizoenen")
@league_vereist
def seizoenen():
    db = get_db()
    lijst = []
    for s in db.execute("SELECT * FROM seasons ORDER BY start_date DESC"):
        speeldagen = db.execute("SELECT COUNT(*) AS n FROM matchdays "
                                "WHERE season_id = ?", (s["id"],)).fetchone()["n"]
        gespeeld = db.execute("""
            SELECT COUNT(*) AS n FROM games g
            JOIN matchdays md ON md.id = g.matchday_id
            WHERE md.season_id = ? AND g.status = 'gespeeld'
        """, (s["id"],)).fetchone()["n"]
        stats = seizoen_stats(db, s["id"]) if gespeeld else None
        status = seizoen_status(s)
        lijst.append({"seizoen": s, "status": status,
                      "speeldagen": speeldagen, "gespeeld": gespeeld,
                      # Inschrijven kan zolang het seizoen niet afgelopen is.
                      "inschrijving": (inschrijfstatus(db, s["id"], session.get("speler_id"))
                                       if status != "afgelopen" else []),
                      "teams": len(liga_motor.ingeschreven(db, s["id"])),
                      "topklimmer": (stats["spelers_klim"][0] if stats and
                                     stats["spelers_klim"] else None)})
    vandaag_sd, is_vandaag = liga_motor.speeldag_vandaag(db)
    return render_template("seizoenen.html", lijst=lijst, start_elo=int(START_ELO),
                           vandaag_sd=vandaag_sd, sd_is_vandaag=is_vandaag)


@app.route("/seizoen/<int:seizoen_id>")
@league_vereist
def seizoen_detail(seizoen_id):
    db = get_db()
    seizoen = db.execute("SELECT * FROM seasons WHERE id = ?", (seizoen_id,)).fetchone()
    if not seizoen:
        abort(404)
    namen = teamnamen(db)

    speeldagen = []
    gepland = []
    for md in db.execute("SELECT * FROM matchdays WHERE season_id = ? ORDER BY date",
                         (seizoen_id,)):
        games = db.execute("SELECT * FROM games WHERE matchday_id = ? "
                           "ORDER BY scheduled_at", (md["id"],)).fetchall()
        speeldagen.append({"speeldag": md, "games": games})
        gepland.extend(g for g in games if g["status"] == "gepland")
    gepland.sort(key=lambda g: g["scheduled_at"] or "")

    # Hoeveel Aura leverde elke wedstrijd op, en welke statistieken zijn er
    # gemeld? Dat stond vroeger op een aparte wedstrijdpagina; het hoort thuis
    # bij het seizoen waarin die wedstrijden gespeeld zijn.
    deltas = {}
    for r in db.execute("SELECT game_id, entity_id, elo_voor, elo_na FROM rating_history "
                        "WHERE entity_type = 'team' AND scope = 'permanent'"):
        deltas[(r["game_id"], r["entity_id"])] = r["elo_na"] - r["elo_voor"]

    stats_per_game = defaultdict(list)
    for r in db.execute(f"""
        SELECT gs.game_id, {WEERGAVE} AS speler, st.name AS stat, st.unit, gs.value
        FROM game_stats gs
        JOIN players p ON p.id = gs.player_id
        JOIN stat_types st ON st.id = gs.stat_type_id
        JOIN games g ON g.id = gs.game_id
        JOIN matchdays md ON md.id = g.matchday_id
        WHERE md.season_id = ?
        ORDER BY st.name, speler
    """, (seizoen_id,)):
        stats_per_game[r["game_id"]].append(r)

    sp_klassement, tm_klassement = seizoen_klassement(db, seizoen_id)

    status = seizoen_status(seizoen)
    vandaag_sd, is_vandaag = liga_motor.speeldag_vandaag(db)
    if vandaag_sd is not None and vandaag_sd["seizoen_id"] != seizoen_id:
        vandaag_sd = None
    return render_template("seizoen.html", seizoen=seizoen, huidig_seizoen=seizoen,
                           vandaag_sd=vandaag_sd, sd_is_vandaag=is_vandaag,
                           inschrijving=(inschrijfstatus(db, seizoen_id,
                                                         session.get("speler_id"))
                                         if status != "afgelopen" else []),
                           ingeschreven_teams=liga_motor.ingeschreven(db, seizoen_id),
                           status=status, speeldagen=speeldagen,
                           gepland=gepland, deltas=deltas,
                           stats_per_game=stats_per_game,
                           namen=namen, stats=seizoen_stats(db, seizoen_id),
                           sp_klassement=sp_klassement, tm_klassement=tm_klassement)


# ---------------------------------------------------------------- speeldag --

def mijn_teams(db, speler_id):
    """De actieve teams waar deze speler in zit."""
    if not speler_id:
        return []
    return db.execute("SELECT * FROM teams WHERE status = 'actief' AND "
                      "(player1_id = ? OR player2_id = ?) ORDER BY name",
                      (speler_id, speler_id)).fetchall()


def inschrijfstatus(db, seizoen_id, speler_id):
    """Per eigen team: doet het mee aan dit seizoen, en mag het er nog uit?

    Wordt op elke pagina getoond waar een seizoen voorkomt, zodat inschrijven
    nooit drie klikken diep zit.
    """
    if not speler_id:
        return []
    ingeschreven = {t["id"] for t in liga_motor.ingeschreven(db, seizoen_id)}
    uit = []
    for t in mijn_teams(db, speler_id):
        gespeeld = db.execute("""
            SELECT COUNT(*) AS n FROM games g
            JOIN matchdays md ON md.id = g.matchday_id
            WHERE md.season_id = ? AND (g.team1_id = ? OR g.team2_id = ?)
        """, (seizoen_id, t["id"], t["id"])).fetchone()["n"]
        uit.append({"team": t, "ingeschreven": t["id"] in ingeschreven,
                    "mag_uit": t["id"] in ingeschreven and not gespeeld})
    return uit


def _mijn_team_of_fout(db, team_id):
    """Mag ik namens dit team handelen? Geeft (team, foutmelding)."""
    team = db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if not team:
        return None, "Dat team bestaat niet."
    if is_organisator():
        return team, None                 # de organisatie mag bijspringen
    speler_id = session.get("speler_id")
    if speler_id not in (team["player1_id"], team["player2_id"]):
        return None, "Je speelt zelf niet in dat team."
    return team, None


@app.route("/speeldag/<int:speeldag_id>")
@league_vereist
def speeldag(speeldag_id):
    """De speeldag zelf: inschrijven, een tegenstander zoeken, uitdagen."""
    db = get_db()
    sd = db.execute("""
        SELECT md.*, s.name AS seizoen, s.id AS seizoen_id
        FROM matchdays md JOIN seasons s ON s.id = md.season_id WHERE md.id = ?
    """, (speeldag_id,)).fetchone()
    if not sd:
        abort(404)

    # Elke keer dat iemand kijkt, controleren we of er gekoppeld kan worden.
    # Er draait geen achtergrondproces; dit ís de motor van de wachtrij.
    for melding in liga_motor.koppel_wachtrij(db, speeldag_id):
        flash(melding, "ok")

    ik = session.get("speler_id")
    eigen = mijn_teams(db, ik)
    ingeschreven = {t["id"] for t in liga_motor.ingeschreven(db, sd["seizoen_id"])}
    in_de_rij = {r["team_id"] for r in liga_motor.wachtrij(db, speeldag_id)}
    aan_de_gang = liga_motor.bezig(db, speeldag_id)

    mijn_status = []
    for t in eigen:
        mijn_status.append({
            "team": t,
            "ingeschreven": t["id"] in ingeschreven,
            "zoekt": t["id"] in in_de_rij,
            "speelt": t["id"] in aan_de_gang,
            "uitdagingen": liga_motor.uitdagingen(db, speeldag_id, t["id"]),
        })

    # Wie kan je uitdagen? Ingeschreven teams die niet spelen en niet van jou zijn.
    eigen_ids = {t["id"] for t in eigen}
    tegenstanders = [t for t in liga_motor.ingeschreven(db, sd["seizoen_id"])
                     if t["id"] not in eigen_ids and t["id"] not in aan_de_gang]

    games = db.execute("SELECT * FROM games WHERE matchday_id = ? "
                       "ORDER BY status, scheduled_at DESC, id DESC",
                       (speeldag_id,)).fetchall()
    return render_template("speeldag.html", sd=sd, huidig_seizoen=sd,
                           mijn_status=mijn_status, tegenstanders=tegenstanders,
                           games=games, namen=teamnamen(db),
                           # De wachtrij zelf is enkel voor de organisatie.
                           rij=(liga_motor.wachtrij(db, speeldag_id)
                                if is_organisator() else None),
                           status=(liga_motor.wachtrij_status(db, speeldag_id)
                                   if is_organisator() else None))


@app.route("/seizoen/<int:seizoen_id>/inschrijven", methods=["POST"])
@speler_vereist
@league_vereist
def seizoen_inschrijven(seizoen_id):
    db = get_db()
    team, fout = _mijn_team_of_fout(db, int(request.form.get("team") or 0))
    if fout:
        flash(fout, "fout")
    else:
        ok, boodschap = liga_motor.inschrijven(db, seizoen_id, team["id"])
        flash(boodschap, "ok" if ok else "fout")
    return redirect(request.form.get("terug") or
                    url_for("seizoen_detail", seizoen_id=seizoen_id))


@app.route("/seizoen/<int:seizoen_id>/uitschrijven", methods=["POST"])
@speler_vereist
@league_vereist
def seizoen_uitschrijven(seizoen_id):
    db = get_db()
    team, fout = _mijn_team_of_fout(db, int(request.form.get("team") or 0))
    if fout:
        flash(fout, "fout")
    else:
        ok, boodschap = liga_motor.uitschrijven(db, seizoen_id, team["id"])
        flash(boodschap, "ok" if ok else "fout")
    return redirect(request.form.get("terug") or
                    url_for("seizoen_detail", seizoen_id=seizoen_id))


@app.route("/speeldag/<int:speeldag_id>/zoeken", methods=["POST"])
@speler_vereist
@league_vereist
def speeldag_zoeken(speeldag_id):
    """Een tegenstander zoeken (of daarmee stoppen)."""
    db = get_db()
    team, fout = _mijn_team_of_fout(db, int(request.form.get("team") or 0))
    if fout:
        flash(fout, "fout")
    elif request.form.get("stop"):
        ok, boodschap = liga_motor.verlaten(db, speeldag_id, team["id"])
        flash(boodschap, "ok" if ok else "fout")
    else:
        ok, boodschap = liga_motor.aansluiten(db, speeldag_id, team["id"])
        flash(boodschap, "ok" if ok else "fout")
        if ok:
            for melding in liga_motor.koppel_wachtrij(db, speeldag_id):
                flash(melding, "ok")
    return redirect(url_for("speeldag", speeldag_id=speeldag_id))


@app.route("/speeldag/<int:speeldag_id>/uitdagen", methods=["POST"])
@speler_vereist
@league_vereist
def speeldag_uitdagen(speeldag_id):
    db = get_db()
    team, fout = _mijn_team_of_fout(db, int(request.form.get("team") or 0))
    if fout:
        flash(fout, "fout")
    else:
        ok, boodschap = liga_motor.uitdagen(db, speeldag_id, team["id"],
                                            int(request.form.get("tegen") or 0))
        flash(boodschap, "ok" if ok else "fout")
    return redirect(url_for("speeldag", speeldag_id=speeldag_id))


@app.route("/uitdaging/<int:uitdaging_id>", methods=["POST"])
@speler_vereist
@league_vereist
def uitdaging_antwoord(uitdaging_id):
    db = get_db()
    c = db.execute("SELECT * FROM challenges WHERE id = ?", (uitdaging_id,)).fetchone()
    if not c:
        flash("Die uitdaging bestaat niet meer.", "fout")
        return redirect(url_for("index"))
    aanvaard = request.form.get("antwoord") == "ja"
    # Aanvaarden of weigeren doet de uitgedaagde ploeg; intrekken de uitdager.
    kant = c["naar_team_id"] if request.form.get("antwoord") else c["van_team_id"]
    team, fout = _mijn_team_of_fout(db, kant)
    if fout:
        flash(fout, "fout")
    elif not request.form.get("antwoord"):
        db.execute("UPDATE challenges SET status = 'ingetrokken' WHERE id = ?",
                   (uitdaging_id,))
        db.commit()
        flash("Uitdaging ingetrokken.", "ok")
    else:
        ok, boodschap = liga_motor.beantwoord_uitdaging(db, uitdaging_id, aanvaard)
        flash(boodschap, "ok" if ok else "fout")
    return redirect(url_for("speeldag", speeldag_id=c["matchday_id"]))


# --------------------------------------------------------------- profielen --

@app.route("/speler/<int:speler_id>")
def speler_profiel(speler_id):
    db = get_db()
    speler = db.execute(f"SELECT p.*, {WEERGAVE} AS naam FROM players p "
                        "WHERE p.id = ?", (speler_id,)).fetchone()
    if not speler:
        abort(404)

    sp_stats, tm_stats = speel_statistieken(db)
    s = sp_stats.get(speler_id, {"gespeeld": 0, "winst": 0})
    rangen = alle_rangen(db)
    namen = teamnamen(db)
    namen_sp = weergavenamen(db)

    is_eigen = (session.get("speler_id") == speler_id)

    # Teams van deze speler: de actieve en de onvolledige (die hij oprichtte).
    # Voor de speler zelf ook de uitnodigingen die hij kreeg.
    teams = []
    onvolledig = []
    open_uitnodiging = {r["team_id"]: r["player_id"] for r in db.execute(
        "SELECT team_id, player_id FROM team_invitations")}
    for t in db.execute("SELECT * FROM teams WHERE player1_id = ? OR player2_id = ? "
                        "ORDER BY elo DESC", (speler_id, speler_id)):
        partner_id = t["player2_id"] if t["player1_id"] == speler_id else t["player1_id"]
        info = {"id": t["id"], "naam": t["name"], "elo": t["elo"],
                "avatar": t["avatar"], "beschrijving": t["description"],
                "partner": namen_sp.get(partner_id, "?"), "partner_id": partner_id}
        if t["status"] == "actief":
            ts = tm_stats.get(t["id"], {"gespeeld": 0, "winst": 0})
            info["gespeeld"] = ts["gespeeld"]
            info["winst"] = ts["winst"]
            teams.append(info)
        else:
            genodigde = open_uitnodiging.get(t["id"])
            info["genodigde"] = namen_sp.get(genodigde) if genodigde else None
            onvolledig.append(info)
    uitnodigingen = []
    if is_eigen:
        for r in db.execute("SELECT t.* FROM team_invitations i JOIN teams t "
                            "ON t.id = i.team_id WHERE i.player_id = ? ORDER BY i.created_at",
                            (speler_id,)):
            uitnodigingen.append({"id": r["id"], "naam": r["name"],
                                  "beschrijving": r["description"],
                                  "partner": namen_sp.get(r["player1_id"], "?")})

    # Toernooien waarvoor deze speler (met een van zijn teams) al ingeschreven is.
    inschrijvingen = []
    for r in db.execute("""
        SELECT tn.id, tn.name, tn.date, tn.start_tijd, t.name AS team, t.id AS team_id,
               t.player1_id, t.player2_id
        FROM tournament_teams tt
        JOIN tournaments tn ON tn.id = tt.tournament_id
        JOIN teams t ON t.id = tt.team_id
        WHERE tn.status = 'opzet' AND (t.player1_id = ? OR t.player2_id = ?)
        ORDER BY tn.date
    """, (speler_id, speler_id)):
        partner_id = r["player2_id"] if r["player1_id"] == speler_id else r["player1_id"]
        wacht = toernooi_motor.wachtlijst(db, r["id"])
        inschrijvingen.append({"id": r["id"], "naam": r["name"], "datum": r["date"],
                               "start": r["start_tijd"], "team": r["team"],
                               "partner": namen_sp.get(partner_id, "?"),
                               "wachtlijst": (wacht.index(r["team_id"]) + 1)
                                             if r["team_id"] in wacht else None})

    eigen_stats = db.execute("""
        SELECT st.name, st.unit, SUM(gs.value) AS totaal,
               COUNT(DISTINCT gs.game_id) AS n
        FROM game_stats gs
        JOIN stat_types st ON st.id = gs.stat_type_id
        WHERE gs.player_id = ? AND st.active = 1
        GROUP BY st.id ORDER BY st.name
    """, (speler_id,)).fetchall()

    historiek = []
    for r in db.execute("""
        SELECT g.*, rh.elo_voor, rh.elo_na
        FROM games g
        JOIN teams t1 ON t1.id = g.team1_id
        JOIN teams t2 ON t2.id = g.team2_id
        LEFT JOIN rating_history rh ON rh.game_id = g.id
             AND rh.entity_type = 'speler' AND rh.entity_id = ? AND rh.scope = 'permanent'
        WHERE g.status = 'gespeeld'
          AND ? IN (t1.player1_id, t1.player2_id, t2.player1_id, t2.player2_id)
        ORDER BY g.played_at DESC, g.id DESC
    """, (speler_id, speler_id)):
        leden = db.execute("SELECT player1_id, player2_id FROM teams WHERE id = ?",
                           (r["team1_id"],)).fetchone()
        eigen_team = (r["team1_id"] if speler_id in (leden["player1_id"],
                                                     leden["player2_id"])
                      else r["team2_id"])
        tegen_team = r["team2_id"] if eigen_team == r["team1_id"] else r["team1_id"]
        historiek.append({
            "game": r, "eigen_team": eigen_team, "tegen_team": tegen_team,
            "gewonnen": r["winner_team_id"] == eigen_team,
            "delta": (r["elo_na"] - r["elo_voor"]) if r["elo_na"] is not None else None,
        })

    punten = elo_verloop(db, speler_id)
    # Staat de league uit, dan mag een speler ook geen leaguewedstrijden melden.
    meldingen = (speler_wedstrijd_meldingen(db, speler_id,
                                            alleen_toernooi=not league_toegankelijk())
                 if is_eigen else [])
    actieve_types = (db.execute("SELECT * FROM stat_types WHERE active = 1 "
                                "ORDER BY name").fetchall() if is_eigen else [])
    # Mogelijke partners voor een nieuw team.
    partners = (db.execute(f"""
        SELECT p.id, {WEERGAVE} AS naam FROM players p
        WHERE p.active = 1 AND p.id != ? ORDER BY naam
    """, (speler_id,)).fetchall() if is_eigen else [])

    # Palmares: per seizoen de eindplaats, per toernooi hoever hij geraakte.
    seizoen_ratings = seizoen_historiek(db, speler_id)
    toernooien_gespeeld = toernooi_historiek(db, speler_id)

    # Toernooicijfers (tellen wel mee voor de permanente Aura).
    tn = db.execute("""
        SELECT COUNT(*) AS n,
               SUM(CASE WHEN g.winner_team_id =
                        (CASE WHEN ? IN (t1.player1_id, t1.player2_id)
                              THEN g.team1_id ELSE g.team2_id END)
                   THEN 1 ELSE 0 END) AS w
        FROM games g
        JOIN teams t1 ON t1.id = g.team1_id
        JOIN teams t2 ON t2.id = g.team2_id
        WHERE g.status = 'gespeeld' AND g.tournament_id IS NOT NULL
          AND g.fase != 'shootout'
          AND ? IN (t1.player1_id, t1.player2_id, t2.player1_id, t2.player2_id)
    """, (speler_id, speler_id)).fetchone()
    toernooi_stats = {"gespeeld": tn["n"] or 0, "winst": tn["w"] or 0,
                      "toernooien": toernooien_gespeeld,
                      "titels": sum(1 for x in toernooien_gespeeld
                                    if x["klasse"] == "goud")}

    plaats, aantal = _plaats_in_klassement(
        db.execute("SELECT id, elo FROM players").fetchall(), speler_id, sp_stats)
    # Vorm: de laatste vijf uitslagen, de oudste links.
    vorm = [h["gewonnen"] for h in historiek[:5]][::-1]

    bewaarde_doppen = []
    if is_eigen:
        actief = kroonkurk.normaliseer(speler["dop"])
        for i, o in enumerate(kroonkurk.bewaarde_lijst(speler["dop_ontwerpen"])):
            bewaarde_doppen.append({"plek": i, "actief": kroonkurk.zelfde(o, actief),
                                    "svg": Markup(kroonkurk.svg(o, f"b{i}"))})

    return render_template("speler.html", speler=speler, stats=s, plaats=plaats,
                           bewaarde_doppen=bewaarde_doppen,
                           aantal_in_klassement=aantal, vorm=vorm,
                           seizoen_ratings=seizoen_ratings,
                           toernooien=toernooien_gespeeld,
                           titels=toernooi_stats["titels"],
                           toernooi_stats=toernooi_stats,
                           verlies=s["gespeeld"] - s["winst"],
                           pct=(100.0 * s["winst"] / s["gespeeld"]) if s["gespeeld"] else None,
                           rang=rang_voor(speler["elo"], rangen), teams=teams,
                           uitnodigingen=uitnodigingen, onvolledig=onvolledig,
                           inschrijvingen=inschrijvingen,
                           coupons=mijn_coupons(db, speler_id) if is_eigen else [],
                           eigen_stats=eigen_stats, historiek=historiek,
                           namen=namen, punten=punten, is_eigen=is_eigen,
                           meldingen=meldingen, actieve_types=actieve_types,
                           partners=partners)


@app.route("/team/<int:team_id>")
def team_profiel(team_id):
    db = get_db()
    team = db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if not team:
        abort(404)
    is_lid = session.get("speler_id") in (team["player1_id"], team["player2_id"])
    is_oprichter = session.get("speler_id") == team["player1_id"]
    uitnodiging = db.execute(f"""
        SELECT i.*, {WEERGAVE} AS naam FROM team_invitations i
        JOIN players p ON p.id = i.player_id WHERE i.team_id = ?
    """, (team_id,)).fetchone()
    kandidaten = []
    if is_oprichter and team["status"] == "onvolledig" and not uitnodiging:
        kandidaten = db.execute(f"""
            SELECT p.id, {WEERGAVE} AS naam FROM players p
            WHERE p.active = 1 AND p.id != ? ORDER BY naam
        """, (team["player1_id"],)).fetchall()

    _, tm_stats = speel_statistieken(db)
    s = tm_stats.get(team_id, {"gespeeld": 0, "winst": 0})
    namen = teamnamen(db)
    leden = db.execute(f"SELECT p.id, {WEERGAVE} AS name, p.elo, p.avatar "
                       "FROM players p WHERE p.id IN (?, ?) "
                       "ORDER BY CASE WHEN p.id = ? THEN 0 ELSE 1 END",
                       (team["player1_id"], team["player2_id"], team["player1_id"])).fetchall()

    historiek = []
    for r in db.execute("""
        SELECT g.*, rh.elo_voor, rh.elo_na
        FROM games g
        LEFT JOIN rating_history rh ON rh.game_id = g.id
             AND rh.entity_type = 'team' AND rh.entity_id = ? AND rh.scope = 'permanent'
        WHERE g.status = 'gespeeld' AND (g.team1_id = ? OR g.team2_id = ?)
        ORDER BY g.played_at DESC, g.id DESC
    """, (team_id, team_id, team_id)):
        tegen = r["team2_id"] if r["team1_id"] == team_id else r["team1_id"]
        historiek.append({
            "game": r, "tegen_team": tegen,
            "gewonnen": r["winner_team_id"] == team_id,
            "delta": (r["elo_na"] - r["elo_voor"]) if r["elo_na"] is not None else None,
        })

    heeft_games = db.execute("SELECT 1 FROM games WHERE team1_id = ? OR team2_id = ? "
                             "LIMIT 1", (team_id, team_id)).fetchone() is not None

    # Palmares van het team: eindplaats per seizoen en resultaat per toernooi.
    seizoen_ratings = seizoen_historiek(db, team_id, "team")
    toernooien_team = toernooi_historiek(db, team_id=team_id)
    titels = sum(1 for x in toernooien_team if x["klasse"] == "goud")

    plaats, aantal = _plaats_in_klassement(
        db.execute("SELECT id, elo FROM teams WHERE status = 'actief'").fetchall(), team_id, tm_stats)
    vorm = [h["gewonnen"] for h in historiek[:5]][::-1]

    return render_template("team.html", team=team, naam=team["name"], leden=leden,
                           plaats=plaats, aantal_in_klassement=aantal, vorm=vorm,
                           stats=s, verlies=s["gespeeld"] - s["winst"],
                           pct=(100.0 * s["winst"] / s["gespeeld"]) if s["gespeeld"] else None,
                           historiek=historiek, namen=namen, is_lid=is_lid,
                           is_oprichter=is_oprichter, uitnodiging=uitnodiging,
                           kandidaten=kandidaten, heeft_games=heeft_games,
                           punten=elo_verloop(db, team_id, "team"),
                           seizoen_ratings=seizoen_ratings,
                           toernooien=toernooien_team, titels=titels)


# ------------------------------------------------------------ spelersaccounts

def _volgende():
    """Waar moet je na het inloggen of registreren naartoe? (enkel deze site)"""
    v = request.args.get("volgende") or ""
    return v if v.startswith("/") and not v.startswith("//") else ""


@app.route("/registreren", methods=["GET", "POST"])
def registreren():
    db = get_db()
    # Het állereerste account wordt eigenaar: iemand moet de organisatie kunnen doen.
    eerste = db.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"] == 0
    volgende = _volgende()
    if request.method == "POST":
        naam = " ".join((request.form.get("naam") or "").split())
        bijnaam = (request.form.get("bijnaam") or "").strip()
        ww = request.form.get("wachtwoord") or ""
        # Twee keer hetzelfde wachtwoord, tegen tikfouten. Klopt het niet, dan
        # tonen we het formulier opnieuw (met je naam er al in).
        if ww != (request.form.get("herhaal") or ""):
            flash("De twee wachtwoorden zijn niet hetzelfde. Tik ze nog eens in.", "fout")
            return render_template("registreren.html", eerste=eerste, volgende=volgende,
                                   overname=None, ingevuld={"naam": naam, "bijnaam": bijnaam})
        bestaand = db.execute("SELECT * FROM players WHERE name = ? COLLATE NOCASE",
                              (naam,)).fetchone() if naam else None
        if (bestaand is not None and not bestaand["password_hash"]
                and bestaand["aangemaakt_door"] and len(ww) >= 6 and len(bijnaam) <= 30):
            # Een ploegmaat schreef je al in en maakte zo een account op jouw naam.
            if request.form.get("dat_ben_ik") == "ja":
                db.execute("UPDATE players SET password_hash = ?, nickname = ?, "
                           "aangemaakt_door = NULL WHERE id = ?",
                           (generate_password_hash(ww), bijnaam, bestaand["id"]))
                _log_claim(db, bestaand, "claim")
                db.commit()
                session["speler_id"] = bestaand["id"]
                session.permanent = True        # ingelogd blijven op dit toestel
                g.pop("ik", None)
                flash(f"Welkom bij Leberschuss Tonzent, {bijnaam or bestaand['name']}! "
                      f"Je account (spelersnummer #{bestaand['id']}) is nu van jou.", "ok")
                return redirect(volgende or url_for("speler_profiel", speler_id=bestaand["id"]))
            return render_template("registreren.html", eerste=eerste, volgende=volgende,
                                   overname=_overname_info(db, bestaand),
                                   ingevuld={"naam": bestaand["name"], "bijnaam": bijnaam})
        # Lijkt je naam sterk op een account dat een ploegmaat voor je klaarzette
        # (met een tikfout, bv.)? Dan vragen we eerst of jij dat bent.
        keuze = request.form.get("gelijkend") or ""
        if (bestaand is None and keuze != "nee" and 3 <= len(naam) <= 40
                and len(bijnaam) <= 30 and len(ww) >= 6):
            kandidaten = gelijkende_klaargezette_accounts(db, naam)
            gekozen = next((k for k in kandidaten if str(k["id"]) == keuze), None)
            if gekozen is not None:
                # Je eigen naam (zoals jij hem tikt) wint van die van je ploegmaat.
                db.execute("UPDATE players SET name = ?, password_hash = ?, nickname = ?, "
                           "aangemaakt_door = NULL WHERE id = ?",
                           (naam, generate_password_hash(ww), bijnaam, gekozen["id"]))
                # Stond de verkeerd getikte naam in een automatische teamnaam
                # ("Nele & Kobe Testman")? Dan verbeteren we die ook.
                for team in db.execute("SELECT id, name FROM teams WHERE player1_id = ? OR player2_id = ?",
                                       (gekozen["id"], gekozen["id"])).fetchall():
                    if gekozen["name"] in team["name"] and gekozen["name"] != naam:
                        nieuw = team["name"].replace(gekozen["name"], naam)[:40]
                        if not db.execute("SELECT 1 FROM teams WHERE name = ? COLLATE NOCASE AND id != ?",
                                          (nieuw, team["id"])).fetchone():
                            db.execute("UPDATE teams SET name = ? WHERE id = ?", (nieuw, team["id"]))
                _log_claim(db, gekozen, "claim")
                db.commit()
                session["speler_id"] = gekozen["id"]
                session.permanent = True        # ingelogd blijven op dit toestel
                g.pop("ik", None)
                flash(f"Welkom bij Leberschuss Tonzent, {bijnaam or naam}! Het account dat "
                      f"klaarstond (spelersnummer #{gekozen['id']}) is nu van jou.", "ok")
                return redirect(volgende or url_for("speler_profiel", speler_id=gekozen["id"]))
            if kandidaten:
                return render_template(
                    "registreren.html", eerste=eerste, volgende=volgende, overname=None,
                    gelijkend=[dict(id=k["id"], naam=k["name"], **_overname_info(db, k))
                               for k in kandidaten],
                    ingevuld={"naam": naam, "bijnaam": bijnaam})
        if len(naam) < 3 or len(naam) > 40:
            flash("Vul je echte voor- en achternaam in (3 tot 40 tekens).", "fout")
        elif len(bijnaam) > 30:
            flash("Hou je bijnaam korter dan 30 tekens.", "fout")
        elif len(ww) < 6:
            flash("Kies een wachtwoord van minstens 6 tekens.", "fout")
        else:
            try:
                nummer = vrij_spelernummer(db)
                db.execute("INSERT INTO players (id, name, nickname, password_hash, "
                           "role, elo) VALUES (?, ?, ?, ?, ?, ?)",
                           (nummer, naam, bijnaam, generate_password_hash(ww),
                            ROL_EIGENAAR if eerste else ROL_SPELER, START_ELO))
                db.commit()
                session["speler_id"] = nummer
                session.permanent = True        # ingelogd blijven op dit toestel
                flash(f"Welkom bij Leberschuss Tonzent, {bijnaam or naam}! Je "
                      f"spelersnummer is #{nummer} en je start op {START_ELO:.0f} Aura. "
                      "Maak of aanvaard een team om mee te spelen.", "ok")
                if eerste:
                    flash("Jij bent het eerste account en dus meteen de eigenaar van "
                          "deze site: jij kan anderen organisator maken via het "
                          "organisatiepaneel.", "ok")
                return redirect(volgende or url_for("speler_profiel", speler_id=nummer))
            except Exception:
                db.rollback()
                if bestaand is not None and not bestaand["password_hash"]:
                    flash("Er staat al een account op jouw naam klaar, aangemaakt door de "
                          "organisatie. Vraag hen om het opeisvenster te openen; dan kies "
                          "je er zelf een wachtwoord voor.", "fout")
                else:
                    flash("Er bestaat al een account met die naam. Ben jij dat? Log dan "
                          "in. Anders: zet er iets bij, bv. je tweede voornaam.", "fout")
    return render_template("registreren.html", eerste=eerste, volgende=volgende,
                           overname=None, ingevuld={})


def _naam_normaal(naam):
    """Kleine letters, zonder accenten, leestekens of dubbele spaties."""
    zonder = "".join(c for c in unicodedata.normalize("NFD", naam or "")
                     if unicodedata.category(c) != "Mn")
    return " ".join("".join(c if c.isalnum() else " " for c in zonder.lower()).split())


def _gelijkenis(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def naam_lijkt_op(a, b):
    """Is dit (waarschijnlijk) dezelfde naam, op een tikfout na?

    Vangt: hoofdletters en accenten (Celine/Céline), een letter te veel of te
    weinig (Testman/Testmans), aan elkaar of los (Vanmeensel/Van Meensel),
    voor- en achternaam omgewisseld, en een kleine fout in één van beide
    terwijl de andere klopt."""
    a, b = _naam_normaal(a), _naam_normaal(b)
    if not a or not b:
        return False
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return True
    ta, tb = a.split(), b.split()
    if sorted(ta) == sorted(tb):
        return True
    if _gelijkenis(a, b) >= 0.9:
        return True
    if len(ta) >= 2 and len(tb) >= 2:
        if ta[0] == tb[0] and _gelijkenis(" ".join(ta[1:]), " ".join(tb[1:])) >= 0.75:
            return True
        if ta[-1] == tb[-1] and _gelijkenis(ta[0], tb[0]) >= 0.75:
            return True
    return False


def gelijkende_klaargezette_accounts(db, naam, max_aantal=3):
    """Accounts die een ploegmaat bij het inschrijven klaarzette (nog zonder
    wachtwoord) en waarvan de naam sterk op deze naam lijkt."""
    norm = _naam_normaal(naam)
    kandidaten = [r for r in db.execute(
        "SELECT * FROM players WHERE password_hash IS NULL AND aangemaakt_door IS NOT NULL "
        "AND active = 1") if naam_lijkt_op(naam, r["name"])]
    kandidaten.sort(key=lambda r: -_gelijkenis(norm, _naam_normaal(r["name"])))
    return kandidaten[:max_aantal]


def _overname_info(db, speler):
    """Wie maakte dit account aan, en in welke teams/toernooien staat het al?"""
    maker = db.execute("SELECT * FROM players WHERE id = ?", (speler["aangemaakt_door"],)).fetchone()
    teams = []
    for t in db.execute("SELECT * FROM teams WHERE player1_id = ? OR player2_id = ?",
                        (speler["id"], speler["id"])):
        toernooien = [r["name"] for r in db.execute(
            "SELECT tn.name FROM tournament_teams tt JOIN tournaments tn ON tn.id = tt.tournament_id "
            "WHERE tt.team_id = ? ORDER BY tn.date", (t["id"],))]
        teams.append({"naam": t["name"], "toernooien": toernooien})
    return {"maker": weergave(maker) if maker else "een ploegmaat", "teams": teams}


@app.route("/inloggen", methods=["GET", "POST"])
def inloggen():
    if request.method == "POST":
        ingave = " ".join((request.form.get("naam") or "").split())
        ww = request.form.get("wachtwoord") or ""
        db = get_db()
        # Inloggen kan met je echte naam of met je spelersnummer (bv. 4821).
        sleutel = ingave.lstrip("#")
        speler = db.execute(
            "SELECT * FROM players WHERE name = ? COLLATE NOCASE OR id = ?",
            (ingave, int(sleutel) if sleutel.isdigit() else -1)).fetchone()
        if speler and speler["password_hash"] and check_password_hash(speler["password_hash"], ww):
            session["speler_id"] = speler["id"]
            session.permanent = True        # ingelogd blijven op dit toestel
            g.pop("ik", None)
            flash(f"Welkom terug, {weergave(speler)}!", "ok")
            volgende = _volgende()
            if volgende:
                return redirect(volgende)
            return redirect(url_for("speler_profiel", speler_id=speler["id"]))
        flash("Naam of wachtwoord klopt niet.", "fout")
    return render_template("inloggen.html")


# ------------------------------------------------------ account opeisen --
#
# De organisator maakt de spelers vooraf aan (uit de inschrijvingen), zonder
# wachtwoord. De speler zelf kiest er later één: hij zoekt zijn naam op /claimen
# en stelt een wachtwoord in. Dat venster staat enkel open wanneer de organisatie
# het openzet — in de praktijk terwijl ze erbij staan. Elke claim wordt gelogd,
# zodat een vergissing zichtbaar is en teruggedraaid kan worden.

def claim_open():
    return instelling(get_db(), "claim_open", "0") == "1"


def externe_url(endpoint, **kwargs):
    """Het volledige adres van een pagina, zoals een bezoeker het moet intypen.

    Flask leidt dat af uit het adres waarmee de pagina opgevraagd werd — dus uit
    het domein waarmee jij het organisatiepaneel opent. Draai je achter nginx,
    dan zorgt ACHTER_PROXY=1 ervoor dat https ook klopt.
    """
    return url_for(endpoint, _external=True, **kwargs)


def qr_svg(tekst, rand=3):
    """Een QR-code als inline SVG, schaalbaar via CSS (None als segno ontbreekt)."""
    try:
        import segno
    except ImportError:
        return None
    code = segno.make(tekst, error="m")
    breedte, hoogte = code.symbol_size(scale=1, border=rand)
    svg = code.svg_inline(scale=1, border=rand, dark="#0f172a", light=None)
    # De vaste afmetingen weg, een viewBox erin: zo vult de code zijn kader.
    svg = re.sub(r'\s(width|height)="[^"]*"', "", svg, count=2)
    return Markup(svg.replace(
        "<svg ", f'<svg viewBox="0 0 {breedte} {hoogte}" '
                 'preserveAspectRatio="xMidYMid meet" ', 1))


def vrije_accounts(db):
    """Accounts zonder wachtwoord: die wachten nog op hun eigenaar."""
    return db.execute(
        f"SELECT p.id, p.name, p.nickname, {WEERGAVE} AS naam FROM players p "
        "WHERE p.password_hash IS NULL ORDER BY p.name COLLATE NOCASE").fetchall()


def _log_claim(db, speler, soort):
    db.execute("INSERT INTO claim_log (player_id, naam, soort, ip) VALUES (?, ?, ?, ?)",
               (speler["id"], speler["name"], soort,
                (request.headers.get("X-Forwarded-For") or request.remote_addr or "")
                .split(",")[0].strip()))


def _claim_venster_dicht():
    """Antwoord voor wie langskomt terwijl het opeisen niet openstaat."""
    flash("Accounts opeisen staat momenteel niet open. Vraag het even aan de "
          "organisatie.", "fout")
    return redirect(url_for("inloggen"))


@app.route("/claimen")
def claimen():
    """Stap 1: kies je naam uit de lijst."""
    if not claim_open():
        return _claim_venster_dicht()
    return render_template("claimen.html", accounts=vrije_accounts(get_db()))


@app.route("/claimen/<int:speler_id>", methods=["GET", "POST"])
def claim_account(speler_id):
    """Stap 2: bijnaam en wachtwoord kiezen voor de naam die je aanklikte."""
    db = get_db()
    if not claim_open():
        return _claim_venster_dicht()
    speler = db.execute("SELECT * FROM players WHERE id = ?", (speler_id,)).fetchone()
    if not speler:
        flash("Die speler bestaat niet (meer). Kies je naam uit de lijst.", "fout")
        return redirect(url_for("claimen"))
    if speler["password_hash"]:
        # Iemand anders was sneller, of dit account was al van iemand.
        flash(f"“{weergave(speler)}” heeft ondertussen al een wachtwoord. Ben jij dat "
              "en lukt inloggen niet? Vraag de organisatie om je account vrij te "
              "geven.", "fout")
        return redirect(url_for("claimen"))

    if request.method == "POST":
        ww = request.form.get("wachtwoord") or ""
        herhaal = request.form.get("herhaal") or ""
        bijnaam = (request.form.get("bijnaam") or "").strip()
        if len(ww) < 6:
            flash("Kies een wachtwoord van minstens 6 tekens.", "fout")
        elif ww != herhaal:
            flash("De twee wachtwoorden zijn niet hetzelfde.", "fout")
        elif len(bijnaam) > 30:
            flash("Hou je bijnaam korter dan 30 tekens.", "fout")
        else:
            # Laatste controle vlak vóór het opslaan: enkel wie nog geen wachtwoord
            # heeft, krijgt er hier één. Zo wint bij twee gelijktijdige pogingen
            # altijd de eerste, en overschrijft de tweede niets.
            klaar = db.execute("UPDATE players SET password_hash = ?, nickname = ? "
                               "WHERE id = ? AND password_hash IS NULL",
                               (generate_password_hash(ww),
                                bijnaam or speler["nickname"], speler_id)).rowcount
            if not klaar:
                db.rollback()
                flash(f"Net te laat: iemand koos zonet al een wachtwoord voor "
                      f"“{weergave(speler)}”. Vraag het even aan de organisatie.", "fout")
                return redirect(url_for("claimen"))
            _log_claim(db, speler, "claim")
            db.commit()
            session["speler_id"] = speler_id
            session.permanent = True        # ingelogd blijven op dit toestel
            g.pop("ik", None)
            flash(f"Welkom, {bijnaam or weergave(speler)}! Dit account is nu van jou. "
                  "Onthoud je wachtwoord: je logt voortaan in met "
                  f"“{speler['name']}” of met #{speler_id}.", "ok")
            return redirect(url_for("speler_profiel", speler_id=speler_id))

    return render_template("claim_account.html", speler=speler)


@app.route("/uitloggen")
def uitloggen():
    session.pop("speler_id", None)
    flash("Je bent uitgelogd.", "ok")
    return redirect(url_for("index"))


@app.route("/profiel/instellingen", methods=["POST"])
@speler_vereist
def profiel_instellingen():
    db = get_db()
    speler_id = session["speler_id"]
    speler = db.execute("SELECT * FROM players WHERE id = ?", (speler_id,)).fetchone()

    bijnaam = (request.form.get("bijnaam") or "").strip()
    if len(bijnaam) > 30:
        flash("Hou je bijnaam korter dan 30 tekens.", "fout")
        return redirect(url_for("speler_profiel", speler_id=speler_id))
    db.execute("UPDATE players SET nickname = ? WHERE id = ?", (bijnaam, speler_id))

    # Profielfoto's bestaan niet meer: de kroonkurk is nu je avatar.
    db.commit()
    flash("Je profiel is bijgewerkt.", "ok")
    return redirect(url_for("speler_profiel", speler_id=speler_id))


# ------------------------------------------------------------------ teams --
#
# Een team begint bij één speler: de oprichter. Zolang er geen tweede speler
# is, is het team "onvolledig". De oprichter kan iemand uitnodigen (één
# uitnodiging tegelijk); aanvaardt die, dan is het team actief en kan het
# spelen. Weigert hij, dan blijft het team gewoon bestaan en kan de oprichter
# iemand anders vragen.

def _uitnodiging_probleem(db, team, partner_id):
    """Waarom kan deze speler (nu) niet uitgenodigd worden? None = het mag."""
    partner = db.execute("SELECT * FROM players WHERE id = ? AND active = 1",
                         (partner_id,)).fetchone()
    if not partner or partner_id == team["player1_id"]:
        return "Kies een geldige teamgenoot."
    if db.execute("SELECT 1 FROM teams WHERE status = 'actief' AND "
                  "((player1_id = ? AND player2_id = ?) OR (player1_id = ? AND player2_id = ?))",
                  (team["player1_id"], partner_id, partner_id, team["player1_id"])).fetchone():
        return "Jullie twee hebben al een team samen."
    if db.execute("SELECT 1 FROM team_invitations i JOIN teams t ON t.id = i.team_id "
                  "WHERE t.player1_id = ? AND i.player_id = ? AND t.id != ?",
                  (team["player1_id"], partner_id, team["id"])).fetchone():
        return "Je hebt die speler al uitgenodigd voor een ander team."
    return None


def _nodig_uit(db, team, partner_id):
    """Zet een uitnodiging klaar. Geeft (gelukt, boodschap)."""
    probleem = _uitnodiging_probleem(db, team, partner_id)
    if probleem:
        return False, probleem
    db.execute("DELETE FROM team_invitations WHERE team_id = ?", (team["id"],))
    db.execute("INSERT INTO team_invitations (team_id, player_id) VALUES (?, ?)",
               (team["id"], partner_id))
    partner = db.execute("SELECT * FROM players WHERE id = ?", (partner_id,)).fetchone()
    return True, (f"{partner['nickname'] or partner['name']} is uitgenodigd voor "
                  f"“{team['name']}”. Zodra die aanvaardt, kunnen jullie spelen.")


@app.route("/team/nieuw", methods=["POST"])
@speler_vereist
def team_nieuw():
    db = get_db()
    speler_id = session["speler_id"]
    naam = (request.form.get("naam") or "").strip()
    beschrijving = (request.form.get("beschrijving") or "").strip()[:200]
    partner_veld = (request.form.get("partner") or "").strip()
    try:
        partner_id = int(partner_veld) if partner_veld else None
    except ValueError:
        partner_id = None

    if not naam or len(naam) > 40:
        flash("Geef je team een naam van maximaal 40 tekens.", "fout")
        return redirect(url_for("speler_profiel", speler_id=speler_id))
    if db.execute("SELECT 1 FROM teams WHERE name = ?", (naam,)).fetchone():
        flash("Er bestaat al een team met die naam.", "fout")
        return redirect(url_for("speler_profiel", speler_id=speler_id))
    if partner_id is not None:
        # Eerst nakijken of de uitnodiging kan, vóór het team er is.
        probleem = _uitnodiging_probleem(db, {"id": 0, "player1_id": speler_id}, partner_id)
        if probleem:
            flash(probleem, "fout")
            return redirect(url_for("speler_profiel", speler_id=speler_id))

    eigen_elo = db.execute("SELECT elo FROM players WHERE id = ?",
                           (speler_id,)).fetchone()["elo"]
    cur = db.execute("INSERT INTO teams (name, description, player1_id, status, elo) "
                     "VALUES (?, ?, ?, 'onvolledig', ?)",
                     (naam, beschrijving, speler_id, eigen_elo))
    team = db.execute("SELECT * FROM teams WHERE id = ?", (cur.lastrowid,)).fetchone()
    if partner_id is not None:
        _ok, boodschap = _nodig_uit(db, team, partner_id)
        flash(f"Team “{naam}” aangemaakt. {boodschap}", "ok")
    else:
        flash(f"Team “{naam}” aangemaakt. Het is nog onvolledig: nodig een "
              "teamgenoot uit op de teampagina.", "ok")
    db.commit()
    if partner_id is None:
        return redirect(url_for("team_profiel", team_id=team["id"]))
    return redirect(url_for("speler_profiel", speler_id=speler_id))


@app.route("/team/<int:team_id>/uitnodigen", methods=["POST"])
@speler_vereist
def team_uitnodigen(team_id):
    db = get_db()
    speler_id = session["speler_id"]
    team = db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if not team or team["player1_id"] != speler_id:
        abort(403)
    if team["status"] != "onvolledig":
        flash("Dit team is al volledig.", "fout")
        return redirect(url_for("team_profiel", team_id=team_id))
    try:
        partner_id = int(request.form.get("partner", ""))
    except ValueError:
        flash("Kies een teamgenoot.", "fout")
        return redirect(url_for("team_profiel", team_id=team_id))
    ok, boodschap = _nodig_uit(db, team, partner_id)
    db.commit()
    flash(boodschap, "ok" if ok else "fout")
    return redirect(url_for("team_profiel", team_id=team_id))


@app.route("/team/<int:team_id>/uitnodiging-intrekken", methods=["POST"])
@speler_vereist
def team_uitnodiging_intrekken(team_id):
    db = get_db()
    team = db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if not team or team["player1_id"] != session["speler_id"]:
        abort(403)
    db.execute("DELETE FROM team_invitations WHERE team_id = ?", (team_id,))
    db.commit()
    flash("Uitnodiging ingetrokken. Je kan iemand anders uitnodigen.", "ok")
    return redirect(url_for("team_profiel", team_id=team_id))


@app.route("/team/<int:team_id>/antwoord", methods=["POST"])
@speler_vereist
def team_antwoord(team_id):
    db = get_db()
    speler_id = session["speler_id"]
    team = db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    uitnodiging = db.execute("SELECT * FROM team_invitations WHERE team_id = ? AND "
                             "player_id = ?", (team_id, speler_id)).fetchone()
    if not team or not uitnodiging or team["status"] != "onvolledig":
        flash("Deze uitnodiging bestaat niet (meer).", "fout")
        return redirect(url_for("speler_profiel", speler_id=speler_id))

    if request.form.get("actie") == "accepteren":
        if db.execute("SELECT 1 FROM teams WHERE status = 'actief' AND "
                      "((player1_id = ? AND player2_id = ?) OR (player1_id = ? AND player2_id = ?))",
                      (team["player1_id"], speler_id, speler_id, team["player1_id"])).fetchone():
            db.execute("DELETE FROM team_invitations WHERE id = ?", (uitnodiging["id"],))
            db.commit()
            flash("Jullie twee hebben intussen al een ander team samen.", "fout")
            return redirect(url_for("speler_profiel", speler_id=speler_id))
        elos = [r["elo"] for r in db.execute("SELECT elo FROM players WHERE id IN (?, ?)",
                                             (team["player1_id"], speler_id))]
        db.execute("UPDATE teams SET player2_id = ?, status = 'actief', elo = ? WHERE id = ?",
                   (speler_id, sum(elos) / len(elos), team_id))
        db.execute("DELETE FROM team_invitations WHERE team_id = ?", (team_id,))
        db.commit()
        flash(f"Je maakt nu deel uit van team “{team['name']}”. Veel succes!", "ok")
    else:
        db.execute("DELETE FROM team_invitations WHERE id = ?", (uitnodiging["id"],))
        db.commit()
        flash("Uitnodiging geweigerd.", "ok")
    return redirect(url_for("speler_profiel", speler_id=speler_id))


@app.route("/team/<int:team_id>/bewerken", methods=["POST"])
@speler_vereist
def team_bewerken(team_id):
    db = get_db()
    speler_id = session["speler_id"]
    team = db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    # De twee spelers beheren hun eigen team; een organisator mag bijspringen om
    # een schrijffout of een ongepaste naam recht te zetten.
    if not team or not (speler_id in (team["player1_id"], team["player2_id"])
                        or is_organisator()):
        abort(403)
    # Vanuit het organisatiepaneel komt enkel een naam mee: dan laten we de
    # beschrijving met rust in plaats van ze leeg te maken.
    terug = request.form.get("terug") or ""
    # Enkel een pad op deze site: anders kan iemand je via een formulier naar
    # een vreemde website sturen.
    if not terug.startswith("/") or terug.startswith("//"):
        terug = url_for("team_profiel", team_id=team_id)

    naam = (request.form.get("naam") or "").strip()
    if not naam or len(naam) > 40:
        flash("Geef het team een naam van maximaal 40 tekens.", "fout")
        return redirect(terug)
    bestaat = db.execute("SELECT 1 FROM teams WHERE name = ? AND id != ?",
                         (naam, team_id)).fetchone()
    if bestaat:
        flash("Er bestaat al een team met die naam.", "fout")
        return redirect(terug)

    db.execute("UPDATE teams SET name = ? WHERE id = ?", (naam, team_id))
    if "beschrijving" in request.form:
        db.execute("UPDATE teams SET description = ? WHERE id = ?",
                   ((request.form.get("beschrijving") or "").strip()[:200], team_id))

    db.commit()
    flash(f"Team heet nu “{naam}”." if naam != team["name"] else "Team bijgewerkt.", "ok")
    return redirect(terug)


@app.route("/team/<int:team_id>/opheffen", methods=["POST"])
@speler_vereist
def team_opheffen(team_id):
    db = get_db()
    speler_id = session["speler_id"]
    team = db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if not team or speler_id not in (team["player1_id"], team["player2_id"]):
        abort(403)
    heeft_games = db.execute("SELECT 1 FROM games WHERE team1_id = ? OR team2_id = ? "
                             "LIMIT 1", (team_id, team_id)).fetchone()
    if heeft_games:
        flash("Dit team heeft al wedstrijden en kan niet opgeheven worden.", "fout")
        return redirect(url_for("team_profiel", team_id=team_id))
    db.execute("DELETE FROM teams WHERE id = ?", (team_id,))
    db.commit()
    flash(f"Team “{team['name']}” is opgeheven.", "ok")
    return redirect(url_for("speler_profiel", speler_id=speler_id))


def _viering(db, game, speler_id, gewonnen, tegen_naam, officieel):
    """Wat het grote scherm na het melden toont (zie viering.js).

    Gewonnen of verloren, tegen wie, en of het al officieel is. Is het
    officieel, dan ook je Aura-verschil en (in de knockout) waar je nu staat.
    """
    v = {"gewonnen": bool(gewonnen), "tegen": tegen_naam, "officieel": bool(officieel),
         "aura": None, "verder": None}
    if officieel:
        r = db.execute("""
            SELECT elo_na - elo_voor AS d FROM rating_history
            WHERE game_id = ? AND entity_type = 'speler' AND entity_id = ?
              AND scope = 'permanent'
        """, (game["id"], speler_id)).fetchone()
        if r is not None and r["d"] is not None:
            v["aura"] = int(round(r["d"]))
        if game["fase"] == "knockout":
            from elo import KO_LABEL, KLEINE_FINALE
            if game["ronde"] == 2:
                v["verder"] = "Toernooiwinnaar!" if gewonnen else "Tweede plaats"
            elif game["ronde"] == KLEINE_FINALE:
                v["verder"] = "Derde plaats" if gewonnen else "Vierde plaats"
            elif gewonnen and game["volgende_game_id"]:
                volgende = db.execute("SELECT ronde FROM games WHERE id = ?",
                                      (game["volgende_game_id"],)).fetchone()
                if volgende:
                    v["verder"] = f"Door naar de {KO_LABEL.get(volgende['ronde'], 'volgende ronde').lower()}"
            elif not gewonnen and game["ronde"] == 4:
                v["verder"] = "Nog een kans op brons in de kleine finale"
            elif not gewonnen:
                v["verder"] = f"Uitgeschakeld in de {KO_LABEL.get(game['ronde'], 'knockout').lower()}"
    return v


@app.route("/wedstrijd/<int:game_id>/melden", methods=["POST"])
def wedstrijd_melden(game_id):
    speler_id = session.get("speler_id")
    if not speler_id:
        flash("Log eerst in om een uitslag te melden.", "fout")
        return redirect(url_for("inloggen"))
    db = get_db()

    # Terugkeren naar de pagina waar het formulier stond (profiel of toernooi).
    volgende = request.form.get("volgende") or ""
    terug = (volgende if volgende.startswith("/") and not volgende.startswith("//")
             else url_for("speler_profiel", speler_id=speler_id))
    game = db.execute("""
        SELECT g.*, t1.player1_id AS a1, t1.player2_id AS a2,
               t2.player1_id AS b1, t2.player2_id AS b2
        FROM games g
        JOIN teams t1 ON t1.id = g.team1_id
        JOIN teams t2 ON t2.id = g.team2_id
        WHERE g.id = ?
    """, (game_id,)).fetchone()
    if not game or game["status"] != "gepland":
        flash("Deze wedstrijd bestaat niet of is al afgerond.", "fout")
        return redirect(terug)
    if speler_id not in (game["a1"], game["a2"], game["b1"], game["b2"]):
        flash("Je speelt zelf niet mee in deze wedstrijd.", "fout")
        return redirect(terug)
    if game["tournament_id"] is None and not league_toegankelijk():
        flash("Het leaguegedeelte staat momenteel niet open; deze wedstrijd kan je "
              "nog niet melden.", "fout")
        return redirect(terug)

    # Op volgorde spelen: eerst je vorige wedstrijd afwerken. Dit geldt voor
    # iedereen die meespeelt, ook voor een organisator — die heeft het
    # organisatiepaneel als uitweg en hoeft hier geen voorrang te krijgen.
    vorige = toernooi_motor.eerdere_wedstrijd(db, game)
    if vorige is not None:
        namen = teamnamen(db)
        flash(f"Werk eerst je vorige wedstrijd af: {namen[vorige['team1_id']]} tegen "
              f"{namen[vorige['team2_id']]} uit speelronde {vorige['ronde']}. "
              "De wedstrijden worden op volgorde gespeeld.", "fout")
        return redirect(terug)

    eigen_team = game["team1_id"] if speler_id in (game["a1"], game["a2"]) else game["team2_id"]
    tegen_team = game["team2_id"] if eigen_team == game["team1_id"] else game["team1_id"]

    keuze = request.form.get("winnaar")
    if keuze not in ("wij", "zij"):
        flash("Duid aan wie gewonnen heeft.", "fout")
        return redirect(terug)
    winnaar_team = eigen_team if keuze == "wij" else tegen_team

    db.execute("""
        INSERT INTO game_reports (game_id, team_id, player_id, winner_team_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(game_id, team_id) DO UPDATE SET
            player_id = excluded.player_id,
            winner_team_id = excluded.winner_team_id,
            created_at = datetime('now', 'localtime')
    """, (game_id, eigen_team, speler_id, winnaar_team))

    # Statistieken van het eigen team (beide spelers, optioneel, geen invloed op Aura).
    lid_ids = ({game["a1"], game["a2"]} if eigen_team == game["team1_id"]
               else {game["b1"], game["b2"]})
    for veld, waarde in request.form.items():
        if not veld.startswith("stat_"):
            continue
        delen = veld.split("_")
        if len(delen) != 3:
            continue
        try:
            stat_id, lid_id = int(delen[1]), int(delen[2])
        except ValueError:
            continue
        if lid_id not in lid_ids:
            continue  # enkel statistieken van je eigen teamleden
        if waarde.strip():
            try:
                db.execute("INSERT OR REPLACE INTO game_stats (game_id, player_id, "
                           "stat_type_id, value) VALUES (?, ?, ?, ?)",
                           (game_id, lid_id, stat_id,
                            float(waarde.replace(",", "."))))
            except ValueError:
                continue
        else:
            db.execute("DELETE FROM game_stats WHERE game_id = ? AND player_id = ? "
                       "AND stat_type_id = ?", (game_id, lid_id, stat_id))
    db.commit()

    tegen = db.execute("SELECT * FROM game_reports WHERE game_id = ? AND team_id = ?",
                       (game_id, tegen_team)).fetchone()
    tegen_naam = teamnamen(db).get(tegen_team, "de tegenstander")
    if tegen is None:
        flash("Je melding is geregistreerd. Zodra iemand van het andere team dezelfde "
              "uitslag meldt, wordt het resultaat officieel.", "ok")
        session["viering"] = _viering(db, game, speler_id, keuze == "wij", tegen_naam,
                                      officieel=False)
    elif tegen["winner_team_id"] == winnaar_team:
        db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, "
                   "played_at = ? WHERE id = ?",
                   (winnaar_team, datetime.now().strftime("%Y-%m-%dT%H:%M"), game_id))
        db.commit()
        for melding in na_resultaat(db, game_id):
            flash(melding, "ok")
        flash("Beide teams meldden dezelfde uitslag. Het resultaat is officieel en "
              "de Aura-ratings zijn bijgewerkt!", "ok")
        session["viering"] = _viering(db, game, speler_id, keuze == "wij", tegen_naam,
                                      officieel=True)
    else:
        flash("Oei: het andere team meldde een andere winnaar. Het resultaat blijft "
              "open tot de meldingen overeenkomen of een organisator beslist.", "fout")
    return redirect(terug)


# ------------------------------------------------------------------ admin --

# ------------------------------------------------------------------ rollen --
#
# Wie mag wat? Een organisator mag nieuwe organisatoren aanduiden en mag zijn
# eigen rol teruggeven, maar kan een collega er niet uitzetten. Enkel de
# eigenaar kan dat — en die kan het eigenaarschap doorgeven wanneer hij stopt.

OVERDRACHTSWOORD = "overdragen"


@app.route("/admin/spelers/<int:speler_id>/organisator", methods=["POST"])
@login_vereist
def rol_toekennen(speler_id):
    """Een gewone speler organisator maken. Elke organisator mag dit."""
    db = get_db()
    speler = db.execute("SELECT * FROM players WHERE id = ?", (speler_id,)).fetchone()
    if not speler:
        flash("Speler niet gevonden.", "fout")
    elif speler["role"] in ORGANISATOREN:
        flash(f"“{weergave(speler)}” is al {ROL_LABEL[speler['role']]}.", "fout")
    elif not speler["password_hash"]:
        flash(f"“{weergave(speler)}” heeft nog geen wachtwoord en kan dus niet "
              "inloggen als organisator.", "fout")
    else:
        db.execute("UPDATE players SET role = ? WHERE id = ?", (ROL_ADMIN, speler_id))
        db.commit()
        flash(f"“{weergave(speler)}” is nu organisator en kan bij het "
              "organisatiepaneel.", "ok")
    return redirect(url_for("admin_spelers") + "#organisatoren")


@app.route("/admin/spelers/<int:speler_id>/rol-afnemen", methods=["POST"])
@login_vereist
def rol_afnemen(speler_id):
    """Organisatorrol afnemen: van jezelf mag altijd, van een ander enkel als eigenaar."""
    db = get_db()
    speler = db.execute("SELECT * FROM players WHERE id = ?", (speler_id,)).fetchone()
    zelf = speler_id == session.get("speler_id")
    if not speler:
        flash("Speler niet gevonden.", "fout")
    elif speler["role"] == ROL_EIGENAAR:
        flash("De eigenaar kan zijn rol niet afgeven. Draag eerst het "
              "eigenaarschap over aan een andere organisator.", "fout")
    elif speler["role"] != ROL_ADMIN:
        flash(f"“{weergave(speler)}” is geen organisator.", "fout")
    elif not zelf and not is_eigenaar():
        flash("Enkel de eigenaar kan de rol van een andere organisator afnemen. "
              "Je kan wel je eigen rol teruggeven.", "fout")
    else:
        db.execute("UPDATE players SET role = ? WHERE id = ?", (ROL_SPELER, speler_id))
        db.commit()
        if zelf:
            flash("Je hebt je organisatorrol teruggegeven. Je bent nu een gewone "
                  "speler.", "ok")
            return redirect(url_for("index"))
        flash(f"“{weergave(speler)}” is geen organisator meer.", "ok")
    return redirect(url_for("admin_spelers") + "#organisatoren")


@app.route("/admin/spelers/<int:speler_id>/eigenaarschap", methods=["POST"])
@login_vereist
@eigenaar_vereist
def eigenaarschap_overdragen(speler_id):
    """Het eigenaarschap doorgeven — bv. als je stopt bij de organisatie.

    De vorige eigenaar wordt gewoon organisator, zodat er nooit twee eigenaars
    zijn en er ook nooit géén eigenaar is.
    """
    db = get_db()
    nieuw = db.execute("SELECT * FROM players WHERE id = ?", (speler_id,)).fetchone()
    woord = (request.form.get("bevestiging") or "").strip().lower()
    if not nieuw:
        flash("Speler niet gevonden.", "fout")
    elif woord != OVERDRACHTSWOORD:
        flash(f"Typ “{OVERDRACHTSWOORD}” in het vakje om de overdracht te "
              "bevestigen. Er is niets gewijzigd.", "fout")
    elif nieuw["role"] != ROL_ADMIN:
        flash("Je kan het eigenaarschap enkel doorgeven aan iemand die al "
              "organisator is. Maak die persoon eerst organisator.", "fout")
    else:
        db.execute("UPDATE players SET role = ? WHERE role = ?",
                   (ROL_ADMIN, ROL_EIGENAAR))
        db.execute("UPDATE players SET role = ? WHERE id = ?",
                   (ROL_EIGENAAR, speler_id))
        db.commit()
        flash(f"“{weergave(nieuw)}” is nu de eigenaar. Jij blijft organisator, "
              "maar kan de rol van anderen niet meer afnemen.", "ok")
    return redirect(url_for("admin_spelers") + "#organisatoren")


# Het organisatiepaneel is opgesplitst in aparte pagina's; deze helpers halen
# telkens enkel op wat die ene pagina nodig heeft.

def _meldingen_per_game(db, alleen_liga=True):
    uit = defaultdict(list)
    filter_sql = "WHERE g.tournament_id IS NULL" if alleen_liga else ""
    for r in db.execute(f"""
        SELECT gr.*, {WEERGAVE} AS speler_naam
        FROM game_reports gr
        JOIN players p ON p.id = gr.player_id
        JOIN games g ON g.id = gr.game_id
        {filter_sql}
        ORDER BY gr.created_at
    """):
        uit[r["game_id"]].append(r)
    return uit


def _team_spelers(db):
    uit = {}
    for t in db.execute("SELECT * FROM teams"):
        rijen = db.execute(f"SELECT p.id, {WEERGAVE} AS name FROM players p "
                           "WHERE p.id IN (?, ?)",
                           (t["player1_id"], t["player2_id"])).fetchall()
        uit[t["id"]] = [dict(x) for x in rijen]
    return uit


def _conflicten(db):
    """Wedstrijden waar de twee teams een andere winnaar meldden."""
    return db.execute("""
        SELECT COUNT(*) AS n FROM (
            SELECT gr.game_id FROM game_reports gr
            JOIN games g ON g.id = gr.game_id AND g.status = 'gepland'
            GROUP BY gr.game_id HAVING COUNT(DISTINCT gr.winner_team_id) > 1
        )
    """).fetchone()["n"]


@app.route("/admin")
@login_vereist
def admin():
    """Startpagina van het organisatiepaneel: overzicht en wegwijzers."""
    db = get_db()

    def tel(sql, *args):
        return db.execute(sql, args).fetchone()["n"]

    cijfers = {
        "spelers": tel("SELECT COUNT(*) AS n FROM players WHERE active = 1"),
        "teams": tel("SELECT COUNT(*) AS n FROM teams WHERE status = 'actief'"),
        "seizoenen": tel("SELECT COUNT(*) AS n FROM seasons"),
        "toernooien": tel("SELECT COUNT(*) AS n FROM tournaments"),
        "open_liga": tel("SELECT COUNT(*) AS n FROM games WHERE status = 'gepland' "
                         "AND tournament_id IS NULL"),
        "open_toernooi": tel("SELECT COUNT(*) AS n FROM games WHERE status = 'gepland' "
                             "AND tournament_id IS NOT NULL AND team1_id IS NOT NULL"),
        "gespeeld": tel("SELECT COUNT(*) AS n FROM games WHERE status = 'gespeeld' "
                        "AND fase != 'shootout'"),
        "conflicten": _conflicten(db),
        "uitnodigingen": tel("SELECT COUNT(*) AS n FROM team_invitations"),
    }
    lopend = db.execute("""
        SELECT * FROM tournaments WHERE status IN ('bracket', 'knockout')
        ORDER BY date DESC
    """).fetchall()
    huidig = huidig_seizoen(db)
    return render_template("admin.html", cijfers=cijfers, lopend=lopend,
                           huidig_seizoen=huidig)


@app.route("/admin/seizoenen")
@login_vereist
def admin_seizoenen():
    db = get_db()
    seizoenen_lijst = []
    for s in db.execute("SELECT * FROM seasons ORDER BY start_date DESC"):
        speeldagen = db.execute("""
            SELECT md.*, (SELECT COUNT(*) FROM games g WHERE g.matchday_id = md.id)
                   AS aantal_games
            FROM matchdays md WHERE md.season_id = ? ORDER BY md.date
        """, (s["id"],)).fetchall()
        tellingen = db.execute("""
            SELECT COUNT(*) AS totaal,
                   SUM(CASE WHEN g.status = 'gepland' THEN 1 ELSE 0 END) AS open
            FROM games g JOIN matchdays md ON md.id = g.matchday_id
            WHERE md.season_id = ?
        """, (s["id"],)).fetchone()
        seizoenen_lijst.append({"seizoen": s, "status": seizoen_status(s),
                                "speeldagen": speeldagen,
                                "aantal_games": tellingen["totaal"] or 0,
                                "openstaand": tellingen["open"] or 0})
    return render_template("admin_seizoenen.html", seizoenen_lijst=seizoenen_lijst)


@app.route("/admin/seizoen/<int:seizoen_id>")
@login_vereist
def admin_seizoen(seizoen_id):
    """Eén seizoen beheren: speeldagen én de wedstrijden die erin gespeeld worden.

    Vroeger stond het plannen van wedstrijden op een eigen tabblad, los van de
    seizoenen. Maar een leaguewedstrijd hoort altijd bij een speeldag, en een
    speeldag hoort altijd bij een seizoen — dus staat alles nu bij elkaar.
    """
    db = get_db()
    seizoen = db.execute("SELECT * FROM seasons WHERE id = ?", (seizoen_id,)).fetchone()
    if not seizoen:
        abort(404)

    speeldagen = db.execute("""
        SELECT md.*, (SELECT COUNT(*) FROM games g WHERE g.matchday_id = md.id)
               AS aantal_games
        FROM matchdays md WHERE md.season_id = ? ORDER BY md.date
    """, (seizoen_id,)).fetchall()

    namen_sp = weergavenamen(db)
    teams = [{"id": t["id"], "naam": t["name"],
              "leden": f'{namen_sp.get(t["player1_id"], "?")} & '
                       f'{namen_sp.get(t["player2_id"], "?")}'}
             for t in db.execute("SELECT * FROM teams WHERE status = 'actief' "
                                 "ORDER BY name")]
    gepland = db.execute("""
        SELECT g.* FROM games g JOIN matchdays md ON md.id = g.matchday_id
        WHERE md.season_id = ? AND g.status = 'gepland' ORDER BY g.scheduled_at
    """, (seizoen_id,)).fetchall()
    recent = db.execute("""
        SELECT g.* FROM games g JOIN matchdays md ON md.id = g.matchday_id
        WHERE md.season_id = ? AND g.status = 'gespeeld'
        ORDER BY g.played_at DESC, g.id DESC LIMIT 20
    """, (seizoen_id,)).fetchall()
    actieve_types = db.execute("SELECT * FROM stat_types WHERE active = 1 "
                               "ORDER BY name").fetchall()
    return render_template("admin_seizoen.html", seizoen=seizoen,
                           status=seizoen_status(seizoen), speeldagen=speeldagen,
                           teams=teams, gepland=gepland, recent=recent,
                           actieve_types=actieve_types, namen=teamnamen(db),
                           team_spelers=_team_spelers(db),
                           meldingen_per_game=_meldingen_per_game(db),
                           nu=datetime.now().strftime("%Y-%m-%dT%H:%M"))


@app.route("/admin/toernooien")
@login_vereist
def admin_toernooien():
    db = get_db()
    toernooien_lijst = db.execute("""
        SELECT tn.*,
               (SELECT COUNT(*) FROM tournament_teams tt
                WHERE tt.tournament_id = tn.id) AS aantal_teams,
               (SELECT COUNT(*) FROM games g
                WHERE g.tournament_id = tn.id AND g.status = 'gepland'
                  AND g.team1_id IS NOT NULL) AS open_games
        FROM tournaments tn ORDER BY tn.date DESC, tn.id DESC
    """).fetchall()
    return render_template("admin_toernooien.html",
                           toernooien_lijst=toernooien_lijst,
                           vandaag=date.today().isoformat())


@app.route("/admin/spelers")
@login_vereist
def admin_spelers():
    db = get_db()
    maand = wedstrijden_deze_maand(db)
    ik_id = session.get("speler_id")
    spelers = []
    for r in db.execute(f"SELECT p.*, {WEERGAVE} AS naam FROM players p ORDER BY naam"):
        d = dict(r)
        d["deze_maand"] = maand.get(r["id"], 0)
        eigen_teams = teams_van_speler(db, r["id"])
        d["teams"] = [t["name"] for t in eigen_teams]
        d["ben_ik"] = r["id"] == ik_id
        d["rol_label"] = ROL_LABEL.get(r["role"], r["role"])
        d["organisator"] = r["role"] in ORGANISATOREN
        # Wie de rol van deze speler mag wijzigen (zie de uitleg bij de auth-helpers).
        d["mag_promoveren"] = (r["role"] == ROL_SPELER and bool(r["password_hash"]))
        d["mag_degraderen"] = (r["role"] == ROL_ADMIN
                               and (is_eigenaar() or d["ben_ik"]))
        d["mag_eigenaar_maken"] = is_eigenaar() and r["role"] == ROL_ADMIN
        # De eigenaar blijft onaantastbaar; enkel de eigenaar raakt aan een organisator.
        d["mag_beheren"] = (r["role"] != ROL_EIGENAAR
                            and (is_eigenaar() or r["role"] == ROL_SPELER))
        d["wis_blokkade"] = None
        if d["ben_ik"]:
            d["wis_blokkade"] = "Je kan je eigen account niet verwijderen."
        elif r["role"] == ROL_EIGENAAR:
            d["wis_blokkade"] = ("De eigenaar kan niet verwijderd worden. Laat het "
                                 "eigenaarschap eerst overdragen.")
        elif not d["mag_beheren"]:
            d["wis_blokkade"] = ("Deze speler is organisator. Enkel de eigenaar kan "
                                 "een organisator verwijderen.")
        elif eigen_teams:
            d["wis_blokkade"] = ("Zit nog in " + ", ".join(d["teams"])
                                 + ". Verwijder die teams eerst.")
        d["verwijderbaar"] = d["wis_blokkade"] is None
        # Steekwoorden voor de filterknoppen boven de lijst.
        d["filters"] = " ".join(
            ["actief" if r["active"] else "inactief"]
            + ([] if r["password_hash"] else ["geen-ww"])
            + (["organisator"] if d["organisator"] else []))
        spelers.append(d)

    namen_sp = weergavenamen(db)
    wedstrijden_per_team = defaultdict(int)
    for r in db.execute("SELECT team1_id, team2_id FROM games"):
        wedstrijden_per_team[r["team1_id"]] += 1
        wedstrijden_per_team[r["team2_id"]] += 1
    teams = []
    for t in db.execute("SELECT * FROM teams ORDER BY status, name"):
        bezig = team_blokkerende_toernooien(db, t["id"])
        teams.append({"id": t["id"], "naam": t["name"], "status": t["status"],
                      "elo": t["elo"], "avatar": t["avatar"],
                      "wedstrijden": wedstrijden_per_team[t["id"]],
                      "blokkerend": [x["name"] for x in bezig],
                      "speler_ids": [p for p in (t["player1_id"], t["player2_id"]) if p],
                      "leden": (f'{namen_sp.get(t["player1_id"], "?")} & '
                                f'{namen_sp.get(t["player2_id"], "?")}'
                                if t["player2_id"] else
                                f'{namen_sp.get(t["player1_id"], "?")} (zoekt een teamgenoot)')})
    for d in spelers:
        d["team_lijst"] = [t for t in teams if d["id"] in t["speler_ids"]]
    log = db.execute("SELECT * FROM claim_log ORDER BY moment DESC, id DESC "
                     "LIMIT 40").fetchall()
    return render_template("admin_spelers.html", spelers=spelers, teams=teams,
                           claim_open=claim_open(), claim_log=log,
                           claim_adres=externe_url("claimen"),
                           vrij=[p for p in spelers if not p["password_hash"]],
                           dubbels=samenvoegen.mogelijke_dubbels(db, naam_lijkt_op),
                           dubbele_teams=samenvoegen.mogelijke_dubbele_teams(db, naam_lijkt_op),
                           namen_sp=namen_sp,
                           maand_label=f"{MAANDEN[datetime.now().month - 1]} "
                                       f"{datetime.now().year}")


@app.route("/admin/klassement")
@login_vereist
def admin_klassement():
    db = get_db()
    return render_template(
        "admin_klassement.html", rangen=alle_rangen(db),
        types=db.execute("SELECT * FROM stat_types ORDER BY active DESC, name").fetchall())


@app.route("/admin/instellingen", methods=["GET"])
@login_vereist
@eigenaar_vereist
def admin_instellingen():
    db = get_db()
    return render_template("admin_instellingen.html",
                           k_speler=instelling(db, "k_speler", "32"),
                           k_team=instelling(db, "k_team", "32"),
                           start_elo=f"{START_ELO:.0f}",
                           backups=backup_lijst()[:6],
                           backup_map=BACKUP_MAP)


@app.route("/admin/seizoenen/nieuw", methods=["POST"])
@login_vereist
def seizoen_nieuw():
    naam = (request.form.get("naam") or "").strip()
    start = request.form.get("start") or ""
    einde = request.form.get("einde") or ""
    if not naam or not start or not einde or start >= einde:
        flash("Geef een naam op en zorg dat de startdatum vóór de einddatum valt.", "fout")
        return redirect(url_for("admin_seizoenen"))
    db = get_db()
    try:
        db.execute("INSERT INTO seasons (name, start_date, end_date) VALUES (?, ?, ?)",
                   (naam, start, einde))
        db.commit()
        flash(f"Seizoen “{naam}” aangemaakt. Voeg nu speeldagen toe.", "ok")
    except Exception:
        flash(f"Er bestaat al een seizoen met de naam “{naam}”.", "fout")
    return redirect(url_for("admin_seizoenen"))


@app.route("/admin/seizoenen/<int:seizoen_id>/verwijderen", methods=["POST"])
@login_vereist
def seizoen_verwijderen(seizoen_id):
    db = get_db()
    db.execute("DELETE FROM seasons WHERE id = ?", (seizoen_id,))
    db.commit()
    flash("Seizoen verwijderd. Gespeelde wedstrijden blijven bestaan, maar horen "
          "niet langer bij een seizoen.", "ok")
    return redirect(url_for("admin_seizoenen"))


@app.route("/admin/speeldagen/nieuw", methods=["POST"])
@login_vereist
def speeldag_nieuw():
    try:
        seizoen_id = int(request.form.get("seizoen", ""))
    except ValueError:
        flash("Kies een seizoen.", "fout")
        return redirect(url_for("admin_seizoenen"))
    datum = request.form.get("datum") or ""
    db = get_db()
    seizoen = db.execute("SELECT * FROM seasons WHERE id = ?", (seizoen_id,)).fetchone()
    if not seizoen or not datum:
        flash("Kies een geldig seizoen en een datum.", "fout")
        return redirect(url_for("admin_seizoenen"))
    terug = redirect(url_for("admin_seizoen", seizoen_id=seizoen_id) + "#speeldagen")
    titel = (request.form.get("titel") or "").strip()
    if not titel:
        n = db.execute("SELECT COUNT(*) AS n FROM matchdays WHERE season_id = ?",
                       (seizoen_id,)).fetchone()["n"]
        titel = f"Speeldag {n + 1}"
    db.execute("INSERT INTO matchdays (season_id, title, date) VALUES (?, ?, ?)",
               (seizoen_id, titel, datum))
    db.commit()
    flash(f"“{titel}” toegevoegd aan {seizoen['name']}.", "ok")
    return terug


@app.route("/admin/speeldagen/<int:speeldag_id>/tafels", methods=["POST"])
@login_vereist
def speeldag_tafels(speeldag_id):
    """Hoeveel wedstrijden kunnen er die avond tegelijk lopen?"""
    db = get_db()
    rij = db.execute("SELECT season_id FROM matchdays WHERE id = ?",
                     (speeldag_id,)).fetchone()
    try:
        tafels = max(1, min(20, int(request.form.get("tafels", ""))))
    except ValueError:
        flash("Geef een aantal tafels tussen 1 en 20.", "fout")
    else:
        db.execute("UPDATE matchdays SET tafels = ? WHERE id = ?", (tafels, speeldag_id))
        db.commit()
        flash(f"Aangepast naar {tafels} tafel(s).", "ok")
        for melding in liga_motor.koppel_wachtrij(db, speeldag_id):
            flash(melding, "ok")
    if rij:
        return redirect(url_for("admin_seizoen", seizoen_id=rij["season_id"]) + "#speeldagen")
    return redirect(url_for("admin_seizoenen"))


@app.route("/admin/speeldagen/<int:speeldag_id>/verwijderen", methods=["POST"])
@login_vereist
def speeldag_verwijderen(speeldag_id):
    db = get_db()
    rij = db.execute("SELECT season_id FROM matchdays WHERE id = ?",
                     (speeldag_id,)).fetchone()
    heeft_games = db.execute("SELECT 1 FROM games WHERE matchday_id = ? LIMIT 1",
                             (speeldag_id,)).fetchone()
    if heeft_games:
        flash("Deze speeldag heeft al wedstrijden en kan niet verwijderd worden.", "fout")
    else:
        db.execute("DELETE FROM matchdays WHERE id = ?", (speeldag_id,))
        db.commit()
        flash("Speeldag verwijderd.", "ok")
    if rij:
        return redirect(url_for("admin_seizoen", seizoen_id=rij["season_id"]) + "#speeldagen")
    return redirect(url_for("admin_seizoenen"))


@app.route("/admin/spelers/nieuw", methods=["POST"])
@login_vereist
def speler_toevoegen():
    """Een speler aanmaken zonder wachtwoord (bv. uit de inschrijvingen).

    De speler kan zo nog niet inloggen; hij kiest zelf een wachtwoord door zijn
    account op te eisen via /claimen. Ondertussen kan je hem al in een team en
    in een toernooi zetten.
    """
    db = get_db()
    naam = " ".join((request.form.get("naam") or "").split())
    bijnaam = (request.form.get("bijnaam") or "").strip()
    if len(naam) < 3 or len(naam) > 40:
        flash("Vul de echte naam in (3 tot 40 tekens).", "fout")
    elif len(bijnaam) > 30:
        flash("Hou de bijnaam korter dan 30 tekens.", "fout")
    else:
        try:
            nummer = vrij_spelernummer(db)
            db.execute("INSERT INTO players (id, name, nickname, elo) VALUES (?, ?, ?, ?)",
                       (nummer, naam, bijnaam, START_ELO))
            db.commit()
            flash(f"“{bijnaam or naam}” toegevoegd met nummer #{nummer}. Er is nog "
                  "geen wachtwoord: de speler kiest er zelf één zodra je het "
                  "opeisen openzet.", "ok")
        except Exception:
            db.rollback()
            flash(f"Er bestaat al een speler met de naam “{naam}”.", "fout")
    return redirect(url_for("admin_spelers") + "#spelers")


@app.route("/admin/teams/nieuw", methods=["POST"])
@login_vereist
def team_toevoegen():
    """Meteen een actief team van twee spelers vormen, zonder uitnodiging."""
    db = get_db()
    naam = (request.form.get("naam") or "").strip()
    try:
        p1 = int(request.form.get("speler1", ""))
        p2 = int(request.form.get("speler2", ""))
    except ValueError:
        flash("Kies twee spelers.", "fout")
        return redirect(url_for("admin_spelers") + "#teams")

    spelers = {r["id"]: r for r in db.execute(
        "SELECT * FROM players WHERE id IN (?, ?)", (p1, p2))}
    if p1 == p2 or len(spelers) != 2:
        flash("Kies twee verschillende, bestaande spelers.", "fout")
    elif not naam or len(naam) > 40:
        flash("Geef het team een naam van maximaal 40 tekens.", "fout")
    elif db.execute("SELECT 1 FROM teams WHERE (player1_id = ? AND player2_id = ?) "
                    "OR (player1_id = ? AND player2_id = ?)", (p1, p2, p2, p1)).fetchone():
        flash("Die twee spelers vormen al een team.", "fout")
    elif db.execute("SELECT 1 FROM teams WHERE name = ?", (naam,)).fetchone():
        flash(f"Er bestaat al een team met de naam “{naam}”.", "fout")
    else:
        db.execute("INSERT INTO teams (name, player1_id, player2_id, status, elo) "
                   "VALUES (?, ?, ?, 'actief', ?)",
                   (naam, p1, p2, (spelers[p1]["elo"] + spelers[p2]["elo"]) / 2.0))
        db.commit()
        flash(f"Team “{naam}” aangemaakt met {weergave(spelers[p1])} en "
              f"{weergave(spelers[p2])}. Het is meteen actief en kan meespelen.", "ok")
    return redirect(url_for("admin_spelers") + "#teams")


@app.route("/admin/spelers/<int:speler_id>/verwijderen", methods=["POST"])
@login_vereist
def speler_verwijderen(speler_id):
    """Zet een speler op inactief of terug op actief (historiek blijft bewaard)."""
    db = get_db()
    speler = db.execute("SELECT * FROM players WHERE id = ?", (speler_id,)).fetchone()
    if not speler:
        flash("Speler niet gevonden.", "fout")
        return redirect(url_for("admin_spelers"))
    bezwaar = rol_blokkade(speler)
    if bezwaar:
        flash(bezwaar, "fout")
        return redirect(url_for("admin_spelers"))

    db.execute("UPDATE players SET active = 1 - active WHERE id = ?", (speler_id,))
    db.commit()
    nu_actief = db.execute("SELECT active FROM players WHERE id = ?",
                           (speler_id,)).fetchone()["active"]
    if nu_actief:
        flash(f"“{speler['name']}” is opnieuw geactiveerd.", "ok")
    else:
        flash(f"“{speler['name']}” is gedeactiveerd en verdwijnt uit het klassement; "
              "zijn wedstrijden en historiek blijven bewaard.", "ok")
    return redirect(url_for("admin_spelers"))


BEVESTIGINGSWOORD = "verwijder"


def _bevestigd():
    """Heeft de organisator het woord 'verwijder' echt ingetypt?"""
    return (request.form.get("bevestiging") or "").strip().lower() == BEVESTIGINGSWOORD


def teams_van_speler(db, speler_id):
    """Alle teams (actief of niet) waar deze speler in zit."""
    return db.execute("SELECT * FROM teams WHERE player1_id = ? OR player2_id = ? "
                      "ORDER BY name", (speler_id, speler_id)).fetchall()


def rol_blokkade(speler):
    """Waarom mag ik niet aan dit account komen? (None = geen bezwaar.)

    De eigenaar is voor iedereen onaantastbaar, een organisator enkel voor de
    eigenaar. Anders zou een organisator een collega kunnen buitenzetten door
    diens wachtwoord te resetten of zijn account te wissen.
    """
    if speler["id"] == session.get("speler_id"):
        return None
    if speler["role"] == ROL_EIGENAAR:
        return ("Dit is de eigenaar van de site: zijn account kan niet door een "
                "organisator gewijzigd worden.")
    if speler["role"] == ROL_ADMIN and not is_eigenaar():
        return (f"“{weergave(speler)}” is organisator. Enkel de eigenaar kan aan "
                "het account van een organisator.")
    return None


def team_blokkerende_toernooien(db, team_id):
    """Lopende toernooien waarin dit team meespeelt: die mag je niet stukmaken."""
    return db.execute("""
        SELECT tn.name FROM tournaments tn
        JOIN tournament_teams tt ON tt.tournament_id = tn.id
        WHERE tt.team_id = ? AND tn.status IN ('bracket', 'knockout')
    """, (team_id,)).fetchall()


@app.route("/admin/spelers/<int:speler_id>/definitief-verwijderen", methods=["POST"])
@login_vereist
def speler_definitief_verwijderen(speler_id):
    """Wis een speler volledig. Kan enkel als hij in geen enkel team meer zit."""
    db = get_db()
    speler = db.execute(f"SELECT p.*, {WEERGAVE} AS naam FROM players p "
                        "WHERE p.id = ?", (speler_id,)).fetchone()
    if not speler:
        flash("Speler niet gevonden.", "fout")
        return redirect(url_for("admin_spelers"))
    if not _bevestigd():
        flash(f"Typ “{BEVESTIGINGSWOORD}” in het vakje om het verwijderen te "
              "bevestigen. Er is niets gewist.", "fout")
        return redirect(url_for("admin_spelers"))
    if speler_id == session.get("speler_id"):
        flash("Je kan je eigen account niet verwijderen.", "fout")
        return redirect(url_for("admin_spelers"))
    bezwaar = rol_blokkade(speler)
    if bezwaar:
        flash(bezwaar, "fout")
        return redirect(url_for("admin_spelers"))

    teams = teams_van_speler(db, speler_id)
    if teams:
        namen = ", ".join(f"“{t['name']}”" for t in teams)
        flash(f"“{speler['naam']}” zit nog in {len(teams)} team(s): {namen}. "
              "Verwijder die eerst; dan kan de speler weg.", "fout")
        return redirect(url_for("admin_spelers"))

    wis_avatar(speler["avatar"])
    db.execute("DELETE FROM game_stats WHERE player_id = ?", (speler_id,))
    db.execute("DELETE FROM game_reports WHERE player_id = ?", (speler_id,))
    # Ook zijn regels uit het claimlogboek: die zouden anders naar een profiel
    # verwijzen dat niet meer bestaat.
    db.execute("DELETE FROM claim_log WHERE player_id = ?", (speler_id,))
    db.execute("DELETE FROM rating_history WHERE entity_type = 'speler' "
               "AND entity_id = ?", (speler_id,))
    db.execute("DELETE FROM season_ratings WHERE entity_type = 'speler' "
               "AND entity_id = ?", (speler_id,))
    db.execute("DELETE FROM players WHERE id = ?", (speler_id,))
    db.commit()
    na_resultaat(db)
    flash(f"Speler “{speler['naam']}” (#{speler_id}) is definitief verwijderd.", "ok")
    return redirect(url_for("admin_spelers"))


@app.route("/admin/teams/<int:team_id>/definitief-verwijderen", methods=["POST"])
@login_vereist
def team_definitief_verwijderen(team_id):
    """Wis een team volledig, inclusief al zijn wedstrijden.

    Alle ratings worden daarna opnieuw berekend alsof die wedstrijden nooit
    gespeeld zijn: wie ooit Aura verloor tegen dit team, krijgt die terug.
    """
    db = get_db()
    team = db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if not team:
        flash("Team niet gevonden.", "fout")
        return redirect(url_for("admin_spelers") + "#teams")
    if not _bevestigd():
        flash(f"Typ “{BEVESTIGINGSWOORD}” in het vakje om het verwijderen te "
              "bevestigen. Er is niets gewist.", "fout")
        return redirect(url_for("admin_spelers") + "#teams")

    bezig = team_blokkerende_toernooien(db, team_id)
    if bezig:
        namen = ", ".join(f"“{t['name']}”" for t in bezig)
        flash(f"“{team['name']}” speelt nog mee in {namen}. Rond dat toernooi eerst "
              "af of verwijder het, anders klopt het schema niet meer.", "fout")
        return redirect(url_for("admin_spelers") + "#teams")

    aantal = db.execute("SELECT COUNT(*) AS n FROM games WHERE team1_id = ? "
                        "OR team2_id = ?", (team_id, team_id)).fetchone()["n"]

    wis_avatar(team["avatar"])
    # De wedstrijden verdwijnen mee; game_stats, meldingen en ratinghistoriek
    # hangen er met ON DELETE CASCADE aan vast.
    db.execute("DELETE FROM games WHERE team1_id = ? OR team2_id = ?",
               (team_id, team_id))
    db.execute("DELETE FROM season_ratings WHERE entity_type = 'team' "
               "AND entity_id = ?", (team_id,))
    db.execute("DELETE FROM game_reports WHERE team_id = ?", (team_id,))
    db.execute("DELETE FROM teams WHERE id = ?", (team_id,))
    db.commit()
    na_resultaat(db)
    flash(f"Team “{team['name']}” is definitief verwijderd, samen met "
          f"{aantal} wedstrijd(en). Alle Aura-ratings zijn herberekend alsof die "
          "wedstrijden nooit gespeeld zijn.", "ok")
    return redirect(url_for("admin_spelers") + "#teams")


# ------------------------------------------------------------ samenvoegen --
#
# Een tweede account omdat iemand zijn wachtwoord vergat, en een tweede team
# met dezelfde ploegmaat erbij: de organisator voegt ze samen. Eerst een
# voorbeeld (GET), dan pas het echte werk (POST, met bevestigingswoord en een
# back-up vooraf). Het eigenlijke verhuizen staat in samenvoegen.py.

SAMENVOEG_WOORD = "samenvoegen"


def _keuze_id(naam):
    try:
        return int(request.values.get(naam, ""))
    except ValueError:
        return None


def _voer_samenvoeging_uit(db, werk):
    """Back-up, `werk()` uitvoeren, alle Aura herberekenen en committen — of
    bij een probleem alles terugdraaien. Geeft het resultaat van werk() terug,
    of None als het mislukte (er staat dan een melding klaar)."""
    try:
        maak_backup("samenvoegen")
    except Exception as fout:
        flash(f"Er kon geen back-up gemaakt worden ({fout}). Er is niets veranderd.", "fout")
        return None
    try:
        resultaat = werk()
        na_resultaat(db)                # herberekent alles en commit
    except samenvoegen.SamenvoegFout as fout:
        db.rollback()
        for b in fout.bezwaren:
            flash(b, "fout")
        return None
    except Exception:
        db.rollback()
        raise
    for bestand in resultaat.get("weg_te_gooien", []):
        wis_avatar(bestand)
    return resultaat


@app.route("/admin/samenvoegen/spelers", methods=["GET", "POST"])
@login_vereist
def spelers_samenvoegen():
    db = get_db()
    bron_id, doel_id = _keuze_id("bron"), _keuze_id("doel")
    terug = redirect(url_for("admin_spelers") + "#samenvoegen")
    if not bron_id or not doel_id:
        flash("Kies welk account blijft en welk verdwijnt.", "fout")
        return terug
    plan = samenvoegen.plan_spelers(db, bron_id, doel_id)
    if not plan["bron"] or not plan["doel"]:
        flash("Speler niet gevonden.", "fout")
        return terug
    # Zelfde regels als bij een wachtwoord resetten: aan het account van de
    # eigenaar of (voor gewone organisatoren) van een collega kom je niet.
    for s in (plan["bron"], plan["doel"]):
        bezwaar = rol_blokkade(s)
        if bezwaar and bezwaar not in plan["bezwaren"]:
            plan["bezwaren"].append(bezwaar)
    if bron_id == session.get("speler_id"):
        plan["bezwaren"].append("Je eigen account kan niet verdwijnen terwijl je ermee "
                                "ingelogd bent. Kies het andere als account dat verdwijnt.")

    if request.method == "POST":
        if plan["bezwaren"]:
            for b in plan["bezwaren"]:
                flash(b, "fout")
            return redirect(url_for("spelers_samenvoegen", bron=bron_id, doel=doel_id))
        if not _woord_klopt(SAMENVOEG_WOORD):
            flash(f"Typ “{SAMENVOEG_WOORD}” om te bevestigen. Er is niets veranderd.", "fout")
            return redirect(url_for("spelers_samenvoegen", bron=bron_id, doel=doel_id))
        teamnamen = {p["doel"]["id"]: request.form.get(f"teamnaam_{p['doel']['id']}")
                     for p in plan["team_samenvoegingen"]}
        bron_naam = weergave(plan["bron"])
        resultaat = _voer_samenvoeging_uit(db, lambda: samenvoegen.spelers_samenvoegen(
            db, bron_id, doel_id,
            wachtwoord_van_bron=request.form.get("wachtwoord") != "doel",
            naam=request.form.get("naam"), teamnamen=teamnamen))
        if not resultaat:
            return redirect(url_for("spelers_samenvoegen", bron=bron_id, doel=doel_id))
        extra = ""
        if resultaat["teams"]:
            extra = " Mee samengevoegd: " + ", ".join(f"“{n}”" for n in resultaat["teams"]) + "."
        flash(f"“{bron_naam}” is samengevoegd met #{doel_id}. Er is nu één account, "
              f"“{resultaat['naam']}”, met de volledige historiek van beide.{extra} "
              "Alle Aura is herberekend; vooraf is een back-up gemaakt.", "ok")
        return redirect(url_for("admin_spelers") + "#samenvoegen")

    namen = weergavenamen(db)
    return render_template("admin_samenvoegen.html", soort="spelers", plan=plan,
                           namen_sp=namen, woord=SAMENVOEG_WOORD,
                           teams_doel=teams_van_speler(db, doel_id),
                           wedstrijden_doel=samenvoegen.aantal_wedstrijden(
                               db, [t["id"] for t in teams_van_speler(db, doel_id)]))


@app.route("/admin/samenvoegen/teams", methods=["GET", "POST"])
@login_vereist
def teams_samenvoegen():
    db = get_db()
    bron_id, doel_id = _keuze_id("bron"), _keuze_id("doel")
    terug = redirect(url_for("admin_spelers") + "#samenvoegen")
    if not bron_id or not doel_id:
        flash("Kies welk team blijft en welk verdwijnt.", "fout")
        return terug
    plan = samenvoegen.plan_teams(db, bron_id, doel_id)
    if not plan["bron"] or not plan["doel"]:
        flash("Team niet gevonden.", "fout")
        return terug

    if request.method == "POST":
        if plan["bezwaren"]:
            for b in plan["bezwaren"]:
                flash(b, "fout")
            return redirect(url_for("teams_samenvoegen", bron=bron_id, doel=doel_id))
        if not _woord_klopt(SAMENVOEG_WOORD):
            flash(f"Typ “{SAMENVOEG_WOORD}” om te bevestigen. Er is niets veranderd.", "fout")
            return redirect(url_for("teams_samenvoegen", bron=bron_id, doel=doel_id))
        bron_naam = plan["bron"]["name"]
        resultaat = _voer_samenvoeging_uit(db, lambda: samenvoegen.teams_samenvoegen(
            db, bron_id, doel_id, request.form.get("naam")))
        if not resultaat:
            return redirect(url_for("teams_samenvoegen", bron=bron_id, doel=doel_id))
        flash(f"“{bron_naam}” is opgegaan in “{resultaat['naam']}”, met alle "
              "wedstrijden en inschrijvingen van beide. Alle Aura is herberekend; "
              "vooraf is een back-up gemaakt.", "ok")
        return redirect(url_for("admin_spelers") + "#samenvoegen")

    return render_template("admin_samenvoegen.html", soort="teams", plan=plan,
                           namen_sp=weergavenamen(db), woord=SAMENVOEG_WOORD,
                           wedstrijden_doel=samenvoegen.aantal_wedstrijden(db, [doel_id]),
                           toernooien_doel=[t["name"] for t in
                                            samenvoegen.toernooien_van(db, doel_id).values()])


@app.route("/admin/spelers/<int:speler_id>/wachtwoord", methods=["POST"])
@login_vereist
def speler_wachtwoord_reset(speler_id):
    db = get_db()
    speler = db.execute("SELECT * FROM players WHERE id = ?", (speler_id,)).fetchone()
    nieuw = request.form.get("nieuw", "")
    if not speler:
        flash("Speler niet gevonden.", "fout")
    elif rol_blokkade(speler):
        # Anders kon een organisator het account van een collega overnemen.
        flash(rol_blokkade(speler), "fout")
    elif len(nieuw) < 6:
        flash("Kies een wachtwoord van minstens 6 tekens.", "fout")
    else:
        db.execute("UPDATE players SET password_hash = ? WHERE id = ?",
                   (generate_password_hash(nieuw), speler_id))
        db.commit()
        flash(f"Het wachtwoord van “{weergave(speler)}” is opnieuw ingesteld.", "ok")
    return redirect(url_for("admin_spelers"))


@app.route("/admin/spelers/<int:speler_id>/naam", methods=["POST"])
@login_vereist
def speler_naam_corrigeren(speler_id):
    """Een typfout in de échte naam rechtzetten.

    Spelers kunnen hun eigen naam nooit wijzigen — die hoort bij hun account.
    Een organisator kan wel een verschrijving verbeteren.
    """
    db = get_db()
    speler = db.execute("SELECT * FROM players WHERE id = ?", (speler_id,)).fetchone()
    naam = " ".join((request.form.get("naam") or "").split())
    if not speler:
        flash("Speler niet gevonden.", "fout")
    elif rol_blokkade(speler):
        flash(rol_blokkade(speler), "fout")
    elif len(naam) < 3 or len(naam) > 40:
        flash("Een naam telt 3 tot 40 tekens.", "fout")
    elif naam == speler["name"]:
        flash("Dat is al de huidige naam.", "fout")
    else:
        try:
            db.execute("UPDATE players SET name = ? WHERE id = ?", (naam, speler_id))
            db.commit()
            flash(f"De naam van #{speler_id} is gecorrigeerd naar “{naam}”. "
                  "Let op: die naam is ook zijn login.", "ok")
        except Exception:
            db.rollback()
            flash(f"Er bestaat al een account met de naam “{naam}”.", "fout")
    return redirect(url_for("admin_spelers") + "#spelers")


@app.route("/admin/spelers/<int:speler_id>/dop/terugzetten", methods=["POST"])
@login_vereist
def speler_dop_terugzetten(speler_id):
    """Een kroonkurk terug naar de basisdop zetten (voor de organisatie)."""
    db = get_db()
    speler = db.execute("SELECT * FROM players WHERE id = ?", (speler_id,)).fetchone()
    if speler:
        db.execute("UPDATE players SET dop = NULL WHERE id = ?", (speler_id,))
        db.commit()
        flash(f"De kroonkurk van “{speler['name']}” is terug de basisdop.", "ok")
    return redirect(url_for("admin_spelers") + "#spelers")


@app.route("/admin/spelers/<int:speler_id>/avatar/wissen", methods=["POST"])
@login_vereist
def speler_avatar_wissen(speler_id):
    db = get_db()
    speler = db.execute("SELECT * FROM players WHERE id = ?", (speler_id,)).fetchone()
    if not speler:
        flash("Speler niet gevonden.", "fout")
    elif rol_blokkade(speler):
        flash(rol_blokkade(speler), "fout")
    else:
        wis_avatar(speler["avatar"])
        db.execute("UPDATE players SET avatar = NULL WHERE id = ?", (speler_id,))
        db.commit()
        flash(f"De profielfoto van “{speler['name']}” is verwijderd.", "ok")
    return redirect(url_for("admin_spelers") + "#spelers")


@app.route("/admin/teams/<int:team_id>/avatar/wissen", methods=["POST"])
@login_vereist
def team_avatar_wissen(team_id):
    db = get_db()
    team = db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if not team:
        flash("Team niet gevonden.", "fout")
    else:
        wis_avatar(team["avatar"])
        db.execute("UPDATE teams SET avatar = NULL WHERE id = ?", (team_id,))
        db.commit()
        flash(f"De teamfoto van “{team['name']}” is verwijderd.", "ok")
    return redirect(url_for("admin_spelers") + "#teams")


def _terug_naar_seizoen(db, speeldag_id=None, game=None, anker=""):
    """Het beheerscherm van het seizoen waar deze wedstrijd bij hoort."""
    if game is not None and speeldag_id is None:
        speeldag_id = game["matchday_id"]
    rij = db.execute("SELECT season_id FROM matchdays WHERE id = ?",
                     (speeldag_id,)).fetchone() if speeldag_id else None
    if rij:
        return redirect(url_for("admin_seizoen", seizoen_id=rij["season_id"]) + anker)
    return redirect(url_for("admin_seizoenen"))


@app.route("/admin/wedstrijden/nieuw", methods=["POST"])
@login_vereist
def wedstrijd_nieuw():
    db = get_db()
    try:
        team1_id = int(request.form.get("team1", ""))
        team2_id = int(request.form.get("team2", ""))
        speeldag_id = int(request.form.get("speeldag", ""))
    except ValueError:
        flash("Kies twee teams en een speeldag.", "fout")
        return redirect(url_for("admin_seizoenen"))
    if team1_id == team2_id:
        flash("Kies twee verschillende teams.", "fout")
        return _terug_naar_seizoen(db, speeldag_id)

    team1 = db.execute("SELECT * FROM teams WHERE id = ? AND status = 'actief'",
                       (team1_id,)).fetchone()
    team2 = db.execute("SELECT * FROM teams WHERE id = ? AND status = 'actief'",
                       (team2_id,)).fetchone()
    speeldag = db.execute("SELECT * FROM matchdays WHERE id = ?",
                          (speeldag_id,)).fetchone()
    if not team1 or not team2 or not speeldag:
        flash("Kies twee actieve teams en een bestaande speeldag.", "fout")
        return _terug_naar_seizoen(db, speeldag_id)

    spelers = {team1["player1_id"], team1["player2_id"],
               team2["player1_id"], team2["player2_id"]}
    if len(spelers) != 4:
        flash("Deze teams delen een speler en kunnen niet tegen elkaar spelen.", "fout")
        return _terug_naar_seizoen(db, speeldag_id)

    moment = request.form.get("moment") or f'{speeldag["date"]}T20:00'
    db.execute("INSERT INTO games (team1_id, team2_id, matchday_id, scheduled_at) "
               "VALUES (?, ?, ?, ?)", (team1_id, team2_id, speeldag_id, moment))
    db.commit()
    flash("Wedstrijd ingepland.", "ok")
    return _terug_naar_seizoen(db, speeldag_id, anker="#openstaand")


@app.route("/admin/wedstrijden/<int:game_id>/resultaat", methods=["POST"])
@login_vereist
def wedstrijd_resultaat(game_id):
    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game or game["status"] != "gepland":
        flash("Deze wedstrijd bestaat niet of heeft al een resultaat.", "fout")
        return redirect(url_for("admin_seizoenen"))

    winnaar = request.form.get("winnaar")
    if winnaar not in ("1", "2"):
        flash("Duid de winnaar aan.", "fout")
        return _terug_naar_seizoen(db, game=game, anker="#openstaand")
    winnaar_team = game["team1_id"] if winnaar == "1" else game["team2_id"]
    gespeeld_op = request.form.get("gespeeld_op") or datetime.now().strftime("%Y-%m-%dT%H:%M")

    db.execute("UPDATE games SET status = 'gespeeld', winner_team_id = ?, played_at = ? "
               "WHERE id = ?", (winnaar_team, gespeeld_op, game_id))

    for veld, waarde in request.form.items():
        if not veld.startswith("stat_") or not waarde.strip():
            continue
        try:
            _, stat_id, speler_id = veld.split("_")
            db.execute("INSERT OR REPLACE INTO game_stats (game_id, player_id, "
                       "stat_type_id, value) VALUES (?, ?, ?, ?)",
                       (game_id, int(speler_id), int(stat_id), float(waarde.replace(",", "."))))
        except ValueError:
            continue

    db.commit()
    for melding in na_resultaat(db, game_id):
        flash(melding, "ok")
    flash("Resultaat opgeslagen. De Aura-ratings en klassementen zijn bijgewerkt.", "ok")
    if game["tournament_id"]:
        return redirect(url_for("toernooi_beheer", toernooi_id=game["tournament_id"])
                        + "#openstaand")
    return _terug_naar_seizoen(db, game=game, anker="#openstaand")


@app.route("/admin/wedstrijden/<int:game_id>/verwijderen", methods=["POST"])
@login_vereist
def wedstrijd_verwijderen(game_id):
    db = get_db()
    game = db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    if not game:
        flash("Wedstrijd niet gevonden.", "fout")
        return redirect(url_for("admin_seizoenen"))
    was_gespeeld = game["status"] == "gespeeld"
    speeldag_id = game["matchday_id"]
    toernooi_id = game["tournament_id"]

    if toernooi_id:
        # Toernooiwedstrijden horen bij het schema: we verwijderen ze nooit, we
        # wissen enkel de uitslag zodat ze opnieuw gespeeld kunnen worden.
        terug = url_for("toernooi_beheer", toernooi_id=toernooi_id) + "#openstaand"
        if not was_gespeeld:
            flash("Deze wedstrijd hoort bij het toernooischema en kan niet los "
                  "verwijderd worden. Gebruik “Loting ongedaan maken” als je het "
                  "hele programma opnieuw wil trekken.", "fout")
            return redirect(terug)

        reden = toernooi_motor.mag_wissen(db, game)
        if reden:
            flash(reden, "fout")
            return redirect(terug)

        db.execute("UPDATE games SET status = 'gepland', winner_team_id = NULL, "
                   "played_at = NULL WHERE id = ?", (game_id,))
        db.execute("DELETE FROM game_reports WHERE game_id = ?", (game_id,))
        db.commit()
        # Ruim op wat op deze uitslag voortbouwde (doorgeschoven winnaar, of het
        # hele knockoutschema bij een bracket- of shootoutuitslag).
        toernooi_motor.herstel_na_wissen(db, toernooi_id, game["fase"], game)
        for melding in na_resultaat(db):
            flash(melding, "ok")
        flash("Het resultaat is gewist; de wedstrijd staat weer open." +
              (" Het knockoutschema wordt opnieuw bepaald zodra de stand vastligt."
               if game["fase"] in ("bracket", "shootout") else ""), "ok")
        return redirect(terug)

    db.execute("DELETE FROM games WHERE id = ?", (game_id,))
    db.commit()
    if was_gespeeld:
        na_resultaat(db)
        flash("Wedstrijd verwijderd en alle ratings herberekend.", "ok")
    else:
        flash("Geplande wedstrijd verwijderd.", "ok")
    return _terug_naar_seizoen(db, speeldag_id)


@app.route("/admin/stats/nieuw", methods=["POST"])
@login_vereist
def stat_nieuw():
    naam = (request.form.get("naam") or "").strip()
    eenheid = (request.form.get("eenheid") or "").strip()
    if not naam:
        flash("Geef de statistiek een naam.", "fout")
        return redirect(url_for("admin_klassement") + "#statistieken")
    db = get_db()
    try:
        db.execute("INSERT INTO stat_types (name, unit) VALUES (?, ?)", (naam, eenheid))
        db.commit()
        flash(f"Statistiek “{naam}” aangemaakt. Ze verschijnt vanaf nu op elk "
              "resultaatformulier en op de statistiekenpagina.", "ok")
    except Exception:
        flash(f"Er bestaat al een statistiek met de naam “{naam}”.", "fout")
    return redirect(url_for("admin_klassement") + "#statistieken")


@app.route("/admin/stats/<int:stat_id>/toggle", methods=["POST"])
@login_vereist
def stat_toggle(stat_id):
    db = get_db()
    db.execute("UPDATE stat_types SET active = 1 - active WHERE id = ?", (stat_id,))
    db.commit()
    flash("Statistiek bijgewerkt.", "ok")
    return redirect(url_for("admin_klassement") + "#statistieken")


@app.route("/admin/rangen/nieuw", methods=["POST"])
@login_vereist
def rang_nieuw():
    naam = (request.form.get("naam") or "").strip()
    kleur = request.form.get("kleur") or "#64748b"
    try:
        min_elo = float(request.form.get("min_elo", ""))
        max_elo = float(request.form.get("max_elo", ""))
    except ValueError:
        flash("Vul geldige Aura-grenzen in.", "fout")
        return redirect(url_for("admin_klassement") + "#rangen")
    if not naam or min_elo >= max_elo:
        flash("Geef een naam op en zorg dat de ondergrens lager is dan de bovengrens.", "fout")
        return redirect(url_for("admin_klassement") + "#rangen")
    db = get_db()
    try:
        db.execute("INSERT INTO ranks (name, min_elo, max_elo, color) VALUES (?, ?, ?, ?)",
                   (naam, min_elo, max_elo, kleur))
        db.commit()
        flash(f"Rang “{naam}” toegevoegd ({min_elo:.0f} tot {max_elo:.0f} Aura).", "ok")
    except Exception:
        flash(f"Er bestaat al een rang met de naam “{naam}”.", "fout")
    return redirect(url_for("admin_klassement") + "#rangen")


@app.route("/admin/rangen/<int:rang_id>/verwijderen", methods=["POST"])
@login_vereist
def rang_verwijderen(rang_id):
    db = get_db()
    db.execute("DELETE FROM ranks WHERE id = ?", (rang_id,))
    db.commit()
    flash("Rang verwijderd.", "ok")
    return redirect(url_for("admin_klassement") + "#rangen")


@app.route("/admin/instellingen", methods=["POST"])
@login_vereist
@eigenaar_vereist
def instellingen_opslaan():
    db = get_db()
    try:
        k_s = float(request.form.get("k_speler", "32"))
        k_t = float(request.form.get("k_team", "32"))
        if not (1 <= k_s <= 200 and 1 <= k_t <= 200):
            raise ValueError
    except ValueError:
        flash("K-factoren moeten getallen tussen 1 en 200 zijn.", "fout")
        return redirect(url_for("admin_instellingen"))
    zet_instelling(db, "k_speler", k_s)
    zet_instelling(db, "k_team", k_t)
    db.commit()
    herbereken_alles(db)
    flash("K-factoren opgeslagen en alle ratings herberekend.", "ok")
    return redirect(url_for("admin_instellingen"))


# ----------------------------------------------------------------- back-up --
#
# De hele competitie zit in één bestand (shuss.db). Een kopie maken kan dus
# altijd — maar níét met een gewone bestandskopie: als er op dat moment
# geschreven wordt, krijg je een halve database. De backup-API van SQLite maakt
# wél een samenhangende kopie, ook terwijl de site draait.

BACKUPS_BEWAREN = 20


def maak_backup(reden="handmatig"):
    """Schrijf een samenhangende kopie van de database naar backups/."""
    os.makedirs(BACKUP_MAP, exist_ok=True)
    naam = f"shuss_{datetime.now():%Y%m%d_%H%M%S}_{reden}.db"
    pad = os.path.join(BACKUP_MAP, naam)
    bron = verbind(DB_PATH)
    doel = sqlite3.connect(pad)
    with doel:
        bron.backup(doel)
    doel.close()
    bron.close()
    _ruim_backups_op()
    return pad


def _ruim_backups_op():
    """Hou enkel de nieuwste back-ups bij, zodat de map niet blijft groeien."""
    if not os.path.isdir(BACKUP_MAP):
        return
    bestanden = sorted(
        (os.path.join(BACKUP_MAP, n) for n in os.listdir(BACKUP_MAP)
         if n.startswith("shuss_") and n.endswith(".db")),
        key=os.path.getmtime, reverse=True)
    for oud in bestanden[BACKUPS_BEWAREN:]:
        try:
            os.remove(oud)
        except OSError:
            pass


def backup_lijst():
    if not os.path.isdir(BACKUP_MAP):
        return []
    uit = []
    for naam in os.listdir(BACKUP_MAP):
        if naam.startswith("shuss_") and naam.endswith(".db"):
            pad = os.path.join(BACKUP_MAP, naam)
            uit.append({"naam": naam, "grootte": os.path.getsize(pad),
                        "moment": datetime.fromtimestamp(os.path.getmtime(pad))})
    return sorted(uit, key=lambda b: b["moment"], reverse=True)


@app.route("/admin/backup/nu", methods=["POST"])
@login_vereist
@eigenaar_vereist
def backup_nu():
    try:
        pad = maak_backup("handmatig")
        flash(f"Back-up gemaakt: {os.path.basename(pad)}. Ze staat in de map "
              "“backups” naast de database.", "ok")
    except Exception as fout:
        flash(f"De back-up is niet gelukt: {fout}", "fout")
    return redirect(url_for("admin_instellingen") + "#backup")


@app.route("/admin/backup/download")
@login_vereist
@eigenaar_vereist
def backup_download():
    """Een verse kopie van de database rechtstreeks downloaden."""
    pad = maak_backup("download")
    return send_from_directory(BACKUP_MAP, os.path.basename(pad),
                               as_attachment=True,
                               download_name=f"shuss_{date.today():%Y-%m-%d}.db")


@app.route("/admin/league", methods=["POST"])
@login_vereist
@eigenaar_vereist
def league_schakelen():
    """Het leaguegedeelte tonen of verbergen voor de spelers."""
    db = get_db()
    aan = request.form.get("league_actief") == "1"
    zet_instelling(db, "league_actief", "1" if aan else "0")
    db.commit()
    if aan:
        flash("Het leaguegedeelte staat aan: iedereen ziet nu het klassement, de "
              "wedstrijden, de statistieken en de seizoenen.", "ok")
    else:
        flash("Het leaguegedeelte staat uit: spelers zien enkel nog het toernooi. "
              "Jij houdt als organisator toegang tot de leaguepagina's.", "ok")
    return redirect(url_for("admin_instellingen") + "#league")


@app.route("/admin/claimvenster", methods=["POST"])
@login_vereist
def claimvenster_schakelen():
    """Het opeisen van accounts open- of dichtzetten."""
    db = get_db()
    aan = request.form.get("claim_open") == "1"
    zet_instelling(db, "claim_open", "1" if aan else "0")
    db.commit()
    if aan:
        vrij = len(vrije_accounts(db))
        flash(f"Accounts opeisen staat open: {vrij} account(s) wachten nog op een "
              "wachtwoord. Zet het weer dicht zodra iedereen binnen is.", "ok")
    else:
        flash("Accounts opeisen staat weer dicht.", "ok")
    return redirect(url_for("admin_spelers") + "#claimen")


@app.route("/admin/claimen/scherm")
@login_vereist
def claim_scherm():
    """Groot scherm voor op de beamer: QR-code naar de opeispagina.

    Ververst zichzelf, zodat je live ziet hoeveel accounts er nog vrij zijn.
    """
    db = get_db()
    adres = externe_url("claimen")
    vrij = vrije_accounts(db)
    geclaimd = db.execute("SELECT COUNT(*) AS n FROM players "
                          "WHERE password_hash IS NOT NULL").fetchone()["n"]
    return render_template("claim_scherm.html", adres=adres, qr=qr_svg(adres),
                           vrij=vrij, geclaimd=geclaimd, open=claim_open())


@app.route("/admin/spelers/<int:speler_id>/vrijgeven", methods=["POST"])
@login_vereist
def speler_vrijgeven(speler_id):
    """Het wachtwoord wissen zodat het account opnieuw opgeëist kan worden.

    Handig als iemand per ongeluk (of ten onrechte) het verkeerde account nam.
    """
    db = get_db()
    speler = db.execute("SELECT * FROM players WHERE id = ?", (speler_id,)).fetchone()
    if not speler:
        flash("Speler niet gevonden.", "fout")
    elif rol_blokkade(speler):
        flash(rol_blokkade(speler), "fout")
    elif not speler["password_hash"]:
        flash(f"“{weergave(speler)}” heeft nog geen wachtwoord.", "fout")
    else:
        db.execute("UPDATE players SET password_hash = NULL WHERE id = ?", (speler_id,))
        _log_claim(db, speler, "vrijgegeven")
        db.commit()
        flash(f"Het wachtwoord van “{weergave(speler)}” is gewist. Het account staat "
              "weer vrij om opgeëist te worden.", "ok")
    return redirect(url_for("admin_spelers") + "#claimen")


# --------------------------------------------- database legen en terugzetten --
#
# Twee zware ingrepen, allebei enkel voor de eigenaar en allebei met een woord
# dat je zelf moet intypen. Vóór elke ingreep gaat er automatisch een back-up
# naar de map backups/, zodat een vergissing nooit definitief is.

# Tabellen die enkel over gespeelde geschiedenis gaan (spelers en teams blijven).
GESCHIEDENIS_TABELLEN = ["game_stats", "game_reports", "rating_history",
                         "season_ratings", "games", "tournament_teams",
                         "tournament_locations", "tournaments", "matchdays",
                         "seasons"]
# Daarbovenop bij "alles wissen":
ALLES_TABELLEN = GESCHIEDENIS_TABELLEN + ["claim_log", "teams", "players"]


def _woord_klopt(verwacht):
    return " ".join((request.form.get("bevestiging") or "").split()).lower() == verwacht


@app.route("/admin/database/legen", methods=["POST"])
@login_vereist
@eigenaar_vereist
def database_legen():
    db = get_db()
    alles = request.form.get("omvang") == "alles"
    woord = "wis alles" if alles else "wis geschiedenis"
    if not _woord_klopt(woord):
        flash(f"Typ “{woord}” in het vakje om te bevestigen. Er is niets gewist.", "fout")
        return redirect(url_for("admin_instellingen") + "#leegmaken")

    try:
        kopie = maak_backup("voor-legen")
    except Exception as fout:
        flash(f"De veiligheidsback-up lukte niet ({fout}); er is niets gewist.", "fout")
        return redirect(url_for("admin_instellingen") + "#leegmaken")

    tabellen = ALLES_TABELLEN if alles else GESCHIEDENIS_TABELLEN
    db.execute("PRAGMA foreign_keys = OFF")
    for tabel in tabellen:
        db.execute(f"DELETE FROM {tabel}")
    if not alles:
        # Spelers en teams blijven, maar beginnen weer van nul.
        db.execute("UPDATE players SET elo = ?", (START_ELO,))
        db.execute("UPDATE teams SET elo = ?", (START_ELO,))
    db.execute("DELETE FROM sqlite_sequence")
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
    for map_ in (UPLOAD_MAP,) if alles else ():
        for naam in os.listdir(map_) if os.path.isdir(map_) else []:
            if _AVATAR_RE.match(naam):
                try:
                    os.remove(os.path.join(map_, naam))
                except OSError:
                    pass
    if alles:
        db.execute("UPDATE settings SET value = '0' WHERE key = 'claim_open'")
        db.commit()
        session.clear()
        flash("De database is volledig leeggemaakt. Maak nu een nieuw account aan. "
              "Het eerste account wordt automatisch de eigenaar. Een back-up van de "
              f"oude stand staat in backups/{os.path.basename(kopie)}.", "ok")
        return redirect(url_for("registreren"))

    flash("Alle wedstrijden, toernooien en seizoenen zijn gewist; spelers en teams "
          f"blijven en staan weer op {START_ELO:.0f} Aura. Een back-up van de oude "
          f"stand staat in backups/{os.path.basename(kopie)}.", "ok")
    return redirect(url_for("admin_instellingen") + "#leegmaken")


def _is_geldige_database(pad):
    """Is dit echt een Leberschuss-database? Geeft None terug als ze deugt."""
    try:
        with open(pad, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                return "Dit is geen databasebestand (.db) van deze site."
        keur = sqlite3.connect(f"file:{pad}?mode=ro", uri=True)
        if keur.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            keur.close()
            return "Het bestand is beschadigd."
        aanwezig = {r[0] for r in keur.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        keur.close()
        ontbreekt = {"players", "teams", "games", "settings"} - aanwezig
        if ontbreekt:
            return ("Dit lijkt geen Leberschuss-database: "
                    f"{', '.join(sorted(ontbreekt))} ontbreekt.")
    except Exception as fout:
        return f"Het bestand kon niet gelezen worden ({fout})."
    return None


@app.route("/admin/backup/terugzetten", methods=["POST"])
@login_vereist
@eigenaar_vereist
def backup_terugzetten():
    if not _woord_klopt("terugzetten"):
        flash("Typ “terugzetten” in het vakje om te bevestigen. Er is niets "
              "gewijzigd.", "fout")
        return redirect(url_for("admin_instellingen") + "#terugzetten")
    bestand = request.files.get("bestand")
    if not bestand or not bestand.filename:
        flash("Kies eerst een back-upbestand.", "fout")
        return redirect(url_for("admin_instellingen") + "#terugzetten")

    os.makedirs(BACKUP_MAP, exist_ok=True)
    tijdelijk = os.path.join(BACKUP_MAP, f"upload_{secrets.token_hex(4)}.db")
    bestand.save(tijdelijk)

    bezwaar = _is_geldige_database(tijdelijk)
    if bezwaar:
        os.remove(tijdelijk)
        flash(f"{bezwaar} Er is niets gewijzigd.", "fout")
        return redirect(url_for("admin_instellingen") + "#terugzetten")

    try:
        kopie = maak_backup("voor-terugzetten")
    except Exception as fout:
        os.remove(tijdelijk)
        flash(f"De veiligheidsback-up lukte niet ({fout}); er is niets gewijzigd.", "fout")
        return redirect(url_for("admin_instellingen") + "#terugzetten")

    # De verbinding van dit verzoek eerst sluiten, anders houdt ze het oude
    # bestand nog vast terwijl we het vervangen.
    oud = g.pop("db", None)
    if oud is not None:
        oud.close()
    os.replace(tijdelijk, DB_PATH)
    # Resten van de vorige database: die horen niet bij het nieuwe bestand.
    for extra in (DB_PATH + "-wal", DB_PATH + "-shm"):
        if os.path.exists(extra):
            os.remove(extra)
    init_db(DB_PATH)                      # schema bijwerken als de kopie ouder is

    db = get_db()
    aantal = db.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"]
    ik = db.execute("SELECT 1 FROM players WHERE id = ?",
                    (session.get("speler_id"),)).fetchone()
    if not ik:
        session.clear()
        flash("De database is vervangen. Jouw account staat niet in dit bestand, "
              "dus je bent uitgelogd. Een back-up van de vorige stand staat in "
              f"backups/{os.path.basename(kopie)}.", "ok")
        return redirect(url_for("inloggen"))
    flash(f"De database is vervangen: {aantal} speler(s) ingeladen. Een back-up van "
          f"de vorige stand staat in backups/{os.path.basename(kopie)}.", "ok")
    return redirect(url_for("admin_instellingen") + "#terugzetten")


@app.route("/mijn-wachtwoord", methods=["POST"])
@speler_vereist
def wachtwoord_wijzigen():
    """Je eigen wachtwoord wijzigen — het huidige wachtwoord is vereist."""
    db = get_db()
    ik = huidige_speler()
    huidig = request.form.get("huidig", "")
    nieuw = request.form.get("nieuw", "")
    terug = redirect(url_for("speler_profiel", speler_id=ik["id"]))
    if not ik["password_hash"] or not check_password_hash(ik["password_hash"], huidig):
        flash("Je huidige wachtwoord klopt niet.", "fout")
    elif len(nieuw) < 6:
        flash("Kies een nieuw wachtwoord van minstens 6 tekens.", "fout")
    elif nieuw != request.form.get("herhaal", ""):
        flash("De twee nieuwe wachtwoorden zijn niet hetzelfde.", "fout")
    else:
        db.execute("UPDATE players SET password_hash = ? WHERE id = ?",
                   (generate_password_hash(nieuw), ik["id"]))
        db.commit()
        flash("Je wachtwoord is gewijzigd.", "ok")
    return terug


@app.route("/admin/herberekenen", methods=["POST"])
@login_vereist
@eigenaar_vereist
def herberekenen():
    db = get_db()
    for melding in na_resultaat(db):
        flash(melding, "ok")
    flash("Alle ratings zijn opnieuw berekend.", "ok")
    return redirect(url_for("admin_instellingen"))


# ---------------------------------------------------------------- toernooi --

def _toernooi_of_404(db, toernooi_id):
    t = toernooi_motor.toernooi(db, toernooi_id)
    if not t:
        abort(404)
    # Een knockout die al liep vóór de kleine finale bestond, krijgt er hier
    # alsnog een (doet niets als hij er al is of niet meer kan).
    if t["status"] == "knockout" and toernooi_motor.kleine_finale(db, toernooi_id) is None:
        toernooi_motor.evalueer(db, toernooi_id)
    return t


TOERNOOI_STATUS = {
    "opzet": ("Inschrijvingen open", "Schrijf je team in. Bij de loting begint het toernooi."),
    "bracket": ("Bracketfase", "Alle teams spelen in één grote bracket."),
    "knockout": ("Knockout", "De besten strijden om de beker."),
    "afgelopen": ("Afgelopen", "Dit toernooi zit erop."),
}


@app.route("/toernooien")
def toernooien():
    db = get_db()
    lijst = []
    for t in db.execute("SELECT * FROM tournaments ORDER BY date DESC, id DESC"):
        aantal = len(toernooi_motor.deelnemers(db, t["id"], ook_teruggetrokken=True))
        gespeeld = db.execute("SELECT COUNT(*) AS n FROM games WHERE tournament_id = ? "
                              "AND status = 'gespeeld'", (t["id"],)).fetchone()["n"]
        totaal = db.execute("SELECT COUNT(*) AS n FROM games WHERE tournament_id = ?",
                            (t["id"],)).fetchone()["n"]
        winnaar = None
        if t["status"] == "afgelopen":
            f = db.execute("SELECT winner_team_id FROM games WHERE tournament_id = ? "
                           "AND fase = 'knockout' AND ronde = 2", (t["id"],)).fetchone()
            if f and f["winner_team_id"]:
                winnaar = db.execute("SELECT id, name FROM teams WHERE id = ?",
                                     (f["winner_team_id"],)).fetchone()
        lijst.append({"t": t, "teams": aantal, "gespeeld": gespeeld, "totaal": totaal,
                      "label": TOERNOOI_STATUS.get(t["status"], ("", ""))[0],
                      "uitleg": TOERNOOI_STATUS.get(t["status"], ("", ""))[1],
                      "winnaar": winnaar})
    return render_template("toernooien.html", lijst=lijst)


@app.route("/toernooi/<int:toernooi_id>")
def toernooi_detail(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    # Nog niet geloot? Dan is de inschrijfpagina dé pagina van dit toernooi:
    # één scherm voor alles, van waar je ook komt (menu, organisatie, beamer...).
    if t["status"] == "opzet":
        return redirect(url_for("toernooi_inschrijven", toernooi_id=toernooi_id))
    teams = toernooi_motor.deelnemers(db, toernooi_id)
    groepen, namen, loc_namen = toernooi_motor.kalender(db, toernooi_id)
    kolommen = toernooi_motor.knockout_kolommen(db, toernooi_id)
    rangschikking, beslissend = ([], [])
    if t["status"] != "opzet":
        rangschikking, beslissend = toernooi_motor.stand(db, toernooi_id)

    deltas = {}
    for r in db.execute("""
        SELECT rh.game_id, rh.entity_id, rh.elo_voor, rh.elo_na
        FROM rating_history rh
        JOIN games g ON g.id = rh.game_id
        WHERE rh.entity_type = 'team' AND rh.scope = 'permanent' AND g.tournament_id = ?
    """, (toernooi_id,)):
        deltas[(r["game_id"], r["entity_id"])] = r["elo_na"] - r["elo_voor"]

    open_shootouts = db.execute(
        "SELECT COUNT(*) AS n FROM games WHERE tournament_id = ? AND fase = 'shootout' "
        "AND status = 'gepland'", (toernooi_id,)).fetchone()["n"]

    winnaar = None
    finale = db.execute("SELECT * FROM games WHERE tournament_id = ? AND "
                        "fase = 'knockout' AND ronde = 2", (toernooi_id,)).fetchone()
    if finale and finale["winner_team_id"]:
        winnaar = db.execute("SELECT * FROM teams WHERE id = ?",
                             (finale["winner_team_id"],)).fetchone()

    # Eigen wedstrijden van de ingelogde speler binnen dit toernooi.
    mijn_wedstrijden = []
    if session.get("speler_id"):
        mijn_wedstrijden = speler_wedstrijd_meldingen(db, session["speler_id"],
                                                      toernooi_id=toernooi_id)
    mijn_gespeeld = []
    if session.get("speler_id"):
        mijn_gespeeld = db.execute("""
            SELECT g.*, t1.player1_id AS a1, t1.player2_id AS a2
            FROM games g
            JOIN teams t1 ON t1.id = g.team1_id
            JOIN teams t2 ON t2.id = g.team2_id
            WHERE g.tournament_id = ? AND g.status = 'gespeeld'
              AND ? IN (t1.player1_id, t1.player2_id, t2.player1_id, t2.player2_id)
            ORDER BY g.played_at DESC, g.id DESC
        """, (toernooi_id, session["speler_id"])).fetchall()
    actieve_types = db.execute("SELECT * FROM stat_types WHERE active = 1 "
                               "ORDER BY name").fetchall()

    mijn_coupon = 0                     # aantal geldige coupons van wie kijkt
    if session.get("speler_id") and t["status"] != "opzet":
        mijn_coupon = sum(1 for v in uitgenodigd_door_speler(db, toernooi_id, session["speler_id"])
                          if coupon_staat(db, toernooi_id, v["team_id"])["staat"] == "geldig")
    return render_template("toernooi.html", t=t, huidig_toernooi=t, teams=teams,
                           mijn_coupon=mijn_coupon,
                           groepen=groepen,
                           mijn_wedstrijden=mijn_wedstrijden,
                           mijn_gespeeld=mijn_gespeeld,
                           actieve_types=actieve_types,
                           namen=namen, loc_namen=loc_namen, kolommen=kolommen,
                           stand=rangschikking, beslissend=beslissend, deltas=deltas,
                           tiebreaks=(toernooi_motor.tiebreak_groepen(db, toernooi_id)
                                      if t["status"] != "opzet" else []),
                           winnaar=winnaar, open_shootouts=open_shootouts,
                           label=TOERNOOI_STATUS.get(t["status"], ("", ""))[0],
                           potten_aantal=toernooi_motor.potten_aantal(db, toernooi_id),
                           heeft_loting=t["status"] != "opzet")


# ------------------------------------------------------------- inschrijven --
#
# Een openbare pagina per toernooi waarmee spelers hun team vooraf inschrijven.
# Bedoeld om rond te sturen (affiche, QR-code), dus zo eenvoudig mogelijk:
#   * niet ingelogd: account maken of inloggen, en je komt hier terug;
#   * al een team met je ploegmaat: dat team aanduiden en klaar;
#   * nog geen team: naam van je ploegmaat intikken. Bestaat die speler al, dan
#     nemen we die; anders maken we een account op die naam aan, dat de echte
#     persoon overneemt door zich met precies die naam te registreren.
# Eén van de twee spelers schrijft in; elk van beiden kan het team ook weer
# uitschrijven, zolang er nog niet geloot is. Met een maximum komen de laatste
# inschrijvers op een wachtlijst, die vanzelf opschuift.

def _inschrijvingen(db, tid):
    """Alle ingeschreven teams in volgorde van inschrijven, met wachtlijstplaats."""
    wacht = toernooi_motor.wachtlijst(db, tid)
    namen_sp = weergavenamen(db)
    uit = []
    for r in db.execute("""
        SELECT tt.team_id, tt.ingeschreven_door, t.name, t.player1_id, t.player2_id, t.avatar
        FROM tournament_teams tt JOIN teams t ON t.id = tt.team_id
        WHERE tt.tournament_id = ? AND tt.teruggetrokken = 0 ORDER BY tt.id
    """, (tid,)):
        uit.append({"id": r["team_id"], "naam": r["name"], "avatar": r["avatar"],
                    "spelers": [r["player1_id"], r["player2_id"]],
                    "spelernamen": " & ".join(namen_sp.get(p, "?") for p in
                                              (r["player1_id"], r["player2_id"]) if p),
                    "wachtlijst": (wacht.index(r["team_id"]) + 1) if r["team_id"] in wacht else None})
    return uit


def _inschrijf_boodschap(db, tid, team_id):
    wacht = toernooi_motor.wachtlijst(db, tid)
    if team_id in wacht:
        return f"op de wachtlijst (plaats {wacht.index(team_id) + 1})"
    return None



# ------------------------------------------------- uitnodigen en coupons --
#
# Wie ingeschreven is, krijgt een eigen uitnodigingslink voor dat toernooi.
# Schrijft een ander team zich via die link in en blijft het ingeschreven tot
# de loting, dan krijgt zijn team een coupon: de eerste 2 pintjes gratis.
# Eén coupon per team per toernooi: zodra er via de link van een van de twee
# spelers een team binnenkomt, kan het team niemand meer uitnodigen. De coupon is een QR-code op zijn profiel; een organisator scant hem
# aan de bar (zie /admin/scan) en daarmee is hij ingelost en verdwenen.
#
# Of iemand een coupon heeft, wordt telkens berekend uit de inschrijvingen: zo
# klopt het vanzelf als een vriend zich uitschrijft. Enkel het inlossen wordt
# bewaard (coupon_inlossingen).

COUPON_PINTJES = 2


def _handtekening(*delen):
    """Een HMAC met de geheime sleutel: niet te raden, wel na te rekenen."""
    return hmac.new(app.secret_key.encode(), ":".join(map(str, delen)).encode(),
                    hashlib.sha256).hexdigest()


def uitnodigcode(toernooi_id, speler_id):
    return f"{speler_id}-{_handtekening('uitnodiging', toernooi_id, speler_id)[:6]}"


def uitnodiger_van_code(toernooi_id, code):
    """De speler achter een uitnodigingscode, of None als ze niet klopt."""
    try:
        speler_id = int((code or "").split("-")[0])
    except ValueError:
        return None
    return speler_id if hmac.compare_digest(code, uitnodigcode(toernooi_id, speler_id)) else None


def coupon_sleutel(toernooi_id, vriend_team_id, houder_id):
    """De sleutel in de QR-code. Hoort bij dit uitgenodigde team én bij wie de
    coupon heeft: komt het team later via iemand anders binnen, dan werkt een
    oude schermafdruk van de eerste uitnodiger niet meer."""
    return _handtekening("coupon", toernooi_id, vriend_team_id, houder_id)[:20]


def uitnodig_limiet(t):
    """Hoeveel teams mag één speler uitnodigen? None = geen limiet."""
    n = t["uitnodigingen_per_speler"] if t and "uitnodigingen_per_speler" in t.keys() else 1
    return None if n == 0 else (n if n is not None else 1)


def team_in_toernooi(db, toernooi_id, speler_id):
    """Met welk team speelt deze speler in dit toernooi? (één per toernooi)"""
    return db.execute("""
        SELECT tt.team_id, tt.teruggetrokken, t.name, t.player1_id, t.player2_id
        FROM tournament_teams tt JOIN teams t ON t.id = tt.team_id
        WHERE tt.tournament_id = ? AND (t.player1_id = ? OR t.player2_id = ?)
    """, (toernooi_id, speler_id, speler_id)).fetchone()


def uitgenodigd_door_speler(db, toernooi_id, speler_id):
    """De teams die via de link van deze speler binnenkwamen, in volgorde."""
    return db.execute("""
        SELECT tt.team_id, tt.teruggetrokken, t.name FROM tournament_teams tt
        JOIN teams t ON t.id = tt.team_id
        WHERE tt.tournament_id = ? AND tt.uitgenodigd_door = ?
          AND t.player1_id != ? AND COALESCE(t.player2_id, 0) != ?
        ORDER BY tt.id""", (toernooi_id, speler_id, speler_id, speler_id)).fetchall()


def coupon_staat(db, toernooi_id, vriend_team_id):
    """Hoe staat het met de coupon voor dit uitgenodigde team?

    Elke speler verdient per team dat via zijn link binnenkomt één coupon
    (2 pintjes), tot aan de limiet van het toernooi. De coupon wordt geldig bij
    de loting: vóór de start kijkt de organisatie na wie er echt is en haalt wie
    er niet is uit het toernooi. Wie later niet opdaagt, wordt teruggetrokken.

    staat: 'geen'      geen coupon (niemand nodigde dit team uit, of boven de limiet)
           'wacht'     ingeschreven, maar er is nog niet geloot
           'geldig'    geloot, beide teams doen mee: in te lossen aan de bar
           'ingelost'  al ingelost
           'vervallen' het uitgenodigde team of de uitnodiger zelf doet niet (meer) mee
    """
    t = toernooi_motor.toernooi(db, toernooi_id)
    uit = {"staat": "geen", "toernooi": t, "vriend_team_id": vriend_team_id, "vriend": None,
           "houder_id": None, "houder": None, "team": None, "spelers": "", "wachtlijst": False,
           "sleutel": None}
    if not t:
        return uit
    namen = weergavenamen(db)
    vriend = db.execute("""
        SELECT tt.uitgenodigd_door, tt.teruggetrokken, t.name FROM tournament_teams tt
        JOIN teams t ON t.id = tt.team_id WHERE tt.tournament_id = ? AND tt.team_id = ?""",
        (toernooi_id, vriend_team_id)).fetchone()
    ingelost = db.execute(f"""
        SELECT ci.ingelost_op, ci.speler_id, {WEERGAVE.replace('p.', 'd.')} AS door
        FROM coupon_inlossingen ci LEFT JOIN players d ON d.id = ci.ingelost_door
        WHERE ci.tournament_id = ? AND ci.vriend_team_id = ?""", (toernooi_id, vriend_team_id)).fetchone()
    houder = vriend["uitgenodigd_door"] if vriend else (ingelost["speler_id"] if ingelost else None)
    if vriend:
        uit["vriend"] = vriend["name"]
    if houder:
        uit["houder_id"], uit["houder"] = houder, namen.get(houder)
        uit["sleutel"] = coupon_sleutel(toernooi_id, vriend_team_id, houder)
        eigen = team_in_toernooi(db, toernooi_id, houder)
        if eigen:
            uit["team"] = eigen["name"]
            uit["spelers"] = " & ".join(namen.get(p, "?") for p in (eigen["player1_id"], eigen["player2_id"]) if p)
    if ingelost:
        uit.update(staat="ingelost", ingelost_op=ingelost["ingelost_op"], ingelost_door=ingelost["door"])
        return uit
    if not vriend or not houder:
        return uit
    # Binnen de limiet van de uitnodiger? (de eerste N teams via zijn link)
    # Een team dat niet kwam opdagen, vervalt meteen en neemt geen plaats in:
    # dan schuift het volgende team dat via deze speler kwam op.
    if vriend["teruggetrokken"]:
        uit["staat"] = "vervallen"
        return uit
    limiet = uitnodig_limiet(t)
    volgorde = [r["team_id"] for r in uitgenodigd_door_speler(db, toernooi_id, houder)
                if not r["teruggetrokken"]]
    if vriend_team_id not in volgorde or (limiet is not None and volgorde.index(vriend_team_id) >= limiet):
        return uit
    eigen = team_in_toernooi(db, toernooi_id, houder)
    if t["status"] == "opzet":
        if eigen:
            uit["staat"] = "wacht"
            uit["wachtlijst"] = vriend_team_id in set(toernooi_motor.wachtlijst(db, toernooi_id))
        return uit
    if not eigen or eigen["teruggetrokken"] or vriend["teruggetrokken"]:
        uit["staat"] = "vervallen"
        return uit
    uit["staat"] = "geldig"
    return uit


def uitnodig_stand(db, toernooi_id, speler_id):
    """Voor de uitnodigingskaart: hoeveel teams kwamen er al via deze speler,
    hoeveel mag hij er nog uitnodigen, en hoe staan zijn coupons?"""
    t = toernooi_motor.toernooi(db, toernooi_id)
    limiet = uitnodig_limiet(t)
    teams = uitgenodigd_door_speler(db, toernooi_id, speler_id)
    coupons = [coupon_staat(db, toernooi_id, r["team_id"]) for r in teams]
    coupons = [c for c in coupons if c["staat"] != "geen"]
    return {"limiet": limiet, "gebruikt": len(teams),
            "nog": None if limiet is None else max(0, limiet - len(teams)),
            "onderweg": sum(1 for c in coupons if c["staat"] == "wacht" and not c["wachtlijst"]),
            "wachtlijst": sum(1 for c in coupons if c["wachtlijst"]),
            "geldig": sum(1 for c in coupons if c["staat"] == "geldig")}


def mijn_coupons(db, speler_id):
    """De coupons van deze speler (wachtend of geldig), over alle toernooien."""
    uit = []
    for r in db.execute("""SELECT DISTINCT tournament_id FROM tournament_teams
                           WHERE uitgenodigd_door = ?""", (speler_id,)):
        for v in uitgenodigd_door_speler(db, r["tournament_id"], speler_id):
            c = coupon_staat(db, r["tournament_id"], v["team_id"])
            if c["staat"] in ("wacht", "geldig"):
                c["toernooi_id"] = r["tournament_id"]
                c["url"] = externe_url("coupon_inlossen", toernooi_id=r["tournament_id"],
                                       vriend_team_id=v["team_id"], houder_id=speler_id,
                                       sleutel=c["sleutel"])
                c["qr"] = qr_svg(c["url"]) if c["staat"] == "geldig" else None
                uit.append(c)
    uit.sort(key=lambda c: (c["staat"] != "geldig", c["toernooi"]["date"]))
    return uit


def uitnodigingen_vervallen(db, team_id, toernooi_id=None):
    """Een team verlaat een toernooi (vóór de loting): wat het met uitnodigen
    verdiende, vervalt voorgoed. Anders kan het zich uitschrijven, opnieuw
    inschrijven via de link van het team dat het zelf uitnodigde, en hebben ze
    allebei een coupon. De uitgenodigde teams blijven gewoon ingeschreven.
    Zonder toernooi: in alle toernooien waarvoor nog niet geloot is."""
    team = db.execute("SELECT player1_id, player2_id FROM teams WHERE id = ?", (team_id,)).fetchone()
    if not team:
        return
    spelers = (team["player1_id"], team["player2_id"] or -1)
    if toernooi_id is not None:
        db.execute("UPDATE tournament_teams SET uitgenodigd_door = NULL "
                   "WHERE tournament_id = ? AND uitgenodigd_door IN (?, ?)", (toernooi_id, *spelers))
    else:
        db.execute("""UPDATE tournament_teams SET uitgenodigd_door = NULL
                      WHERE uitgenodigd_door IN (?, ?) AND tournament_id IN
                      (SELECT tt.tournament_id FROM tournament_teams tt JOIN tournaments tn
                       ON tn.id = tt.tournament_id AND tn.status = 'opzet' WHERE tt.team_id = ?)""",
                   (*spelers, team_id))


def _deel_tekst(t, url):
    return (f"Doe je mee aan {t['name']} op {filter_dag(t['date'])}? "
            f"Schrijf je team hier in: {url}")


@app.route("/toernooi/<int:toernooi_id>/inschrijven", methods=["GET"])
def toernooi_inschrijven(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    ik = session.get("speler_id")
    # Binnengekomen via iemands uitnodigingslink? Dat onthouden we, ook als je
    # eerst nog een account moet maken.
    via = uitnodiger_van_code(toernooi_id, request.args.get("via"))
    if via and via != ik:
        uitnodigingen = dict(session.get("via") or {})
        uitnodigingen[str(toernooi_id)] = via
        session["via"] = uitnodigingen
    via = (session.get("via") or {}).get(str(toernooi_id))
    via_naam = weergavenamen(db).get(via) if via and via != ik else None
    lijst = _inschrijvingen(db, toernooi_id)
    bezet = {p: x for x in lijst for p in x["spelers"] if p}
    mijn = next((x for x in lijst if ik in x["spelers"]), None) if ik else None

    mijn_teams = []
    if ik and not mijn:
        namen_sp = weergavenamen(db)
        for team in mijn_teams_van(db, ik):
            partner = team["player2_id"] if team["player1_id"] == ik else team["player1_id"]
            probleem = None
            if partner in bezet:
                probleem = (f"{namen_sp.get(partner, '?')} is al ingeschreven met "
                            f"“{bezet[partner]['naam']}”")
            mijn_teams.append({"id": team["id"], "naam": team["name"], "avatar": team["avatar"],
                               "partner": namen_sp.get(partner, "?"), "probleem": probleem})
    spelers = [] if not ik else [
        {"id": r["id"], "naam": r["naam"], "echt": r["name"]}
        for r in db.execute(f"SELECT p.id, p.name, {WEERGAVE} AS naam FROM players p "
                            "WHERE p.active = 1 AND p.id != ? ORDER BY naam", (ik,))]
    bevestigd = sum(1 for x in lijst if not x["wachtlijst"])
    terug = url_for("toernooi_inschrijven", toernooi_id=toernooi_id)
    # Al ingeschreven: je eigen uitnodigingslink en hoe het met je coupon staat.
    uitnodiging = None
    if mijn and t["status"] == "opzet":
        link = externe_url("toernooi_inschrijven", toernooi_id=toernooi_id,
                           via=uitnodigcode(toernooi_id, ik))
        uitnodiging = {"link": link, "tekst": _deel_tekst(t, link),
                       # volledig gecodeerd, ook de schuine strepen (vraagt Snapchat)
                       "link_code": quote(link, safe=""),
                       "stand": uitnodig_stand(db, toernooi_id, ik)}
    return render_template("inschrijven.html", t=t, lijst=lijst, mijn=mijn,
                           uitnodiging=uitnodiging, via_naam=via_naam,
                           mijn_teams=mijn_teams, spelers=spelers, bevestigd=bevestigd,
                           open=t["status"] == "opzet", hierheen=terug, huidig_toernooi=t,
                           ik_naam=weergave(huidige_speler()) if ik else "",
                           adres=externe_url("toernooi_inschrijven", toernooi_id=toernooi_id))


def mijn_teams_van(db, speler_id):
    """Actieve teams van een speler, alfabetisch."""
    return db.execute("SELECT * FROM teams WHERE status = 'actief' AND "
                      "(player1_id = ? OR player2_id = ?) ORDER BY name",
                      (speler_id, speler_id)).fetchall()


@app.route("/toernooi/<int:toernooi_id>/inschrijven", methods=["POST"])
@speler_vereist
def toernooi_inschrijven_post(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    ik = session["speler_id"]
    terug = url_for("toernooi_inschrijven", toernooi_id=toernooi_id)
    if t["status"] != "opzet":
        flash("De inschrijvingen voor dit toernooi zijn gesloten: er is al geloot.", "fout")
        return redirect(terug)
    lijst = _inschrijvingen(db, toernooi_id)
    bezet = {p: x for x in lijst for p in x["spelers"] if p}
    if ik in bezet:
        flash(f"Je bent al ingeschreven met “{bezet[ik]['naam']}”.", "fout")
        return redirect(terug)

    keuze = request.form.get("keuze") or ""
    te_bevestigen = None
    if keuze.startswith("team:"):
        # 1. Een bestaand team van mij.
        try:
            team_id = int(keuze[5:])
        except ValueError:
            team_id = -1
        team = db.execute("SELECT * FROM teams WHERE id = ? AND status = 'actief' AND "
                          "(player1_id = ? OR player2_id = ?)", (team_id, ik, ik)).fetchone()
        if not team:
            flash("Kies een van je eigen teams.", "fout")
            return redirect(terug)
    else:
        # 2. Een (nieuwe) ploegmaat: bestaande speler of nieuwe naam.
        partner_naam = " ".join((request.form.get("partner_naam") or "").split())
        try:
            partner_id = int(request.form.get("partner_id") or 0)
        except ValueError:
            partner_id = 0
        partner = None
        if partner_id:
            partner = db.execute("SELECT * FROM players WHERE id = ? AND active = 1",
                                 (partner_id,)).fetchone()
        if partner is None and partner_naam:
            # Dezelfde naam (hoofdletters maken niet uit) = dezelfde persoon.
            partner = db.execute("SELECT * FROM players WHERE name = ? COLLATE NOCASE",
                                 (partner_naam,)).fetchone()
        if partner is None:
            if len(partner_naam) < 3 or len(partner_naam) > 40 or " " not in partner_naam:
                flash("Tik de volledige naam van je ploegmaat in (voor- en achternaam).", "fout")
                return redirect(terug + "#nieuw")
            nummer = vrij_spelernummer(db)
            db.execute("INSERT INTO players (id, name, nickname, password_hash, role, elo, "
                       "aangemaakt_door) VALUES (?, ?, '', NULL, ?, ?, ?)",
                       (nummer, partner_naam, ROL_SPELER, START_ELO, ik))
            partner = db.execute("SELECT * FROM players WHERE id = ?", (nummer,)).fetchone()
        if partner["id"] == ik:
            flash("Je kan niet met jezelf in een team.", "fout")
            return redirect(terug + "#nieuw")
        if partner["id"] in bezet:
            db.rollback()
            flash(f"{weergave(partner)} is al ingeschreven met “{bezet[partner['id']]['naam']}”. "
                  "Is dat een vergissing? Dan kan een van hen dat team uitschrijven.", "fout")
            return redirect(terug)
        # Hebben jullie al een team samen? Dan nemen we dat.
        team = db.execute("SELECT * FROM teams WHERE status = 'actief' AND "
                          "((player1_id = ? AND player2_id = ?) OR (player1_id = ? AND player2_id = ?))",
                          (ik, partner["id"], partner["id"], ik)).fetchone()
        if team is None:
            # Nieuw duo: heeft de ploegmaat al een eigen account, dan krijgt hij
            # een melding en kan hij het ongedaan maken. (Een klaargezet account
            # ziet het bij het registreren; een bestaand team kent elkaar al.)
            te_bevestigen = partner["id"] if partner["password_hash"] else None
            teamnaam = (request.form.get("teamnaam") or "").strip()[:40]
            if not teamnaam:
                teamnaam = f"{weergave(huidige_speler())} & {weergave(partner)}"[:40]
            if db.execute("SELECT 1 FROM teams WHERE name = ? COLLATE NOCASE", (teamnaam,)).fetchone():
                db.rollback()
                flash(f"Er bestaat al een team met de naam “{teamnaam}”. Kies een andere naam.", "fout")
                return redirect(terug + "#nieuw")
            elos = [r["elo"] for r in db.execute("SELECT elo FROM players WHERE id IN (?, ?)",
                                                 (ik, partner["id"]))]
            cur = db.execute("INSERT INTO teams (name, player1_id, player2_id, status, elo) "
                             "VALUES (?, ?, ?, 'actief', ?)",
                             (teamnaam, ik, partner["id"], sum(elos) / len(elos)))
            team = db.execute("SELECT * FROM teams WHERE id = ?", (cur.lastrowid,)).fetchone()
            # Een eventuele openstaande uitnodiging tussen jullie twee is nu overbodig.
            db.execute("DELETE FROM team_invitations WHERE player_id = ? AND team_id IN "
                       "(SELECT id FROM teams WHERE player1_id = ? AND status = 'onvolledig')",
                       (partner["id"], ik))

    partner_id = team["player2_id"] if team["player1_id"] == ik else team["player1_id"]
    if partner_id in bezet:
        db.rollback()
        flash("Je ploegmaat is al ingeschreven met een ander team.", "fout")
        return redirect(terug)
    # Via iemands uitnodigingslink binnengekomen? Dan krijgt die het krediet,
    # tenzij hij zelf in dit team zit (jezelf uitnodigen telt niet).
    via = (session.get("via") or {}).get(str(toernooi_id))
    if via in (team["player1_id"], team["player2_id"]):
        via = None
    db.execute("INSERT INTO tournament_teams (tournament_id, team_id, ingeschreven_door, "
               "ingeschreven_op, te_bevestigen, uitgenodigd_door) "
               "VALUES (?, ?, ?, datetime('now', 'localtime'), ?, ?)",
               (toernooi_id, team["id"], ik, te_bevestigen, via))
    db.commit()
    partner_naam = weergavenamen(db).get(partner_id, "je ploegmaat")
    wacht = _inschrijf_boodschap(db, toernooi_id, team["id"])
    session["viering"] = {"soort": "inschrijving", "toernooi": t["name"], "team": team["name"],
                          "partner": partner_naam, "wachtlijst": wacht}
    if via:
        uitnodigingen = dict(session.get("via") or {})
        uitnodigingen.pop(str(toernooi_id), None)
        session["via"] = uitnodigingen
    flash(f"“{team['name']}” is ingeschreven voor {t['name']}"
          + (f", {wacht}." if wacht else ". Tot dan!"), "ok")
    if via:
        # Zwart op wit dat de uitnodiging meetelt: geen discussie achteraf.
        flash(f"Je schreef je in via de uitnodiging van {weergavenamen(db).get(via, 'een vriend')}: "
              "dat telt voor zijn gratis pintjes.", "ok")
    # Meteen bij de uitnodigingskaart uitkomen: nu is het moment om te delen.
    return redirect(terug + "#uitnodigen")


@app.route("/toernooi/<int:toernooi_id>/uitschrijven", methods=["POST"])
@speler_vereist
def toernooi_uitschrijven(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    ik = session["speler_id"]
    terug = url_for("toernooi_inschrijven", toernooi_id=toernooi_id)
    try:
        team_id = int(request.form.get("team", ""))
    except ValueError:
        team_id = -1
    team = db.execute("SELECT * FROM teams WHERE id = ? AND (player1_id = ? OR player2_id = ?)",
                      (team_id, ik, ik)).fetchone()
    if not team:
        abort(403)
    if t["status"] != "opzet":
        flash("Er is al geloot: uitschrijven kan nu enkel nog via de organisatie.", "fout")
        return redirect(terug)
    voor = toernooi_motor.wachtlijst(db, toernooi_id)
    uitnodigingen_vervallen(db, team_id, toernooi_id)
    db.execute("DELETE FROM tournament_teams WHERE tournament_id = ? AND team_id = ?",
               (toernooi_id, team_id))
    db.commit()
    na = set(toernooi_motor.wachtlijst(db, toernooi_id))
    flash(f"“{team['name']}” is uitgeschreven voor {t['name']}.", "ok")
    opgeschoven = [x for x in voor if x not in na and x != team_id]
    if opgeschoven:
        naam = db.execute("SELECT name FROM teams WHERE id = ?", (opgeschoven[0],)).fetchone()["name"]
        flash(f"“{naam}” schuift daardoor op van de wachtlijst.", "ok")
    return redirect(terug)



# ---------------------------------------------------------------- coupons --

def _coupon_antwoord(html, status=200):
    """Couponpagina's nooit bewaren (browser, app, tussenliggende servers)."""
    antwoord = app.make_response((html, status))
    antwoord.headers["Cache-Control"] = "no-store"
    return antwoord


@app.route("/coupon/<int:toernooi_id>/<int:vriend_team_id>/<int:houder_id>/<sleutel>")
def coupon_inlossen(toernooi_id, vriend_team_id, houder_id, sleutel):
    """Hier komt de QR-code van een coupon uit. Een organisator die hem opent
    (met de scanner in de app of met de camera van zijn gsm), lost hem meteen
    in. Iemand anders krijgt enkel uitleg."""
    db = get_db()
    if not hmac.compare_digest(sleutel, coupon_sleutel(toernooi_id, vriend_team_id, houder_id)):
        abort(404)
    # Enkel een echte scan lost in: de link geopend als pagina. Een browser die
    # hem op voorhand ophaalt (prefetch/prerender) of als afbeelding laadt, niet.
    if "prefetch" in (request.headers.get("Sec-Purpose", "") + request.headers.get("Purpose", "")).lower():
        return _coupon_antwoord("", 204)
    # (Via de service worker van de app komt een echte scan binnen als "empty",
    # dus enkel soorten weigeren die nooit een scan kunnen zijn.)
    if request.headers.get("Sec-Fetch-Dest", "") in ("image", "script", "style", "font", "audio",
                                                      "video", "track", "object", "embed",
                                                      "worker", "sharedworker", "manifest", "xslt"):
        abort(403)
    c = coupon_staat(db, toernooi_id, vriend_team_id)
    if c["houder_id"] != houder_id:
        # Echte code, maar de coupon is niet (meer) van deze persoon: het team
        # schreef zich intussen via iemand anders in, of werd verwijderd.
        c = dict(c, staat="geen", houder=weergavenamen(db).get(houder_id, "?"), team=None)
    if not is_organisator():
        return _coupon_antwoord(render_template("coupon.html", soort="info", c=c))
    if c["staat"] == "geldig":
        try:
            db.execute("INSERT INTO coupon_inlossingen (tournament_id, vriend_team_id, speler_id, "
                       "ingelost_door) VALUES (?, ?, ?, ?)",
                       (toernooi_id, vriend_team_id, houder_id, session["speler_id"]))
            db.commit()
            return _coupon_antwoord(render_template("coupon.html", soort="ok", c=c,
                                                    pintjes=COUPON_PINTJES))
        except sqlite3.IntegrityError:          # net tegelijk twee keer gescand
            db.rollback()
            c = coupon_staat(db, toernooi_id, vriend_team_id)
    return _coupon_antwoord(render_template("coupon.html", soort="fout", c=c))


@app.route("/coupon/<int:toernooi_id>/<int:vriend_team_id>/<int:houder_id>/<sleutel>/staat.json")
def coupon_staat_json(toernooi_id, vriend_team_id, houder_id, sleutel):
    """Voor het profiel: zo verdwijnt de coupon zodra hij aan de bar gescand is."""
    if not hmac.compare_digest(sleutel, coupon_sleutel(toernooi_id, vriend_team_id, houder_id)):
        abort(404)
    c = coupon_staat(get_db(), toernooi_id, vriend_team_id)
    staat = c["staat"] if c["houder_id"] == houder_id else "geen"
    antwoord = app.response_class(json.dumps({"staat": staat}), mimetype="application/json")
    antwoord.headers["Cache-Control"] = "no-store"
    return antwoord


@app.route("/admin/scan")
@login_vereist
def coupon_scanner():
    return render_template("scan.html")


# ------------------------------------------------- melding voor de ploegmaat --
#
# Schrijft iemand een nieuw team in met een speler die al een account heeft,
# dan ziet die speler bij zijn volgende bezoek een melding. "OK" laat alles zoals
# het is; "Dit klopt niet" schrijft het team uit en haalt hem er weer uit.

def _te_bevestigen(db, speler_id):
    """De eerste openstaande melding voor deze speler, of None."""
    return db.execute(f"""
        SELECT tt.tournament_id, tt.team_id, tn.name AS toernooi, tn.date, tn.start_tijd,
               t.name AS team, {WEERGAVE.replace('p.', 'd.')} AS door
        FROM tournament_teams tt
        JOIN tournaments tn ON tn.id = tt.tournament_id AND tn.status = 'opzet'
        JOIN teams t ON t.id = tt.team_id
        LEFT JOIN players d ON d.id = tt.ingeschreven_door
        WHERE tt.te_bevestigen = ?
        ORDER BY tt.id LIMIT 1""", (speler_id,)).fetchone()


def _melding_rij(db, toernooi_id, team_id):
    rij = db.execute("SELECT * FROM tournament_teams WHERE tournament_id = ? AND team_id = ? "
                     "AND te_bevestigen = ?", (toernooi_id, team_id, session["speler_id"])).fetchone()
    if not rij:
        abort(404)
    return rij


@app.route("/inschrijving/<int:toernooi_id>/<int:team_id>/ok", methods=["POST"])
@speler_vereist
def inschrijving_ok(toernooi_id, team_id):
    db = get_db()
    _melding_rij(db, toernooi_id, team_id)
    db.execute("UPDATE tournament_teams SET te_bevestigen = NULL "
               "WHERE tournament_id = ? AND team_id = ?", (toernooi_id, team_id))
    db.commit()
    return redirect(_volgende() or url_for("toernooi_inschrijven", toernooi_id=toernooi_id))


@app.route("/inschrijving/<int:toernooi_id>/<int:team_id>/klopt-niet", methods=["POST"])
@speler_vereist
def inschrijving_klopt_niet(toernooi_id, team_id):
    """Uitschrijven én de ploegmaat weer uit het (nieuwe) team halen."""
    db = get_db()
    _melding_rij(db, toernooi_id, team_id)
    ik = session["speler_id"]
    team = db.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    t = toernooi_motor.toernooi(db, toernooi_id)
    voor = toernooi_motor.wachtlijst(db, toernooi_id)
    uitnodigingen_vervallen(db, team_id)
    db.execute("DELETE FROM tournament_teams WHERE tournament_id = ? AND team_id = ?",
               (toernooi_id, team_id))
    andere = team["player2_id"] if team["player1_id"] == ik else team["player1_id"]
    gespeeld = db.execute("SELECT 1 FROM games WHERE team1_id = ? OR team2_id = ? LIMIT 1",
                          (team_id, team_id)).fetchone()
    lopend = db.execute("""SELECT 1 FROM tournament_teams tt JOIN tournaments tn
                           ON tn.id = tt.tournament_id AND tn.status != 'opzet'
                           WHERE tt.team_id = ? LIMIT 1""", (team_id,)).fetchone()
    if not gespeeld and not lopend:
        # Het team bestond enkel door deze inschrijving: ik ga eruit, de andere
        # blijft alleen achter (onvolledig) en kan iemand anders uitnodigen.
        db.execute("UPDATE teams SET player1_id = ?, player2_id = NULL, status = 'onvolledig' "
                   "WHERE id = ?", (andere, team_id))
        # Ook nergens anders meer ingeschreven: alleen kan je niet meedoen.
        db.execute("""DELETE FROM tournament_teams WHERE team_id = ? AND tournament_id IN
                      (SELECT id FROM tournaments WHERE status = 'opzet')""", (team_id,))
    db.commit()
    flash(f"In orde: je bent uitgeschreven voor {t['name']} en je zit niet meer in "
          f"“{team['name']}”.", "ok")
    na = set(toernooi_motor.wachtlijst(db, toernooi_id))
    for x in voor:
        if x not in na and x != team_id:
            n = db.execute("SELECT name FROM teams WHERE id = ?", (x,)).fetchone()
            if n:
                flash(f"“{n['name']}” schuift daardoor op van de wachtlijst.", "ok")
            break
    return redirect(_volgende() or url_for("index"))


# ------------------------------------------------------------ beamerscherm --
#
# Eén groot scherm voor op de beamer, dat de fase van het toernooi volgt: de
# stand tijdens de bracketfase, het knockoutschema daarna. De pagina laadt één
# keer; daarna haalt ze om de 20 seconden de gegevens op via /beamer.json en
# toont ze de veranderingen met een animatie. Zo blijft het scherm ook in
# volledig scherm staan (herladen zou dat telkens afsluiten).

def _beamer_gegevens(db, toernooi_id):
    from elo import KO_LABEL
    t = toernooi_motor.toernooi(db, toernooi_id)
    teams = {x["id"]: x for x in toernooi_motor.deelnemers(db, toernooi_id,
                                                           ook_teruggetrokken=True)}

    def team(team_id):
        if not team_id or team_id not in teams:
            return None
        x = teams[team_id]
        return {"id": team_id, "naam": x["naam"], "spelers": x["spelers"],
                "foto": dop_team(team_id)}

    stand = []
    if t["status"] != "opzet":
        geordend, _ = toernooi_motor.stand(db, toernooi_id)
        for r in geordend:
            stand.append({"id": r["team_id"], "naam": r["naam"], "spelers": r["spelers"],
                          "foto": dop_team(r["team_id"]),
                          "positie": r["positie"], "gespeeld": r["gespeeld"],
                          "winst": r["winst"], "verlies": r["verlies"],
                          "punten": r["punten"], "vorm": r["vorm"][-5:],
                          "doorstoot": r["doorstoot"], "onbeslist": r["onbeslist"],
                          "gedeeld": r.get("gedeeld", False)})

    def wedstrijd(g):
        return {"id": g["id"], "ronde": g["ronde"], "positie": g["positie"],
                "volgende": g["volgende_game_id"], "slot": g["volgende_slot"],
                "team1": team(g["team1_id"]), "team2": team(g["team2_id"]),
                "winnaar": g["winner_team_id"], "gespeeld": g["status"] == "gespeeld",
                "tijd": g["scheduled_at"]}

    kolommen = []
    kleine = None
    for kolom in toernooi_motor.knockout_kolommen(db, toernooi_id):
        kolommen.append({"titel": kolom["titel"], "ronde": kolom["ronde"],
                         "games": [wedstrijd(g) for g in kolom["games"]]})
        if kolom["kleine_finale"] is not None:
            kleine = wedstrijd(kolom["kleine_finale"])

    kampioen = None
    if kolommen and kolommen[-1]["games"] and kolommen[-1]["games"][0]["gespeeld"]:
        kampioen = team(kolommen[-1]["games"][0]["winnaar"])

    laatste = []
    for g in db.execute("""
        SELECT * FROM games WHERE tournament_id = ? AND status = 'gespeeld'
        ORDER BY played_at DESC, (fase = 'knockout') DESC,
                 CASE WHEN fase = 'knockout' THEN ronde END ASC, id DESC LIMIT 7
    """, (toernooi_id,)):
        if g["fase"] == "knockout":
            label = KO_LABEL.get(g["ronde"], "Knockout")
        elif g["fase"] == "shootout":
            label = "Shootout"
        else:
            label = f"Speelronde {g['ronde']}" if g["ronde"] else "Bracketfase"
        laatste.append({"id": g["id"], "label": label, "team1": team(g["team1_id"]),
                        "team2": team(g["team2_id"]), "winnaar": g["winner_team_id"],
                        "fase": g["fase"]})

    # Een vingerafdruk van alles wat op het scherm kan veranderen: is die gelijk,
    # dan hoeft de pagina niets te doen.
    afdruk = hashlib.sha1(json.dumps([t["status"], stand, kolommen, kleine, laatste],
                                     sort_keys=True, default=str).encode()).hexdigest()[:12]
    fase = "knockout" if t["status"] in ("knockout", "afgelopen") else "stand"
    return {"versie": afdruk, "naam": t["name"], "status": t["status"],
            "status_label": TOERNOOI_STATUS.get(t["status"], ("", ""))[0],
            "fase": fase, "cut": t["ko_teams"], "stand": stand,
            "knockout": {"kolommen": kolommen, "kleine_finale": kleine,
                         "kampioen": kampioen},
            "laatste": laatste}


@app.route("/toernooi/<int:toernooi_id>/beamer")
def toernooi_beamer(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    if t["status"] == "opzet" and not is_organisator():
        return redirect(url_for("toernooi_detail", toernooi_id=toernooi_id))
    gegevens = _beamer_gegevens(db, toernooi_id)
    adres = externe_url("toernooi_detail", toernooi_id=toernooi_id)
    try:
        elke = max(3, min(120, int(request.args.get("elke", 20))))
    except ValueError:
        elke = 20
    return render_template("beamer.html", gegevens=gegevens, toernooi_id=toernooi_id,
                           adres=adres, qr=qr_svg(adres), elke=elke)


@app.route("/toernooi/<int:toernooi_id>/beamer.json")
def toernooi_beamer_json(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    if t["status"] == "opzet" and not is_organisator():
        abort(404)
    antwoord = app.response_class(json.dumps(_beamer_gegevens(db, toernooi_id)),
                                  mimetype="application/json")
    antwoord.headers["Cache-Control"] = "no-store"
    return antwoord


@app.route("/toernooi/<int:toernooi_id>/loting")
def toernooi_loting(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    if t["status"] == "opzet":
        flash("Dit toernooi is nog niet geloot.", "fout")
        return redirect(url_for("toernooi_detail", toernooi_id=toernooi_id))
    potten, rondes = toernooi_motor.loting_data(db, toernooi_id)
    return render_template("toernooi_loting.html", t=t, huidig_toernooi=t,
                           potten=potten, rondes=rondes,
                           potten_aantal=toernooi_motor.potten_aantal(db, toernooi_id))


# ---------------------------------------------------------- toernooi-admin --

@app.route("/admin/toernooien/nieuw", methods=["POST"])
@login_vereist
def toernooi_nieuw():
    db = get_db()
    naam = (request.form.get("naam") or "").strip()
    datum = request.form.get("datum") or ""
    if not naam or not datum:
        flash("Geef het toernooi een naam en een datum.", "fout")
        return redirect(url_for("admin_toernooien"))
    try:
        db.execute("""
            INSERT INTO tournaments (name, description, date, start_tijd,
                                     bracket_ronden, ko_teams, potten, slot_minuten,
                                     uitnodigingen_per_speler)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (naam, (request.form.get("beschrijving") or "").strip(), datum,
              request.form.get("start_tijd") or "19:00",
              int(request.form.get("ronden") or 4),
              int(request.form.get("ko_teams") or 8),
              toernooi_motor.STANDAARD_POTTEN,
              int(request.form.get("slot") or 20)))
        db.commit()
        nieuw = db.execute("SELECT id FROM tournaments WHERE name = ?", (naam,)).fetchone()
        flash(f"Toernooi “{naam}” aangemaakt. Voeg nu teams en tafels toe.", "ok")
        return redirect(url_for("toernooi_beheer", toernooi_id=nieuw["id"]))
    except ValueError:
        flash("Vul geldige getallen in.", "fout")
    except Exception:
        flash(f"Er bestaat al een toernooi met de naam “{naam}”.", "fout")
    return redirect(url_for("admin_toernooien"))


@app.route("/admin/toernooi/<int:toernooi_id>")
@login_vereist
def toernooi_beheer(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    teams = toernooi_motor.deelnemers(db, toernooi_id, ook_wachtlijst=True)
    wacht = toernooi_motor.wachtlijst(db, toernooi_id)
    for team in teams:
        team["wachtlijst"] = (wacht.index(team["id"]) + 1) if team["id"] in wacht else None
    if t["status"] == "opzet":
        # Vóór de loting: in volgorde van inschrijving, met wie en wanneer.
        info = {r["team_id"]: r for r in db.execute(f"""
            SELECT tt.team_id, tt.id AS volgorde, tt.ingeschreven_op,
                   {WEERGAVE.replace('p.', 'd.')} AS door,
                   {WEERGAVE.replace('p.', 'u.')} AS via
            FROM tournament_teams tt LEFT JOIN players d ON d.id = tt.ingeschreven_door
            LEFT JOIN players u ON u.id = tt.uitgenodigd_door
            WHERE tt.tournament_id = ?""", (toernooi_id,))}
        zonder_account = {r["id"] for r in db.execute(
            "SELECT id FROM players WHERE password_hash IS NULL")}
        namen_w = weergavenamen(db)
        for team in teams:
            r = info.get(team["id"])
            team["door"] = r["door"] if r else None
            team["via"] = r["via"] if r else None
            team["op"] = r["ingeschreven_op"] if r else ""
            team["zonder_account"] = [namen_w.get(pid, "?") for pid in
                                      (team["player1_id"], team["player2_id"]) if pid in zonder_account]
        teams.sort(key=lambda x: info[x["id"]]["volgorde"] if x["id"] in info else 0)
    gekozen = {x["id"] for x in teams}

    namen_sp = weergavenamen(db)
    # Spelers die al meedoen: hun andere teams kunnen er niet meer bij, want
    # niemand kan aan twee tafels tegelijk staan.
    bezet = {}
    for x in teams:
        bezet[x["player1_id"]] = x["naam"]
        bezet[x["player2_id"]] = x["naam"]

    beschikbaar = []
    for r in db.execute("SELECT * FROM teams WHERE status = 'actief' ORDER BY elo DESC"):
        if r["id"] in gekozen:
            continue
        botsing = next((pid for pid in (r["player1_id"], r["player2_id"])
                        if pid in bezet), None)
        beschikbaar.append({"id": r["id"], "naam": r["name"], "elo": r["elo"],
                            "spelers": f'{namen_sp.get(r["player1_id"], "?")} & '
                                       f'{namen_sp.get(r["player2_id"], "?")}',
                            "botsing": (f"{namen_sp.get(botsing, 'een speler')} speelt "
                                        f"al voor “{bezet[botsing]}”")
                                       if botsing is not None else None})

    openstaand = db.execute("""
        SELECT * FROM games WHERE tournament_id = ? AND status = 'gepland'
          AND team1_id IS NOT NULL AND team2_id IS NOT NULL
        ORDER BY scheduled_at, id
    """, (toernooi_id,)).fetchall()
    recent = db.execute("""
        SELECT * FROM games WHERE tournament_id = ? AND status = 'gespeeld'
        ORDER BY played_at DESC, id DESC LIMIT 12
    """, (toernooi_id,)).fetchall()

    meldingen_per_game = defaultdict(list)
    for r in db.execute(f"""
        SELECT gr.*, {WEERGAVE} AS speler_naam
        FROM game_reports gr
        JOIN players p ON p.id = gr.player_id
        JOIN games g ON g.id = gr.game_id
        WHERE g.tournament_id = ?
        ORDER BY gr.created_at
    """, (toernooi_id,)):
        meldingen_per_game[r["game_id"]].append(r)

    # Wie nodigde welk team uit? De organisatie kan het invullen of verbeteren,
    # bv. als de vriend de link op een andere gsm opende dan waarop hij inschreef.
    namen_via = weergavenamen(db)
    via_van = {r["team_id"]: r["uitgenodigd_door"] for r in db.execute(
        "SELECT team_id, uitgenodigd_door FROM tournament_teams WHERE tournament_id = ?", (toernooi_id,))}
    for team in teams:
        team["via_id"] = via_van.get(team["id"])
        team["via"] = namen_via.get(team["via_id"]) if team["via_id"] else None
    via_keuzes = sorted(({"id": p, "naam": namen_via.get(p, "?"), "team": x["naam"], "team_id": x["id"]}
                         for x in teams for p in (x["player1_id"], x["player2_id"]) if p),
                        key=lambda v: v["naam"].lower())

    coupons = []
    for r in db.execute("SELECT team_id FROM tournament_teams WHERE tournament_id = ? "
                        "AND uitgenodigd_door IS NOT NULL ORDER BY id", (toernooi_id,)):
        c = coupon_staat(db, toernooi_id, r["team_id"])
        if c["staat"] != "geen":
            coupons.append(c)
    # ook al ingeloste coupons van teams die intussen niet meer ingeschreven zijn
    for r in db.execute("SELECT vriend_team_id FROM coupon_inlossingen WHERE tournament_id = ? "
                        "AND vriend_team_id NOT IN (SELECT team_id FROM tournament_teams "
                        "WHERE tournament_id = ?)", (toernooi_id, toernooi_id)):
        coupons.append(coupon_staat(db, toernooi_id, r["vriend_team_id"]))
    return render_template("admin_toernooi.html", t=t, teams=teams, coupons=coupons, via_keuzes=via_keuzes,
                           beschikbaar=beschikbaar,
                           locaties=toernooi_motor.locaties(db, toernooi_id),
                           loc_namen={l["id"]: l["name"] for l in
                                      toernooi_motor.locaties(db, toernooi_id)},
                           openstaand=openstaand, recent=recent,
                           namen=teamnamen(db),
                           meldingen_per_game=meldingen_per_game,
                           inzet=toernooi_motor.shootout_inzet(db, toernooi_id),
                           potten_aantal=toernooi_motor.potten_aantal(db, toernooi_id),
                           inschrijf_adres=externe_url("toernooi_inschrijven", toernooi_id=toernooi_id),
                           wachtend=len(wacht),
                           terugtrek_opties={
                               team["id"]: toernooi_motor.terugtrek_opties(
                                   db, toernooi_id, team["id"])
                               for team in teams} if t["status"] == "bracket" else {},
                           controle=toernooi_motor.controleer(db, toernooi_id),
                           nu=datetime.now().strftime("%Y-%m-%dT%H:%M"))


@app.route("/admin/toernooi/<int:toernooi_id>/instellingen", methods=["POST"])
@login_vereist
def toernooi_instellingen(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    try:
        velden = {
            "name": (request.form.get("naam") or t["name"]).strip(),
            "description": (request.form.get("beschrijving") or "").strip(),
            "date": request.form.get("datum") or t["date"],
            "start_tijd": request.form.get("start_tijd") or t["start_tijd"],
            "bracket_ronden": int(request.form.get("ronden") or t["bracket_ronden"]),
            "ko_teams": int(request.form.get("ko_teams") or t["ko_teams"]),
            "slot_minuten": int(request.form.get("slot") or t["slot_minuten"]),
            "max_teams": (int(request.form["max_teams"]) if (request.form.get("max_teams") or "").strip()
                          else None),
            "uitnodigingen_per_speler": (int(request.form["uitnodigingen"])
                                         if (request.form.get("uitnodigingen") or "").strip()
                                         else t["uitnodigingen_per_speler"]),
        }
        if not 0 <= velden["uitnodigingen_per_speler"] <= 50:
            raise ValueError
        if velden["max_teams"] is not None and velden["max_teams"] < 4:
            raise ValueError
    except ValueError:
        flash("Vul geldige getallen in.", "fout")
        return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))
    if t["status"] != "opzet":
        # Na de loting mogen enkel de tekstvelden nog wijzigen.
        velden = {k: velden[k] for k in ("name", "description")}
    db.execute(f"UPDATE tournaments SET {', '.join(f'{k} = ?' for k in velden)} "
               "WHERE id = ?", (*velden.values(), toernooi_id))
    db.commit()
    flash("Toernooi bijgewerkt.", "ok")
    return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))


@app.route("/admin/toernooi/<int:toernooi_id>/teams", methods=["POST"])
@login_vereist
def toernooi_teams(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    if t["status"] != "opzet":
        flash("Het toernooi is al geloot; je kan de deelnemers niet meer wijzigen.", "fout")
        return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))

    # Wie doet er al mee? Een speler kan niet in twee teams tegelijk spelen —
    # hij zou dan op hetzelfde moment aan twee tafels moeten staan.
    bezet = {}
    for r in db.execute("""
        SELECT t.name, t.player1_id, t.player2_id
        FROM tournament_teams tt JOIN teams t ON t.id = tt.team_id
        WHERE tt.tournament_id = ?
    """, (toernooi_id,)):
        bezet[r["player1_id"]] = r["name"]
        bezet[r["player2_id"]] = r["name"]
    namen_sp = weergavenamen(db)

    toegevoegd, geweigerd = 0, []
    for waarde in request.form.getlist("team"):
        try:
            team_id = int(waarde)
        except ValueError:
            continue
        team = db.execute("SELECT * FROM teams WHERE id = ? AND status = 'actief'",
                          (team_id,)).fetchone()
        if not team:
            continue
        botsing = next((pid for pid in (team["player1_id"], team["player2_id"])
                        if pid in bezet), None)
        if botsing is not None:
            geweigerd.append(f"“{team['name']}” niet: {namen_sp.get(botsing, 'een speler')} "
                             f"speelt al voor “{bezet[botsing]}”")
            continue
        try:
            db.execute("INSERT INTO tournament_teams (tournament_id, team_id, ingeschreven_op) "
                       "VALUES (?, ?, datetime('now', 'localtime'))", (toernooi_id, team_id))
            toegevoegd += 1
            bezet[team["player1_id"]] = team["name"]
            bezet[team["player2_id"]] = team["name"]
        except Exception:
            pass
    db.commit()
    if toegevoegd:
        flash(f"{toegevoegd} team(s) toegevoegd aan het toernooi.", "ok")
    if geweigerd:
        flash("Elke speler kan maar voor één team per toernooi spelen. "
              + "; ".join(geweigerd) + ".", "fout")
    elif not toegevoegd:
        flash("Er zijn geen teams toegevoegd.", "fout")
    return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))


@app.route("/admin/toernooi/<int:toernooi_id>/teams/<int:team_id>/weg", methods=["POST"])
@login_vereist
def toernooi_team_weg(toernooi_id, team_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    if t["status"] != "opzet":
        flash("Het toernooi is al geloot; je kan de deelnemers niet meer wijzigen.", "fout")
    else:
        wacht_voor = set(toernooi_motor.wachtlijst(db, toernooi_id))
        naam = db.execute("SELECT name FROM teams WHERE id = ?", (team_id,)).fetchone()
        uitnodigingen_vervallen(db, team_id, toernooi_id)
        db.execute("DELETE FROM tournament_teams WHERE tournament_id = ? AND team_id = ?",
                   (toernooi_id, team_id))
        db.commit()
        flash(f"“{naam['name'] if naam else 'Team'}” uit het toernooi gehaald.", "ok")
        for tid_ in wacht_voor - set(toernooi_motor.wachtlijst(db, toernooi_id)) - {team_id}:
            n = db.execute("SELECT name FROM teams WHERE id = ?", (tid_,)).fetchone()
            flash(f"“{n['name']}” schuift daardoor op van de wachtlijst.", "ok")
    return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))


@app.route("/admin/toernooi/<int:toernooi_id>/teams/<int:team_id>/via", methods=["POST"])
@login_vereist
def toernooi_team_via(toernooi_id, team_id):
    """Wie nodigde dit team uit? Voor als het krediet verloren ging (bv. de
    vriend opende de link op een andere gsm dan waarop hij inschreef)."""
    db = get_db()
    _toernooi_of_404(db, toernooi_id)
    terug = url_for("toernooi_beheer", toernooi_id=toernooi_id) + "#teams"
    team = db.execute("SELECT t.* FROM tournament_teams tt JOIN teams t ON t.id = tt.team_id "
                      "WHERE tt.tournament_id = ? AND tt.team_id = ?", (toernooi_id, team_id)).fetchone()
    if not team:
        abort(404)
    if db.execute("SELECT 1 FROM coupon_inlossingen WHERE tournament_id = ? AND vriend_team_id = ?",
                  (toernooi_id, team_id)).fetchone():
        flash("De coupon voor dit team is al ingelost; de uitnodiger kan niet meer veranderen.", "fout")
        return redirect(terug)
    keuze = (request.form.get("speler") or "").strip()
    nieuw = int(keuze) if keuze.isdigit() else None
    if nieuw is not None:
        uitnodiger = team_in_toernooi(db, toernooi_id, nieuw)
        if nieuw in (team["player1_id"], team["player2_id"]) or not uitnodiger:
            flash("Die speler kan dit team niet uitgenodigd hebben: hij moet zelf met een "
                  "ander team meedoen aan dit toernooi.", "fout")
            return redirect(terug)
    db.execute("UPDATE tournament_teams SET uitgenodigd_door = ? WHERE tournament_id = ? AND team_id = ?",
               (nieuw, toernooi_id, team_id))
    db.commit()
    flash(f"“{team['name']}” " + (f"telt nu als uitgenodigd door {weergavenamen(db).get(nieuw)}."
                                  if nieuw else "telt niet meer als uitgenodigd."), "ok")
    return redirect(terug)


@app.route("/admin/toernooi/<int:toernooi_id>/teams/<int:team_id>/terugtrekken",
           methods=["POST"])
@login_vereist
def toernooi_team_terugtrekken(toernooi_id, team_id):
    """Een team komt niet opdagen: schrappen en de rest opnieuw loten."""
    db = get_db()
    _toernooi_of_404(db, toernooi_id)
    gekozen = request.form.get("doel")
    ok, boodschap = toernooi_motor.terugtrekken(
        db, toernooi_id, team_id,
        doel=int(gekozen) if gekozen and gekozen.isdigit() else None)
    if ok:
        herbereken_alles(db)
        for m in toernooi_motor.evalueer(db, toernooi_id):
            flash(m, "ok")
    flash(boodschap, "ok" if ok else "fout")
    return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))


@app.route("/admin/toernooi/<int:toernooi_id>/locaties", methods=["POST"])
@login_vereist
def toernooi_locatie_nieuw(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    if t["status"] != "opzet":
        flash("Na de loting liggen de tafels vast. Maak eerst de loting ongedaan "
              "als je ze wil wijzigen.", "fout")
        return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id) + "#locaties")
    naam = (request.form.get("naam") or "").strip()
    if not naam:
        flash("Geef de locatie een naam, bv. “Tafel 1”.", "fout")
        return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))
    try:
        db.execute("INSERT INTO tournament_locations (tournament_id, name) VALUES (?, ?)",
                   (toernooi_id, naam))
        db.commit()
        flash(f"Locatie “{naam}” toegevoegd.", "ok")
    except Exception:
        flash(f"“{naam}” bestaat al voor dit toernooi.", "fout")
    return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))


@app.route("/admin/toernooi/<int:toernooi_id>/locaties/<int:locatie_id>/weg",
           methods=["POST"])
@login_vereist
def toernooi_locatie_weg(toernooi_id, locatie_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    if t["status"] != "opzet":
        flash("Na de loting liggen de tafels vast: de wedstrijden op die tafel "
              "zouden anders hun plaats kwijtraken.", "fout")
        return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id) + "#locaties")
    db.execute("DELETE FROM tournament_locations WHERE id = ? AND tournament_id = ?",
               (locatie_id, toernooi_id))
    db.commit()
    flash("Locatie verwijderd.", "ok")
    return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))


@app.route("/admin/toernooi/<int:toernooi_id>/genereren", methods=["POST"])
@login_vereist
def toernooi_genereren(toernooi_id):
    db = get_db()
    _toernooi_of_404(db, toernooi_id)
    ok, boodschap = toernooi_motor.genereer(db, toernooi_id)
    flash(boodschap, "ok" if ok else "fout")
    if ok:
        return redirect(url_for("toernooi_loting", toernooi_id=toernooi_id))
    return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))


@app.route("/admin/toernooi/<int:toernooi_id>/bijwerken", methods=["POST"])
@login_vereist
def toernooi_bijwerken(toernooi_id):
    """Stand, shootouts en knockoutschema opnieuw laten bepalen."""
    db = get_db()
    _toernooi_of_404(db, toernooi_id)
    meldingen = toernooi_motor.evalueer(db, toernooi_id)
    for melding in meldingen:
        flash(melding, "ok")
    if not meldingen:
        flash("De stand is bijgewerkt; er viel niets te wijzigen.", "ok")
    return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))


@app.route("/admin/toernooi/<int:toernooi_id>/herloten", methods=["POST"])
@login_vereist
def toernooi_herloten(toernooi_id):
    db = get_db()
    _toernooi_of_404(db, toernooi_id)
    ok, boodschap = toernooi_motor.herloot(db, toernooi_id)
    flash(boodschap, "ok" if ok else "fout")
    return redirect(url_for("toernooi_beheer", toernooi_id=toernooi_id))


@app.route("/admin/toernooi/<int:toernooi_id>/verwijderen", methods=["POST"])
@login_vereist
def toernooi_verwijderen(toernooi_id):
    db = get_db()
    t = _toernooi_of_404(db, toernooi_id)
    gespeeld = db.execute("SELECT COUNT(*) AS n FROM games WHERE tournament_id = ? "
                          "AND status = 'gespeeld'", (toernooi_id,)).fetchone()["n"]
    db.execute("DELETE FROM tournaments WHERE id = ?", (toernooi_id,))
    db.commit()
    if gespeeld:
        na_resultaat(db)
    flash(f"Toernooi “{t['name']}” verwijderd." +
          (" Alle ratings zijn herberekend." if gespeeld else ""), "ok")
    return redirect(url_for("admin_toernooien"))


# -------------------------------------------------------------------- main --

init_db()
os.makedirs(UPLOAD_MAP, exist_ok=True)


def _backup_bij_opstarten(minstens_uren=1):
    """Bij het opstarten een kopie maken: zo heb je altijd een recent vangnet.

    Draait de site achter een webserver met meerdere processen, dan start deze
    code meermaals; en tijdens het sleutelen herstart je misschien tien keer na
    elkaar. Daarom slaan we het over als er al een verse back-up staat.
    """
    try:
        if not os.path.exists(DB_PATH) or os.path.getsize(DB_PATH) == 0:
            return
        recent = backup_lijst()
        if recent and (datetime.now() - recent[0]["moment"]).total_seconds() < minstens_uren * 3600:
            return
        maak_backup("opstart")
    except Exception as fout:                       # nooit de start tegenhouden
        print(f"Let op: de back-up bij het opstarten is niet gelukt ({fout}).")


_backup_bij_opstarten()


def _eigen_adressen(poort):
    """De adressen waarop de site bereikbaar is, om af te drukken bij het starten."""
    import socket
    adressen = [f"http://localhost:{poort}"]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # niets wordt echt verstuurd
        adressen.append(f"http://{s.getsockname()[0]}:{poort}")
        s.close()
    except OSError:
        pass
    return adressen


def zet_eigenaar(zoek: str) -> int:
    """Noodluik vanaf de terminal: maak iemand eigenaar (hoofdorganisator).

    Nodig na het bijwerken van een oude database (daar had niemand een rol) of
    als de eigenaar zijn wachtwoord kwijt is. Wie aan de server kan, mag dit —
    die persoon kan sowieso al aan het databasebestand zelf.
    """
    db = verbind(DB_PATH)
    sleutel = zoek.strip().lstrip("#")
    speler = db.execute(
        "SELECT * FROM players WHERE name = ? COLLATE NOCASE OR id = ?",
        (zoek.strip(), int(sleutel) if sleutel.isdigit() else -1)).fetchone()
    if not speler:
        print(f"Geen speler gevonden met naam of nummer “{zoek}”. Bestaande spelers:")
        for r in db.execute("SELECT id, name, role FROM players ORDER BY name"):
            print(f"   #{r['id']}  {r['name']}  ({r['role']})")
        db.close()
        return 1
    db.execute("UPDATE players SET role = ? WHERE role = ?", (ROL_ADMIN, ROL_EIGENAAR))
    db.execute("UPDATE players SET role = ? WHERE id = ?", (ROL_EIGENAAR, speler["id"]))
    db.commit()
    db.close()
    print(f"{speler['name']} (#{speler['id']}) is nu de eigenaar. Laat hem inloggen "
          "met zijn eigen account; het organisatiepaneel staat dan open.")
    return 0


def _waarschuw_zonder_eigenaar():
    """Zeg bij het opstarten hoe je een eigenaar aanduidt, als er nog geen is."""
    db = verbind(DB_PATH)
    aantal = db.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"]
    heeft = db.execute("SELECT 1 FROM players WHERE role IN (?, ?)",
                       (ROL_ADMIN, ROL_EIGENAAR)).fetchone()
    db.close()
    if aantal and not heeft:
        print("\n  Let op: nog niemand is organisator. Duid een eigenaar aan met:")
        print("      python app.py --eigenaar \"Voornaam Achternaam\"\n")
    elif not aantal:
        print("\n  Nog geen accounts: het eerste account dat zich registreert, "
              "wordt automatisch de eigenaar.\n")


def start(host="0.0.0.0", poort=5000):
    """Start de site met waitress: een echte server die meerdere bezoekers
    tegelijk bedient, in tegenstelling tot de testserver van Flask."""
    _waarschuw_zonder_eigenaar()
    try:
        from waitress import serve
    except ImportError:
        print("waitress ontbreekt (pip install waitress); ik val terug op de "
              "testserver van Flask.")
        app.run(host=host, port=poort, debug=False, threaded=True)
        return

    print("Leberschuss Tonzent draait op:")
    for adres in _eigen_adressen(poort):
        print("   ", adres)
    serve(app, host=host, port=poort, threads=8, ident="Leberschuss Tonzent")


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Leberschuss Tonzent")
    p.add_argument("--poort", type=int, default=int(os.environ.get("POORT", 5000)))
    p.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    p.add_argument("--eigenaar", metavar="NAAM_OF_NUMMER",
                   help="maak deze speler eigenaar (hoofdorganisator) en stop")
    args = p.parse_args()
    if args.eigenaar:
        raise SystemExit(zet_eigenaar(args.eigenaar))
    start(args.host, args.poort)

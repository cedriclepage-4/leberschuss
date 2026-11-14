"""Vanwaar een bezoeker ongeveer komt: gemeente en gewest, uit het IP-adres.

Het opzoeken gebeurt volledig op de eigen server, in de gratis databank van
DB-IP (IP to City Lite, CC BY 4.0: https://db-ip.com). Er gaat geen enkel
IP-adres naar buiten, en het IP-adres zelf wordt nergens bewaard: enkel de
plaatsnaam die eruit volgt.

Nauwkeurigheid: het land klopt bijna altijd, het gewest meestal. De gemeente
is een gok: op mobiele data komt iemand vaak uit bij de knooppunten van zijn
provider (Brussel, Antwerpen, Mechelen), thuis op wifi zit het meestal dichter.

De databank (± 130 MB) staat in geo/ en zit niet in git. `python
geo_bijwerken.py` haalt de nieuwste versie op (één keer per maand volstaat).
Zonder databank werkt alles gewoon, maar zonder plaatsnamen.
"""

import ipaddress
import os
import re
import threading
import time

HIER = os.path.dirname(os.path.abspath(__file__))
MAP = os.path.join(HIER, "geo")
BESTAND = os.environ.get("GEO_BESTAND") or os.path.join(MAP, "dbip-city-lite.mmdb")
OPNIEUW_KIJKEN = 600        # zo vaak kijken of er een nieuwe databank staat (s)

# Belgische plaatsen en gewesten in het Nederlands (de databank geeft Engels).
_NL = {
    "Brussels": "Brussel", "City of Brussels": "Brussel", "Ghent": "Gent",
    "Antwerp": "Antwerpen", "Bruges": "Brugge", "Louvain": "Leuven",
    "Liège": "Luik", "Namur": "Namen", "Mons": "Bergen", "Courtrai": "Kortrijk",
    "Ypres": "Ieper", "Malines": "Mechelen", "Ostend": "Oostende",
    "Tournai": "Doornik", "Alost": "Aalst", "Saint-Nicolas": "Sint-Niklaas",
    "Hasselt": "Hasselt", "Termonde": "Dendermonde", "Audenarde": "Oudenaarde",
    "Renaix": "Ronse", "Tongres": "Tongeren", "Wavre": "Waver",
    "Nivelles": "Nijvel", "Arlon": "Aarlen", "Mouscron": "Moeskroen",
    "Saint-Trond": "Sint-Truiden", "Flanders": "Vlaanderen",
    "Wallonia": "Wallonië", "Brussels Capital": "Brussel",
}
_LANDEN = {
    "BE": "België", "NL": "Nederland", "FR": "Frankrijk", "DE": "Duitsland",
    "LU": "Luxemburg", "GB": "Verenigd Koninkrijk", "ES": "Spanje",
    "IT": "Italië", "US": "Verenigde Staten", "PL": "Polen", "PT": "Portugal",
    "TR": "Turkije", "MA": "Marokko", "CH": "Zwitserland", "AT": "Oostenrijk",
    "IE": "Ierland",
}
_HAAKJES = re.compile(r"\s*\(.*?\)\s*")

_slot = threading.Lock()
_lezer = None
_lezer_mtime = None
_laatst_gekeken = 0.0


def _vertaal(naam):
    naam = _HAAKJES.sub(" ", naam or "").strip()
    return _NL.get(naam, naam)


def _databank():
    """De geopende databank, of None als die er (nog) niet is. Een nieuwere
    versie op schijf wordt vanzelf opgepikt, zonder de site te herstarten."""
    global _lezer, _lezer_mtime, _laatst_gekeken
    if time.time() - _laatst_gekeken < OPNIEUW_KIJKEN:
        return _lezer
    with _slot:
        _laatst_gekeken = time.time()
        try:
            mtime = os.path.getmtime(BESTAND)
        except OSError:
            _lezer = None
            return None
        if mtime != _lezer_mtime:
            try:
                import maxminddb
                nieuw = maxminddb.open_database(BESTAND)
            except Exception as fout:             # pakket of bestand stuk
                print(f"Let op: plaatsdatabank niet geopend ({fout}).")
                return _lezer
            _lezer, _lezer_mtime = nieuw, mtime
        return _lezer


def plaats(ip):
    """'Gent (Vlaanderen)', 'Lille (Frankrijk)', … of None als het onbekend is."""
    try:
        adres = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    if not adres.is_global:          # thuisnetwerk, localhost, …
        return None
    lezer = _databank()
    if lezer is None:
        return None
    try:
        gegevens = lezer.get(str(adres)) or {}
    except Exception:
        return None
    stad = _vertaal(gegevens.get("city", {}).get("names", {}).get("en"))
    land = gegevens.get("country", {}).get("iso_code")
    if land == "BE":
        gewest = gegevens.get("subdivisions") or [{}]
        gewest = _vertaal(gewest[0].get("names", {}).get("en"))
        if stad and gewest and stad != gewest:
            return f"{stad} ({gewest})"[:60]
        return (stad or gewest or "België")[:60]
    landnaam = _LANDEN.get(land) or gegevens.get("country", {}).get("names", {}).get("en")
    if stad and landnaam:
        return f"{stad} ({landnaam})"[:60]
    naam = landnaam or stad
    return naam[:60] if naam else None

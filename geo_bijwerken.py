"""De plaatsdatabank voor de bezoekersstatistieken ophalen of vernieuwen.

    python geo_bijwerken.py

Haalt de gratis "IP to City Lite"-databank van DB-IP (CC BY 4.0) op en zet
die in geo/dbip-city-lite.mmdb. DB-IP brengt elke maand een nieuwe versie uit;
op de server kan dit maandelijks via cron (zie DEPLOY.md). De draaiende site
pikt de nieuwe versie binnen tien minuten vanzelf op.
"""

import gzip
import os
import shutil
import sys
import tempfile
import urllib.request
from datetime import date

import geo

ADRES = "https://download.db-ip.com/free/dbip-city-lite-{jaar}-{maand:02d}.mmdb.gz"


def maanden():
    """Deze maand, en anders de vorige (de nieuwe staat er pas vanaf de 1e)."""
    vandaag = date.today()
    yield vandaag.year, vandaag.month
    if vandaag.month == 1:
        yield vandaag.year - 1, 12
    else:
        yield vandaag.year, vandaag.month - 1


def main():
    os.makedirs(os.path.dirname(geo.BESTAND), exist_ok=True)
    for jaar, maand in maanden():
        adres = ADRES.format(jaar=jaar, maand=maand)
        print(f"Ophalen: {adres}")
        try:
            antwoord = urllib.request.urlopen(adres, timeout=120)
        except Exception as fout:
            print(f"  niet gelukt ({fout})")
            continue
        # Eerst naast het echte bestand uitpakken en dan in één keer vervangen,
        # zodat de site nooit een half bestand leest.
        fd, tijdelijk = tempfile.mkstemp(dir=os.path.dirname(geo.BESTAND), suffix=".mmdb")
        try:
            with os.fdopen(fd, "wb") as uit, gzip.GzipFile(fileobj=antwoord) as gz:
                shutil.copyfileobj(gz, uit, 1024 * 1024)
            import maxminddb
            maxminddb.open_database(tijdelijk).close()       # is het bestand heel?
            os.replace(tijdelijk, geo.BESTAND)
        except Exception as fout:
            print(f"  uitpakken niet gelukt ({fout})")
            if os.path.exists(tijdelijk):
                os.remove(tijdelijk)
            continue
        grootte = os.path.getsize(geo.BESTAND) / 1e6
        print(f"Klaar: {geo.BESTAND} ({grootte:.0f} MB)")
        return 0
    print("Geen databank kunnen ophalen. De site werkt gewoon verder, zonder plaatsnamen.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

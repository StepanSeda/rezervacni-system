# IronBox Fitness – rezervační systém (maturitní práce)

Webová aplikace pro rezervaci lekcí. Python + Flask + SQLite.

## Spuštění
```
pip install -r requirements.txt
python app.py
```
Otevři http://127.0.0.1:5000. Databáze `ironbox.db` se vytvoří a naplní ukázkovými daty sama.
Admin: `admin@ironbox.cz` / `admin123` (změň!).

## Splnění zadání
| Požadavek | Kde |
|---|---|
| Nabídka lekcí a termínů | `/`, tabulky `classes`, `sessions` |
| Rezervace termínu | `POST /rezervovat/<id>` |
| Databáze | SQLite, 4 tabulky (`users`, `classes`, `sessions`, `bookings`) |
| Zpracování formulářů | registrace, přihlášení, admin, rezervace (validace na serveru) |
| Logika rezervací | kapacita, zákaz duplicit (UNIQUE), jen budoucí termíny, rušení |
| Uživatelé a role | hesla hashovaná, admin vs. klient |
| Zabezpečení | CSRF tokeny, hashovaná hesla, role, validace na serveru |
| Kontakt, profil, správa lekcí a uživatelů | `/kontakt`, `/profil`, `/admin` |

## Rozšíření
- Kredity: každá rezervace stojí 1 kredit, storno ho vrací, admin kredity přidává
- Čekací listina s automatickým přihlášením při uvolnění místa
- Týdenní opakování termínů v adminu, export prezenčky do CSV

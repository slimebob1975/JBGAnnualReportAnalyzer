"""Hur många samtidiga anrop tål kontot?

Frågan avgör om det är värt att göra om analysslingan. Väntan på modellen är
69 procent av en körning, men den tiden går bara att korta om kontot orkar
svara på flera frågor samtidigt. Taket sätts av hastighetsgränserna, inte av
maskinen, och det går att mäta på några minuter i stället för att gissa.

Skriptet rör inte analysen. Det skickar egna anrop av samma storleksordning
som en nyckeltalsextraktion och mäter vad som kommer tillbaka.

    python scripts/measure_concurrency.py --model gpt-5.2

Nyckeln tas från OPENAI_API_KEY om den finns, annars frågar skriptet efter
den utan att visa den. Det går också att ge den med --api-key, men då hamnar
den i skalets historik och i processlistan; i PowerShell är

    $env:OPENAI_API_KEY = "sk-..."

bättre, och gäller bara det fönstret.

Kostar några tiondels dollar: fyra omgångar om ett par anrop var. Höj --calls
om siffrorna ser skakiga ut, men börja lågt.
"""

import argparse
import os
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from getpass import getpass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from openai import OpenAI, RateLimitError
    from openai import __version__ as openai_version
except ImportError:
    sys.exit("openai-paketet saknas. Kör i samma miljö som tjänsten.")

# En nyckeltalsextraktion låg på omkring 32 000 tokens per anrop. Texten nedan
# upprepas tills frågan är i samma storleksordning, så att mätningen säger
# något om den verkliga belastningen och inte om en leksak.
FILLER = (
    "Arbetslöshetskassan redovisar medlemsavgifter, administrationskostnader "
    "och finansieringsavgift enligt Inspektionen för arbetslöshetsförsäkringens "
    "föreskrifter. Beloppen anges i tusental kronor om inte annat anges. "
)
TARGET_CHARS = 60000
# Ett riktigt extraktionsanrop tar 90-125 sekunder, och tiden går åt till att
# skriva svaret - omkring 8 000 tokens - inte till att läsa frågan. En mätning
# som ber om ett ord mäter därför fel halva: inläsningen är snabb och
# parallelliserar bra ändå. Svaret här begärs långt nog att generering
# dominerar, utan att varje omgång tar en kvart.
TARGET_ANSWER_WORDS = 1200
PROMPT = (
    "Skriv en sammanhängande beskrivning på minst "
    f"{TARGET_ANSWER_WORDS} ord av hur en svensk arbetslöshetskassas "
    "resultaträkning och balansräkning är uppbyggda enligt IAF:s "
    "föreskrifter. Texten nedan är bakgrund och ska inte sammanfattas."
)


class Outcome:
    def __init__(self):
        self.durations: list[float] = []
        self.rate_limited = 0
        self.errors = 0
        self.lock = threading.Lock()


def one_call(client, model: str, text: str, outcome: Outcome) -> None:
    started = time.monotonic()
    try:
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": text},
            ],
        }
        # gpt-5 och o-serien tillåter bara standardtemperaturen.
        if not model.lower().startswith(("gpt-5", "o1", "o3", "o4")):
            kwargs["temperature"] = 0
        client.chat.completions.create(**kwargs)
        elapsed = time.monotonic() - started
        with outcome.lock:
            outcome.durations.append(elapsed)
    except RateLimitError:
        with outcome.lock:
            outcome.rate_limited += 1
    except TypeError as ex:
        # Inte ett svar från API:et: anropet kom aldrig i väg. Nästan alltid
        # att openai och httpx inte hör ihop, vilket händer när skriptet körs
        # i en annan miljö än tjänsten.
        with outcome.lock:
            outcome.errors += 1
            print(f"      fel: {ex}. Det här är inget svar från API:et utan "
                  "ett paketfel - kör i samma venv som tjänsten.")
    except Exception as ex:
        with outcome.lock:
            outcome.errors += 1
            print(f"      fel: {type(ex).__name__}: {str(ex)[:90]}")


def run_round(client, model: str, workers: int, calls: int, text: str) -> dict:
    outcome = Outcome()
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in range(calls):
            pool.submit(one_call, client, model, text, outcome)
    wall = time.monotonic() - started

    done = len(outcome.durations)
    return {
        "arbetare": workers,
        "anrop": calls,
        "klara": done,
        "sekunder": wall,
        "per_minut": (done / wall * 60) if wall else 0,
        "median": statistics.median(outcome.durations) if outcome.durations else 0,
        "hastighetsgrans": outcome.rate_limited,
        "fel": outcome.errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gpt-5.2")
    parser.add_argument("--calls", type=int, default=0,
                        help="anrop per omgång. Förval: dubbelt så många som "
                             "arbetarna, så att varje omgång faktiskt prövar "
                             "sin samtidighet.")
    parser.add_argument("--workers", default="1,2,4,8",
                        help="samtidiga anrop att pröva")
    parser.add_argument("--api-key", default=None,
                        help="API-nyckel. Hamnar i skalets historik och i "
                             "processlistan - ange hellre OPENAI_API_KEY, "
                             "eller låt skriptet fråga.")
    args = parser.parse_args()

    api_key = args.api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Fråga i stället för att avbryta: det är den väg som varken kräver
        # en .env-fil eller lämnar nyckeln efter sig.
        api_key = getpass("OpenAI API-nyckel (visas inte): ").strip()
    if not api_key:
        sys.exit("Ingen API-nyckel angiven.")

    text = (FILLER * (TARGET_CHARS // len(FILLER) + 1))[:TARGET_CHARS]
    # Inga återförsök: poängen är att se hastighetsgränserna, inte att
    # gömma dem. Tjänsten själv har tre försök i SDK:n och fem i sin egen
    # slinga, vilket döljer precis det vi vill mäta.
    client = OpenAI(api_key=api_key, max_retries=0, timeout=300.0)

    # Versionerna först. Ett anrop som faller på "process() takes no keyword
    # arguments" beror inte på modellen utan på att openai och httpx kommer
    # från olika miljöer - kör i samma venv som tjänsten.
    try:
        import httpx
        versions = f"openai {openai_version}, httpx {httpx.__version__}"
    except Exception:
        versions = f"openai {openai_version}"
    per_round = (f"{args.calls} anrop per omgång"
                 if args.calls else "dubbelt så många anrop som arbetare")
    print(f"Modell {args.model}, {len(text):,} tecken in och minst "
          f"{TARGET_ANSWER_WORDS} ord ut, {per_round}.".replace(",", " "))
    print(f"Miljö: {sys.executable}")
    print(f"Paket: {versions}\n")
    print(f"{'arbetare':>9} {'klara':>6} {'sekunder':>9} {'anrop/min':>10} "
          f"{'median s':>9} {'429':>5} {'fel':>5}")

    results = []
    for workers in [int(w) for w in args.workers.split(",")]:
        # Fyra anrop i en pool om åtta ger fyra samtidiga, inte åtta. Utan
        # det här mäter de två sista omgångarna samma sak.
        calls = args.calls or workers * 2
        result = run_round(client, args.model, workers, calls, text)
        results.append(result)
        print(f"{result['arbetare']:>9} {result['klara']:>6} "
              f"{result['sekunder']:>9.1f} {result['per_minut']:>10.1f} "
              f"{result['median']:>9.1f} {result['hastighetsgrans']:>5} "
              f"{result['fel']:>5}")
        # Låt kontots fönster återhämta sig mellan omgångarna, annars mäter
        # nästa omgång den förras skuld.
        time.sleep(20)

    print()
    base = next((r for r in results if r["arbetare"] == 1), results[0])
    best = max(results, key=lambda r: r["per_minut"])
    if base["per_minut"]:
        print(f"Bäst: {best['arbetare']} samtidiga gav "
              f"{best['per_minut'] / base['per_minut']:.1f} gånger genomströmningen "
              "mot ett i taget.")
    if all(r["klara"] == 0 for r in results):
        print("Inga anrop gick igenom. Mätningen säger ingenting om "
              "hastighetsgränserna.")
        return
    if any(r["hastighetsgrans"] for r in results):
        first = next(r for r in results if r["hastighetsgrans"])
        print(f"Hastighetsgränsen slog till vid {first['arbetare']} samtidiga. "
              "Fler arbetare än så ger återförsök, inte fart.")
    else:
        print("Ingen hastighetsgräns slog till. Taket ligger högre än det "
              "som prövades.")
    slowest = max(r["median"] for r in results if r["klara"])
    fastest = min(r["median"] for r in results if r["klara"])
    if fastest and slowest / fastest > 1.5:
        print("Medianen per anrop stiger med fler arbetare: anropen köar "
              "redan, och genomströmningen är nära sitt tak.")
    else:
        print("Medianen per anrop är i stort sett oberoende av antalet "
              "arbetare. Köbildningen har inte börjat.")
    print(f"\nMätningen gäller anrop på ungefär {TARGET_ANSWER_WORDS} ord ut. "
          "Ett riktigt\nextraktionsanrop skriver mer än så, så räkna med att "
          "taket ligger något lägre\nän det som syns här.")


if __name__ == "__main__":
    main()

"""Download CDC WONDER weather deaths by county into data/cdc_wonder_weather_deaths.txt.

Database: Underlying Cause of Death, 2018-2024, Single Race (D158).
Query: group by County, limited to your states and ICD-10 X30-X39 (Exposure to forces of nature).

WONDER's API can't return state or county death data, so this drives the web form in Chrome.
You read the data use agreement and click "I Agree" yourself; the script does the rest.
"""
import argparse
import csv
import shutil
import tempfile
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

from scrape_power_plants import DATA

AGREEMENT = "https://wonder.cdc.gov/ucd-icd10-expanded.html"
OUT = DATA / "cdc_wonder_weather_deaths.txt"


def wait_for(cond, timeout, step=1):
    end = time.time() + timeout
    while time.time() < end:
        result = cond()
        if result:
            return result
        time.sleep(step)
    return None


def fill_and_send(d, state_fips, icd):
    js = lambda script, *a: d.execute_script(script, *a)
    Select(d.find_element(By.NAME, "B_1")).select_by_value("D158.V9-level2")  # group by County
    states = Select(d.find_element(By.NAME, "F_D158.V9"))
    states.deselect_all()
    for f in state_fips:
        states.select_by_value(f)
    d.find_element(By.ID, "RO_ucdD158.V2").click()  # cause of death: ICD-10 codes
    # type the codes into the finder's advanced box instead of walking the ICD-10 tree
    js("document.getElementsByName('O_V2_fmode')[0].value = 'fadv';"
       "document.getElementById('TD158.V2').value = arguments[0];", icd)
    for box in ("CO_show_zeros", "CO_show_suppressed", "export-option"):  # keep 0 and suppressed counties, export a file
        js("document.getElementById(arguments[0]).checked = true;", box)
    js("document.getElementById('export-format').value = 'tsv';")
    d.find_element(By.ID, "submit-button1").click()


def main():
    plants = list(csv.DictReader(open(DATA / "power_plants.csv", encoding="utf-8")))
    fips_of = {p["state"]: p["county_fips"][:2] for p in plants if p["county_fips"]}
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--states", nargs="+", default=sorted(fips_of), help="states from power_plants.csv (default: all of them)")
    ap.add_argument("--icd", default="X30-X39", help="ICD-10 codes or ranges, one per line (default: X30-X39)")
    ap.add_argument("--wait", type=int, default=600, help="seconds to wait for you to click I Agree (default: 600)")
    args = ap.parse_args()
    missing = [s for s in args.states if s.upper() not in fips_of]
    if missing:
        ap.error(f"not in power_plants.csv: {missing}; run scrape_power_plants.py --states first")

    downloads = Path(tempfile.mkdtemp())
    opts = webdriver.ChromeOptions()
    opts.add_experimental_option("prefs", {"download.default_directory": str(downloads)})
    d = webdriver.Chrome(options=opts)
    try:
        d.get(AGREEMENT)
        print("Read the terms in the Chrome window and click 'I Agree' at the bottom of the page...")
        if not wait_for(lambda: d.find_elements(By.NAME, "B_1"), args.wait):
            raise SystemExit("Timed out waiting for 'I Agree'.")
        print("Filling the request form and sending the query (WONDER can take a minute)...")
        fill_and_send(d, [fips_of[s.upper()] for s in args.states], args.icd)
        done = wait_for(lambda: [f for f in downloads.iterdir() if not f.name.endswith((".crdownload", ".tmp"))], 300)
        if not done:
            raise SystemExit("No export arrived within 5 minutes; check the Chrome window for an error message.")
        time.sleep(1)  # let Chrome finish renaming the file
        shutil.move(done[0], OUT)
    finally:
        d.quit()
    print(f"Saved {OUT}. Now run: python scraper/relate_weather.py")


if __name__ == "__main__":
    main()

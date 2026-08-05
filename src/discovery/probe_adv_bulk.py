"""
One-time probe: download the official SEC ADV bulk data file and print its
real column headers, so extraction code is built against verified reality
rather than a guessed schema.

Run this locally (not in a sandboxed environment) since it needs to reach
sec.gov directly.
"""
import io
import zipfile
import requests

URL = ("https://www.sec.gov/files/investment/data/other/"
       "information-about-registered-investment-advisers-exempt-reporting-advisers/"
       "ia08032026.zip")
UA = {"User-Agent": "Muhammad Ahmad research ahmadfarooq282828@gmail.com"}

print(f"downloading {URL} ...")
r = requests.get(URL, headers=UA, timeout=60)
print(f"status: {r.status_code}, size: {len(r.content)} bytes")

if r.status_code != 200:
    print("FAILED - check the URL is current on the SEC data page, "
          "the monthly filename changes")
    raise SystemExit(1)

with zipfile.ZipFile(io.BytesIO(r.content)) as z:
    names = z.namelist()
    print(f"\nfiles inside zip: {names}")

    # take the first file, likely a single CSV/XLSX
    inner = names[0]
    print(f"\nreading first 3000 chars of: {inner}")
    with z.open(inner) as f:
        data = f.read()

    if inner.lower().endswith((".csv", ".txt")):
        text = data.decode("utf-8", errors="replace")
        lines = text.split("\n")
        print("\n--- HEADER ROW (split by common delimiters) ---")
        header = lines[0]
        print(repr(header[:2000]))
        print("\n--- FIRST DATA ROW ---")
        if len(lines) > 1:
            print(repr(lines[1][:2000]))
    else:
        print(f"\nNOTE: inner file is {inner} - not plain CSV, likely needs "
              f"openpyxl or similar to read. Saving raw bytes to inspect "
              f"separately if needed.")
        with open("adv_bulk_raw.bin", "wb") as out:
            out.write(data)
        print("saved raw bytes to adv_bulk_raw.bin - open with pandas.read_excel")
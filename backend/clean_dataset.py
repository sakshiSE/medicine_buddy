
import csv
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
INPUT_FILE = DATA_DIR / "medicine_dataset.csv"
OUTPUT_FILE = DATA_DIR / "medicines_cleaned.csv"

def clean(value):
    return " ".join(str(value or "").strip().split())

seen_ids = set()
cleaned_count = 0
removed_count = 0

with open(INPUT_FILE, "r", encoding="utf-8-sig", newline="") as source:
    reader = csv.DictReader(source)

    required = {
        "id", "name", "manufacturer_name",
        "type", "pack_size_label",
        "short_composition1", "short_composition2",
        "Is_discontinued"
    }

    if not required.issubset(reader.fieldnames or []):
        raise ValueError("CSV is missing one or more required columns.")

    with open(OUTPUT_FILE, "w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
        writer.writeheader()

        for row in reader:
            row = {key: clean(value) for key, value in row.items()}

            medicine_id = row["id"]
            name = row["name"]

            if not name or medicine_id in seen_ids:
                removed_count += 1
                continue

            if not row["short_composition1"] and not row["short_composition2"]:
                removed_count += 1
                continue

            seen_ids.add(medicine_id)

            status = row["Is_discontinued"].upper()
            if status in ("TRUE", "FALSE"):
                row["Is_discontinued"] = status
            else:
                row["Is_discontinued"] = ""

            writer.writerow(row)
            cleaned_count += 1

print("\nDataset cleaning completed!")
print(f"Cleaned records: {cleaned_count}")
print(f"Removed records: {removed_count}")
print(f"Saved file: {OUTPUT_FILE}")

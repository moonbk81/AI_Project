import csv
import yaml

INPUT_CSV = "csv/rag_golden_eval_details.csv"
OUTPUT_YAML = "csv/rag_golden_eval_details_output.yaml"

data = []

with open(INPUT_CSV, "r", encoding="utf-8") as csv_file:
    reader = csv.DictReader(csv_file)

    for row in reader:
        # 숫자 자동 변환 (선택사항)
        converted_row = {}

        for key, value in row.items():
            value = value.strip()

            if value.isdigit():
                converted_row[key] = int(value)
            else:
                try:
                    converted_row[key] = float(value)
                except ValueError:
                    converted_row[key] = value

        data.append(converted_row)

with open(OUTPUT_YAML, "w", encoding="utf-8") as yaml_file:
    yaml.dump(
        data,
        yaml_file,
        allow_unicode=True,
        sort_keys=False
    )

print(f"Converted: {INPUT_CSV} -> {OUTPUT_YAML}")

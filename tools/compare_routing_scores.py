import argparse
import csv
from pathlib import Path

def load_csv(path):
    rows = {}

    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for row in reader:
            key = f"{row.get('suite', '')}:{row.get('case_id', '')}"
            rows[key] = row

    return rows

def to_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except ValueError:
        return None

def to_bool(value):
    return str(value).lower() in {"true", "1", "yes", "passed"}

def fmt_score(value):
    if value is None:
        return "-"
    return f"{value:.3f}"

def compare_reports(before_path, after_path, min_delta=0.01):
    before = load_csv(before_path)
    after = load_csv(after_path)

    all_keys = sorted(set(before.keys()) | set(after.keys()))

    pass_changed = []
    score_changed = []
    missing = []

    for key in all_keys:
        old = before.get(key)
        new = after.get(key)

        if old is None or new is None:
            missing.append((key, old is not None, new is not None))
            continue

        old_pass = to_bool(old.get("passed"))
        new_pass = to_bool(new.get("passed"))

        old_top1 = to_float(old.get("top1_score"))
        new_top1 = to_float(new.get("top1_score"))

        old_top2 = to_float(old.get("top2_score"))
        new_top2 = to_float(new.get("top2_score"))

        top1_delta = None
        top2_delta = None

        if old_top1 is not None and new_top1 is not None:
            top1_delta = new_top1 - old_top1

        if old_top2 is not None and new_top2 is not None:
            top2_delta = new_top2 - old_top2

        if old_pass != new_pass:
            pass_changed.append({
                "key": key,
                "query": new.get("query") or old.get("query"),
                "old_pass": old_pass,
                "new_pass": new_pass,
                "old_top1": old.get("top1_intent"),
                "new_top1": new.get("top1_intent"),
                "old_top1_score": old_top1,
                "new_top1_score": new_top1,
            })

        if top1_delta is not None and abs(top1_delta) >= min_delta:
            score_changed.append({
                "key": key,
                "query": new.get("query") or old.get("query"),
                "old_top1": old.get("top1_intent"),
                "new_top1": new.get("top1_intent"),
                "old_top1_score": old_top1,
                "new_top1_score": new_top1,
                "top1_delta": top1_delta,
                "old_top2": old.get("top2_intent"),
                "new_top2": new.get("top2_intent"),
                "old_top2_score": old_top2,
                "new_top2_score": new_top2,
                "top2_delta": top2_delta,
            })

    print("\n=== Routing Report Compare ===")
    print(f"Before: {before_path}")
    print(f"After : {after_path}")
    print(f"Cases : {len(all_keys)}")
    print(f"PASS changed : {len(pass_changed)}")
    print(f"Top1 score changed >= {min_delta}: {len(score_changed)}")
    print(f"Missing cases : {len(missing)}")

    if pass_changed:
        print("\n=== PASS / FAIL Changes ===")
        for item in pass_changed:
            direction = "✅ FAIL → PASS" if item["new_pass"] else "❌ PASS → FAIL"
            print(f"\n{direction} | {item['key']}")
            print(f"Query: {item['query']}")
            print(
                f"Top1: {item['old_top1']} {fmt_score(item['old_top1_score'])}"
                f" → {item['new_top1']} {fmt_score(item['new_top1_score'])}"
            )

    if score_changed:
        print("\n=== Top1 Score Changes ===")

        score_changed.sort(key=lambda x: abs(x["top1_delta"]), reverse=True)

        for item in score_changed:
            arrow = "▲" if item["top1_delta"] > 0 else "▼"
            print(f"\n{arrow} {item['key']} | Δ {item['top1_delta']:+.3f}")
            print(f"Query: {item['query']}")
            print(
                f"Top1: {item['old_top1']} {fmt_score(item['old_top1_score'])}"
                f" → {item['new_top1']} {fmt_score(item['new_top1_score'])}"
            )

            if item["top2_delta"] is not None:
                print(
                    f"Top2: {item['old_top2']} {fmt_score(item['old_top2_score'])}"
                    f" → {item['new_top2']} {fmt_score(item['new_top2_score'])}"
                    f" | Δ {item['top2_delta']:+.3f}"
                )

    if missing:
        print("\n=== Missing Cases ===")
        for key, in_before, in_after in missing:
            print(f"{key} | before={in_before}, after={in_after}")

def find_latest_two_reports(report_dir):
    report_dir = Path(report_dir)
    files = sorted(report_dir.glob("routing_scores_*.csv"))

    if len(files) < 2:
        raise FileNotFoundError("비교할 CSV가 최소 2개 필요합니다.")

    return files[-2], files[-1]

def main():
    parser = argparse.ArgumentParser(description="Compare routing score CSV reports.")
    parser.add_argument("--before", type=str, help="이전 routing_scores CSV 경로")
    parser.add_argument("--after", type=str, help="이후 routing_scores CSV 경로")
    parser.add_argument("--report-dir", type=str, default="test_reports")
    parser.add_argument("--min-delta", type=float, default=0.01)

    args = parser.parse_args()

    if args.before and args.after:
        before_path = Path(args.before)
        after_path = Path(args.after)
    else:
        before_path, after_path = find_latest_two_reports(args.report_dir)

    compare_reports(before_path, after_path, min_delta=args.min_delta)

if __name__ == "__main__":
    main()
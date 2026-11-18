"""Suggest a local grocery list or compare rankers on held-out grocery trips."""

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean, median

COLUMNS = {"shopper_id", "trip_id", "purchase_date", "item"}
METHODS = ("previous-trip", "cadence")
LIMIT = 5


@dataclass
class Trip:
    shopper_id: str
    trip_id: str
    purchase_date: date
    items: dict[str, str]  # Normalized item key to display name.


@dataclass(frozen=True)
class Suggestion:
    item: str
    reason: str


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return parsed
    except ValueError:
        pass
    raise ValueError(f"Expected a YYYY-MM-DD date, got {value!r}")


def load_trips(path: Path) -> list[Trip]:
    """Read completed trips and collapse repeated item rows within each trip."""
    trips: dict[tuple[str, str], Trip] = {}
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file, strict=True)
        headers = reader.fieldnames or []
        missing = COLUMNS.difference(headers)
        if missing:
            raise ValueError(f"Missing CSV columns: {', '.join(sorted(missing))}")
        if len(headers) != len(set(headers)):
            raise ValueError("CSV column names must be unique")

        for row in reader:
            line = f"CSV line {reader.line_num}"
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{line}: row length differs from the header")
            shopper_id = row["shopper_id"].strip()
            trip_id = row["trip_id"].strip()
            name = " ".join(row["item"].split())
            if not shopper_id or not trip_id or not name:
                raise ValueError(f"{line}: shopper_id, trip_id and item are required")
            try:
                purchased = parse_date(row["purchase_date"].strip())
            except ValueError as error:
                raise ValueError(f"{line}: {error}") from error
            key = (shopper_id, trip_id)
            if key not in trips:
                trips[key] = Trip(shopper_id, trip_id, purchased, {})
            trip = trips[key]
            if trip.purchase_date != purchased:
                raise ValueError(f"{line}: a shopper's trip_id has multiple dates")
            item_key = name.casefold()
            trip.items.setdefault(item_key, name)
    return list(trips.values())


def _trip_order(trip: Trip) -> tuple[date, str]:
    return trip.purchase_date, trip.trip_id


def _days(value: float) -> str:
    return f"{value:g} {'day' if value == 1 else 'days'}"


def _rank(history: list[Trip], planned_date: date, method: str) -> list[tuple[str, Suggestion]]:
    if not history:
        return []
    if method == "previous-trip":
        latest = max(history, key=_trip_order)
        elapsed = (planned_date - latest.purchase_date).days
        return [
            (key, Suggestion(latest.items[key], f"You bought this on your last trip, {_days(elapsed)} ago."))
            for key in sorted(latest.items)[:LIMIT]
        ]

    purchases: dict[str, list[Trip]] = defaultdict(list)
    for trip in sorted(history, key=_trip_order):
        for key in trip.items:
            purchases[key].append(trip)

    ranked = []
    for key, item_trips in purchases.items():
        latest = item_trips[-1]
        elapsed = (planned_date - latest.purchase_date).days
        name = latest.items[key]
        dates = sorted({trip.purchase_date for trip in item_trips})
        count = len(item_trips)
        last_purchase = f"It’s been {_days(elapsed)} since your last purchase."
        if len(dates) >= 3:
            gap = median((later - earlier).days for earlier, later in zip(dates, dates[1:]))
            if elapsed < gap:
                continue
            order = (0, -elapsed / gap, -count, elapsed, key)
            reason = f"You usually buy {name} every {_days(gap)}. {last_purchase}"
        else:
            order = (1, 0, -count, elapsed, key)
            noun = "trip" if count == 1 else "trips"
            reason = f"You bought {name} on {count} {noun}. {last_purchase}"
        ranked.append((order, key, Suggestion(name, reason)))

    ranked.sort(key=lambda entry: entry[0])
    return [(key, suggestion) for _, key, suggestion in ranked[:LIMIT]]


def recommend(
    trips: list[Trip], shopper_id: str, planned_date: date, method: str = "cadence"
) -> list[Suggestion]:
    if method not in METHODS:
        raise ValueError(f"Unknown grocery ranking method: {method}")
    history = [
        trip for trip in trips
        if trip.shopper_id == shopper_id and trip.purchase_date < planned_date
    ]
    return [suggestion for _, suggestion in _rank(history, planned_date, method)]


def evaluate(trips: list[Trip]) -> dict:
    """Compare rankers on the latest dated trip(s) for each shopper."""
    by_shopper: dict[str, list[Trip]] = defaultdict(list)
    for trip in trips:
        by_shopper[trip.shopper_id].append(trip)

    results: dict[str, list[tuple[float, float]]] = {method: [] for method in METHODS}
    without_history = held_out_count = without_repeats = 0
    for shopper_trips in by_shopper.values():
        cutoff = max(trip.purchase_date for trip in shopper_trips)
        history = [trip for trip in shopper_trips if trip.purchase_date < cutoff]
        if not history:
            without_history += 1
            continue
        prior_items = {key for trip in history for key in trip.items}
        predictions = {
            method: {key for key, _ in _rank(history, cutoff, method)}
            for method in METHODS
        }
        for held_out in shopper_trips:
            if held_out.purchase_date != cutoff:
                continue
            held_out_count += 1
            repeat_targets = prior_items.intersection(held_out.items)
            if not repeat_targets:
                without_repeats += 1
                continue
            for method, predicted in predictions.items():
                hits = len(predicted.intersection(repeat_targets))
                results[method].append((hits / LIMIT, hits / len(repeat_targets)))

    return {
        "shoppers": len(by_shopper),
        "shoppers_without_prior_trips": without_history,
        "held_out_trips": held_out_count,
        "no_repeat_target_trips": without_repeats,
        "evaluated_trips": held_out_count - without_repeats,
        "metrics": {
            method: {
                "precision_at_5": mean(precision for precision, _ in scores) if scores else None,
                "recall_at_5": mean(recall for _, recall in scores) if scores else None,
            }
            for method, scores in results.items()
        },
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    suggest = subcommands.add_parser("suggest", help="Make a list for one shopper")
    suggest.add_argument("--csv", type=Path, required=True)
    suggest.add_argument("--shopper-id", required=True)
    suggest.add_argument("--planned-date", required=True, metavar="YYYY-MM-DD")
    suggest.add_argument("--method", choices=METHODS, default="cadence")
    compare = subcommands.add_parser("evaluate", help="Compare both methods on later trips")
    compare.add_argument("--csv", type=Path, required=True)
    compare.add_argument(
        "--data-kind", choices=("real", "synthetic"), required=True,
        help="Declare whether the CSV contains real or synthetic trips. Demo metrics are suppressed.",
    )
    args = parser.parse_args(argv)

    try:
        trips = load_trips(args.csv)
        if args.command == "suggest":
            items = recommend(trips, args.shopper_id.strip(), parse_date(args.planned_date), args.method)
            if not items:
                print("No grocery suggestions for this shopper on the planned date.")
            for index, suggestion in enumerate(items, 1):
                print(f"{index}. {suggestion.item}: {suggestion.reason}")
        else:
            report = evaluate(trips)
            if args.data_kind == "synthetic":
                del report["metrics"]
                report["note"] = "Synthetic demo data. Protocol check only."
            report["data_kind"] = args.data_kind
            print(json.dumps(report, indent=2))
    except (OSError, UnicodeError, ValueError, csv.Error) as error:
        parser.exit(1, f"Grocery command failed: {error}\n")


if __name__ == "__main__":
    main()

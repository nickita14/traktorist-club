import statistics

from django.core.management.base import BaseCommand

from club import achievements

COUNTER_NAMES = {
    "veteran": "finished games played",
    "feeder": "sum of buy-ins, lei",
    "addon": "known add-ons",
}


class Command(BaseCommand):
    help = (
        "Print the rank ladders' counters (per player, quartiles, players per rung) and how "
        "complete the data is, to set real thresholds. Reads only; writes nothing."
    )

    def handle(self, *args, **options):
        loaded = achievements.load()
        counters = loaded.counters()
        for ladder in loaded.ladders:
            self._ladder(ladder, counters[ladder.code], loaded.players)
        self._completeness(loaded.completeness())

    def _ladder(self, ladder, values, players):
        self.stdout.write(f"{ladder.title} [{ladder.code}]: {COUNTER_NAMES[ladder.code]}")
        ordered = sorted(values.items(), key=lambda item: (-item[1], players[item[0]].label))
        for pk, value in ordered:
            self.stdout.write(f"  {value:>7}  {players[pk].label}")
        numbers = [value for _, value in ordered]
        if len(numbers) >= 2:
            q1, median, q3 = statistics.quantiles(numbers, n=4, method="inclusive")
            self.stdout.write(f"  quartiles: Q1 {q1:g}, median {median:g}, Q3 {q3:g}")
        elif numbers:
            self.stdout.write(f"  quartiles: one player, {numbers[0]}")
        else:
            self.stdout.write("  no players")
        self.stdout.write("  players per rung with the current steps:")
        first = ladder.steps[0].threshold if ladder.steps else None
        if first is not None:
            below = sum(1 for value in numbers if value < first)
            self.stdout.write(f"    {'below the first step':<30} {below:>4}")
        for index, step in enumerate(ladder.steps):
            upper = ladder.steps[index + 1].threshold if index + 1 < len(ladder.steps) else None
            count = sum(
                1
                for value in numbers
                if value >= step.threshold and (upper is None or value < upper)
            )
            self.stdout.write(f"    {f'{step.title} ({step.threshold})':<30} {count:>4}")
        self.stdout.write("")

    def _completeness(self, data):
        self.stdout.write("Completeness (finished games)")
        rows = [
            ("tournaments with every place known", data.tournaments_all_places, data.tournaments),
            ("tour results with rebuys known", data.rebuys_known, data.tour_results),
            ("tour results with add-on known", data.addon_known, data.tour_results),
        ]
        for label, known, total in rows:
            self.stdout.write(f"  {label}: {known} of {total}")

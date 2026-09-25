#!/usr/bin/env python3
"""Search tower layouts where the top floor is reachable but some floor is not.

Rules:
  - Floors -B..-1 (basement) and 0 (ground) are connected by stairs; the player
    starts on the ground floor, so batteries there are always collected.
  - Floors 1..F are only reached by the cannon on floor -B. Loading any 1..S
    collected batteries with total V lands the player on floor -B + V.
  - Every hatch drops the player back to floor -B.
"""

import argparse
import itertools
import random
import sys


def subset_sums(volts, max_slots):
    """Map each achievable sum to one combination (tuple of volts) using 1..max_slots batteries."""
    sums = {}
    for r in range(1, min(max_slots, len(volts)) + 1):
        for combo in itertools.combinations(volts, r):
            sums.setdefault(sum(combo), combo)
    return sums


def explore(F, B, S, batteries, extra=()):
    """batteries: dict floor -> volts; extra: volts brought in from elsewhere.
    Returns (explored floor -> cannon combo, collected volts, sequence)."""
    explored = {f: None for f in range(-B, 1)}
    collected = [v for f, v in batteries.items() if f <= 0] + list(extra)
    sequence = [f"Walk ground + basement (floors {-B}..0), collect {sorted(collected) or 'nothing'}"]

    while True:
        sums = subset_sums(collected, S)
        reachable = {s - B: combo for s, combo in sums.items() if 1 <= s - B <= F}
        new = {f: c for f, c in reachable.items() if f not in explored}
        if not new:
            break
        with_battery = [f for f in new if f in batteries]
        target = min(with_battery) if with_battery else min(new)
        combo = new[target]
        explored[target] = combo
        step = f"Cannon with {list(combo)} (={sum(combo)}V) -> floor {target}"
        if target in batteries:
            collected.append(batteries[target])
            step += f", collect {batteries[target]}V"
        sequence.append(step + f", fall to {-B}")
    return explored, collected, sequence


def missing_batteries(F, B, S, collected, floor, max_volt):
    """Single extra voltages that would make `floor` reachable."""
    need = floor + B
    partial = {0} | set(subset_sums(collected, S - 1)) if S > 1 else {0}
    return sorted({need - p for p in partial if 1 <= need - p <= max_volt})


def unlock_sets(F, B, S, batteries, max_volt, n):
    """Sets of exactly n extra batteries that make every floor reachable, or None if fewer than n suffice."""
    for k in range(1, n + 1):
        found = [extra for extra in itertools.combinations_with_replacement(range(1, max_volt + 1), k)
                 if len(explore(F, B, S, batteries, extra)[0]) == F + B + 1]
        if found:
            return found if k == n else None
    return []


def evaluate(F, B, S, batteries, max_volt, min_unreachable, max_unreachable=None, extra_needed=None):
    explored, collected, sequence = explore(F, B, S, batteries)
    if F not in explored:
        return None
    unreachable = [f for f in range(1, F + 1) if f not in explored]
    if len(unreachable) < min_unreachable:
        return None
    if max_unreachable is not None and len(unreachable) > max_unreachable:
        return None
    sets = None
    if extra_needed is not None:
        sets = unlock_sets(F, B, S, batteries, max_volt, extra_needed)
        if not sets:
            return None
    return {
        "unlock_sets": sets,
        "F": F, "B": B, "S": S,
        "batteries": dict(sorted(batteries.items())),
        "combos": {f: c for f, c in explored.items() if c is not None},
        "unreachable": {
            f: {"needs_sum": f + B, "missing_single_battery": missing_batteries(F, B, S, collected, f, max_volt)}
            for f in unreachable
        },
        "sequence": sequence,
    }


def layouts_exhaustive(F, B, max_batteries, max_volt):
    floors = range(-B, F + 1)
    for k in range(1, max_batteries + 1):
        for positions in itertools.combinations(floors, k):
            if not any(p <= 0 for p in positions):
                continue  # nothing to start the cannon with
            for volts in itertools.product(range(1, max_volt + 1), repeat=k):
                yield dict(zip(positions, volts))


def layouts_random(F, B, max_batteries, max_volt, samples, rng):
    floors = list(range(-B, F + 1))
    for _ in range(samples):
        k = rng.randint(1, max_batteries)
        positions = rng.sample(floors, k)
        if not any(p <= 0 for p in positions):
            positions[0] = rng.randint(-B, 0)
            positions = list(dict.fromkeys(positions))
        yield {p: rng.randint(1, max_volt) for p in positions}


def draw_tower(sol):
    """ASCII tower: '|' walls = explored, '#' walls = unreachable, '=' walls = walkable by stairs."""
    F, B = sol["F"], sol["B"]
    width = 12
    lines = [" " * 7 + "_" * (width + 2)]
    for f in range(F, -B - 1, -1):
        room = f"[{sol['batteries'][f]}V]" if f in sol["batteries"] else ""
        if f == -B:
            room = (room + " CANNON").strip()
        if f in sol["unreachable"]:
            info = sol["unreachable"][f]
            wall = "#"
            note = (f"LOCKED: needs {info['needs_sum']}V, add "
                    + " or ".join(f"{v}V" for v in info["missing_single_battery"]))
        elif f > 0:
            combo = sol["combos"][f]
            wall = "|"
            note = f"cannon {' + '.join(f'{v}V' for v in combo)} = {sum(combo)}V"
        else:
            wall = "="
            note = "ground floor (start), stairs down" if f == 0 else "basement, stairs"
        lines.append(f"{f:>5}  {wall}{room.center(width)}{wall}   {note}")
    lines.append(" " * 7 + "=" * (width + 2))
    return lines


def format_solution(sol):
    lines = [f"F={sol['F']} B={sol['B']} S={sol['S']}"]
    lines += ["  " + line for line in draw_tower(sol)]
    lines.append("  Sequence:")
    lines += [f"    {i}. {s}" for i, s in enumerate(sol["sequence"], 1)]
    if sol["unlock_sets"]:
        sets = sol["unlock_sets"]
        shown = ", ".join("[" + " + ".join(f"{v}V" for v in s) + "]" for s in sets[:8])
        more = f" ... ({len(sets)} options)" if len(sets) > 8 else ""
        lines.append(f"  Unlock all floors with {len(sets[0])} extra batteries: {shown}{more}")
    return "\n".join(lines)


def parse_range(text):
    lo, _, hi = text.partition("-")
    return range(int(lo), int(hi or lo) + 1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--F", type=parse_range, default=parse_range("6-8"), help="floor range, e.g. 6-10 (min 5)")
    p.add_argument("--B", type=parse_range, default=parse_range("1-2"), help="basement range, e.g. 1-3 (min 1)")
    p.add_argument("--S", type=parse_range, default=parse_range("2-3"), help="slot range, e.g. 2-4 (min 2)")
    p.add_argument("--max-batteries", type=int, default=3, help="max batteries placed in the tower")
    p.add_argument("--max-volt", type=int, default=None, help="max battery voltage (default F+B)")
    p.add_argument("--min-unreachable", type=int, default=1, help="min number of unreachable floors")
    p.add_argument("--max-unreachable", type=int, default=None, help="max number of unreachable floors")
    p.add_argument("--unreachable", type=int, default=None, metavar="X",
                   help="exactly X unreachable floors (overrides min/max)")
    p.add_argument("--extra", type=int, default=None, metavar="N",
                   help="exactly N batteries from elsewhere are the minimum needed to unlock every floor")
    p.add_argument("--mode", choices=["exhaustive", "random"], default="exhaustive")
    p.add_argument("--samples", type=int, default=100000, help="samples per (F,B,S) in random mode")
    p.add_argument("--limit", type=int, default=5, help="solutions printed per (F,B,S); 0 = all")
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()

    if args.F.start < 5 or args.B.start < 1 or args.S.start < 2:
        sys.exit("Constraints: F >= 5, B >= 1, S >= 2")
    if args.unreachable is not None:
        args.min_unreachable = args.max_unreachable = args.unreachable

    rng = random.Random(args.seed)
    for F, B, S in itertools.product(args.F, args.B, args.S):
        max_volt = args.max_volt or F + B
        if args.mode == "exhaustive":
            layouts = layouts_exhaustive(F, B, args.max_batteries, max_volt)
        else:
            layouts = layouts_random(F, B, args.max_batteries, max_volt, args.samples, rng)

        seen, found, shown = set(), 0, 0
        for batteries in layouts:
            key = tuple(sorted(batteries.items()))
            if key in seen:
                continue
            seen.add(key)
            sol = evaluate(F, B, S, batteries, max_volt, args.min_unreachable, args.max_unreachable, args.extra)
            if sol is None:
                continue
            found += 1
            if not args.limit or shown < args.limit:
                print(format_solution(sol), end="\n\n")
                shown += 1
        print(f"== F={F} B={B} S={S}: {found} valid layouts out of {len(seen)} checked ==\n")


if __name__ == "__main__":
    main()

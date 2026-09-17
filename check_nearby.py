"""Check the checkpoint service from the GCS machine.

Calls the configured /nearby endpoint and reports whether this GCS can read
what comes back, naming any field it cannot place. Run it on the machine that
runs the GCS, so it uses the same network path the app does:

    python check_nearby.py
    python check_nearby.py --latitude 18.5204 --longitude 73.8567 --radius-m 5000
    python check_nearby.py --base http://100.111.81.89:8000

Exits non-zero if the lookup could not be read, so it can gate a smoke test.
"""

import argparse
import json
import sys

import config
from core.checkpoint_client import (checkpoint_distance_m, checkpoint_name,
                                    checkpoint_position,
                                    checkpoints_from_response,
                                    fetch_nearby_checkpoints)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=None,
                        help="service root; defaults to config.CHECKPOINT_API_BASE")
    parser.add_argument("--latitude", type=float, default=18.5204)
    parser.add_argument("--longitude", type=float, default=73.8567)
    parser.add_argument("--radius-m", type=float, default=5000.0,
                        help="metres; 5 km matches an equipment max_range_km of 5")
    parser.add_argument("--timeout", type=float,
                        default=getattr(config, "CHECKPOINT_API_TIMEOUT", 5.0))
    return parser.parse_args()


def main():
    args = parse_args()
    base = args.base or getattr(config, "CHECKPOINT_API_BASE", "")

    if not base:
        print("CHECKPOINT_API_BASE is empty in config.py and --base was not "
              "given, so the GCS would not query at all.")
        return 2

    print(f"service      {base}")
    print(f"centre       {args.latitude}, {args.longitude}")
    print(f"radius_m     {args.radius_m}")
    print()

    payload = fetch_nearby_checkpoints(base, args.latitude, args.longitude,
                                       args.radius_m, timeout=args.timeout)
    print()
    if payload is None:
        print("RESULT: no readable reply. The error above says why — a refused "
              "connection or timeout usually means the wrong port, and a 404 "
              "means the path is not /nearby on this service.")
        return 1

    print("--- wrapper ---")
    for key in ("center", "radius_m", "checkpoint_count"):
        state = "present" if key in payload else "MISSING"
        print(f"  {key:17} {state}"
              f"{'' if key not in payload else '  ' + json.dumps(payload[key])}")
    extra = [k for k in payload if k not in
             ("center", "radius_m", "checkpoint_count", "checkpoints")]
    if extra:
        print(f"  other keys        {', '.join(sorted(extra))}")

    checkpoints = checkpoints_from_response(payload)
    reported = payload.get("checkpoint_count")
    print()
    print("--- checkpoints ---")
    print(f"  checkpoint_count  {reported}")
    print(f"  readable as list  {len(checkpoints)}")

    if reported and not checkpoints:
        print()
        print("RESULT: the service reports checkpoints but this GCS could not "
              "read the `checkpoints` value as a list. Its type is "
              f"{type(payload.get('checkpoints')).__name__}.")
        return 1

    if not checkpoints:
        print()
        print("RESULT: the service replied correctly and found nothing at this "
              "centre and radius. The panel will say '0 checkpoints'. Try a "
              "larger --radius-m, or a centre nearer your checkpoint data.")
        return 0

    print()
    unplaceable = []
    for index, checkpoint in enumerate(checkpoints, 1):
        name = checkpoint_name(checkpoint)
        lat, lon = checkpoint_position(checkpoint)
        away = checkpoint_distance_m(checkpoint)
        away_text = f", {away:,.0f} m away" if away is not None else ""
        if lat is None or lon is None:
            unplaceable.append(checkpoint)
            print(f"  {index:>3}. {name}: NO USABLE POSITION{away_text}")
        else:
            print(f"  {index:>3}. {name}: {lat:.6f}, {lon:.6f}{away_text}")

    print()
    print("--- first checkpoint, verbatim ---")
    print(json.dumps(checkpoints[0], indent=2, default=str)[:1200])

    print()
    if unplaceable:
        keys = sorted({k for c in unplaceable if isinstance(c, dict) for k in c})
        print(f"RESULT: {len(unplaceable)} of {len(checkpoints)} checkpoints "
              f"have no position this GCS recognises, so they are listed in "
              f"the side panel but drawn on no map marker.")
        print(f"        Fields present: {', '.join(keys) or '(none)'}")
        print("        Send this output on and the reader can be taught these "
              "names.")
        return 1

    print(f"RESULT: all {len(checkpoints)} checkpoints read cleanly. The side "
          f"panel will list them and the map will mark each one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

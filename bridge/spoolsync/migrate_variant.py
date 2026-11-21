#!/usr/bin/env python3
"""Migrate existing Spoolman filaments to include extra.variant from filament name.

Reads the Spoolman API, parses the filament name to extract subtype, and writes
extra.variant for filaments that don't already have one.

This is a ONE-TIME migration to populate extra.variant for existing filaments
that were created before the spool-sync bridge fix was deployed. After the
bridge fix is deployed, the reconcile loop will handle all future updates
automatically.

Usage:
    python3 migrate_variant.py --base-url http://block:7912
    python3 migrate_variant.py --base-url http://192.168.0.100:7912 --dry-run

Options:
    --base-url      Spoolman base URL (default: http://block:7912)
    --dry-run       Show what would be changed without making changes
    --filament-id   Migrate only a specific filament ID (for testing)
"""

import argparse
import json
import re
import sys

import httpx


# Common variant keywords that appear in filament names.
# Order matters: check multi-word variants before single-word ones.
# The FIRST match wins, so order these from most specific to least specific.
VARIANT_KEYWORDS = [
    # Multi-word variants (most specific first)
    "High Speed",     # e.g. "High Speed Red"
    "for AMS",        # e.g. "TPU for AMS"
    # Single-word variants
    "Silk",           # matches both "Silk" and "Silk+" (simpler, no regex edge cases)
    "Matte",          # e.g. "Matte Black"
    "Glow",           # e.g. "Glow Green"
    "Marble",         # e.g. "PLA Marble"
    "Sparkle",        # e.g. "PLA Sparkle"
    "Tough",          # e.g. "PLA Tough"
    "Aero",           # e.g. "PLA Aero"
    "Dynamic",        # e.g. "PLA Dynamic"
    "Galaxy",         # e.g. "PLA Galaxy"
    "Wood",           # e.g. "Wood Brown"
    "Translucent",    # e.g. "PLA Translucent"
    "Lite",           # e.g. "PLA Lite"
    "Metal",          # e.g. "PLA Metal"
    "Rapid",          # e.g. "PETG Rapid"
    "HF",             # e.g. "PETG HF"
    "CF",             # e.g. "ASA CF Black"
    "GF",             # e.g. "PA6-GF"
    "FR",             # e.g. "PC FR"
    "Basic",          # e.g. "Basic Dark Blue"
    "Pro",            # e.g. "Pro White"
    "Ultra",          # e.g. "PLA Ultra"
    "Premium",        # e.g. "PETG Premium"
]


def _variant_pattern(keyword: str) -> re.Pattern[str]:
    """Build a regex that matches the keyword as a standalone token.

    Uses \\b word boundaries where possible, but handles keywords ending
    (or starting) with non-word characters (e.g. "Silk+") by substituting
    lookahead/lookbehind for the impossible \\b.

    Examples:
        "Silk"        → \\bSilk\\b       (word boundary on both sides)
        "High Speed"  → \\bHigh Speed\\b (space is a word boundary)
        "Silk+"       → (?<!\\w)Silk\\+(?!\\w)  (lookaround for +)
    """
    escaped = re.escape(keyword)
    # If first char is \\w, use \\b; otherwise use lookbehind for non-\\w or start
    if re.match(r'\w', keyword):
        prefix = r'\b'
    else:
        prefix = r'(?<!\w)'
    # If last char is \\w, use \\b; otherwise use lookahead for non-\\w or end
    if re.match(r'\w', keyword[-1]):
        suffix = r'\b'
    else:
        suffix = r'(?!\w)'
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


def parse_variant_from_name(name: str) -> str | None:
    """Best-effort extraction of variant from filament name.

    Spoolman filament names follow patterns like:
      - "<subtype> <material> <color>" (e.g., "Matte Black")
      - "<material> <subtype> <color>" (e.g., "PLA Matte")
      - "<brand> <material> <color>" (e.g., "CR-PETG White")

    We scan for known variant keywords and return the first match.

    Args:
        name: The filament name from Spoolman (e.g., "Matte Black")

    Returns:
        The variant string if found, None otherwise.

    Examples:
        >>> parse_variant_from_name("Matte Black")
        'Matte'
        >>> parse_variant_from_name("Basic Dark Blue")
        'Basic'
        >>> parse_variant_from_name("Silk+ Red")
        'Silk'
        >>> parse_variant_from_name("Silk PLA White")
        'Silk'
    """
    if not name:
        return None

    for variant in VARIANT_KEYWORDS:
        if _variant_pattern(variant).search(name):
            return variant

    return None


def migrate_filaments(base_url: str, dry_run: bool = False,
                      filament_id: int | None = None) -> tuple[int, int, int]:
    """Migrate Spoolman filaments to include extra.variant.

    Args:
        base_url: Spoolman base URL (e.g., "http://block:7912")
        dry_run: If True, only show what would change
        filament_id: If set, only migrate this specific filament

    Returns:
        (migrated, skipped, errors) counts
    """
    base = base_url.rstrip("/")
    with httpx.Client(
        base_url=base,
        timeout=30.0,
        headers={"Content-Type": "application/json"},
    ) as http:
        # Fetch filaments
        if filament_id is not None:
            resp = http.get(f"/api/v1/filament/{filament_id}")
            resp.raise_for_status()
            filaments = [resp.json()]
        else:
            resp = http.get("/api/v1/filament")
            resp.raise_for_status()
            filaments = resp.json()

        migrated = 0
        skipped = 0
        errors = 0

        for f in filaments:
            fid = f["id"]
            name = f.get("name", "")
            material = f.get("material", "")
            extra = f.get("extra", {})

            # Skip if variant already set
            if extra.get("variant"):
                skipped += 1
                continue

            # Extract variant from name
            variant = parse_variant_from_name(name)
            if not variant:
                skipped += 1
                continue

            # Build the PATCH body
            body = {"extra": {"variant": json.dumps(variant)}}

            if dry_run:
                print(f"  [DRY-RUN] Would update filament {fid}: '{name}' ({material}) → variant='{variant}'")
                migrated += 1  # Count as "would migrate"
                continue

            # Write variant
            try:
                resp = http.patch(f"/api/v1/filament/{fid}", json=body)
                resp.raise_for_status()
                migrated += 1
                print(f"  Migrated filament {fid}: '{name}' ({material}) → variant='{variant}'")
            except Exception as e:
                errors += 1
                print(f"  ERROR migrating filament {fid}: {e}", file=sys.stderr)

        return migrated, skipped, errors


def main():
    parser = argparse.ArgumentParser(
        description="Migrate Spoolman filaments to include extra.variant from filament name.",
        epilog="This is a ONE-TIME migration. After the spool-sync bridge fix is deployed, "
               "the reconcile loop will handle all future updates automatically.",
    )
    parser.add_argument(
        "--base-url",
        default="http://block:7912",
        help="Spoolman base URL (default: http://block:7912)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without making changes",
    )
    parser.add_argument(
        "--filament-id",
        type=int,
        default=None,
        help="Migrate only a specific filament ID (for testing)",
    )
    args = parser.parse_args()

    print(f"Spoolman URL: {args.base_url}")
    print(f"Dry run: {args.dry_run}")
    if args.filament_id:
        print(f"Target filament ID: {args.filament_id}")
    print()

    migrated, skipped, errors = migrate_filaments(
        args.base_url,
        dry_run=args.dry_run,
        filament_id=args.filament_id,
    )

    print()
    print(f"Migration complete:")
    print(f"  Migrated:  {migrated}")
    print(f"  Skipped:   {skipped} (no variant found or already set)")
    print(f"  Errors:    {errors}")

    if args.dry_run:
        print()
        print("This was a dry run. Remove --dry-run to apply changes.")

    sys.exit(1 if errors > 0 else 0)


if __name__ == "__main__":
    main()

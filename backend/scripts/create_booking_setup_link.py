from __future__ import annotations

import argparse
from typing import Any

from app import main as app_main


def create_link(client_id: str, *, valid_hours: int, runtime: Any = None) -> str:
    booking_runtime = runtime or app_main.booking_runtime
    if booking_runtime is None:
        raise RuntimeError(
            "Booking setup is not configured. Set the booking encryption, session, "
            "public URL, and Supabase environment variables."
        )
    return str(
        booking_runtime.create_setup_link(client_id, valid_hours=valid_hours)
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a single-use contractor booking setup link."
    )
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--valid-hours", type=int, default=24)
    args = parser.parse_args()
    if args.valid_hours < 1 or args.valid_hours > 168:
        parser.error("--valid-hours must be between 1 and 168")
    print(create_link(args.client_id, valid_hours=args.valid_hours))


if __name__ == "__main__":
    main()

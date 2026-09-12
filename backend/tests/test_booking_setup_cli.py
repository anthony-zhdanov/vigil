from __future__ import annotations

import unittest

from scripts.create_booking_setup_link import create_link


class FakeRuntime:
    def create_setup_link(self, client_id: str, *, valid_hours: int) -> str:
        return f"https://vigil.example/setup/{client_id}?hours={valid_hours}"


class BookingSetupCliTests(unittest.TestCase):
    def test_create_link_delegates_to_configured_runtime(self) -> None:
        link = create_link(
            "client-1",
            valid_hours=12,
            runtime=FakeRuntime(),
        )

        self.assertEqual(
            link,
            "https://vigil.example/setup/client-1?hours=12",
        )

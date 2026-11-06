from datetime import date, timedelta
import unittest
from unittest.mock import patch

import test_vehicle_service

from app.models import VehicleDocument
from app.services import vehicle_document_service as documents


class DocumentStateBoundaryTests(unittest.TestCase):
    def test_state_expiration_boundaries(self):
        day = date(2026, 3, 15)
        for remaining, expected in [
            (None, "ok"),
            (-1, "overdue"),
            (0, "soon"),
            (1, "soon"),
            (14, "soon"),
            (15, "ok"),
        ]:
            with self.subTest(remaining=remaining):
                document = VehicleDocument(
                    expires_at=(
                        day + timedelta(days=remaining)
                        if remaining is not None
                        else None
                    ),
                )

                result = documents.state(document, day=day)

                self.assertEqual(result["status"], expected)
                self.assertEqual(result["remaining_days"], remaining)


class VehicleDocumentStateTests(test_vehicle_service.VehicleServiceTestCase):
    def test_expiring_today_matches_query_and_badge(self):
        vehicle = self.vehicle()
        day = date(2026, 3, 15)
        document = documents.save_document(
            self.owner,
            vehicle.id,
            dict(
                kind="osago",
                expires_at=day.isoformat(),
            ),
        )

        self.assertEqual(documents.expiring_soon(self.owner, day=day), [document])
        self.assertEqual(documents.state(document, day=day)["status"], "soon")
        self.assertEqual(documents.state(document, day=day)["remaining_days"], 0)
        self.assertEqual(
            documents.state(document, day=day + timedelta(days=1))["status"], "overdue"
        )

        self.login()
        with patch.object(documents.vehicles, "today", return_value=day):
            response = self.client.get(f"/vehicles/{vehicle.id}?tab=documents")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            '<span class="badge bg-warning">Скоро истекает</span>',
            response.get_data(as_text=True),
        )

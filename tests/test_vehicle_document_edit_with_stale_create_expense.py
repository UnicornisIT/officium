from datetime import date

import test_vehicle_service
from app.models import Expense, VehicleCost
from app.services import (
    vehicle_document_service as documents,
    vehicle_service as vehicles,
)
from extensions import db


class VehicleDocumentStaleCreateExpenseTests(
    test_vehicle_service.VehicleServiceTestCase
):
    def test_edit_linked_document_ignores_stale_create_expense(self):
        self.login()
        for payer in ("self", "organization"):
            with self.subTest(payer=payer):
                vehicle = vehicles.create_vehicle(
                    self.owner, dict(is_company=True, name="Insurance " + payer)
                )
                row = documents.save_document(
                    self.owner,
                    vehicle.id,
                    dict(
                        kind="osago",
                        title="Original title",
                        amount="15000",
                        issued_date="2026-01-15",
                        expires_at="2027-01-15",
                        create_expense="on",
                        payer=payer,
                    ),
                )
                links = (row.expense_id, row.vehicle_cost_id)
                model, spend_id = (
                    (Expense, row.expense_id)
                    if payer == "self"
                    else (VehicleCost, row.vehicle_cost_id)
                )
                spend = db.session.get(model, spend_id)
                original = {
                    column.name: getattr(spend, column.name)
                    for column in model.__table__.columns
                }
                counts = (Expense.query.count(), VehicleCost.query.count())

                response = self.client.post(
                    f"/vehicles/{vehicle.id}/documents/{row.id}",
                    data=dict(
                        kind="osago",
                        title="Updated title",
                        comment="Updated comment",
                        amount="15000",
                        issued_date="2026-02-15",
                        expires_at="2027-02-15",
                        create_expense="on",
                        payer="invalid",
                    ),
                )

                self.assertEqual(response.status_code, 302)
                self.assertTrue(
                    response.location.endswith(f"/vehicles/{vehicle.id}?tab=documents")
                )
                db.session.refresh(row)
                db.session.refresh(spend)
                self.assertEqual(row.title, "Updated title")
                self.assertEqual(row.comment, "Updated comment")
                self.assertEqual(row.issued_date, date(2026, 2, 15))
                self.assertEqual(row.expires_at, date(2027, 2, 15))
                self.assertEqual((row.expense_id, row.vehicle_cost_id), links)
                self.assertEqual(
                    (Expense.query.count(), VehicleCost.query.count()), counts
                )
                self.assertEqual(
                    {
                        column.name: getattr(spend, column.name)
                        for column in model.__table__.columns
                    },
                    original,
                )

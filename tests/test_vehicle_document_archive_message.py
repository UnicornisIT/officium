from decimal import Decimal

import test_vehicle_service
from app.models import Expense, VehicleCost
from app.services import (
    vehicle_document_service as documents,
    vehicle_service as vehicles,
)
from extensions import db


class VehicleDocumentArchiveMessageTests(test_vehicle_service.VehicleServiceTestCase):
    def test_edit_archived_vehicle_document_amount_has_document_message(self):
        for payer in ("self", "organization"):
            with self.subTest(payer=payer):
                vehicle = vehicles.create_vehicle(
                    self.owner, dict(name="Страхование " + payer, is_company=True)
                )
                row = documents.save_document(
                    self.owner,
                    vehicle.id,
                    dict(
                        kind="osago", amount="15000", create_expense="on", payer=payer
                    ),
                )
                model, spend_id = (
                    (Expense, row.expense_id)
                    if payer == "self"
                    else (VehicleCost, row.vehicle_cost_id)
                )
                vehicles.archive_vehicle(self.owner, vehicle.id, "sold")

                with self.assertRaises(ValueError) as error:
                    documents.save_document(
                        self.owner,
                        vehicle.id,
                        dict(
                            kind="osago", amount="20000", linked_amount_action="update"
                        ),
                        row.id,
                    )

                self.assertEqual(
                    str(error.exception),
                    "Нельзя изменить документ архивированного автомобиля. "
                    "Сначала восстановите автомобиль.",
                )
                self.assertEqual(row.amount, Decimal("15000"))
                self.assertEqual(
                    db.session.get(model, spend_id).amount, Decimal("15000")
                )

    def test_vehicle_edit_keeps_vehicle_message(self):
        vehicle = self.vehicle()
        vehicles.archive_vehicle(self.owner, vehicle.id, "sold")

        with self.assertRaises(ValueError) as error:
            vehicles.update_vehicle(self.owner, vehicle.id, dict(name="Новое название"))

        self.assertEqual(
            str(error.exception), "Автомобиль в архиве: сначала восстановите его."
        )

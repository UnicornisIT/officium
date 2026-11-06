import io
import unittest
from datetime import date

from werkzeug.datastructures import FileStorage

import test_vehicle_attachments as attachments
from app.models import Expense, VehicleAttachment
from extensions import db


class MonthlyReceiptTests(unittest.TestCase):
    setUp = attachments.AttachmentTests.setUp
    tearDown = attachments.AttachmentTests.tearDown
    vehicle = attachments.AttachmentTests.vehicle
    login = attachments.AttachmentTests.login

    def submit(self, expense_id=None, upload=None):
        vehicle = self.vehicle() if expense_id is None else None
        url = "/expenses" if expense_id is None else f"/expenses/edit/{expense_id}"
        return self.client.post(
            url,
            data=dict(
                amount="1250",
                title="Receipt expense",
                category="transport",
                expense_date="2026-01-15",
                is_monthly="on",
                payer="self",
                vehicle_id=(
                    str(vehicle.id)
                    if vehicle
                    else str(db.session.get(Expense, expense_id).vehicle_id)
                ),
                file=upload or (attachments.photo("PNG").stream, "receipt.png"),
            ),
            content_type="multipart/form-data",
        )

    def test_create_monthly_with_receipt(self):
        self.login()
        response = self.submit()
        self.assertEqual(response.status_code, 302)
        db.session.remove()
        self.assertEqual(Expense.query.count(), 3)
        attachment = VehicleAttachment.query.one()
        expense = db.session.get(Expense, attachment.entity_id)
        self.assertTrue(expense.is_monthly)
        self.assertEqual(expense.expense_date, date(2026, 1, 15))
        self.assertEqual(attachment.vehicle_id, expense.vehicle_id)

    def test_update_monthly_with_receipt(self):
        from app.services.expense_service import create_expense

        vehicle = self.vehicle()
        expense = create_expense(
            user_id=self.owner.id,
            vehicle_id=vehicle.id,
            amount="10",
            title="Original",
            expense_date="2026-01-15",
            is_monthly=True,
        )
        self.login()
        response = self.submit(expense.id)
        self.assertEqual(response.status_code, 302)
        db.session.remove()
        self.assertEqual(Expense.query.count(), 3)
        attachment = VehicleAttachment.query.one()
        self.assertEqual(
            db.session.get(Expense, attachment.entity_id).title, "Receipt expense"
        )

    def test_invalid_receipt_rolls_back_monthly_generation(self):
        from app.services import expense_service, vehicle_attachment_service

        vehicle = self.vehicle()
        with self.assertRaises(ValueError):
            vehicle_attachment_service.save_with_upload(
                self.owner,
                lambda **kw: expense_service.create_expense(
                    user_id=self.owner.id,
                    vehicle_id=vehicle.id,
                    amount="10",
                    title="Invalid receipt",
                    expense_date="2026-01-15",
                    is_monthly=True,
                    **kw,
                ),
                FileStorage(
                    io.BytesIO(b"invalid"),
                    filename="receipt.png",
                    content_type="image/png",
                ),
                "expense",
            )
        db.session.remove()
        self.assertEqual(Expense.query.count(), 0)
        self.assertEqual(VehicleAttachment.query.count(), 0)

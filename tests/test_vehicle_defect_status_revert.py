from datetime import date

import test_vehicle_service
from app.models import VehicleDefect
from app.services import (
    vehicle_maintenance_service as maintenance,
    vehicle_service as vehicles,
)
from extensions import db


class DefectStatusRevertTests(test_vehicle_service.VehicleServiceTestCase):
    def defect(self, vehicle, **values):
        return maintenance.save_defect(
            self.owner,
            vehicle.id,
            dict(
                title="Проверка неисправности",
                severity="critical",
                found_date="2026-03-01",
                set_in_repair="on",
            )
            | values,
        )

    def downgrade(self, vehicle, defect, severity="minor", **options):
        return maintenance.save_defect(
            self.owner,
            vehicle.id,
            dict(
                title=defect.title,
                severity=severity,
                found_date=defect.found_date.isoformat(),
            ),
            defect.id,
            **options,
        )

    def test_last_critical_defect_downgrade_restores_active_status(self):
        for severity in ("minor", "attention"):
            with self.subTest(severity=severity):
                vehicle = vehicles.create_vehicle(self.owner, dict(name=severity))
                defect = self.defect(vehicle)
                self.assertEqual(vehicle.status, "in_repair")
                self.downgrade(vehicle, defect, severity)
                db.session.refresh(vehicle)
                self.assertEqual(vehicle.status, "active")
                self.assertEqual(
                    VehicleDefect.query.filter_by(vehicle_id=vehicle.id).count(), 1
                )

    def test_other_open_critical_defect_keeps_vehicle_in_repair(self):
        vehicle = self.vehicle()
        defect = self.defect(vehicle)
        other = self.defect(vehicle)
        self.downgrade(vehicle, defect)
        self.assertEqual(vehicle.status, "in_repair")
        self.downgrade(vehicle, other)
        self.assertEqual(vehicle.status, "active")

    def test_fixed_critical_and_foreign_defects_do_not_block_restore(self):
        vehicle = self.vehicle()
        defect = self.defect(vehicle)
        fixed = self.defect(vehicle)
        fixed.status, fixed.fixed_date = "fixed", date(2026, 3, 2)
        foreign = vehicles.create_vehicle(self.owner, dict(name="Другой автомобиль"))
        self.defect(foreign)
        db.session.commit()
        self.downgrade(vehicle, defect)
        self.assertEqual(vehicle.status, "active")
        self.assertEqual(foreign.status, "in_repair")

    def test_noncritical_edit_does_not_clear_manual_repair_status(self):
        vehicle = self.vehicle(status="in_repair")
        defect = self.defect(vehicle, severity="attention")
        self.downgrade(vehicle, defect)
        self.assertEqual(vehicle.status, "in_repair")

    def test_downgrade_without_commit_can_be_rolled_back(self):
        vehicle = self.vehicle()
        defect = self.defect(vehicle)
        self.downgrade(vehicle, defect, commit=False)
        self.assertEqual(vehicle.status, "active")
        db.session.rollback()
        self.assertEqual(vehicle.status, "in_repair")
        self.assertEqual(defect.severity, "critical")

    def test_defect_edit_form_and_post_restore_status(self):
        vehicle = self.vehicle()
        defect = self.defect(vehicle)
        self.login()
        response = self.client.get(f"/vehicles/{vehicle.id}?tab=defects")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(f'action="/vehicles/{vehicle.id}/defects/{defect.id}"', html)
        self.assertIn("Автомобиль будет возвращён в активный статус", html)
        self.assertIn("data-defect-status-hint", html)
        response = self.client.post(
            f"/vehicles/{vehicle.id}/defects/{defect.id}",
            data=dict(title=defect.title, severity="minor", found_date="2026-03-01"),
        )
        self.assertEqual(response.status_code, 302)
        db.session.refresh(vehicle)
        self.assertEqual(vehicle.status, "active")

    def test_hint_absent_when_another_open_critical_defect_remains(self):
        vehicle = self.vehicle()
        self.defect(vehicle)
        self.defect(vehicle)
        self.login()
        html = self.client.get(f"/vehicles/{vehicle.id}?tab=defects").get_data(
            as_text=True
        )
        self.assertNotIn("data-defect-status-hint", html)

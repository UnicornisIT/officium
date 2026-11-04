import io
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import NotFound

import test_vehicle_service
from app.models import (
    ActivityLog,
    VehicleAttachment,
    VehicleDefect,
    VehicleDocument,
    VehicleServiceRecord,
    VehicleCost,
    VehicleRefueling,
    Expense,
)
from app.services import (
    vehicle_attachment_service as service,
    vehicle_service as vehicles,
)
from extensions import db


def photo(fmt="JPEG", color="red", mime=None):
    data = io.BytesIO()
    Image.new("RGBA" if fmt in ("PNG", "WEBP") else "RGB", (320, 240), color).save(
        data, format=fmt
    )
    data.seek(0)
    return FileStorage(
        data, filename="private." + fmt.lower(), content_type=mime or Image.MIME[fmt]
    )


class AttachmentTests(unittest.TestCase):
    login = test_vehicle_service.VehicleServiceTestCase.login
    vehicle = test_vehicle_service.VehicleServiceTestCase.vehicle
    expense = test_vehicle_service.VehicleServiceTestCase.expense

    def setUp(self):
        test_vehicle_service.VehicleServiceTestCase.setUp(self)
        self.directory = tempfile.TemporaryDirectory()
        self.app.config["VEHICLE_UPLOAD_ROOT"] = self.directory.name

    def tearDown(self):
        test_vehicle_service.VehicleServiceTestCase.tearDown(self)
        self.directory.cleanup()

    def save(self, vehicle, **kw):
        return service.save_attachment(
            self.owner,
            vehicle.id,
            kw.pop("file", photo()),
            kw.pop("kind", "vehicle"),
            **kw,
        )

    def test_formats_and_white_previews(self):
        vehicle = self.vehicle()
        for fmt in ("JPEG", "PNG", "WEBP"):
            upload = photo(fmt, (0, 0, 0, 0) if fmt == "PNG" else "red")
            original = upload.stream.getvalue()
            row = self.save(vehicle, file=upload)
            self.assertEqual((row.width, row.height), (320, 240))
            self.assertEqual(row.mime_type, Image.MIME[fmt])
            paths = service.variants(row)
            self.assertEqual(
                service.storage_path(paths["original"]).read_bytes(), original
            )
            with Image.open(service.storage_path(paths["thumb"])) as thumb:
                self.assertEqual(thumb.size, (200, 200))
            with Image.open(service.storage_path(paths["medium"])) as medium:
                self.assertEqual(medium.format, "JPEG")
                if fmt == "PNG":
                    self.assertEqual(medium.getpixel((0, 0)), (255, 255, 255))

    def test_invalid_and_bounded_images(self):
        vehicle = self.vehicle()
        invalid = [
            photo("GIF"),
            photo(mime="application/pdf"),
            photo("PNG", mime="image/jpeg"),
            FileStorage(
                io.BytesIO(b"%PDF-1.7"), filename="fake.jpg", content_type="image/jpeg"
            ),
            FileStorage(
                io.BytesIO(b"x" * (service.MAX_BYTES + 1)), content_type="image/jpeg"
            ),
            FileStorage(io.BytesIO(), content_type="image/jpeg"),
        ]
        for upload in invalid:
            with self.subTest(upload=upload), self.assertRaises(ValueError):
                self.save(vehicle, file=upload)
        with patch.object(service, "MAX_PIXELS", 1), self.assertRaises(ValueError):
            self.save(vehicle)
        self.assertEqual(VehicleAttachment.query.count(), 0)
        self.assertEqual(list(Path(self.directory.name).rglob("*")), [])

    def test_dedup_and_private_audit(self):
        vehicle = self.vehicle()
        row = self.save(vehicle, caption="Private caption")
        duplicate = self.save(vehicle)
        self.assertNotEqual(duplicate.id, row.id)
        self.assertEqual(duplicate.file_path, row.file_path)
        second = vehicles.create_vehicle(self.owner, dict(name="Second"))
        other = self.save(second)
        self.assertEqual(other.vehicle_id, second.id)
        self.assertEqual(other.file_path, row.file_path)
        self.assertEqual(VehicleAttachment.query.count(), 3)
        logs = (
            ActivityLog.query.filter_by(action="vehicle_attachment_save")
            .order_by(ActivityLog.id)
            .all()
        )
        self.assertEqual(
            [log.entity_id for log in logs], [row.id, duplicate.id, other.id]
        )
        for log in logs:
            self.assertFalse(log.description)
            self.assertEqual(log.entity_type, "vehicle_attachment")
            self.assertNotIn("Private caption", str(log.description))

    def test_quotas_and_delete_releases_space(self):
        vehicle = self.vehicle()
        for setting in (
            "VEHICLE_UPLOAD_USER_QUOTA_MB",
            "VEHICLE_UPLOAD_VEHICLE_QUOTA_MB",
        ):
            with patch.dict(self.app.config, {setting: 0}), self.assertRaises(
                ValueError
            ):
                self.save(vehicle)
        row = self.save(vehicle)
        files = [service.storage_path(name) for name in service.variants(row).values()]
        service.delete_attachment(self.owner, row.id)
        self.assertTrue(all(not p.exists() for p in files))
        self.assertEqual(VehicleAttachment.query.count(), 0)
        self.save(vehicle)
        self.assertEqual(
            ActivityLog.query.filter_by(action="vehicle_attachment_delete").count(), 1
        )

    def test_entity_scope_and_all_kinds(self):
        vehicle, second = self.vehicle(), vehicles.create_vehicle(
            self.owner, dict(name="Second")
        )
        rows = dict(
            expense=self.expense(vehicle),
            defect=VehicleDefect(
                vehicle_id=vehicle.id,
                title="Defect",
                severity="minor",
                found_date=vehicles.today(),
            ),
            document=VehicleDocument(vehicle_id=vehicle.id, kind="pts"),
            service_record=VehicleServiceRecord(
                vehicle_id=vehicle.id, service_date=vehicles.today()
            ),
            vehicle_cost=VehicleCost(
                vehicle_id=vehicle.id,
                user_id=self.owner.id,
                cost_date=vehicles.today(),
                amount=1,
                kind="parts",
            ),
        )
        db.session.add_all(list(rows.values()))
        db.session.commit()
        for index, (kind, entity) in enumerate(rows.items()):
            self.save(
                vehicle,
                kind=kind,
                entity_id=entity.id,
                file=photo(color=(index, 10, 20)),
            )
            with self.assertRaises(NotFound):
                self.save(second, kind=kind, entity_id=entity.id)
            with self.assertRaises(ValueError):
                self.save(vehicle, kind=kind)
        with self.assertRaises(ValueError):
            self.save(vehicle, entity_id=1)
        with self.assertRaises(ValueError):
            self.save(vehicle, kind="unknown")

    def test_ownership_archive_and_gated_routes(self):
        vehicle = self.vehicle()
        row = self.save(vehicle)
        with self.assertRaises(NotFound):
            service.get_attachment(self.other, row.id)
        with self.assertRaises(NotFound):
            service.save_attachment(self.other, vehicle.id, photo(), "vehicle")
        self.login(self.other)
        for url in (
            f"/vehicles/{vehicle.id}/attachments",
            f"/vehicles/attachments/{row.id}/delete",
        ):
            self.assertEqual(
                (
                    self.client.post(url)
                    if url.endswith("delete")
                    else self.client.get(url)
                ).status_code,
                404,
            )
        self.assertEqual(
            self.client.get("/vehicles/uploads/" + row.file_path).status_code, 403
        )
        vehicles.archive_vehicle(self.owner, vehicle.id, "sold")
        with self.assertRaises(ValueError):
            self.save(vehicle)
        self.app.config["VEHICLES_ENABLED"] = False
        self.login()
        for method, url in [
            ("get", f"/vehicles/{vehicle.id}/attachments"),
            ("post", f"/vehicles/{vehicle.id}/attachments"),
            ("get", "/vehicles/uploads/" + row.file_path),
            ("delete", f"/vehicles/attachments/{row.id}"),
            ("post", f"/vehicles/attachments/{row.id}/delete"),
        ]:
            self.assertEqual(getattr(self.client, method)(url).status_code, 404)

    def test_upload_resolution_traversal_orphan_and_cache(self):
        vehicle = self.vehicle()
        row = self.save(vehicle)
        self.login()
        for name in service.variants(row).values():
            response = self.client.get("/vehicles/uploads/" + name)
            self.assertEqual(response.status_code, 200)
            self.assertIn("no-store", response.headers["Cache-Control"])
            response.close()
        for bad in (
            f"{self.owner.id}/../secret.jpg",
            f"{self.owner.id}/x/../../secret",
            "C:/secret",
            f"{self.owner.id}\\secret",
        ):
            self.assertEqual(
                self.client.get("/vehicles/uploads/" + bad).status_code, 403
            )
        self.assertEqual(
            self.client.get(
                f"/vehicles/uploads/{self.owner.id}/orphan.jpg"
            ).status_code,
            404,
        )
        with tempfile.TemporaryDirectory() as outside:
            try:
                (Path(self.directory.name) / str(self.owner.id) / "link").symlink_to(
                    outside, target_is_directory=True
                )
            except OSError:
                pass  # Windows may lack CreateSymbolicLink privilege.
            else:
                self.assertEqual(
                    self.client.get(
                        f"/vehicles/uploads/{self.owner.id}/link/private.jpg"
                    ).status_code,
                    403,
                )

    def test_json_upload_list_delete_and_html(self):
        vehicle = self.vehicle()
        self.login()
        response = self.client.post(
            f"/vehicles/{vehicle.id}/attachments",
            data=dict(
                file=(photo().stream, "car.jpg", "image/jpeg"),
                kind="vehicle",
                caption="<script>x</script>",
            ),
            headers={"Accept": "application/json"},
        )
        self.assertEqual(response.status_code, 201)
        row = response.json
        self.assertEqual(
            len(self.client.get(f"/vehicles/{vehicle.id}/attachments").json), 1
        )
        for tab in (
            "overview",
            "photos",
            "defects",
            "documents",
            "expenses",
            "maintenance",
            "tco",
        ):
            response = self.client.get(f"/vehicles/{vehicle.id}?tab={tab}")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b"<script>x</script>", response.data)
            self.assertIn("Аналитика".encode(), response.data)
        self.assertEqual(
            self.client.delete(f"/vehicles/attachments/{row['id']}").status_code, 204
        )

    def test_database_failure_cleans_files_and_logs(self):
        vehicle = self.vehicle()
        with patch.object(db.session, "commit", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                self.save(vehicle)
        self.assertEqual(VehicleAttachment.query.count(), 0)
        self.assertFalse(any(p.is_file() for p in Path(self.directory.name).rglob("*")))
        self.assertEqual(
            ActivityLog.query.filter_by(action="vehicle_attachment_save").count(), 0
        )

    def test_related_entity_upload_rolls_back_atomically(self):
        vehicle = self.vehicle()
        self.login()
        url = f"/vehicles/{vehicle.id}/defects"
        data = dict(
            title="Private defect",
            severity="minor",
            found_date=vehicles.today().isoformat(),
            file=(io.BytesIO(b"bad"), "fake.jpg", "image/jpeg"),
        )
        self.client.post(url, data=data)
        self.assertEqual(VehicleDefect.query.count(), 0)
        data["file"] = (photo().stream, "real.jpg", "image/jpeg")
        self.client.post(url, data=data)
        self.assertEqual(VehicleDefect.query.count(), 1)
        self.assertEqual(
            VehicleAttachment.query.one().entity_id, VehicleDefect.query.one().id
        )

    def test_document_service_and_both_spending_forms(self):
        vehicle = self.vehicle(is_company=True)
        self.login()
        day = vehicles.today().isoformat()
        cases = [
            (
                f"/vehicles/{vehicle.id}/documents",
                dict(kind="pts"),
                VehicleDocument,
                "document",
            ),
            (
                f"/vehicles/{vehicle.id}/service",
                dict(service_date=day, odometer_km="100"),
                VehicleServiceRecord,
                "service_record",
            ),
            (
                f"/vehicles/{vehicle.id}/spend",
                dict(
                    title="Receipt",
                    cost_date=day,
                    amount="100",
                    kind="parts",
                    payer="organization",
                ),
                VehicleCost,
                "vehicle_cost",
            ),
            (
                "/expenses",
                dict(
                    vehicle_id=str(vehicle.id),
                    title="Personal receipt",
                    expense_date=day,
                    amount="100",
                    kind="fuel",
                    payer="self",
                ),
                Expense,
                "expense",
            ),
        ]
        for index, (url, data, model, kind) in enumerate(cases):
            with self.subTest(kind=kind):
                data["file"] = (
                    photo(color=(index * 40, 20, 30)).stream,
                    "receipt.jpg",
                    "image/jpeg",
                )
                response = self.client.post(url, data=data, follow_redirects=True)
                self.assertEqual(response.status_code, 200)
                attachment = VehicleAttachment.query.filter_by(kind=kind).one()
                self.assertEqual(attachment.entity_id, model.query.one().id)
                if kind == "vehicle_cost":
                    response = self.client.get(
                        f"/vehicles/costs/{attachment.entity_id}"
                    )
                if kind in ("expense", "vehicle_cost"):
                    self.assertIn(b"data-vehicle-lightbox", response.data)
        for tab in ("documents", "maintenance", "expenses"):
            response = self.client.get(f"/vehicles/{vehicle.id}?tab={tab}")
            self.assertIn(b"data-vehicle-lightbox", response.data)

    def test_duplicate_photo_does_not_rollback_new_entity(self):
        vehicle = self.vehicle()
        self.save(vehicle)
        self.login()
        self.client.post(
            f"/vehicles/{vehicle.id}/documents",
            data=dict(kind="pts", file=(photo().stream, "same.jpg", "image/jpeg")),
        )
        self.assertEqual(VehicleDocument.query.count(), 1)
        self.assertEqual(VehicleAttachment.query.count(), 2)
        self.assertEqual(
            VehicleAttachment.query.filter_by(kind="document").one().entity_id,
            VehicleDocument.query.one().id,
        )

    def test_refueling_receipt_is_attached_to_linked_spending(self):
        vehicle = self.vehicle(is_company=True)
        self.login()
        for index, payer in enumerate(("self", "organization")):
            response = self.client.post(
                f"/vehicles/{vehicle.id}/refueling",
                data=dict(
                    refuel_date=f"2026-01-{index+1:02}",
                    liters="10",
                    price_per_liter="60",
                    total_amount="600",
                    payer=payer,
                    file=(
                        photo(color=(index * 90, 30, 40)).stream,
                        "fuel.jpg",
                        "image/jpeg",
                    ),
                ),
                follow_redirects=True,
            )
            self.assertEqual(response.status_code, 200)
            kind = "expense" if payer == "self" else "vehicle_cost"
            attachment = VehicleAttachment.query.filter_by(kind=kind).one()
            refueling = VehicleRefueling.query.filter_by(payer=payer).one()
            self.assertEqual(
                attachment.entity_id,
                refueling.expense_id if payer == "self" else refueling.vehicle_cost_id,
            )
            self.assertIn(b"data-vehicle-lightbox", response.data)

    def test_exif_orientation_only_changes_previews(self):
        vehicle = self.vehicle()
        upload = photo()
        buffer = io.BytesIO()
        with Image.open(upload.stream) as image:
            exif = Image.Exif()
            exif[274] = 6
            image.save(buffer, format="JPEG", exif=exif)
        original = buffer.getvalue()
        row = self.save(
            vehicle,
            file=FileStorage(
                io.BytesIO(original), filename="portrait.jpg", content_type="image/jpeg"
            ),
        )
        self.assertEqual((row.width, row.height), (320, 240))
        self.assertEqual(service.storage_path(row.file_path).read_bytes(), original)
        with Image.open(
            service.storage_path(service.variants(row)["medium"])
        ) as medium:
            self.assertEqual(medium.size, (240, 320))

    def test_vehicle_cascade_keeps_inaccessible_orphans(self):
        vehicle = self.vehicle()
        row = self.save(vehicle)
        files = [service.storage_path(name) for name in service.variants(row).values()]
        filename = row.file_path
        db.session.delete(vehicle)
        db.session.commit()
        self.assertEqual(VehicleAttachment.query.count(), 0)
        self.assertTrue(all(path.exists() for path in files))
        self.login()
        self.assertEqual(
            self.client.get("/vehicles/uploads/" + filename).status_code, 404
        )

    def test_perf_5mb_upload_and_50_row_listing(self):
        vehicle = self.vehicle()
        upload = photo()
        original = upload.stream.getvalue()
        upload.stream = io.BytesIO(
            original + b"\0" * (service.MAX_BYTES - len(original))
        )
        start = time.perf_counter()
        self.save(vehicle, file=upload)
        self.assertLess(time.perf_counter() - start, 3)
        for index in range(49):
            db.session.add(
                VehicleAttachment(
                    user_id=self.owner.id,
                    vehicle_id=vehicle.id,
                    kind="vehicle",
                    file_path=f"fixture-{index}.jpg",
                    mime_type="image/jpeg",
                    size_bytes=1,
                    sha256=f"{index:064x}",
                )
            )
        db.session.commit()
        with self.app.test_request_context():
            start = time.perf_counter()
            self.assertEqual(len(service.list_attachments(self.owner, vehicle.id)), 50)
            self.assertLess(time.perf_counter() - start, 0.2)

    def test_csrf_required_for_upload_and_delete(self):
        vehicle = self.vehicle()
        self.login()
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.assertEqual(
            self.client.post(f"/vehicles/{vehicle.id}/attachments").status_code, 400
        )
        self.assertEqual(
            self.client.post("/vehicles/attachments/1/delete").status_code, 400
        )

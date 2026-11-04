from functools import partial
import unittest
from unittest.mock import patch

import test_vehicle_attachments
from app.models import VehicleAttachment
from app.services import (
    vehicle_attachment_service as service,
    vehicle_document_service as documents,
)
from extensions import db


class AttachmentDedupTests(unittest.TestCase):
    setUp = test_vehicle_attachments.AttachmentTests.setUp
    tearDown = test_vehicle_attachments.AttachmentTests.tearDown
    vehicle = test_vehicle_attachments.AttachmentTests.vehicle

    def test_duplicate_upload_links_new_document_and_preserves_shared_files(self):
        vehicle = self.vehicle()
        records = [
            service.save_with_upload(
                self.owner,
                partial(
                    documents.save_document, self.owner, vehicle.id, dict(kind=kind)
                ),
                test_vehicle_attachments.photo("PNG"),
                "document",
            )
            for kind in ("pts", "sts")
        ]
        attachments = [
            VehicleAttachment.query.filter_by(
                kind="document", entity_id=document.id
            ).one_or_none()
            for document in records
        ]
        self.assertTrue(all(attachment is not None for attachment in attachments))
        first, second = attachments
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.file_path, second.file_path)
        paths = [
            service.storage_path(name) for name in service.variants(first).values()
        ]
        self.assertTrue(all(path.is_file() for path in paths))
        service.delete_attachment(self.owner, first.id)
        self.assertTrue(all(path.is_file() for path in paths))
        for name in service.variants(second).values():
            self.assertTrue(service.resolve_upload(self.owner, name).is_file())
        service.delete_attachment(self.owner, second.id)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_shared_file_counts_once_toward_storage_quotas(self):
        vehicle = self.vehicle()
        first = service.save_attachment(
            self.owner, vehicle.id, test_vehicle_attachments.photo(), "vehicle"
        )
        limit = first.size_bytes / (1024 * 1024)
        settings = dict(
            VEHICLE_UPLOAD_USER_QUOTA_MB=limit, VEHICLE_UPLOAD_VEHICLE_QUOTA_MB=limit
        )
        with patch.dict(self.app.config, settings):
            for _ in range(2):
                duplicate = service.save_attachment(
                    self.owner, vehicle.id, test_vehicle_attachments.photo(), "vehicle"
                )
                self.assertEqual(duplicate.file_path, first.file_path)
            with self.assertRaises(ValueError):
                service.save_attachment(
                    self.owner,
                    vehicle.id,
                    test_vehicle_attachments.photo(color="blue"),
                    "vehicle",
                )
        self.assertEqual(VehicleAttachment.query.count(), 3)

    def test_failed_duplicate_save_preserves_original_files(self):
        vehicle = self.vehicle()
        first = service.save_attachment(
            self.owner, vehicle.id, test_vehicle_attachments.photo(), "vehicle"
        )
        paths = [
            service.storage_path(name) for name in service.variants(first).values()
        ]
        with patch.object(db.session, "commit", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                service.save_attachment(
                    self.owner, vehicle.id, test_vehicle_attachments.photo(), "vehicle"
                )
        self.assertEqual(VehicleAttachment.query.count(), 1)
        self.assertTrue(all(path.is_file() for path in paths))

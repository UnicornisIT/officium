"""Private, bounded image storage. Original bytes are never modified."""
import hashlib
import io
import warnings
from pathlib import Path, PurePosixPath
from uuid import uuid4

from flask import abort, current_app, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import update

from app.models import (User, VehicleAttachment, VehicleDefect, VehicleDocument,
                        VehicleServiceRecord, Expense, VehicleCost)
from app.services import vehicle_service as vehicles
from app.utils import record_activity
from extensions import db

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 20_000_000  # Bound decoded memory on the small production worker.
FORMATS = {'JPEG': ('image/jpeg', 'jpg'), 'PNG': ('image/png', 'png'), 'WEBP': ('image/webp', 'webp')}
ENTITIES = dict(defect=VehicleDefect, document=VehicleDocument, expense=Expense,
                vehicle_cost=VehicleCost, service_record=VehicleServiceRecord)
KINDS = dict(vehicle='Фото автомобиля', defect='Фото дефекта', document='Скан документа',
             expense='Фото чека', vehicle_cost='Чек организации', service_record='Заказ-наряд')


def _lock_owner(user):
    if db.session.get_bind().dialect.name == 'sqlite':
        # SQLite ignores FOR UPDATE; acquire its write lock before reading quotas.
        db.session.execute(update(User).where(User.id == user.id).values(id=User.id))
    else:
        User.query.filter_by(id=user.id).with_for_update().one()


def save_with_upload(user, operation, file_storage, kind):
    """Save an entity and its optional image in a single database transaction."""
    if not file_storage or not file_storage.filename:
        return operation(commit=True)
    vehicles._user(user)
    _lock_owner(user)
    try:
        options = {'defer_commit': True} if kind in ('expense', 'spend') else {'commit': False}
        row = operation(**options)
        actual_kind = ('expense' if isinstance(row, Expense) else 'vehicle_cost') if kind == 'spend' else kind
        entity_id = row.id
        if kind == 'refueling':
            actual_kind, entity_id = ('expense', row.expense_id) if row.expense_id else ('vehicle_cost', row.vehicle_cost_id)
        save_attachment(user, row.vehicle_id, file_storage, actual_kind, entity_id)
        return row
    except Exception:
        db.session.rollback()
        raise


def root():
    configured = current_app.config.get('VEHICLE_UPLOAD_ROOT')
    return Path(configured or Path(current_app.instance_path) / 'vehicle_uploads').resolve()


def storage_path(filename):
    """Reject traversal, platform separators, absolute paths and symlink escapes."""
    if not filename or '\\' in filename or ':' in filename:
        abort(403)
    relative = PurePosixPath(filename)
    if relative.is_absolute() or any(p in ('..', '.') for p in filename.split('/')):
        abort(403)
    target = root().joinpath(*relative.parts).resolve()
    if not target.is_relative_to(root()):
        abort(403)
    return target


def variants(row):
    path = PurePosixPath(row.file_path)
    return dict(original=row.file_path, thumb=str(path.with_name(path.stem + '_thumb.jpg')),
                medium=str(path.with_name(path.stem + '_medium.jpg')))


def serialize(row):
    return dict(id=row.id, vehicle_id=row.vehicle_id, kind=row.kind, entity_id=row.entity_id,
                caption=row.caption, width=row.width, height=row.height, size_bytes=row.size_bytes,
                **{key: url_for('vehicle_upload', filename=value) for key, value in variants(row).items()})


def validate_entity(vehicle, kind, entity_id):
    if kind not in KINDS:
        raise ValueError('Выберите тип вложения.')
    if kind == 'vehicle':
        if entity_id not in (None, ''):
            raise ValueError('Фото автомобиля не требует связанной записи.')
        return None
    try:
        entity_id = int(entity_id)
    except (TypeError, ValueError):
        raise ValueError('Выберите связанную запись.') from None
    query = ENTITIES[kind].query.filter_by(id=entity_id, vehicle_id=vehicle.id)
    if kind in ('expense', 'vehicle_cost'):
        query = query.filter_by(user_id=vehicle.user_id)
    query.first_or_404()
    return entity_id


def prepare_image(file_storage):
    if file_storage is None:
        raise ValueError('Выберите изображение JPEG, PNG или WebP.')
    data = file_storage.stream.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Размер изображения должен быть от 1 байта до 5 МБ.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                fmt = FORMATS.get(image.format)
                if not fmt or fmt[0] != file_storage.mimetype:
                    raise ValueError('Содержимое файла должно соответствовать типу JPEG, PNG или WebP.')
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError('Изображение слишком большое: максимум 20 миллионов пикселей.')
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                oriented = ImageOps.exif_transpose(image)
                rgba = oriented.convert('RGBA')
                rgb = Image.new('RGB', rgba.size, 'white')
                rgb.paste(rgba, mask=rgba.getchannel('A'))
                thumb = ImageOps.fit(rgb, (200, 200), method=Image.Resampling.LANCZOS)
                rgb.thumbnail((1024, 768), Image.Resampling.LANCZOS)
                outputs = []
                for picture, quality in ((thumb, 80), (rgb, 85)):
                    buffer = io.BytesIO()
                    picture.save(buffer, format='JPEG', quality=quality)
                    outputs.append(buffer.getvalue())
        return data, fmt, width, height, outputs
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValueError('Не удалось прочитать изображение JPEG, PNG или WebP.') from None


def save_attachment(user, vehicle_id, file_storage, kind, entity_id=None, caption=None):
    written = []
    try:
        vehicles._user(user)
        # Serialize quota and dedup decisions across all this user's vehicles on MySQL.
        _lock_owner(user)
        vehicle = vehicles.get_vehicle_for_user(user, vehicle_id, for_update=True)
        vehicles._writable(vehicle)
        entity_id = validate_entity(vehicle, kind, entity_id)
        caption = str(caption or '').strip()
        if len(caption) > 500:
            raise ValueError('Подпись — до 500 символов.')
        data, fmt, width, height, previews = prepare_image(file_storage)
        digest = hashlib.sha256(data).hexdigest()
        existing = VehicleAttachment.query.filter_by(user_id=user.id, sha256=digest).with_for_update().first()
        if existing:
            db.session.commit()  # Includes any newly created related entity; release the quota lock.
            return existing
        for field, value, setting, label in (
            ('user_id', user.id, 'VEHICLE_UPLOAD_USER_QUOTA_MB', 'пользователя'),
            ('vehicle_id', vehicle.id, 'VEHICLE_UPLOAD_VEHICLE_QUOTA_MB', 'автомобиля')):
            # Locking reads see the current committed rows even under MySQL REPEATABLE READ.
            used = sum(size for (size,) in db.session.query(VehicleAttachment.size_bytes).filter(
                getattr(VehicleAttachment, field) == value).with_for_update().all())
            limit = current_app.config[setting] * 1024 * 1024
            if used + len(data) > limit:
                raise ValueError(f'Превышен лимит хранения {label}. Удалите ненужные вложения.')
        # UUID avoids reusing a deleted attachment's URL or overwriting another transaction.
        relative = f'{user.id}/{vehicle.id}/{kind}/{uuid4().hex}.{fmt[1]}'
        row = VehicleAttachment(user_id=user.id, vehicle_id=vehicle.id, kind=kind, entity_id=entity_id,
            file_path=relative, original_name=Path(file_storage.filename or '').name[:255],
            mime_type=fmt[0], size_bytes=len(data), width=width, height=height, sha256=digest, caption=caption or None)
        for filename, content in zip(variants(row).values(), (data, *previews)):
            target = storage_path(filename)
            folder = root()
            folder.mkdir(mode=0o750, parents=True, exist_ok=True)
            for part in PurePosixPath(filename).parts[:-1]:
                folder = folder / part
                folder.mkdir(mode=0o750, exist_ok=True)
            with target.open('xb') as stream:
                written.append(target)
                stream.write(content)
            target.chmod(0o640)
        db.session.add(row)
        db.session.flush()
        record_activity('vehicle_attachment_save', user, entity_type='vehicle_attachment', entity_id=row.id, commit=False)
        db.session.commit()
        return row
    except Exception:
        db.session.rollback()
        for path in written:
            path.unlink(missing_ok=True)
        raise


def list_attachments(user, vehicle_id, *, kind=None, entity_id=None):
    vehicles.get_vehicle_for_user(user, vehicle_id)
    query = VehicleAttachment.query.filter_by(user_id=user.id, vehicle_id=vehicle_id)
    if kind is not None:
        query = query.filter_by(kind=kind)
    if entity_id is not None:
        query = query.filter_by(entity_id=entity_id)
    return [serialize(row) for row in query.order_by(VehicleAttachment.created_at.desc(), VehicleAttachment.id.desc()).all()]


def get_attachment(user, attachment_id):
    vehicles._user(user)
    return VehicleAttachment.query.filter_by(id=attachment_id, user_id=user.id).first_or_404()


def delete_attachment(user, attachment_id):
    row = get_attachment(user, attachment_id)
    paths = [storage_path(name) for name in variants(row).values()]
    # Commit first: an unlink failure leaves an orphan, never a live record with missing files.
    try:
        db.session.delete(row)
        record_activity('vehicle_attachment_delete', user, entity_type='vehicle_attachment', entity_id=row.id, commit=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    for path in paths:
        path.unlink(missing_ok=True)


def resolve_upload(user, filename):
    vehicles._user(user)
    path = storage_path(filename)
    if filename.split('/')[0] != str(user.id):
        abort(403)
    relative = PurePosixPath(filename)
    stem = relative.stem
    for suffix in ('_thumb', '_medium'):
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
    # Match a real DB attachment; a user-id prefix alone does not authorize orphan files.
    prefix = str(relative.parent / stem)
    candidates = VehicleAttachment.query.filter_by(user_id=user.id).filter(
        VehicleAttachment.file_path.in_([prefix + '.' + ext for _, ext in FORMATS.values()])).all()
    if not any(filename in variants(row).values() for row in candidates):
        abort(404)
    return path

import ipaddress
import json
import re
import uuid
from urllib.parse import urlsplit, urlunsplit

from app.utils import get_setting, set_setting


SETTING_KEY = 'public_contacts'
CONTACT_TYPES = {
    'telegram': 'Telegram',
    'email': 'Email',
    'github': 'GitHub',
    'website': 'Сайт',
    'phone': 'Телефон',
    'vk': 'ВКонтакте',
    'other': 'Другая HTTPS-ссылка',
}
CONTACT_ICONS = {
    'envelope': 'Конверт',
    'telegram': 'Telegram',
    'github': 'GitHub',
    'globe2': 'Глобус',
    'telephone': 'Телефон',
    'link-45deg': 'Ссылка',
    'people': 'Пользователи',
}
CONTACT_LOCATIONS = {'footer', 'login', 'help', 'mini_app'}
_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
_PHONE_RE = re.compile(r'^\+?[0-9 ()-]{5,32}$')
_TELEGRAM_RE = re.compile(r'^@?[A-Za-z0-9_]{5,32}$')


class ContactValidationError(ValueError):
    pass


def _public_https_url(raw_value, *, required_host=None):
    parsed = urlsplit(raw_value.strip())
    if parsed.scheme.lower() != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ContactValidationError('Ссылка должна быть абсолютным HTTPS-адресом без учётных данных.')
    hostname = parsed.hostname.rstrip('.').lower()
    if required_host and hostname not in (required_host, f'www.{required_host}'):
        raise ContactValidationError(f'Для этого типа разрешён только домен {required_host}.')
    if hostname in {'localhost', 'localhost.localdomain'} or hostname.endswith('.local'):
        raise ContactValidationError('Локальные адреса публиковать нельзя.')
    try:
        address = ipaddress.ip_address(hostname.strip('[]'))
    except ValueError:
        address = None
    if address and not address.is_global:
        raise ContactValidationError('Частные и служебные IP-адреса публиковать нельзя.')
    try:
        port = parsed.port
    except ValueError as exc:
        raise ContactValidationError('В HTTPS-ссылке указан некорректный порт.') from exc
    if port not in (None, 443):
        raise ContactValidationError('Для публичной HTTPS-ссылки разрешён только стандартный порт.')
    path = parsed.path or '/'
    return urlunsplit(('https', hostname, path, parsed.query, ''))


def _normalize_address(contact_type, raw_address):
    address = str(raw_address or '').strip()
    if not address or len(address) > 500:
        raise ContactValidationError('Укажите адрес контакта длиной до 500 символов.')
    if contact_type == 'email':
        email = address.removeprefix('mailto:').strip().lower()
        if len(email) > 254 or not _EMAIL_RE.fullmatch(email):
            raise ContactValidationError('Укажите корректный email.')
        return email, f'mailto:{email}'
    if contact_type == 'phone':
        phone = address.removeprefix('tel:').strip()
        if not _PHONE_RE.fullmatch(phone):
            raise ContactValidationError('Укажите корректный номер телефона.')
        normalized = ('+' if phone.startswith('+') else '') + re.sub(r'\D', '', phone)
        return phone, f'tel:{normalized}'
    if contact_type == 'telegram':
        username = address
        if address.startswith('https://'):
            parsed = urlsplit(_public_https_url(address, required_host='t.me'))
            username = parsed.path.strip('/').split('/', 1)[0]
        username = username.lstrip('@')
        if not _TELEGRAM_RE.fullmatch(username):
            raise ContactValidationError('Укажите Telegram username или ссылку t.me.')
        return f'@{username}', f'https://t.me/{username}'
    host = {'github': 'github.com', 'vk': 'vk.com'}.get(contact_type)
    normalized = _public_https_url(address, required_host=host)
    return normalized, normalized


def validate_contact(payload, existing_id=None):
    contact_type = str(payload.get('type') or '').strip()
    if contact_type not in CONTACT_TYPES:
        raise ContactValidationError('Выбран неподдерживаемый тип контакта.')
    label = str(payload.get('label') or '').strip()
    if not label or len(label) > 80:
        raise ContactValidationError('Название должно содержать от 1 до 80 символов.')
    icon = str(payload.get('icon') or '').strip()
    if icon not in CONTACT_ICONS:
        raise ContactValidationError('Выбрана неподдерживаемая иконка.')
    raw_locations = payload.get('locations') or []
    if isinstance(raw_locations, str):
        raw_locations = [raw_locations]
    locations = sorted({str(item) for item in raw_locations})
    if not locations or any(item not in CONTACT_LOCATIONS for item in locations):
        raise ContactValidationError('Выберите хотя бы одно допустимое место публикации.')
    try:
        order = int(payload.get('order', 0))
    except (TypeError, ValueError) as exc:
        raise ContactValidationError('Порядок должен быть целым числом.') from exc
    if not 0 <= order <= 9999:
        raise ContactValidationError('Порядок должен быть от 0 до 9999.')
    address, href = _normalize_address(contact_type, payload.get('address'))
    return {
        'id': existing_id or uuid.uuid4().hex,
        'type': contact_type,
        'label': label,
        'address': address,
        'href': href,
        'icon': icon,
        'published': bool(payload.get('published')),
        'locations': locations,
        'order': order,
    }


def load_contacts(*, published_only=False, location=None):
    try:
        raw_contacts = json.loads(get_setting(SETTING_KEY, '[]') or '[]')
    except (TypeError, ValueError):
        return []
    contacts = []
    if not isinstance(raw_contacts, list):
        return []
    for item in raw_contacts[:30]:
        if not isinstance(item, dict):
            continue
        try:
            contact = validate_contact(item, existing_id=str(item.get('id') or '') or None)
        except ContactValidationError:
            continue
        if published_only and not contact['published']:
            continue
        if location and location not in contact['locations']:
            continue
        contacts.append(contact)
    return sorted(contacts, key=lambda item: (item['order'], item['label'].casefold()))


def save_contact(payload, *, contact_id=None):
    contacts = load_contacts()
    if contact_id:
        if not any(item['id'] == contact_id for item in contacts):
            raise ContactValidationError('Контакт не найден.')
        updated = validate_contact(payload, existing_id=contact_id)
        contacts = [updated if item['id'] == contact_id else item for item in contacts]
    else:
        if len(contacts) >= 30:
            raise ContactValidationError('Можно сохранить не более 30 контактов.')
        contacts.append(validate_contact(payload))
    set_setting(SETTING_KEY, json.dumps(contacts, ensure_ascii=False, separators=(',', ':')))


def delete_contact(contact_id):
    contacts = load_contacts()
    filtered = [item for item in contacts if item['id'] != contact_id]
    if len(filtered) == len(contacts):
        raise ContactValidationError('Контакт не найден.')
    set_setting(SETTING_KEY, json.dumps(filtered, ensure_ascii=False, separators=(',', ':')))

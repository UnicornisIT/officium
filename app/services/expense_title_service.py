import re


DEFAULT_EXPENSE_TITLE = 'Операция по карте'


def clean_expense_title(description):
    text = _collapse_spaces(description)
    if not text:
        return DEFAULT_EXPENSE_TITLE

    text = _remove_card_masks(text)
    text = _remove_bank_timestamps(text)
    text = _normalize_bank_merchant_codes(text)

    special_title = _special_operation_title(text)
    if special_title:
        return special_title

    text = _remove_money_values(text)
    text = _remove_bank_service_phrases(text)
    text = _remove_terminal_and_location_parts(text)
    text = _remove_private_tail_numbers(text)
    text = _collapse_spaces(text).strip(' -—.,')

    if not text:
        return DEFAULT_EXPENSE_TITLE
    return _humanize_known_title(text)[:150]


def expense_group_key(title):
    cleaned = clean_expense_title(title)
    key = cleaned.lower().replace('ё', 'е')
    key = re.sub(r'[^0-9a-zа-я.]+', ' ', key)
    return _collapse_spaces(key)


def _collapse_spaces(value):
    return re.sub(r'\s+', ' ', str(value or '').replace('\xa0', ' ').replace('\u202f', ' ')).strip()


def _remove_card_masks(text):
    return re.sub(r'\*{2,}\d+', ' ', text)


def _remove_bank_timestamps(text):
    text = re.sub(r'\b\d{2}:\d{2}\d{2}\.\d{2}\.\d{4}\b', ' ', text)
    text = re.sub(r'\b\d{2}\.\d{2}\.\d{4}\d{2}:\d{2}\b', ' ', text)
    return re.sub(r'\b\d{2}:\d{2}\b', ' ', text)


def _normalize_bank_merchant_codes(text):
    text = re.sub(r'(?i)\byandex\*\d+\*(go|eda|plus)\b', r'YANDEX \1', text)
    text = re.sub(r'(?i)\boto\*', ' ', text)
    text = re.sub(r'(?i)\b([a-zа-яё]+)\*\d+\*([a-zа-яё]+)\b', r'\1 \2', text)
    return text.replace('*', ' ')


def _special_operation_title(text):
    normalized = text.lower().replace('ё', 'е')
    if 'внешний перевод по номеру телефона' in normalized:
        return 'Внешний перевод'
    if 'внутренний перевод на договор' in normalized:
        return 'Внутренний перевод'
    if 'пополнение кубышки' in normalized:
        return 'Пополнение Кубышки'
    if 'снятие наличных' in normalized:
        return 'Снятие наличных'
    return ''


def _remove_money_values(text):
    return re.sub(
        r'[+−-]?\s*(?:\d{1,3}(?:\s\d{3})+|\d+)(?:[,.]\d{2})\s*(?:₽|руб\.?|rub|rur)?',
        ' ',
        text,
        flags=re.IGNORECASE,
    )


def _remove_bank_service_phrases(text):
    text = re.sub(r'(?i)\bоперация по карте\b.*$', ' ', text)
    text = re.sub(r'(?i)\bпокупка по сбп.*$', ' ', text)
    text = re.sub(r'(?i)\bсписание денежных средств.*$', ' ', text)
    text = re.sub(r'(?i)^\s*оплата\s+в\s+', ' ', text)
    text = re.sub(r'(?i)^\s*покупка\s+в\s+', ' ', text)
    text = re.sub(r'(?i)^\s*оплата\s+', ' ', text)
    text = re.sub(r'(?i)^\s*покупка\s+', ' ', text)
    return text


def _remove_terminal_and_location_parts(text):
    text = re.sub(r'(?i)\b(rus|russia|россия)\s*\d+\b', ' ', text)
    text = re.sub(r'(?i)\b(rus|russia|россия)\b', ' ', text)

    prefix_locations = (
        'moscow', 'moskva', 'москва', 'stavropolskij', 'stavropolj',
        'stavropol', 'ставрополь', 'nizhniy novgo',
    )
    prefix_pattern = '|'.join(re.escape(location) for location in prefix_locations)
    text = re.sub(rf'(?i)^({prefix_pattern})\s+', ' ', text)

    suffix_locations = (
        'moscow', 'moskva', 'москва', 'stavropolj', 'stavropol',
        'ставрополь', 'nizhniy novgo',
    )
    suffix_pattern = '|'.join(re.escape(location) for location in suffix_locations)
    return re.sub(rf'(?i)\s+({suffix_pattern})\b.*$', ' ', text)


def _remove_private_tail_numbers(text):
    text = re.sub(r'(?<!\d)\+7\s*xxx\s*\d{4}(?!\d)', ' ', text)
    text = re.sub(r'\bxxx\d{4}\b', ' ', text)
    text = re.sub(r'(?i)\bдоговор\b', ' ', text)
    return re.sub(r'\b\d{6,}\b', ' ', text)


def _humanize_known_title(text):
    normalized = text.lower().replace('ё', 'е')
    aliases = (
        ('telegram', 'Telegram'),
        ('yandex go', 'YANDEX GO'),
        ('yandex eda', 'YANDEX EDA'),
        ('yandex plus', 'YANDEX PLUS'),
        ('point pita', 'POINT PITA'),
        ('panda lenina', 'PANDA LENINA'),
        ('familnaya pekarnya', 'FAMILNAYA PEKARNYA'),
    )
    for marker, title in aliases:
        if marker in normalized:
            return title
    return text

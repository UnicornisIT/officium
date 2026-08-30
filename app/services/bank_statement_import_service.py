import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from xml.etree import ElementTree

from app.services.expense_title_service import clean_expense_title
from app.utils import EXPENSE_CATEGORIES, PAYMENT_METHODS


EXPENSE_CATEGORY_KEYS = {key for key, _ in EXPENSE_CATEGORIES}
PAYMENT_METHOD_KEYS = {key for key, _ in PAYMENT_METHODS}

SUPPORTED_EXTENSIONS = {'.csv', '.txt', '.tsv', '.xlsx', '.pdf'}
MAX_STATEMENT_BYTES = 10 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_XLSX_MEMBERS = 500
MAX_PDF_PAGES = 200
MONEY = Decimal('0.01')

HEADER_ALIASES = {
    'date': (
        'дата операции', 'дата транзакции', 'дата платежа', 'дата списания',
        'дата', 'operation date', 'transaction date', 'date',
    ),
    'description': (
        'описание операции', 'описание', 'назначение платежа', 'назначение',
        'операция', 'получатель', 'контрагент', 'наименование', 'merchant',
        'description', 'details',
    ),
    'amount': (
        'сумма операции', 'сумма платежа', 'сумма в валюте счета',
        'сумма', 'amount', 'transaction amount',
    ),
    'debit': (
        'расход', 'списание', 'дебет', 'debit', 'withdrawal', 'outcome',
    ),
    'credit': (
        'приход', 'зачисление', 'кредит', 'credit', 'income',
    ),
    'category': (
        'категория', 'category', 'тип операции',
    ),
    'card': (
        'карта', 'номер карты', 'счет', 'счёт', 'account', 'card',
    ),
}

BANK_ALIASES = (
    ('sber', ('сбер', 'sber', 'sberbank')),
    ('tbank', ('т-банк', 'тбанк', 'тинькофф', 't-bank', 'tbank', 'tinkoff')),
    ('alfabank', ('альфа', 'alfa', 'alfabank', 'alpha')),
    ('vtb', ('втб', 'vtb')),
)

BANK_LABELS = {
    'sber': 'Сбер',
    'tbank': 'Т-Банк',
    'alfabank': 'Альфа-Банк',
    'vtb': 'ВТБ',
    'unknown': 'Не определен',
}

CATEGORY_RULES = (
    ('products', (
        'продукт', 'супермаркет', 'пятероч', '5ka', 'перекресток', 'perekrestok',
        'магнит', 'magnit', 'лента', 'lenta', 'вкусвилл', 'dixy', 'дикси',
        'ашан', 'auchan', 'самокат', 'лавка', 'яндекс еда', 'yandex eda',
        'yandex*5814*eda', '*eda',
    )),
    ('transport', (
        'такси', 'taxi', 'яндекс go', 'yandex go', 'uber', 'метро', 'metro',
        'тройка', 'transport', 'ржд', 'rzd', 'аэрофлот', 'aeroflot',
        'авиасейлс', 'azs', 'азс', 'бензин', 'fuel', 'yandex*4121*go',
        'yandex*go', 'автомобиль', 'автомобил', 'авто ',
    )),
    ('communication', (
        'мтс', 'mts', 'билайн', 'beeline', 'мегафон', 'megafon', 'tele2',
        'yota', 'йота', 'связь', 'интернет', 'internet',
    )),
    ('rent', (
        'аренда', 'rent', 'жкх', 'квартплата', 'коммуналь', 'mosenergo',
        'мосэнерго', 'водоканал',
    )),
    ('loans', (
        'кредит', 'loan', 'ипотек', 'mortgage', 'платеж по кредиту',
        'платёж по кредиту',
    )),
    ('restaurants', (
        'рестораны и кафе', 'ресторан', 'restaurant', 'кафе', 'cafe',
        'пекарн', 'point pita', 'panda', 'familnaya pekarnya', 'kulinariya',
        'кулинар', 'hachaturyan', 'hachatryan',
    )),
    ('entertainment', (
        'кино', 'cinema', 'театр', 'бар', 'steam', 'playstation', 'xbox',
        'развлеч',
    )),
    ('health', (
        'аптека', 'pharmacy', 'clinic', 'клиник', 'медицин', 'здоров',
        'лаборатор', 'инвитро', 'gemotest',
    )),
    ('education', (
        'курс', 'курсы', 'обуч', 'school', 'университет', 'udemy',
        'coursera', 'skillbox', 'geekbrains',
    )),
    ('clothing', (
        'одеж', 'clothes', 'fashion', 'lamoda', 'zara', 'befree',
        'gloria jeans', 'ostin', 'wildberries', 'wb ',
    )),
    ('subscriptions', (
        'подпис', 'subscription', 'spotify', 'netflix', 'youtube', 'apple.com/bill',
        'google', 'яндекс плюс', 'yandex plus', 'ivi', 'kion', 'okko', 'wink',
        'vk music', 'telegram', 'телеграм', 'yandex oblako', 'яндекс облако',
        'oblako',
    )),
)


@dataclass
class ImportedExpenseRow:
    title: str
    amount: Decimal
    expense_date: date
    category: str
    payment_method: str
    comment: str
    bank: str = 'unknown'
    source_category: str = ''
    raw_amount: str = ''
    row_number: int = 0
    duplicate: bool = False
    monthly_match_id: int = None
    monthly_match_title: str = ''
    monthly_match_amount: str = ''
    monthly_match_category: str = ''
    monthly_match_score: int = 0
    default_import_action: str = 'create'

    def to_dict(self):
        return {
            'title': self.title,
            'amount': str(self.amount),
            'expense_date': self.expense_date.isoformat(),
            'category': self.category,
            'payment_method': self.payment_method,
            'comment': self.comment,
            'bank': self.bank,
            'bank_label': BANK_LABELS.get(self.bank, BANK_LABELS['unknown']),
            'source_category': self.source_category,
            'raw_amount': self.raw_amount,
            'row_number': self.row_number,
            'duplicate': self.duplicate,
            'monthly_match_id': self.monthly_match_id,
            'monthly_match_title': self.monthly_match_title,
            'monthly_match_amount': self.monthly_match_amount,
            'monthly_match_category': self.monthly_match_category,
            'monthly_match_score': self.monthly_match_score,
            'default_import_action': self.default_import_action,
        }


@dataclass
class BankStatementParseResult:
    bank: str
    rows: list
    skipped_income: int = 0
    skipped_empty: int = 0
    errors: list = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def bank_label(self):
        return BANK_LABELS.get(self.bank, BANK_LABELS['unknown'])


def parse_bank_statement(file_bytes, filename):
    if not isinstance(file_bytes, (bytes, bytearray)) or not file_bytes:
        raise ValueError('Файл пустой.')
    if len(file_bytes) > MAX_STATEMENT_BYTES:
        raise ValueError('Файл слишком большой. Максимальный размер — 10 МБ.')

    extension = _file_extension(filename)
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError('Поддерживаются файлы CSV, TXT, TSV, XLSX и PDF.')

    if extension == '.pdf':
        return _parse_pdf_statement(file_bytes)

    rows = _read_xlsx_rows(file_bytes) if extension == '.xlsx' else _read_csv_rows(file_bytes)
    rows = _trim_table(rows)
    if not rows:
        raise ValueError('В файле не найдены строки с операциями.')

    bank = _detect_bank(rows)
    header_index, mapping = _detect_header(rows)
    data_rows = rows[header_index + 1:]
    result = BankStatementParseResult(bank=bank, rows=[])

    for offset, raw_row in enumerate(data_rows, start=header_index + 2):
        if not any(str(cell).strip() for cell in raw_row):
            result.skipped_empty += 1
            continue
        try:
            imported = _parse_operation_row(raw_row, mapping, bank, offset)
        except ValueError as exc:
            result.errors.append(f'Строка {offset}: {exc}')
            continue

        if imported is None:
            result.skipped_income += 1
            continue
        result.rows.append(imported)

    if not result.rows and not result.errors:
        raise ValueError('В файле не найдено расходов. Возможно, выписка содержит только пополнения.')
    return result


def _parse_pdf_statement(file_bytes):
    text = _extract_pdf_text(file_bytes)
    lines = _normalize_pdf_lines(text)
    if not lines:
        raise ValueError('PDF не содержит распознаваемого текста. Если это скан, загрузите CSV/XLSX или текстовый PDF.')

    bank = _detect_bank([[line] for line in lines[:30]])

    if _is_sber_credit_card_pdf(text):
        return _finalize_pdf_result(_parse_sber_credit_card_pdf(lines, bank))

    if _is_tbank_movement_pdf(text):
        return _finalize_pdf_result(_parse_tbank_movement_pdf(lines, bank))

    result = BankStatementParseResult(bank=bank, rows=[])

    for line_number, line in enumerate(lines, start=1):
        try:
            imported = _parse_pdf_operation_line(line, bank, line_number)
        except ValueError as exc:
            result.errors.append(f'Строка PDF {line_number}: {exc}')
            continue

        if imported is None:
            continue
        result.rows.append(imported)

    return _finalize_pdf_result(result)


def _finalize_pdf_result(result):
    if not result.rows and not result.errors:
        raise ValueError('В PDF не найдено расходов. Поддерживаются текстовые PDF Сбера и Т-Банка, а также CSV/XLSX.')
    return result


def _extract_pdf_text(file_bytes):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError('Для импорта PDF установите зависимость pypdf и перезапустите приложение.')

    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError('PDF содержит слишком много страниц. Максимум — 200.')
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    except ValueError:
        raise
    except Exception:
        raise ValueError('Не удалось прочитать PDF-файл. Проверьте, что файл не поврежден и не защищен паролем.')

    if len(text.strip()) < 10:
        raise ValueError('PDF не содержит распознаваемого текста. Если это скан, загрузите CSV/XLSX или текстовый PDF.')
    return text


def _normalize_pdf_lines(text):
    lines = []
    for line in text.splitlines():
        cleaned = re.sub(r'\s+', ' ', _clean_cell(line))
        if cleaned:
            lines.append(cleaned)
    return lines


def _is_sber_credit_card_pdf(text):
    normalized = _normalize_header(text)
    return 'выписка по счету кредитной карты' in normalized and 'расшифровка операций' in normalized


def _is_tbank_movement_pdf(text):
    normalized = _normalize_header(text)
    has_bank_name = any(alias in normalized for alias in ('тбанк', 'т-банк', 'тинькофф', 'tbank', 't-bank'))
    return has_bank_name and 'справка о движении средств' in normalized and 'движение средств за период' in normalized


def _parse_sber_credit_card_pdf(lines, bank):
    result = BankStatementParseResult(bank='sber' if bank == 'unknown' else bank, rows=[])
    in_operations = False
    block = []
    block_line_number = 0

    for line_number, line in enumerate(lines, start=1):
        if 'расшифровка операций' in _normalize_header(line):
            in_operations = True
            continue
        if in_operations and _is_sber_pdf_operations_end_line(line):
            _append_sber_credit_card_block(result, block, block_line_number)
            block = []
            in_operations = False
            continue
        if not in_operations or _is_sber_pdf_noise_line(line):
            continue

        if _is_pdf_date_line(line):
            _append_sber_credit_card_block(result, block, block_line_number)
            block = [line]
            block_line_number = line_number
        elif block:
            block.append(line)

    _append_sber_credit_card_block(result, block, block_line_number)
    return result


def _append_sber_credit_card_block(result, block, line_number):
    if not block:
        return
    try:
        imported = _parse_sber_credit_card_block(block, result.bank, line_number)
    except ValueError as exc:
        result.errors.append(f'Строка PDF {line_number}: {exc}')
        return

    if imported is None:
        result.skipped_income += 1
        return
    result.rows.append(imported)


def _parse_sber_credit_card_block(block, bank, line_number):
    operation_date = _parse_date_value(block[0])
    if operation_date is None:
        raise ValueError('не распознана дата операции')

    source_category = ''
    description_parts = []
    for line in block[1:]:
        if _is_sber_processing_date_line(line) or _is_sber_pdf_noise_line(line):
            continue

        category_match = re.match(r'^\d{6}(.+)$', line)
        if category_match:
            source_category = category_match.group(1).strip()
            continue

        description_parts.append(line)
        if len(_extract_sber_pdf_money_values(' '.join(description_parts))) >= 2:
            break

    description = ' '.join(description_parts)
    money_values = _extract_sber_pdf_money_values(description)
    if not money_values:
        raise ValueError('не распознана сумма операции')

    amount_raw = money_values[0]
    amount = _parse_amount_value(amount_raw)
    if amount is None:
        raise ValueError('не распознана сумма операции')
    if amount_raw.strip().startswith('+'):
        return None

    clean_description = _clean_pdf_description(description, money_values)
    title = _normalize_title(clean_description or source_category or 'Операция по карте')
    category = _guess_category(' '.join((source_category, clean_description)))
    bank_label = BANK_LABELS.get(bank, BANK_LABELS['unknown'])
    return ImportedExpenseRow(
        title=title,
        amount=abs(amount),
        expense_date=operation_date,
        category=category,
        payment_method=_guess_payment_method(clean_description),
        comment=f'Импорт PDF-выписки: {bank_label}; категория банка: {source_category}; строка PDF: {line_number}',
        bank=bank,
        source_category=source_category,
        raw_amount=amount_raw,
        row_number=line_number,
    )


def _extract_sber_pdf_money_values(description):
    search_area = re.split(r'\*{2,}\d{4}', description)[-1]
    money_values = [match.group(0).strip() for match in _find_money_matches(search_area)]
    if not money_values:
        money_values = [match.group(0).strip() for match in _find_money_matches(description)]
    return money_values


def _parse_tbank_movement_pdf(lines, bank):
    result = BankStatementParseResult(bank='tbank' if bank == 'unknown' else bank, rows=[])
    in_operations = False
    block = []
    block_line_number = 0

    for line_number, line in enumerate(lines, start=1):
        if 'движение средств за период' in _normalize_header(line):
            in_operations = True
            continue
        if not in_operations or _is_tbank_pdf_noise_line(line):
            continue

        if _is_pdf_date_line(line):
            _append_tbank_movement_block(result, block, block_line_number)
            block = [line]
            block_line_number = line_number
        elif block:
            block.append(line)

    _append_tbank_movement_block(result, block, block_line_number)
    return result


def _append_tbank_movement_block(result, block, line_number):
    if not block:
        return
    try:
        imported = _parse_tbank_movement_block(block, result.bank, line_number)
    except ValueError as exc:
        result.errors.append(f'Строка PDF {line_number}: {exc}')
        return

    if imported is None:
        result.skipped_income += 1
        return
    result.rows.append(imported)


def _parse_tbank_movement_block(block, bank, line_number):
    operation_date = _parse_date_value(block[0])
    if operation_date is None:
        raise ValueError('не распознана дата операции')

    details = ' '.join(line for line in block[1:] if not _is_tbank_processing_date_line(line))
    money_matches = _find_money_matches(details)
    if not money_matches:
        raise ValueError('не распознана сумма операции')

    amount_raw = money_matches[0].group(0).strip()
    amount = _parse_amount_value(amount_raw)
    if amount is None:
        raise ValueError('не распознана сумма операции')
    if amount >= 0:
        return None

    description_start = money_matches[1].end() if len(money_matches) > 1 else money_matches[0].end()
    description = _clean_pdf_description(details[description_start:])
    title = _normalize_title(description or 'Операция Т-Банка')
    bank_label = BANK_LABELS.get(bank, BANK_LABELS['unknown'])
    return ImportedExpenseRow(
        title=title,
        amount=abs(amount),
        expense_date=operation_date,
        category=_guess_category(description),
        payment_method=_guess_payment_method(description),
        comment=f'Импорт PDF-выписки: {bank_label}; строка PDF: {line_number}',
        bank=bank,
        raw_amount=amount_raw,
        row_number=line_number,
    )


def _is_pdf_date_line(line):
    return re.match(r'^\d{2}\.\d{2}\.\d{4}$', line) is not None


def _is_sber_processing_date_line(line):
    return re.match(r'^\d{2}\.\d{2}\.\d{4}\d{2}:\d{2}$', line) is not None


def _is_sber_pdf_operations_end_line(line):
    return _normalize_header(line).startswith('дата формирования документа')


def _is_tbank_processing_date_line(line):
    return re.match(r'^\d{2}:\d{2}\d{2}\.\d{2}\.\d{4}$', line) is not None


def _is_sber_pdf_noise_line(line):
    normalized = _normalize_header(line)
    if not normalized:
        return True
    return (
        normalized.startswith('дата операции')
        or normalized.startswith('дата обработки')
        or 'код авторизациикатегория' in normalized
        or normalized.startswith('описание операции')
        or normalized.startswith('сумма в валюте')
        or normalized.startswith('операции') and 'остаток средств' in normalized
        or normalized.startswith('остаток средств')
        or normalized.startswith('продолжение на следующей странице')
        or normalized.startswith('выписка по счету кредитной карты страница')
        or normalized.startswith('индивидуальная выписка по счету кредитной карты')
        or normalized.startswith('дата формирования документа')
        or normalized.startswith('пао сбербанк')
        or normalized.startswith('денежные средства списываются')
        or normalized.startswith('в выписке отображаются')
        or normalized.startswith('срок обработки операций')
        or normalized.startswith('согласно статье')
        or normalized.startswith('электронной подписью')
        or normalized.startswith('правоотношениях')
        or normalized.startswith('скачать электронный формат подписи')
        or normalized.startswith('проверить подпись')
        or normalized == '*'
        or re.match(r'^[0-9a-f]{20,}$', normalized) is not None
        or re.match(r'^с \d{2}\.\d{2}\.\d{4} по \d{2}\.\d{2}\.\d{4}$', normalized) is not None
    )


def _is_tbank_pdf_noise_line(line):
    normalized = _normalize_header(line)
    if not normalized:
        return True
    header_lines = {
        'дата и время',
        'операциидата',
        'списаниясумма в валюте',
        'операциисумма операции',
        'в валюте картыописание',
        'операцииномер',
        'карты',
    }
    return (
        normalized in header_lines
        or normalized.isdigit()
        or normalized.startswith('ао «тбанк»')
        or normalized.startswith('бик ')
        or normalized.startswith('с уважением')
        or normalized.startswith('руководитель')
        or normalized.endswith('пополнения:')
        or normalized.endswith('расходы:')
    )


def _find_money_matches(text):
    return list(re.finditer(
        r'[+−-]?\s*(?:\d{1,3}(?:\s\d{3})+|\d+)(?:[,.]\d{2})\s*(?:₽|руб\.?|rub|rur)?',
        text,
        flags=re.IGNORECASE,
    ))


def _clean_pdf_description(description, money_values=None):
    text = _clean_cell(description)
    text = re.sub(r'\*{2,}\d{4}', ' ', text)
    for value in money_values or []:
        text = text.replace(value, ' ', 1)
    text = re.sub(r'(?i)\bоперация по карте\b.*$', ' ', text)
    text = re.sub(r'\b\d{2}:\d{2}\d{2}\.\d{2}\.\d{4}\b', ' ', text)
    text = re.sub(r'\b\d{2}\.\d{2}\.\d{4}\d{2}:\d{2}\b', ' ', text)
    text = re.sub(r'\b\d{2}:\d{2}\b', ' ', text)
    text = _mask_private_numbers(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip(' -—.,')


def _mask_private_numbers(text):
    text = re.sub(r'(?<!\d)(\+7)\d{7,}(\d{4})(?!\d)', r'\1 xxx \2', text)
    return re.sub(r'\b\d{6,}(\d{4})\b', r'xxx\1', text)


def _guess_payment_method(description):
    normalized = _normalize_header(description)
    if 'снятие наличных' in normalized or 'cash' in normalized:
        return 'cash'
    if 'перевод' in normalized or 'пополнение кубышки' in normalized:
        return 'transfer'
    return 'card'


def _parse_pdf_operation_line(line, bank, line_number):
    date_matches = re.findall(r'\d{2}[./-]\d{2}[./-]\d{2,4}', line)
    if not date_matches:
        return None

    line_without_dates = line
    for value in date_matches:
        line_without_dates = line_without_dates.replace(value, ' ', 1)

    amount_matches = [match.group(0).strip() for match in _find_money_matches(line_without_dates)]
    if not amount_matches:
        return None

    amount_raw = amount_matches[-1]
    amount = _parse_amount_value(amount_raw)
    if amount is None:
        raise ValueError('не распознана сумма операции')

    operation_date = _parse_date_value(date_matches[0])
    if operation_date is None:
        raise ValueError('не распознана дата операции')

    description = line
    for value in date_matches:
        description = description.replace(value, ' ', 1)
    description = description.replace(amount_raw, ' ', 1)
    description = re.sub(r'\s+', ' ', description).strip(' -—')
    if not description:
        description = 'Операция по карте'

    is_expense = amount < 0 or _looks_like_expense(description)
    if not is_expense:
        return None

    category = _guess_category(description)
    bank_label = BANK_LABELS.get(bank, BANK_LABELS['unknown'])
    return ImportedExpenseRow(
        title=_normalize_title(description),
        amount=abs(amount),
        expense_date=operation_date,
        category=category,
        payment_method='card',
        comment=f'Импорт PDF-выписки: {bank_label}; строка PDF: {line_number}',
        bank=bank,
        raw_amount=amount_raw,
        row_number=line_number,
    )


def _file_extension(filename):
    filename = (filename or '').lower().strip()
    if '.' not in filename:
        return ''
    return '.' + filename.rsplit('.', 1)[1]


def _decode_text(file_bytes):
    if file_bytes.startswith((b'\xff\xfe', b'\xfe\xff')):
        return file_bytes.decode('utf-16')

    for encoding in ('utf-8-sig', 'cp1251', 'windows-1251'):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode('utf-8', errors='ignore')


def _read_csv_rows(file_bytes):
    text = _decode_text(file_bytes)
    delimiter = _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[_clean_cell(cell) for cell in row] for row in reader]


def _detect_delimiter(text):
    lines = [line for line in text.splitlines()[:30] if line.strip()]
    best = (';', -1, -1)
    for delimiter in (';', '\t', ','):
        column_counts = [len(line.split(delimiter)) for line in lines]
        multi_column_rows = sum(1 for count in column_counts if count > 1)
        widest_row = max(column_counts or [1])
        candidate = (delimiter, multi_column_rows, widest_row)
        if candidate[1:] > best[1:]:
            best = candidate
    return best[0]


def _read_xlsx_rows(file_bytes):
    try:
        archive = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile:
        raise ValueError('XLSX-файл поврежден или имеет неподдерживаемый формат.')

    members = archive.infolist()
    if len(members) > MAX_XLSX_MEMBERS:
        archive.close()
        raise ValueError('XLSX-файл содержит слишком много внутренних файлов.')
    uncompressed_size = sum(member.file_size for member in members)
    if uncompressed_size > MAX_XLSX_UNCOMPRESSED_BYTES:
        archive.close()
        raise ValueError('XLSX-файл слишком велик после распаковки.')
    if any(
        member.file_size > 1024 * 1024
        and member.compress_size > 0
        and member.file_size / member.compress_size > 200
        for member in members
    ):
        archive.close()
        raise ValueError('XLSX-файл имеет небезопасную степень сжатия.')

    try:
        shared_strings = _xlsx_shared_strings(archive)
        worksheet_name = _first_worksheet_name(archive)
        namespace = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        root = ElementTree.fromstring(archive.read(worksheet_name))
        rows = []
        for row_el in root.findall('.//x:sheetData/x:row', namespace):
            values = []
            for cell_el in row_el.findall('x:c', namespace):
                cell_ref = cell_el.attrib.get('r', '')
                col_index = _xlsx_column_index(cell_ref)
                while len(values) < col_index:
                    values.append('')
                values.append(_xlsx_cell_value(cell_el, shared_strings, namespace))
            rows.append(values)
        return rows
    except (ElementTree.ParseError, KeyError):
        raise ValueError('XLSX-файл поврежден или имеет неподдерживаемую структуру.')
    finally:
        archive.close()


def _xlsx_shared_strings(archive):
    if 'xl/sharedStrings.xml' not in archive.namelist():
        return []
    namespace = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    root = ElementTree.fromstring(archive.read('xl/sharedStrings.xml'))
    strings = []
    for item in root.findall('.//x:si', namespace):
        parts = [node.text or '' for node in item.findall('.//x:t', namespace)]
        strings.append(''.join(parts))
    return strings


def _first_worksheet_name(archive):
    names = [name for name in archive.namelist() if name.startswith('xl/worksheets/sheet') and name.endswith('.xml')]
    if not names:
        raise ValueError('В XLSX-файле не найден лист с операциями.')
    return sorted(names)[0]


def _xlsx_column_index(cell_ref):
    letters = ''.join(ch for ch in cell_ref if ch.isalpha()).upper()
    if not letters:
        return 1
    index = 0
    for letter in letters:
        index = index * 26 + (ord(letter) - ord('A') + 1)
    return index


def _xlsx_cell_value(cell_el, shared_strings, namespace):
    cell_type = cell_el.attrib.get('t')
    if cell_type == 'inlineStr':
        parts = [node.text or '' for node in cell_el.findall('.//x:t', namespace)]
        return _clean_cell(''.join(parts))

    value_el = cell_el.find('x:v', namespace)
    if value_el is None or value_el.text is None:
        return ''

    value = value_el.text
    if cell_type == 's':
        try:
            return _clean_cell(shared_strings[int(value)])
        except (ValueError, IndexError):
            return ''
    return _clean_cell(value)


def _trim_table(rows):
    trimmed = []
    for row in rows:
        normalized = [_clean_cell(cell) for cell in row]
        while normalized and normalized[-1] == '':
            normalized.pop()
        if normalized:
            trimmed.append(normalized)
    return trimmed


def _clean_cell(value):
    return str(value or '').replace('\xa0', ' ').replace('\u202f', ' ').strip()


def _detect_bank(rows):
    text = _normalize_header(' '.join(' '.join(row) for row in rows[:12]))
    for bank, aliases in BANK_ALIASES:
        if any(alias in text for alias in aliases):
            return bank
    return 'unknown'


def _detect_header(rows):
    best = None
    for index, row in enumerate(rows[:40]):
        mapping = _map_headers(row)
        score = _header_score(mapping)
        if score >= 5 and (best is None or score > best[0]):
            best = (score, index, mapping)

    if best is None:
        raise ValueError('Не удалось определить заголовки выписки. Проверьте, что есть колонки даты, описания и суммы.')
    return best[1], best[2]


def _map_headers(row):
    mapping = {}
    for index, header in enumerate(row):
        normalized = _normalize_header(header)
        for key, aliases in HEADER_ALIASES.items():
            if key in mapping:
                continue
            if any(alias in normalized for alias in aliases):
                mapping[key] = index
    return mapping


def _header_score(mapping):
    score = 0
    if 'date' in mapping:
        score += 2
    if 'description' in mapping:
        score += 2
    if 'amount' in mapping:
        score += 2
    if 'debit' in mapping:
        score += 2
    if 'credit' in mapping:
        score += 1
    if 'category' in mapping:
        score += 1
    return score


def _normalize_header(value):
    value = str(value or '').lower().replace('ё', 'е')
    value = re.sub(r'[\n\r\t]+', ' ', value)
    value = re.sub(r'\s+', ' ', value)
    return value.strip()


def _parse_operation_row(row, mapping, bank, row_number):
    operation_date = _parse_date_value(_get(row, mapping.get('date')))
    if operation_date is None:
        raise ValueError('не распознана дата операции')

    description = _get(row, mapping.get('description')) or 'Операция по карте'
    source_category = _get(row, mapping.get('category'))
    card = _get(row, mapping.get('card'))
    amount, is_expense, raw_amount = _extract_amount(row, mapping, ' '.join((description, source_category)))
    if amount is None:
        raise ValueError('не распознана сумма операции')
    if not is_expense:
        return None

    title = _normalize_title(description)
    category = _guess_category(' '.join((description, source_category)))
    payment_method = 'card' if card or bank in {'sber', 'tbank', 'alfabank', 'vtb'} else 'other'
    comment_parts = [f'Импорт выписки: {BANK_LABELS.get(bank, BANK_LABELS["unknown"])}']
    if source_category:
        comment_parts.append(f'категория банка: {source_category}')
    if card:
        comment_parts.append(f'счет/карта: {card}')

    return ImportedExpenseRow(
        title=title,
        amount=amount,
        expense_date=operation_date,
        category=category,
        payment_method=payment_method if payment_method in PAYMENT_METHOD_KEYS else 'other',
        comment='; '.join(comment_parts),
        bank=bank,
        source_category=source_category,
        raw_amount=raw_amount,
        row_number=row_number,
    )


def _extract_amount(row, mapping, expense_hint=''):
    debit_value = _parse_amount_value(_get(row, mapping.get('debit')))
    credit_value = _parse_amount_value(_get(row, mapping.get('credit')))

    if debit_value and debit_value > 0:
        raw = _get(row, mapping.get('debit'))
        return debit_value, True, raw
    if credit_value and credit_value > 0 and not debit_value:
        raw = _get(row, mapping.get('credit'))
        return credit_value, False, raw

    amount_raw = _get(row, mapping.get('amount'))
    amount = _parse_amount_value(amount_raw)
    if amount is None:
        return None, False, amount_raw
    return abs(amount), amount < 0 or _looks_like_expense(expense_hint), amount_raw


def _get(row, index):
    if index is None or index >= len(row):
        return ''
    return _clean_cell(row[index])


def _parse_date_value(value):
    value = _clean_cell(value)
    if not value:
        return None

    excel_serial = _parse_excel_date(value)
    if excel_serial:
        return excel_serial

    candidates = [
        value,
        value.split(' ')[0],
        value.split('T')[0],
    ]
    formats = (
        '%d.%m.%Y', '%d.%m.%y', '%Y-%m-%d', '%d/%m/%Y', '%d/%m/%y',
        '%d-%m-%Y', '%d-%m-%y', '%Y.%m.%d',
    )
    for candidate in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def _parse_excel_date(value):
    try:
        serial = float(str(value).replace(',', '.'))
    except ValueError:
        return None
    if serial < 20000 or serial > 80000:
        return None
    return date(1899, 12, 30) + timedelta(days=int(serial))


def _parse_amount_value(value):
    text = _clean_cell(value)
    if not text:
        return None

    negative = text.startswith('-') or text.startswith('−') or (text.startswith('(') and text.endswith(')'))
    text = text.replace('−', '-').replace('₽', '').replace('руб.', '').replace('руб', '')
    text = re.sub(r'(?i)\b(rub|rur)\b', '', text)
    text = re.sub(r'[^0-9,.\-]', '', text)
    if text in {'', '-', '.', ','}:
        return None

    text = text.strip('-')
    if ',' in text and '.' in text:
        last_comma = text.rfind(',')
        last_dot = text.rfind('.')
        decimal_sep = ',' if last_comma > last_dot else '.'
        thousands_sep = '.' if decimal_sep == ',' else ','
        text = text.replace(thousands_sep, '')
        text = text.replace(decimal_sep, '.')
    else:
        text = text.replace(',', '.')

    try:
        amount = Decimal(text)
        if not amount.is_finite():
            return None
        amount = amount.quantize(MONEY, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    return -amount if negative else amount


def _normalize_title(description):
    return clean_expense_title(description)


def _guess_category(text):
    normalized = _normalize_header(text)
    for category, keywords in CATEGORY_RULES:
        if category in EXPENSE_CATEGORY_KEYS and any(keyword in normalized for keyword in keywords):
            return category
    return 'other'


def _looks_like_expense(text):
    normalized = _normalize_header(text)
    if _guess_category(normalized) != 'other':
        return True
    expense_words = (
        'покупка', 'оплата', 'списание', 'расход', 'withdrawal',
        'purchase', 'payment',
    )
    return any(word in normalized for word in expense_words)

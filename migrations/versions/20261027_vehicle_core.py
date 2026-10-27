"""Vehicle core and registration countries.

Revision ID: 20261027_vehicle_core
Revises: 20261018_last_seen_release
"""
from alembic import op
import sqlalchemy as sa

revision = '20261027_vehicle_core'
down_revision = '20261018_last_seen_release'
branch_labels = None
depends_on = None

COUNTRIES = [
    ('RU', 'Россия'), ('AB', 'Абхазия'), ('BY', 'Беларусь'),
    ('KZ', 'Казахстан'), ('AM', 'Армения'), ('KG', 'Кыргызстан'), ('OTHER', 'Другая'),
]


def upgrade():
    countries = op.create_table('registration_countries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(8), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('flag_path', sa.String(150)),
        sa.Column('strict_format', sa.Boolean(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.UniqueConstraint('code', name='uq_registration_countries_code'),
    )
    op.bulk_insert(countries, [dict(code=code, name=name,
        flag_path=f'images/flags/{code.lower()}.svg' if code != 'OTHER' else None,
        strict_format=code == 'RU', sort_order=index, is_active=True)
        for index, (code, name) in enumerate(COUNTRIES)])
    op.create_table('vehicles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('body_type', sa.String(20), nullable=False),
        sa.Column('color', sa.String(7), nullable=False),
        sa.Column('fuel_type', sa.String(20), nullable=False),
        sa.Column('make', sa.String(80)), sa.Column('model', sa.String(80)),
        sa.Column('year', sa.String(20)), sa.Column('vin', sa.String(17)),
        sa.Column('registration_country_id', sa.Integer()),
        sa.Column('plate', sa.String(12)),
        sa.Column('is_company', sa.Boolean(), nullable=False),
        sa.Column('default_payer', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('archived_at', sa.DateTime()),
        sa.Column('archive_reason', sa.String(20)), sa.Column('archive_note', sa.String(2000)),
        sa.Column('tractor_id', sa.Integer()),
        sa.Column('mileage_reminder_days', sa.Integer(), nullable=False),
        sa.Column('mileage_snooze_until', sa.Date()),
        sa.Column('created_at', sa.DateTime(), nullable=False), sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('user_id', 'name', name='uq_vehicles_user_name'),
        sa.CheckConstraint("default_payer = 'self' OR is_company = 1", name='ck_vehicles_company_payer'),
        sa.CheckConstraint('mileage_reminder_days >= 0', name='ck_vehicles_reminder_days'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_vehicles_user_id'),
        sa.ForeignKeyConstraint(['registration_country_id'], ['registration_countries.id'], name='fk_vehicles_country_id'),
        sa.ForeignKeyConstraint(['tractor_id'], ['vehicles.id'], name='fk_vehicles_tractor_id', ondelete='SET NULL'),
    )
    op.create_index('ix_vehicles_user_id', 'vehicles', ['user_id'])
    op.create_table('vehicle_odometer_readings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('vehicle_id', sa.Integer(), nullable=False),
        sa.Column('reading_date', sa.Date(), nullable=False),
        sa.Column('odometer_km', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.UniqueConstraint('vehicle_id', 'reading_date', name='uq_vehicle_reading_date'),
        sa.CheckConstraint('odometer_km >= 0', name='ck_vehicle_reading_nonnegative'),
        sa.ForeignKeyConstraint(['vehicle_id'], ['vehicles.id'], name='fk_vehicle_reading_vehicle_id', ondelete='CASCADE'),
    )
    with op.batch_alter_table('expenses') as batch:
        batch.add_column(sa.Column('vehicle_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_expenses_vehicle_id', 'vehicles', ['vehicle_id'], ['id'], ondelete='SET NULL')
        batch.create_index('ix_expenses_vehicle_id', ['vehicle_id'])


def downgrade():
    # Preserve vehicle history: this release uses forward-only schema changes.
    pass

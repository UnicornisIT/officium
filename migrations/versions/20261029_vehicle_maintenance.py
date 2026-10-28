"""Maintenance templates, service history and defects; copied user schedules."""
from alembic import op
import sqlalchemy as sa
revision = '20261029_vehicle_maintenance'
down_revision = '20261028_vehicle_cost_details'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('consumable_templates',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('template_set', sa.String(length=20), primary_key=False, nullable=False),
        sa.Column('name', sa.String(length=150), primary_key=False, nullable=False),
        sa.Column('interval_km', sa.Integer(), primary_key=False, nullable=True),
        sa.Column('interval_months', sa.Integer(), primary_key=False, nullable=True),
        sa.Column('applies_to_electric', sa.Boolean(), primary_key=False, nullable=False),
        sa.Column('sort_order', sa.Integer(), primary_key=False, nullable=False),
        sa.Column('is_active', sa.Boolean(), primary_key=False, nullable=False),
        sa.CheckConstraint('(interval_km > 0 OR interval_km IS NULL) AND (interval_months > 0 OR interval_months IS NULL) AND (interval_km IS NOT NULL OR interval_months IS NOT NULL)', name='ck_consumable_template_intervals'),
        sa.CheckConstraint("template_set IN ('passenger', 'truck')", name='ck_consumable_template_set'),
    )
    op.create_table('vehicle_maintenance_items',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', name='fk_maintenance_item_vehicle', ondelete='CASCADE'), primary_key=False, nullable=False),
        sa.Column('template_id', sa.Integer(), sa.ForeignKey('consumable_templates.id', name='fk_maintenance_item_template', ondelete='SET NULL'), primary_key=False, nullable=True),
        sa.Column('name', sa.String(length=150), primary_key=False, nullable=False),
        sa.Column('interval_km', sa.Integer(), primary_key=False, nullable=True),
        sa.Column('interval_months', sa.Integer(), primary_key=False, nullable=True),
        sa.Column('last_done_date', sa.Date(), primary_key=False, nullable=True),
        sa.Column('last_done_km', sa.Integer(), primary_key=False, nullable=True),
        sa.Column('is_active', sa.Boolean(), primary_key=False, nullable=False),
        sa.CheckConstraint('(interval_km > 0 OR interval_km IS NULL) AND (interval_months > 0 OR interval_months IS NULL) AND (interval_km IS NOT NULL OR interval_months IS NOT NULL)', name='ck_maintenance_item_intervals'),
        sa.CheckConstraint('last_done_km >= 0 OR last_done_km IS NULL', name='ck_maintenance_item_km'),
    )
    op.create_index('ix_vehicle_maintenance_items_vehicle_id', 'vehicle_maintenance_items', ['vehicle_id'])
    op.create_table('vehicle_service_records',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', name='fk_service_record_vehicle', ondelete='CASCADE'), primary_key=False, nullable=False),
        sa.Column('service_date', sa.Date(), primary_key=False, nullable=False),
        sa.Column('odometer_km', sa.Integer(), primary_key=False, nullable=True),
        sa.Column('comment', sa.String(length=2000), primary_key=False, nullable=True),
        sa.Column('expense_id', sa.Integer(), sa.ForeignKey('expenses.id', name='fk_service_record_expense', ondelete='SET NULL'), primary_key=False, nullable=True),
        sa.Column('vehicle_cost_id', sa.Integer(), sa.ForeignKey('vehicle_costs.id', name='fk_service_record_cost', ondelete='SET NULL'), primary_key=False, nullable=True),
        sa.CheckConstraint('odometer_km >= 0 OR odometer_km IS NULL', name='ck_service_record_km'),
        sa.CheckConstraint('expense_id IS NULL OR vehicle_cost_id IS NULL', name='ck_service_record_single_spend'),
    )
    op.create_index('ix_vehicle_service_records_vehicle_id', 'vehicle_service_records', ['vehicle_id'])
    op.create_table('vehicle_service_items',
        sa.Column('service_record_id', sa.Integer(), sa.ForeignKey('vehicle_service_records.id', name='fk_service_items_record', ondelete='CASCADE'), primary_key=True, nullable=False),
        sa.Column('maintenance_item_id', sa.Integer(), sa.ForeignKey('vehicle_maintenance_items.id', name='fk_service_items_item', ondelete='CASCADE'), primary_key=True, nullable=False),
    )
    op.create_table('vehicle_defects',
        sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
        sa.Column('vehicle_id', sa.Integer(), sa.ForeignKey('vehicles.id', name='fk_defect_vehicle', ondelete='CASCADE'), primary_key=False, nullable=False),
        sa.Column('title', sa.String(length=150), primary_key=False, nullable=False),
        sa.Column('severity', sa.String(length=20), primary_key=False, nullable=False),
        sa.Column('status', sa.String(length=20), primary_key=False, nullable=False),
        sa.Column('found_date', sa.Date(), primary_key=False, nullable=False),
        sa.Column('fixed_date', sa.Date(), primary_key=False, nullable=True),
        sa.Column('service_record_id', sa.Integer(), sa.ForeignKey('vehicle_service_records.id', name='fk_defect_service', ondelete='SET NULL'), primary_key=False, nullable=True),
        sa.Column('comment', sa.String(length=2000), primary_key=False, nullable=True),
        sa.CheckConstraint("(status = 'open' AND fixed_date IS NULL) OR (status = 'fixed' AND fixed_date IS NOT NULL AND fixed_date >= found_date)", name='ck_vehicle_defect_dates'),
        sa.CheckConstraint("severity IN ('critical', 'attention', 'minor')", name='ck_vehicle_defect_severity'),
        sa.CheckConstraint("status IN ('open', 'fixed')", name='ck_vehicle_defect_status'),
    )
    op.create_index('ix_vehicle_defects_vehicle_id', 'vehicle_defects', ['vehicle_id'])
    seed_templates()


def seed_templates():
    table = sa.table('consumable_templates',
        sa.column('template_set', sa.String), sa.column('name', sa.String),
        sa.column('interval_km', sa.Integer), sa.column('interval_months', sa.Integer),
        sa.column('applies_to_electric', sa.Boolean), sa.column('sort_order', sa.Integer),
        sa.column('is_active', sa.Boolean))
    passenger = [
        ('Масло ДВС и масляный фильтр', 10000, 12, False),
        ('Воздушный фильтр', 20000, 24, True),
        ('Салонный фильтр', 15000, 12, True),
        ('Свечи', 30000, 36, False),
        ('Тормозные колодки', 30000, None, True),
        ('Тормозная жидкость', None, 24, True),
        ('Антифриз', 60000, 48, True),
        ('Ремень или цепь ГРМ', 90000, 60, False),
        ('Масло КПП', 60000, 48, True),
        ('АКБ', None, 48, True),
        ('Щётки стеклоочистителя', None, 12, True),
        ('Сезонная смена шин', None, 6, True),
    ]
    # The brief specifies only the truck oil override; other shared intervals
    # retain the passenger baseline and remain editable by a superadmin.
    truck = [(name, 30000 if index == 0 else km, months, electric)
             for index, (name, km, months, electric) in enumerate(passenger)] + [
        ('Топливный фильтр и сепаратор', 30000, 12, False),
        ('Масло в мостах', 100000, 24, True),
        ('Фильтр-осушитель пневмосистемы', None, 12, True),
        ('Тормозные диски и барабаны', 100000, None, True),
        ('Смазка шкворня ССУ', 5000, 1, True),
    ]
    rows = [dict(template_set=group, name=name, interval_km=km,
                 interval_months=months, applies_to_electric=electric,
                 sort_order=index, is_active=True)
            for group, entries in [('passenger', passenger), ('truck', truck)]
            for index, (name, km, months, electric) in enumerate(entries)]
    op.bulk_insert(table, rows)


def downgrade():
    raise RuntimeError('Destructive downgrade is disabled to preserve maintenance history.')

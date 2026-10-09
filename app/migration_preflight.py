import click
from sqlalchemy import inspect, text


BASELINE_REVISION = '73459c8513a1'
BASELINE_TABLES = {
    'activity_logs',
    'app_settings',
    'debts',
    'dictionary_entries',
    'expenses',
    'incomes',
    'payments',
    'users',
}
REVISION_ALIASES = {
    '20260502_add_mortgage_debt_type': '20260502_mortgage',
    '20260502_add_activity_log_ip_user_agent': '20260502_log_ip_ua',
}
VERSION_TABLE = 'alembic_version'


class MigrationPreflightError(RuntimeError):
    pass


def _baseline_state(user_tables):
    if not user_tables:
        return 'empty'
    if BASELINE_TABLES.issubset(user_tables):
        return 'stamp_baseline'

    missing = ', '.join(sorted(BASELINE_TABLES - user_tables))
    raise MigrationPreflightError(
        'Existing schema is partial and cannot be safely stamped. '
        f'Missing baseline tables: {missing}'
    )


def _read_versions(connection):
    return [
        row[0]
        for row in connection.execute(text(f'SELECT version_num FROM {VERSION_TABLE}'))
        if row[0]
    ]


def inspect_migration_state(engine):
    diagnostics = []
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    user_tables = tables - {VERSION_TABLE}

    if VERSION_TABLE not in tables:
        return _baseline_state(user_tables), diagnostics

    columns = {column['name'] for column in inspector.get_columns(VERSION_TABLE)}
    if 'version_num' not in columns:
        with engine.begin() as connection:
            row_count = connection.execute(
                text(f'SELECT COUNT(*) FROM {VERSION_TABLE}')
            ).scalar_one()
            if row_count:
                raise MigrationPreflightError(
                    f'{VERSION_TABLE} exists without version_num and contains rows. '
                    'Cannot infer the current migration revision safely.'
                )
            diagnostics.append(
                f'{VERSION_TABLE} exists without version_num; adding the missing column.'
            )
            connection.execute(
                text(f'ALTER TABLE {VERSION_TABLE} ADD COLUMN version_num VARCHAR(32)')
            )
        return _baseline_state(user_tables), diagnostics

    with engine.begin() as connection:
        for old_revision, new_revision in REVISION_ALIASES.items():
            result = connection.execute(
                text(
                    f'UPDATE {VERSION_TABLE} '
                    'SET version_num = :new_revision '
                    'WHERE version_num = :old_revision'
                ),
                {'old_revision': old_revision, 'new_revision': new_revision},
            )
            if result.rowcount and result.rowcount > 0:
                diagnostics.append(
                    f'Normalized Alembic revision {old_revision} -> {new_revision}.'
                )
        versions = _read_versions(connection)

    if not versions:
        return _baseline_state(user_tables), diagnostics

    too_long = [revision for revision in versions if len(revision) >= 32]
    if too_long:
        joined = ', '.join(too_long)
        raise MigrationPreflightError(
            'Alembic version_num contains revision ids that are too long '
            f'for this project policy: {joined}'
        )
    if not user_tables:
        raise MigrationPreflightError(
            f'{VERSION_TABLE} has version_num={", ".join(versions)}, '
            'but no application tables were found.'
        )

    diagnostics.append(f'Alembic version table found: {", ".join(versions)}.')
    return 'ready', diagnostics


def register_migration_preflight_commands(app, db):
    @app.cli.command('db-preflight')
    def db_preflight():
        """Validate whether the database can be upgraded safely."""
        try:
            state, diagnostics = inspect_migration_state(db.engine)
        except MigrationPreflightError as exc:
            raise click.ClickException(str(exc)) from exc
        for message in diagnostics:
            click.echo(message, err=True)
        click.echo(state)

    @app.cli.command('db-baseline-revision')
    def db_baseline_revision():
        """Print the single baseline revision used by deployment."""
        click.echo(BASELINE_REVISION)

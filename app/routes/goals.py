from datetime import date

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.routes.main import goal_operation_form_values
from app.services import goal_service
from app.services.financial_plan_service import build_financial_plan, get_financial_plan_preference
from app.utils import is_local_test_user, record_goal_activity
from extensions import db


def _owned_goal(key):
    if is_local_test_user():
        abort(404)
    try:
        return goal_service.get_goal_ref(current_user.id, key)
    except goal_service.GoalNotFound:
        abort(404)


def _plan_goals():
    plan = build_financial_plan(current_user.id, get_financial_plan_preference(current_user.id))
    return plan['goals']


def _operation_action(ref):
    if ref.is_emergency:
        return url_for('add_emergency_fund_transaction')
    return url_for('add_financial_goal_transaction', goal_id=ref.goal.id)


def init_app(app):
    @app.route('/goals')
    @login_required
    def goals():
        if is_local_test_user():
            return render_template('goals.html', cards=[], is_demo=True)
        cards = goal_service.goal_overview(current_user.id, _plan_goals(), date.today())
        return render_template('goals.html', cards=cards, is_demo=False)

    @app.route('/goals/<key>')
    @login_required
    def goal_detail(key):
        ref = _owned_goal(key)
        plan_goal = next(
            item for item in _plan_goals()
            if goal_service.goal_key(item['id']) == ref.key
        )
        card = goal_service.goal_overview(current_user.id, [plan_goal], date.today())[0]
        history = goal_service.operation_history(ref)
        return render_template(
            'goal_detail.html',
            goal=card,
            history=history,
            months=goal_service.monthly_summary(history),
            operation_action=_operation_action(ref),
            today=date.today(),
        )

    @app.route('/goals/<key>/operations/<int:transaction_id>/edit', methods=['POST'])
    @login_required
    def edit_goal_operation(key, transaction_id):
        ref = _owned_goal(key)
        try:
            goal_service.update_operation(ref, transaction_id, *goal_operation_form_values(request.form))
        except goal_service.GoalNotFound:
            abort(404)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
            return redirect(url_for('goal_detail', key=ref.key))
        except Exception:
            db.session.rollback()
            current_app.logger.exception('Failed to update goal operation')
            flash('Не удалось изменить операцию.', 'danger')
            return redirect(url_for('goal_detail', key=ref.key))
        record_goal_activity('Изменил операцию цели', ref, transaction_id)
        flash('Операция изменена.', 'success')
        return redirect(url_for('goal_detail', key=ref.key))

    @app.route('/goals/<key>/operations/<int:transaction_id>/delete', methods=['POST'])
    @login_required
    def delete_goal_operation(key, transaction_id):
        ref = _owned_goal(key)
        try:
            goal_service.delete_operation(ref, transaction_id)
        except goal_service.GoalNotFound:
            abort(404)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), 'danger')
            return redirect(url_for('goal_detail', key=ref.key))
        record_goal_activity('Удалил операцию цели', ref, transaction_id)
        flash('Операция удалена.', 'success')
        return redirect(url_for('goal_detail', key=ref.key))

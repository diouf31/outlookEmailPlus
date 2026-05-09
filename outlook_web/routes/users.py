from __future__ import annotations

from flask import Blueprint

from outlook_web.controllers import users as users_controller


def create_blueprint() -> Blueprint:
    bp = Blueprint("users", __name__)

    bp.add_url_rule("/api/users/me", view_func=users_controller.get_me, methods=["GET"])
    bp.add_url_rule("/api/users/me/password", view_func=users_controller.change_my_password, methods=["PUT"])

    bp.add_url_rule("/api/users", view_func=users_controller.list_users, methods=["GET"])
    bp.add_url_rule("/api/users", view_func=users_controller.create_user, methods=["POST"])
    bp.add_url_rule("/api/users/<int:user_id>", view_func=users_controller.delete_user, methods=["DELETE"])
    bp.add_url_rule("/api/users/<int:user_id>/password", view_func=users_controller.reset_user_password, methods=["PUT"])
    bp.add_url_rule("/api/users/<int:user_id>/role", view_func=users_controller.update_user_role, methods=["PUT"])

    return bp

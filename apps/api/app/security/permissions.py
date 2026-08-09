from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    ADMINISTRATOR = "administrator"
    ANALYST = "analyst"
    VERIFICATION_OFFICER = "verification_officer"
    VIEWER = "viewer"


class Action(StrEnum):
    MANAGE_ORGANISATION = "manage_organisation"
    MANAGE_MEMBERS = "manage_members"
    MANAGE_SITES = "manage_sites"
    CONTROL_MONITORING = "control_monitoring"
    VIEW_SITE = "view_site"
    REMOTE_REVIEW = "remote_review"
    INSTITUTIONAL_VERIFY = "institutional_verify"
    VIEW_APPROVED_SUMMARY = "view_approved_summary"
    EXPORT = "export"


_ROLE_ACTIONS: dict[Role, frozenset[Action]] = {
    Role.OWNER: frozenset(Action),
    Role.ADMINISTRATOR: frozenset(
        {
            Action.MANAGE_ORGANISATION,
            Action.MANAGE_MEMBERS,
            Action.MANAGE_SITES,
            Action.CONTROL_MONITORING,
            Action.VIEW_SITE,
            Action.VIEW_APPROVED_SUMMARY,
            Action.EXPORT,
        }
    ),
    Role.ANALYST: frozenset(
        {
            Action.VIEW_SITE,
            Action.REMOTE_REVIEW,
            Action.VIEW_APPROVED_SUMMARY,
            Action.EXPORT,
        }
    ),
    Role.VERIFICATION_OFFICER: frozenset(
        {Action.INSTITUTIONAL_VERIFY, Action.VIEW_APPROVED_SUMMARY}
    ),
    Role.VIEWER: frozenset({Action.VIEW_APPROVED_SUMMARY}),
}


def is_allowed(
    role: Role | str,
    action: Action | str,
    *,
    sensitive_site: bool = False,
    has_team_grant: bool = False,
    has_assignment: bool = False,
    approved_summary: bool = False,
) -> bool:
    resolved_role = Role(role)
    resolved_action = Action(action)
    if resolved_action not in _ROLE_ACTIONS[resolved_role]:
        return False
    if resolved_action == Action.VIEW_APPROVED_SUMMARY:
        return approved_summary or resolved_role in {Role.OWNER, Role.ADMINISTRATOR, Role.ANALYST}
    if resolved_role == Role.VERIFICATION_OFFICER:
        return has_assignment
    if resolved_action == Action.VIEW_SITE and sensitive_site:
        return resolved_role in {Role.OWNER, Role.ADMINISTRATOR} or (
            resolved_role == Role.ANALYST and has_team_grant
        )
    return True

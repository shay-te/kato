from core_lib.rule_validator.rule_validator import RuleValidator


def validate_payload(validator: RuleValidator, payload: dict) -> None:
    try:
        validator.validate_dict(payload)
    except PermissionError as exc:
        raise ValueError(str(exc)) from exc

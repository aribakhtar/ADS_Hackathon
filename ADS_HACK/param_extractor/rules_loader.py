from pathlib import Path
from typing import Dict


RULES_DIR = Path(__file__).resolve().parent / "rules"

FIELD_TO_RULE_FILE = {
    "age": "age.md",
    "step_therapy_requirements": "step_therapy_requirements_documented_in_policy.md",
    "number_of_steps_brands": "number_of_steps_through_brands.md",
    "number_of_steps_generic": "number_of_steps_through_generic.md",
    "step_through_phototherapy": "step_through_phototherapy.md",
    "tb_test_required": "tb_test_required.md",
    "initial_auth_duration": "initial_authorization_duration_in_months.md",
    "reauthorization_duration": "reauthorization_duration_in_months.md",
    "reauthorization_required": "reauthorization_required.md",
    "reauthorization_requirements": "reauthorization_requirements_documented_in_policy.md",
    "specialist_types": "specialist_types.md",
    "quantity_limits": "quantity_limits.md",
}


def load_parameter_rules(compact: bool = False) -> Dict[str, str]:
    rules = {}
    for field_name, rule_file in FIELD_TO_RULE_FILE.items():
        rule_path = RULES_DIR / rule_file
        rules[field_name] = rule_path.read_text(encoding="utf-8").strip()
    return rules

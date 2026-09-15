"""Local, deterministic follow-up workflow; no model, replay, booking or sender."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from assistant.adapters.mock import MockSalonDataProvider
from assistant.config.loader import load_config
from assistant.demo_data.validate import DatasetError
from assistant.tools.registry import dispatch


def walkthrough(directory: Path, designer_ref: str, as_of: datetime) -> dict:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise DatasetError("as_of: explicit timezone required")
    config = load_config()
    provider = MockSalonDataProvider(directory, config=config)
    scope = next((s for s in provider.designer_scopes() if s.designer_ref == designer_ref), None)
    if scope is None:
        raise DatasetError("designer: not present in the supplied dataset")

    def call(name, arguments):
        return dispatch(name, arguments, provider, scope, config, as_of=as_of)

    inactive = call("list_inactive_customers", {"inactive_days": 60, "limit": 5})
    steps = [inactive]
    if not inactive["ok"] or not inactive["rows"]:
        return {"mode": "local_tools_only", "sent": False, "steps": steps}
    # Explicit walkthrough policy: inspect the first returned inactive customer.
    customer_ref = inactive["rows"][0]["customer_ref"]
    steps.append(call("get_customer_history", {"customer_ref": customer_ref, "limit": 10}))
    steps.append(call("draft_follow_up_message", {
        "customer_ref": customer_ref, "reason": config.follow_up_templates[0].id,
    }))
    return {"mode": "local_tools_only", "sent": False, "steps": steps}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--designer-ref", required=True)
    parser.add_argument("--as-of", required=True, help="ISO timestamp including timezone")
    args = parser.parse_args()
    try:
        result = walkthrough(args.directory, args.designer_ref, datetime.fromisoformat(args.as_of))
    except (ValueError, OSError):
        print("INVALID: check dataset, designer selection and timezone-aware as_of")
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

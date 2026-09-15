"""Validate local synthetic datasets without echoing customer data in errors.

This is structural/ownership validation, not anonymization or consent checking.
It never writes files or contacts services.
"""

import argparse
import json
from datetime import date, time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from assistant.adapters.schemas import ConversationState, MessageRole, ServiceFamily

Ref = Annotated[str, Field(min_length=1, max_length=200)]


class DatasetError(ValueError):
    """A safe message contains table/index and reason, never supplied values."""


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Designer(Row):
    designer_ref: Ref
    display_name: str
    store_name: str
    joined_at: AwareDatetime


class Customer(Row):
    customer_ref: Ref
    designer_ref: Ref
    full_name: str
    phone: str | None = None
    created_at: AwareDatetime
    line_user_ref: str | None = None
    pos_customer_id: str | None = None


class Visit(Row):
    visit_ref: Ref
    customer_ref: Ref
    designer_ref: Ref
    visited_at: AwareDatetime
    service_family: ServiceFamily
    amount_twd: Annotated[int, Field(ge=0)] | None


class Appointment(Row):
    appointment_ref: Ref
    customer_ref: Ref
    designer_ref: Ref
    starts_at: AwareDatetime
    service_family: ServiceFamily
    status: Literal["pending", "confirmed", "cancelled"]


class Message(Row):
    role: MessageRole
    created_at: AwareDatetime
    content: str


class Draft(Row):
    service_family: ServiceFamily | None = None
    preferred_date: date | None = None
    preferred_time: time | None = None


class Conversation(Row):
    conversation_ref: Ref
    customer_ref: Ref
    designer_ref: Ref
    state: ConversationState
    identity_ambiguity: bool
    safe_draft_fields: Draft
    updated_at: AwareDatetime
    messages: list[Message]


TABLES = {
    "designers": (Designer, "designer_ref"),
    "customers": (Customer, "customer_ref"),
    "visits": (Visit, "visit_ref"),
    "appointments": (Appointment, "appointment_ref"),
    "conversations": (Conversation, "conversation_ref"),
}
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000


def validate_dataset(dataset: dict) -> dict[str, int]:
    """Reject invalid rows and ambiguous ownership; preserve the input unchanged."""
    if not isinstance(dataset, dict) or set(dataset) != set(TABLES):
        raise DatasetError("dataset: expected exactly the five documented tables")
    parsed = {}
    for table, (model, id_field) in TABLES.items():
        rows = dataset[table]
        if not isinstance(rows, list) or len(rows) > MAX_ROWS:
            raise DatasetError(f"{table}: expected a list of at most {MAX_ROWS} rows")
        parsed[table] = {}
        for index, row in enumerate(rows):
            try:
                # JSON strict mode accepts ISO timestamps, not strings as money/bools.
                record = model.model_validate_json(json.dumps(row), strict=True)
            except (ValidationError, TypeError, ValueError, RecursionError):
                raise DatasetError(f"{table}[{index}]: invalid row schema") from None
            key = getattr(record, id_field)
            if key in parsed[table]:
                raise DatasetError(f"{table}[{index}]: duplicate identifier")
            parsed[table][key] = record

    for index, customer in enumerate(parsed["customers"].values()):
        if customer.designer_ref not in parsed["designers"]:
            raise DatasetError(f"customers[{index}]: unknown owner")
    for table in ("visits", "appointments", "conversations"):
        for index, record in enumerate(parsed[table].values()):
            customer = parsed["customers"].get(record.customer_ref)
            if customer is None:
                raise DatasetError(f"{table}[{index}]: unknown customer")
            if record.designer_ref != customer.designer_ref:
                raise DatasetError(f"{table}[{index}]: customer owner mismatch")
            if table == "conversations":
                stamps = [message.created_at for message in record.messages]
                if stamps != sorted(stamps) or any(stamp > record.updated_at for stamp in stamps):
                    raise DatasetError(f"{table}[{index}]: inconsistent message timestamps")
    return {table: len(rows) for table, rows in parsed.items()}


def load_validated_dataset(directory: Path | str) -> dict:
    """Bound each JSON file read; malformed input never prints file content/path."""
    root = Path(directory)
    data = {}
    for table in TABLES:
        try:
            with (root / f"{table}.json").open("rb") as source:
                raw = source.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise DatasetError(f"{table}: file exceeds 10 MiB")
            data[table] = json.loads(raw, object_pairs_hook=_unique_fields)
        except (OSError, ValueError, RecursionError):
            raise DatasetError(f"{table}: missing, oversized or invalid JSON file") from None
    validate_dataset(data)
    return data


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DatasetError("duplicate JSON field")
        result[key] = value
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        data = load_validated_dataset(args.directory)
    except DatasetError as exc:
        print(f"INVALID: {exc}")
        return 1
    print(json.dumps({"valid": True, "rows": {key: len(value) for key, value in data.items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

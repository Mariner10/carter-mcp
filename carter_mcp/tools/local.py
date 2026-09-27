"""Local-store tools — describe, query, seed and delete records in the paired
phone's on-device store (spec 2026-09-24-local-readback-design §6.2).

Four routed verbs on the studio socket: `local.describe` (metadata, no consent),
`local.query` (read), `local.upsert` / `local.delete` (write). The first record verb
of a session raises a consent sheet on the phone and answers `consent-pending`; the
tools poll `local.describe` until the owner decides, then re-send once. Seeds are
read from a file on disk and never pass through the model. Stage-based deletes are
carterkit-only: `local_delete` takes ids. Pure shapes live in `protocol`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from carter_mcp import mesh, protocol, state
from carter_mcp.app import mcp
from carter_mcp.layoutio import load_records_arg
from carter_mcp.tools.device import PROVISION_WAIT_SECONDS

#: How long to wait on the consent sheet (the device times it out at 60 s).
CONSENT_WAIT_SECONDS = PROVISION_WAIT_SECONDS
#: Poll cadence for `local.describe` while the sheet is up.
CONSENT_POLL_SECONDS = 1.0


def _timeout_message(verb: str) -> str:
    return (f"Device did not respond to '{verb}' (timeout) — the installed app likely "
            f"predates the local.* verbs, or the phone isn't running. Run check_sources.")


async def _device() -> tuple[Optional[str], Optional[str]]:
    if err := mesh.connection_error():
        return None, err
    device_id = await mesh.get_device_id()
    if not device_id:
        return None, mesh.no_device_error()
    return device_id, None


async def _send(device_id: str, verb: str, payload):
    return await state.socket.request("route_msg", {
        "target_id": device_id, "type": verb, "payload": payload,
    }, timeout=5.0)


async def _await_consent(device_id: str) -> Optional[str]:
    """Poll `local.describe` until its consent state leaves "pending" or the wait
    runs out. Returns the last state seen ("granted" / "none" / "pending" / None)."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + CONSENT_WAIT_SECONDS
    last = "pending"
    while loop.time() < deadline:
        await asyncio.sleep(CONSENT_POLL_SECONDS)
        reply = await _send(device_id, "local.describe",
                            protocol.build_local_describe_request())
        seen = protocol.local_consent_state(reply)
        if seen is None:
            continue
        last = seen
        if seen != "pending":
            return seen
    return last


async def _call(device_id: str, verb: str, payload) -> tuple[Optional[dict], Optional[str]]:
    """One routed local.* call with the consent loop. Returns (reply, None) on
    success or (None, prose) on any failure."""
    reply = await _send(device_id, verb, payload)
    if reply is None:
        return None, _timeout_message(verb)
    if protocol.is_consent_pending(reply):
        decision = await _await_consent(device_id)
        if decision == "pending":
            return None, (f"No decision on the phone's consent sheet within "
                          f"{CONSENT_WAIT_SECONDS}s. Nothing was read or written.")
        if decision != "granted":
            return None, protocol.local_error_message(
                {"error": "denied", "reason": "not granted"})
        reply = await _send(device_id, verb, payload)  # resend once after the grant
        if reply is None:
            return None, _timeout_message(verb)
    if msg := protocol.local_error_message(reply):
        return None, msg
    if not isinstance(reply, dict) or not reply.get("ok"):
        return None, f"Unexpected '{verb}' reply: {reply!r}"[:300]
    return reply, None


async def _describe(device_id: str) -> tuple[Optional[dict], Optional[str]]:
    return await _call(device_id, "local.describe", protocol.build_local_describe_request())


async def _upsert_pages(device_id: str, collection: str, records: list, mode: str,
                        namespace: str) -> tuple[Optional[dict], Optional[str]]:
    """Page records through `local.upsert` 100 at a time; merge the replies."""
    merged = {"collection": collection, "inserted": 0, "updated": 0, "ids": [],
              "total": None, "calls": 0}
    pages = protocol.page_records(records)
    for n, page in enumerate(pages):
        reply, err = await _call(device_id, "local.upsert",
                                 protocol.build_local_upsert_request(collection, page, mode, namespace))
        if err:
            done = sum(len(p) for p in pages[:n])
            prefix = (f"Stopped at page {n + 1} of {len(pages)} ({done} of "
                      f"{len(records)} records already written). ") if n else ""
            return None, prefix + err
        merged["inserted"] += int(reply.get("inserted", 0) or 0)
        merged["updated"] += int(reply.get("updated", 0) or 0)
        merged["ids"].extend(reply.get("ids") or [])
        merged["total"] = reply.get("total", merged["total"])
        merged["calls"] += 1
    return merged, None


@mcp.tool()
async def local_describe() -> str:
    """Collections, field types, counts and consent state of the local store on the
    paired phone. Call this first: it tells you the namespace and collection names
    the other local_* tools need, and whether the owner has granted access yet."""
    device_id, err = await _device()
    if err:
        return err
    reply, err = await _describe(device_id)
    return err or protocol.format_local_describe(reply)


@mcp.tool()
async def local_query(collection: str, stage_json: str = "", namespace: str = "",
                      max_rows: int = 50) -> str:
    """Read records from the phone's local store. The owner approves the first read
    on the device. Returns a compact table (id + declared fields), never more than
    max_rows (cap 200); use a stage with where/orderBy/limit to narrow.

    Args:
        collection: Collection (or view) name from local_describe.
        stage_json: Optional stage object as JSON, e.g.
                    {"where": {"rating": {"gte": 4}}, "orderBy": "-finished", "limit": 20}.
        namespace: Optional; defaults to the active layout's namespace ("shared" for shared collections).
        max_rows: Rows to show (1-200).
    """
    if not collection.strip():
        return "collection is required."
    stage = {}
    if stage_json.strip():
        try:
            stage = json.loads(stage_json)
        except json.JSONDecodeError as e:
            return f"stage_json is not valid JSON: {e}"
        if not isinstance(stage, dict):
            return "stage_json must be a JSON object (a stage: where/orderBy/limit/groupBy/aggregate)."
    device_id, err = await _device()
    if err:
        return err
    # Metadata first (consent-free): surfaces no-layout/unknown-collection early and
    # gives the declared field order for the table.
    desc, err = await _describe(device_id)
    if err:
        return err
    entry = protocol.find_local_collection(desc, collection, namespace)
    fields = list((entry or {}).get("fields") or {})
    reply, err = await _call(device_id, "local.query", protocol.build_local_query_request(
        collection, stage, namespace, max_rows=max_rows))
    if err:
        return err
    return protocol.format_local_rows(reply, max_rows=max_rows, fields=fields)


@mcp.tool()
async def local_upsert(collection: str, records_json: str = "", records_path: str = "",
                       mode: str = "upsert", namespace: str = "") -> str:
    """Insert or replace records. Pass records_json for a handful, records_path for a
    JSON array file (seeds never transit the model). Owner approves write access once
    per session. Returns inserted/updated counts and the ids.

    Args:
        collection: Collection name from local_describe.
        records_json: Inline JSON array of {"id"?: str, "fields": {...}} (small batches only).
        records_path: Path to a JSON array file of the same shape (≤1 MB).
        mode: "upsert" (replace whole record by id) or "insert" (duplicate id fails the call).
        namespace: Optional; defaults to the active layout's namespace.
    """
    if not collection.strip():
        return "collection is required."
    if mode not in protocol.LOCAL_UPSERT_MODES:
        return f"mode must be one of {', '.join(protocol.LOCAL_UPSERT_MODES)}."
    records, err = load_records_arg(records_json, records_path)
    if err:
        return err
    if not records:
        return "No records to write (the array is empty)."
    device_id, err = await _device()
    if err:
        return err
    merged, err = await _upsert_pages(device_id, collection, records, mode, namespace)
    if err:
        return err
    return protocol.format_local_upsert(merged)


@mcp.tool()
async def local_seed(collection: str, records_path: str, namespace: str = "",
                     replace: bool = False) -> str:
    """Load a JSON array file into a collection: reads the file, pages it through
    local_upsert in 100-record calls, and reports the final total. replace=True first
    deletes every record (needs the owner's write consent and reads the live count for
    confirm_total).

    Args:
        collection: Collection name from local_describe.
        records_path: Path to a JSON array file of {"id"?: str, "fields": {...}} (≤1 MB).
        namespace: Optional; defaults to the active layout's namespace.
        replace: Delete every existing record in the collection before seeding.
    """
    if not collection.strip():
        return "collection is required."
    records, err = load_records_arg(records_path=records_path, allow_inline=False)
    if err:
        return err
    device_id, err = await _device()
    if err:
        return err
    lines = []
    if replace:
        cleared, err = await _clear(device_id, collection, namespace)
        if err:
            return err
        lines.append(f"Cleared {cleared} existing record(s).")
    if records:
        merged, err = await _upsert_pages(device_id, collection, records, "upsert", namespace)
        if err:
            return "\n".join(lines + [err])
        lines.append(f"Seeded {len(records)} record(s) in {merged['calls']} call(s): "
                     f"{merged['inserted']} inserted, {merged['updated']} updated.")
        lines.append(f"Final total in `{collection}`: {merged['total']}.")
    else:
        lines.append("The seed file is an empty array; nothing to write.")
    return "\n".join(lines)


async def _clear(device_id: str, collection: str, namespace: str) -> tuple[int, Optional[str]]:
    """Delete everything via `where:{}` + confirmTotal (the live count from describe),
    looping while the device's per-call cap leaves rows behind."""
    desc, err = await _describe(device_id)
    if err:
        return 0, err
    entry = protocol.find_local_collection(desc, collection, namespace)
    if entry is None:
        return 0, protocol.local_error_message({"error": "unknown-collection", "reason": collection})
    total = int(entry.get("count") or 0)
    cleared = 0
    for _ in range(100):  # 100 × 1000-row cap ≫ the 50k-per-collection store cap
        if total <= 0:
            break
        reply, err = await _call(device_id, "local.delete",
                                 protocol.build_local_clear_request(collection, total, namespace))
        if err:
            return cleared, err
        deleted = int(reply.get("deleted", 0) or 0)
        cleared += deleted
        total = int(reply.get("total", 0) or 0)
        if deleted <= 0:
            break
    return cleared, None


@mcp.tool()
async def local_delete(collection: str, ids: list[str], namespace: str = "") -> str:
    """Delete records by id. Stage-based deletes stay in carterkit only; the model
    never deletes by predicate.

    Args:
        collection: Collection name from local_describe.
        ids: Record ids to delete (1-1000).
        namespace: Optional; defaults to the active layout's namespace.
    """
    if not collection.strip():
        return "collection is required."
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        return "ids must be a non-empty list of record id strings."
    if len(ids) > protocol.LOCAL_DELETE_CAP:
        return f"At most {protocol.LOCAL_DELETE_CAP} ids per call."
    device_id, err = await _device()
    if err:
        return err
    reply, err = await _call(device_id, "local.delete",
                             protocol.build_local_delete_request(collection, ids, namespace))
    return err or protocol.format_local_delete(reply, collection)

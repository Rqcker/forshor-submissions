"""Table JSON is not a circuit design selection format."""

DESIGN_KEYS = {"readout", "submittedDesign", "submitted_design", "submitted_readout", "qft", "modmul",
               "submittedPrimitive", "submitted_primitive",
               "submittedFullRunDesign", "submitted_full_run",
               "phase_product", "synthesis", "synthesis_backend"}


# A refusal says WHICH cause refused, because the causes need different actions: a valid request that this check
# does not price is named as such, a malformed one has to be rewritten. Each message is a leading sentence followed
# by the cause after a colon.
COHERENT_ROUTE = "is not supported by the coherent pricing route"
#: Readouts that are checked locally but not priced here. Anything else is not a readout mode.
QUALIFIED_READOUTS = ("semiclassical",)
#: The leading sentence both full-run gates below raise, so the two cannot drift apart.
FULL_RUN_ROUTE = "compiled submitted_full_run is accepted only together with the semiclassical readout declaration"
#: The only selection object shape the gates below will let through.
FULL_RUN_SELECTION_SCHEMA = "forshor.full-run-selection/v1"


def _full_run_selection(document):
    """(the key is present, the selection) for a compiled document, tolerating a document that is not an object."""
    if not isinstance(document, dict) or "submitted_full_run" not in document:
        return False, None
    return True, document["submitted_full_run"]


def check_no_full_run_export(document):
    """The default gate: a compiled `submitted_full_run` key is refused on PRESENCE, whatever it holds.

    Every route that does not price the semiclassical readout uses this gate. The measured route uses
    `check_measured_full_run_export` below instead, and the local full-run check uses
    `allow_qualified_full_run_export`; they are separate functions so that no other route can reach the looser
    tests by accident."""
    present, selected = _full_run_selection(document)
    if present:
        profile = selected.get("profile") if isinstance(selected, dict) else None
        cause = (f"the export selects the full-run profile {profile!r}" if isinstance(profile, str)
                 else "the export carries a `submitted_full_run` key with no profile")
        raise ValueError(f"{FULL_RUN_ROUTE}: {cause}")


def allow_qualified_full_run_export(document):
    """The local full-run check's gate: it checks a named profile and does not publish.

    The same gate as above, except that a well-formed selection object naming a profile is let through for
    conversion, which refuses by name everything it cannot price. `null`, a wrong schema, a missing or empty profile
    and a non-dict selection stay refused here. Absence is accepted, exactly as the default gate accepts it: a policy
    with no selection is an ordinary policy."""
    present, selected = _full_run_selection(document)
    if present and not (isinstance(selected, dict) and selected.get("schema") == FULL_RUN_SELECTION_SCHEMA
                        and isinstance(selected.get("profile"), str) and selected["profile"]):
        profile = selected.get("profile") if isinstance(selected, dict) else None
        cause = (f"the export selects the full-run profile {profile!r}, but not as a well-formed "
                 f"`{FULL_RUN_SELECTION_SCHEMA}` selection" if isinstance(profile, str) else
                 f"the export carries a `submitted_full_run` key that is not a `{FULL_RUN_SELECTION_SCHEMA}` "
                 "selection naming a profile")
        raise ValueError(f"{FULL_RUN_ROUTE}: {cause}")


#: The compiled schema of an export built with the semiclassical readout.
MEASURED_EXPORT_SCHEMA = "forshor.policy-submission-semiclassical/v1"
#: The leading sentence the gate below raises.
MEASURED_FULL_RUN_REFUSAL = "compiled submitted_full_run is not accepted"


def check_measured_full_run_export(document):
    """The gate for an export compiled with the semiclassical readout, the one export a full-run design can be on.

    The verifier emits a full-run selection only beside the semiclassical readout, so a selection is let through for
    conversion only on such an export, and only as a well-formed `forshor.full-run-selection/v1` object naming
    exactly one of a non-empty `profile` or an explicit `design` body. The conversion that follows refuses by name
    everything it does not price. Absence is accepted: a policy with no selection is priced exactly as before."""
    present, selected = _full_run_selection(document)
    if not present:
        return
    if not (isinstance(document, dict) and document.get("schema") == MEASURED_EXPORT_SCHEMA
            and document.get("submitted_readout") == "semiclassical"):
        raise ValueError(f"{MEASURED_FULL_RUN_REFUSAL}: a full-run design is accepted only on an export compiled with "
                         "the semiclassical readout, and this export is not one")
    if not (isinstance(selected, dict) and selected.get("schema") == FULL_RUN_SELECTION_SCHEMA
            and (("profile" in selected) != ("design" in selected))
            and ((isinstance(selected.get("profile"), str) and selected["profile"]) if "profile" in selected
                 else isinstance(selected.get("design"), dict))):
        raise ValueError(f"{MEASURED_FULL_RUN_REFUSAL}: the export carries a `submitted_full_run` key that is not a "
                         f"`{FULL_RUN_SELECTION_SCHEMA}` selection naming exactly one of a profile or a design body")


def check_coherent_export(document):
    check_no_full_run_export(document)
    # A document that is not a JSON object is refused here with a ValueError, in the same class as every other thing
    # this check will not price, rather than failing on `in` or `.get` with a TypeError or AttributeError.
    if not isinstance(document, dict):
        raise ValueError(f"the compiled export {COHERENT_ROUTE}: ForShor printed {type(document).__name__} where a "
                         "policy document was expected; this route reads a JSON object")
    if "submitted_design" in document:
        raise ValueError(f"compiled submitted_design metadata {COHERENT_ROUTE}")
    if "readout" in document:
        raise ValueError(f"the compiled readout choice {COHERENT_ROUTE}: the export carries a legacy `readout` key; "
                         "this route reads the compiled `submitted_readout` instead")
    selected = document.get("submitted_readout", "coherent")
    if selected != "coherent":
        cause = ("it is priced only together with the semiclassical readout, and this check prices the coherent one"
                 if selected in QUALIFIED_READOUTS else
                 "it is not a readout mode; the coherent route prices 'coherent' only")
        raise ValueError(f"the compiled readout choice {COHERENT_ROUTE}: the export selects "
                         f"submitted_readout {selected!r} and {cause}")


def check_table_options(document):
    # Follow policy wrappers and tables, not unrelated metadata or notes.
    pending = [document]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            keys = sorted(DESIGN_KEYS.intersection(item))
            if keys:
                raise ValueError(f"table policy JSON cannot select circuit design options ({', '.join(keys)}); use the supported Lean policy interface")
            if item.get("negation", "oneQubit") != "oneQubit":
                raise ValueError(f"the policy asks for negation {item['negation']!r}; the boards price oneQubit only")
            for key in ("policy", "policies", "recursion", "bands", "band_tables"):
                if isinstance(wrapped := item.get(key), (dict, list)):
                    pending.append(wrapped)

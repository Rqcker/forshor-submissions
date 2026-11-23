"""Table JSON is not a circuit design selection format."""

DESIGN_KEYS = {"readout", "submittedDesign", "submitted_design", "submitted_readout", "qft", "modmul",
               "submittedPrimitive", "submitted_primitive",
               "submittedFullRunDesign", "submitted_full_run",
               "phase_product", "synthesis", "synthesis_backend"}


def check_no_full_run_export(document):
    if isinstance(document, dict) and "submitted_full_run" in document:
        raise ValueError("compiled submitted_full_run metadata requires dedicated nonpublishing full-run qualification")


def check_coherent_export(document):
    check_no_full_run_export(document)
    if "submitted_design" in document:
        raise ValueError("compiled submitted_design metadata is not supported by the coherent pricing route")
    if "readout" in document or document.get("submitted_readout", "coherent") != "coherent":
        raise ValueError("the compiled readout choice is not supported by the coherent pricing route")


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

"""Publish the existing website model over the verified archive. No research/search for new tables."""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "estimator"))
from forshor_qualtran import cost_conventions as cc
from forshor_qualtran.bloqs.policy_phase_product import PolicyPhaseProduct
from forshor_qualtran.policy_planner import Planner, chain_to_policy, leaf_cost, level_cost
from forshor_qualtran.sto_policy import table_from_sto

ESTIMATOR_REVISION = "fe58769bbd4ec6dfcc59e67771252e3382c051ba"


def records(archive: Path, index: dict):
    result = []
    for policy in index["policies"]:
        pid = policy["policy_id"]
        if len(pid) != 40 or any(c not in "0123456789abcdef" for c in pid):
            raise ValueError("Invalid archived policy identity")
        doc = json.loads((archive / "results/policies" / pid / "operations.json").read_text())
        if doc["policy_id"] != pid:
            raise ValueError("Operations do not belong to the archived submission")
        for target in doc["targets"]:
            metric = next(m for m in policy["results"] if (m["mode"], m["k"]) == (target["mode"], target["k"]))
            result.append((pid, target, metric))
    return result


def catalog(rows, incumbent_tables):
    champions = {}
    old_ids = {(t["mode"], t["k"]): t["policyId"] for t in incumbent_tables}
    for pid, target, metric in rows:
        key = target["mode"], target["k"]
        candidate = (pid, target, metric)
        def order(row):
            return row[2]["weighted_cost"], row[0] != old_ids.get(key), row[0]
        if key not in champions or order(candidate) < order(champions[key]):
            champions[key] = candidate
    eligible = [(pid, t, m) for pid, t, m in rows if t["mode"] == "PhaseProduct"
                and m["parallel_phase_product_layer_count"] <= champions[t["mode"], t["k"]][2]["parallel_phase_product_layer_count"]]
    tables = [table_from_sto(t, f"{pid}-k{t['k']}") for pid, t, _ in eligible]
    scores = [{"mode": mode, "k": k, "policyId": pid, **metric}
              for (mode, k), (pid, _, metric) in sorted(champions.items(), key=lambda item: (item[0][1], item[0][0]))]
    return tables, scores


def subtree(planner, width, entry):
    chain = []
    while entry[1] is not None:
        chain.append((width, entry[1]))
        width, entry = entry[2], planner.front(entry[2])[entry[3]]
    return chain + [(width, None)], entry[4]


def display(chain, leaf, tables):
    policy = chain_to_policy(chain, tables)
    if leaf is None:
        leaf = cc.resolve_leaf(policy, 2048, 2048, cc.QUALTRAN)
    spec = cc.QUALTRAN.with_leaf(leaf)
    bloq = PolicyPhaseProduct(policy, 2048, convention=spec)
    cost = cc.tree_cost(policy, 2048, 2048, spec, leaf)
    levels, nodes, workspace_weight = [], 1, 1
    for width, name in chain:
        if name is None:
            raw, gates, work = cc.leaf_cost(width, width, leaf), leaf_cost("gates", width), 0
            level = {"type": "base", "inputWidth": width, "leaf": leaf}
        else:
            table = tables[name]
            child = table.next_width(width, width)
            raw = level_cost("t", table, width, child, spec)
            gates = level_cost("gates", table, width, child)
            work = level_cost("reserve", table, width, child)
            pid = name.split("-k")[0]
            level = {"type": "recursive", "inputWidth": width, "k": table.k,
                     "childWidth": child, "callsPerNode": 2 * table.k - 1,
                     "policyFull": pid, "policyId": pid[:12], "table": f"{pid[:12]}-k{table.k}"}
        level.update(nodes=nodes, basePerNode=raw.base, base=nodes * raw.base,
                     rotPerNode=raw.rot, rot=nodes * raw.rot,
                     gatesPerNode=gates, gates=nodes * gates,
                     workPerNode=work, work=workspace_weight * work)
        levels.append(level)
        if name is not None:
            nodes *= 2 * tables[name].k - 1
            workspace_weight *= tables[name].k
    top = levels[0]
    return {"t": round(spec.price(cost)), "gates": sum(bloq.lean_resources().values()), "qubits": bloq.qubits(),
            "tBase": cost.base, "rotations": cost.rot, "tPerRotation": round(spec.t_per_rotation(cost.rot), 2),
            "reserve": sum(policy.reserve_need(2048, 2048)), "operandQubits": 4096,
            "topK": top["k"], "topPolicy": top["policyId"], "topPolicyFull": top["policyFull"],
            "childWidth": top["childWidth"], "levels": len(levels) - 1, "leaf": leaf, "chain": levels}


def boards(tables):
    by_name = {t.name: t for t in tables}
    result = {}
    for objective in ("t", "gates"):
        planner = Planner(tables, objective)
        rows = []
        for table in tables:
            child = table.next_width(2048, 2048)
            if child >= 2048:
                continue
            entries = planner.front(child)
            entry = min(entries, key=lambda e: planner.value(planner.node_raw(table.name, 2048, e[0])))
            tail, leaf = subtree(planner, child, entry)
            rows.append(display([(2048, table.name)] + tail, leaf, by_name))
        rows.sort(key=lambda row: (row[objective], row["topPolicyFull"], row["topK"]))
        for rank, row in enumerate(rows, 1):
            row["rank"] = rank
        result[objective] = rows
    return result


def generate(archive, state, metadata=None):
    previous = state["legacy"]
    if previous["width"] != 2048 or previous["engine"]["convention"] != "QUALTRAN":
        raise ValueError("Only the existing 2048-bit Qualtran model is supported")
    index = json.loads((archive / "leaderboard/results.json").read_text())
    old_ids = {p["policy_id"] for p in state["archive"]["policies"]}
    if not old_ids <= {p["policy_id"] for p in index["policies"]}:
        raise ValueError("A publication cannot drop an accepted submission")
    tables, scores = catalog(records(archive, index), previous["tables"])
    result = boards(tables)
    best = copy.deepcopy(result["t"][0])
    best["gateSplit"] = PolicyPhaseProduct(chain_to_policy(
        [(level["inputWidth"], f"{level['policyFull']}-k{level['k']}" if level["type"] == "recursive" else None)
         for level in best["chain"]], {t.name: t for t in tables}), 2048).lean_resources()
    legacy = {**previous, "policyCount": len(index["policies"]), "tableCount": len(tables),
              "best": best, "boards": result, "tables": scores, "history": list(previous["history"])}
    if metadata:
        legacy["lastUpdated"] = metadata["evaluatedAt"]
        gates = result["gates"][0]
        legacy["history"].append({"date": metadata["evaluatedAt"][:10], "commit": metadata["archiveCommit"],
            "t": best["t"], "tPlanGates": best["gates"], "tPlanQubits": best["qubits"],
            "gates": gates["gates"], "gatesPlanT": gates["t"], "qubits": gates["qubits"],
            "policies": legacy["policyCount"], "tables": legacy["tableCount"],
            "recordT": best["t"] < previous["best"]["t"],
            "recordGates": gates["gates"] < previous["boards"]["gates"][0]["gates"],
            "record": best["t"] < previous["best"]["t"] or gates["gates"] < previous["boards"]["gates"][0]["gates"]})
    provenance = copy.deepcopy(state["provenance"])
    if metadata:
        provenance["archiveCommit"] = metadata["archiveCommit"]
        provenance["attribution"] = [a for a in provenance["attribution"] if a["submissionId"] != metadata["headSha"]]
        provenance["attribution"].append({"submissionId": metadata["headSha"], "headSha": metadata["headSha"],
            "githubLogin": metadata["githubLogin"], "evidenceUrl": metadata["pullRequestUrl"]})
    return {"legacy": legacy, "archive": index, "provenance": provenance,
            "baseGeneration": state.get("baseGeneration"), "evaluation": metadata,
            "estimatorRevision": ESTIMATOR_REVISION, "qualtranVersion": "0.7.0"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload = generate(args.archive, json.loads(args.state.read_text()), json.loads(args.metadata.read_text()))
    args.out.write_text(json.dumps(payload, separators=(",", ":")) + "\n")
    print(f"Evaluated {payload['legacy']['policyCount']} accepted submissions / {payload['legacy']['tableCount']} eligible tables")

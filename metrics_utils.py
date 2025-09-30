from __future__ import annotations

import os
import json
from collections import Counter, defaultdict
from typing import Dict, Set, Iterable, Tuple

from rdflib import Graph, URIRef, BNode, Literal
from rdflib.namespace import RDF, SH, OWL, RDFS
import os, json, time
from typing import Dict, Set
from rdflib import Graph, URIRef
from rdflib.namespace import RDF, SH

def _extract_shape_paths(sg: Graph) -> Set[URIRef]:
    P = set()
    for prop_node in sg.objects(None, SH.property):
        for p in sg.objects(prop_node, SH.path):
            if isinstance(p, URIRef):
                P.add(p)
    for p in sg.objects(None, SH.path):
        if isinstance(p, URIRef):
            P.add(p)
    return P

def compute_metrics_dict(method_name: str,
                         timings: Dict[str, float],
                         gprime: Graph,
                         shapes: Graph,
                         same_nodes: Dict[URIRef, Set[URIRef]]) -> Dict:
    P = _extract_shape_paths(shapes)
    edges_by_path = {str(p): sum(1 for _ in gprime.triples((None, p, None))) for p in P}

    components = sum(1 for subs in same_nodes.values() if subs)
    total_subsumed = sum(len(subs) for subs in same_nodes.values())

    focus_size = len(same_nodes)

    node_shapes = len(set(shapes.subjects(RDF.type, SH.NodeShape)))
    prop_shapes = len(set(shapes.objects(None, SH.property)))
    target_classes = len(set(shapes.objects(None, SH.targetClass)))
    target_nodes = len(set(shapes.objects(None, SH.targetNode)))

    return {
        "method": method_name,
        "timings": timings,  #(when available)
        "focus": {"F": focus_size},
        "graph": {"triples": len(gprime), "edges_by_path": edges_by_path},
        "sameAs": {"components": components, "total_subsumed": total_subsumed},
        "shapes": {
            "node_shapes": node_shapes,
            "property_shapes": prop_shapes,
            "target_classes": target_classes,
            "target_nodes": target_nodes,
        },
    }

def save_metrics(dataset_name: str, method: str, run_idx: int, metrics: Dict) -> str:
    outdir = f"Outputs/{dataset_name}/metrics"
    os.makedirs(outdir, exist_ok=True)
    path = f"{outdir}/{method}_run{run_idx}.json"
    with open(path, "w") as f:
        json.dump(metrics, f, indent=2)
    return path

def summarize_metrics_line(metrics: Dict) -> str:
    t = metrics["timings"].get("total_pre_validation", 0.0)
    vtrip = metrics["graph"]["triples"]
    F = metrics["focus"]["F"]
    comps = metrics["sameAs"]["components"]
    subs = metrics["sameAs"]["total_subsumed"]
    return f"[{metrics['method']}] build={t:.3f}s  G′|triples|={vtrip}  |F|={F}  sameAs: comps={comps}, subsumed={subs}"

def check_directory_exists_otherwise_create(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _is_predicate_fast(g: Graph):
    """O(1) avg-time check using rdflib's predicate index; memoized per graph."""
    from functools import lru_cache

    @lru_cache(maxsize=200_000)
    def _is_pred(u) -> bool:
        return isinstance(u, URIRef) and next(g.triples((None, u, None)), None) is not None

    return _is_pred

def _harvest_from_shapes(sg: Graph) -> Tuple[Set[URIRef], Set[URIRef], Set[URIRef], Set[URIRef]]:
    """
    Robustly gather:
      C: target classes
      P: IRI-valued sh:path
      N: target nodes
      P_node: paths whose property shapes delegate via sh:node
    """
    C, P, N, P_node = set(), set(), set(), set()

    for ns in sg.subjects(RDF.type, SH.NodeShape):
        for c in sg.objects(ns, SH.targetClass):
            if isinstance(c, URIRef):
                C.add(c)
        for n in sg.objects(ns, SH.targetNode):
            if isinstance(n, URIRef):
                N.add(n)

        for prop in sg.objects(ns, SH.property):
            ps = set()
            for p in sg.objects(prop, SH.path):
                if isinstance(p, URIRef):
                    P.add(p); ps.add(p)
            if next(sg.objects(prop, SH.node), None) is not None:
                P_node |= ps

    for _s, _p, p in sg.triples((None, SH.path, None)):
        if isinstance(p, URIRef):
            P.add(p)

    return C, P, N, P_node

def _sameas_components_from_map(same_nodes: Dict[URIRef, Set[URIRef]]) -> Dict[str, float]:
    """
    Build components from the mapping {rep_or_focus : set(aliases)}.
    Count only components that intersect the focus (i.e., keys of same_nodes).
    """
    focus = set(same_nodes.keys())
    nodes = set(focus)
    adj = defaultdict(set)

    for k, vals in same_nodes.items():
        for v in vals:
            nodes.add(v)
            adj[k].add(v)
            adj[v].add(k)

    seen = set()
    comps = []
    for n in nodes:
        if n in seen:
            continue
        stack = [n]
        comp = {n}
        seen.add(n)
        while stack:
            x = stack.pop()
            for y in adj.get(x, set()):
                if y not in seen:
                    seen.add(y)
                    comp.add(y)
                    stack.append(y)
        comps.append(comp)

    comps_touch = [c for c in comps if c & focus]
    total_nodes = sum(len(c) for c in comps_touch)
    total_subsumed = total_nodes - len(comps_touch)
    max_sz = max((len(c) for c in comps_touch), default=0)
    avg_sz = (total_nodes / len(comps_touch)) if comps_touch else 0.0

    return {
        "components": len(comps_touch),
        "total_subsumed": int(total_subsumed),
        "avg_component_size": float(avg_sz),
        "max_component_size": int(max_sz),
    }

def _edges_by_path(g: Graph, paths: Iterable[URIRef] | None = None, topk: int | None = None) -> Dict[str, int]:
    counts = Counter()
    if paths is None:
        for _s, p, _o in g.triples((None, None, None)):
            if isinstance(p, URIRef):
                counts[str(p)] += 1
    else:
        for p in paths:
            if isinstance(p, URIRef):
                counts[str(p)] = sum(1 for _ in g.triples((None, p, None)))
    return dict(counts.most_common(topk)) if topk else dict(counts)


def _active_nodes_in_graph(g: Graph) -> Set[URIRef]:
    """All URIRefs that appear as s or o in g and are not predicates."""
    is_pred = _is_predicate_fast(g)
    out = set()
    for s, p, o in g.triples((None, None, None)):
        if isinstance(s, URIRef) and not is_pred(s):
            out.add(s)
        if isinstance(o, URIRef) and not is_pred(o):
            out.add(o)
    return out


def _class_presence(g: Graph, classes: Iterable[URIRef]) -> Set[URIRef]:
    present = set()
    for c in classes:
        if isinstance(c, URIRef) and next(g.triples((None, RDF.type, c)), None) is not None:
            present.add(c)
    return present

def collect_common_metrics(
    g_merged: Graph,
    shapes_graph: Graph,
    same_nodes: Dict[URIRef, Set[URIRef]],
    topk_predicates: int = 60,
) -> Dict:
    """
    Compute a comparable bundle of metrics for both original ReSHACL (merged_graph)
    and your class-ReSHACL (merged_graph_class).
    """
    C, P, N, P_node = _harvest_from_shapes(shapes_graph)
    edges_counts = _edges_by_path(g_merged, paths=P)  # counts only for target paths
    path_covered = sum(1 for _p, cnt in edges_counts.items() if cnt > 0)
    types_present = _class_presence(g_merged, C)
    active_nodes = _active_nodes_in_graph(g_merged)

    metrics = {
        "harvest": {
            "C": len(C),
            "P": len(P),
            "N": len(N),
            "P_node": len(P_node),
        },
        "focus": {
            "keys_in_same_nodes": len(same_nodes),
            "active_nodes_in_graph": len(active_nodes),
        },
        "graph": {
            "triples": len(g_merged),
            "predicates": len(set(p for _s, p, _o in g_merged if isinstance(p, URIRef))),
            "rdf_type_triples": sum(1 for _ in g_merged.triples((None, RDF.type, None))),
            "owl_sameAs_triples": sum(1 for _ in g_merged.triples((None, OWL.sameAs, None))),
            "edges_by_path": {k: int(v) for k, v in (edges_counts.items())},
            "top_predicates_overall": _edges_by_path(g_merged, paths=None, topk=topk_predicates),
        },
        "coverage": {
            "paths_covered": path_covered,
            "paths_total": len(P),
            "paths_ratio": (path_covered / len(P)) if P else 0.0,
            "classes_present": len(types_present),
            "classes_total": len(C),
            "classes_ratio": (len(types_present) / len(C)) if C else 0.0,
        },
        "sameAs": _sameas_components_from_map(same_nodes),
    }
    return metrics

def collect_violation_metrics(v_report_graph: Graph, topk: int = 15) -> Dict:
    """
    Summarize the SHACL validation report graph:
      - totals
      - by severity
      - top source shapes
      - top result paths
      - top focus nodes
    """
    result_nodes = [r for _s, _p, r in v_report_graph.triples((None, SH.result, None))]
    total = len(result_nodes)

    by_severity = Counter()
    by_source_shape = Counter()
    by_result_path = Counter()
    by_focus_node = Counter()

    for r in result_nodes:
        sev = next(v_report_graph.objects(r, SH.resultSeverity), None)
        if isinstance(sev, URIRef):
            by_severity[str(sev)] += 1
        src = next(v_report_graph.objects(r, SH.sourceShape), None)
        if isinstance(src, (URIRef, BNode)):
            by_source_shape[str(src)] += 1
        path = next(v_report_graph.objects(r, SH.resultPath), None)
        if isinstance(path, URIRef):
            by_result_path[str(path)] += 1
        fn = next(v_report_graph.objects(r, SH.focusNode), None)
        if isinstance(fn, (URIRef, BNode, Literal)):
            by_focus_node[str(fn)] += 1

    def top(counter: Counter, k: int) -> Dict[str, int]:
        return dict(counter.most_common(k))

    return {
        "total_results": total,
        "by_severity": dict(by_severity),
        "top_source_shapes": top(by_source_shape, topk),
        "top_result_paths": top(by_result_path, topk),
        "top_focus_nodes": top(by_focus_node, topk),
    }

def save_metrics(dataset_name: str, method: str, common: Dict, violations: Dict | None = None) -> None:
    base = f"Outputs/{dataset_name}/metrics/"
    check_directory_exists_otherwise_create(base)
    with open(os.path.join(base, f"{method}_common.json"), "w") as f:
        json.dump(common, f, indent=2, sort_keys=True)
    if violations is not None:
        with open(os.path.join(base, f"{method}_violations.json"), "w") as f:
            json.dump(violations, f, indent=2, sort_keys=True)


def print_metrics(common: Dict, violations: Dict | None = None) -> None:
    print(json.dumps(common, indent=2, sort_keys=True))
    if violations is not None:
        print(json.dumps(violations, indent=2, sort_keys=True))

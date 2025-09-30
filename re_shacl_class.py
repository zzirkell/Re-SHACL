from typing import Tuple, Set, Dict, Optional        
from rdflib import Graph, URIRef, BNode                    
from rdflib.namespace import RDF, SH, OWL, RDFS            
from pyshacl.pytypes import GraphLike                       
from collections import deque                               
from .re_shacl import load_graph                             

# Feature toggles
ENABLE_INV = False          # prp-inv1/2   : add owl:inverseOf edges in G′
ENABLE_TRP = False          # prp-trp      : add transitive closure for marked properties
ENABLE_FP_IFP = False       # prp-fp/ifp   : consolidate functional/inverse-functional edges
ENABLE_PDW_DETECT = False   # prp-pdw      : propertyDisjointWith metrics (debug only)
ENABLE_CAX_DW_DETECT = False# cax-dw       : class disjointness metrics (debug only)
ENABLE_CLS_COM_DETECT = False# cls-com     : complements metrics (debug only)
FOCUS_FROM_SHAPES_ONLY = True            
INCLUDE_PATH_TOUCHED_FOCUS = True        

from functools import lru_cache

def make_is_predicate(vg: Graph):
    """
    Returns a memoized predicate tester bound to the specific data graph 'vg'.
    It avoids scanning the whole graph by leveraging rdflib's predicate index.
    """
    @lru_cache(maxsize=200_000)                      # cache results per IRI
    def _is_predicate(u: URIRef) -> bool:
        if not isinstance(u, URIRef):                # literals/bnodes → never predicates
            return False
        return next(vg.triples((None, u, None)), None) is not None
    return _is_predicate


# ------------------------------ shapes harvesting ------------------------------

def _harvest_targets(sg: Graph) -> Tuple[
    Set[URIRef], Set[URIRef], Set[URIRef], Set[URIRef], Set[URIRef]
]:
    """
    Extract targets from the shapes graph 'sg'.

    Returns: (C, P, N, F_init, P_node)
      C      : sh:targetClass IRIs
      P      : IRI-valued sh:path predicates (from property shapes and a fallback sweep)
      N      : sh:targetNode IRIs
      F_init : early focus seeds (starts from N; may be extended by caller)
      P_node : subset of P where the property shape also has sh:node (delegates object to a node shape)
    """
    C: Set[URIRef] = set()
    P: Set[URIRef] = set()
    N: Set[URIRef] = set()
    F_init: Set[URIRef] = set()
    P_node: Set[URIRef] = set()  # properties whose objects will be validated by a node shape

    # Iterate node shapes
    for node_shape in sg.subjects(RDF.type, SH.NodeShape):
        # collect sh:targetClass
        for c in sg.objects(node_shape, SH.targetClass):
            if isinstance(c, URIRef):
                C.add(c)
        # collect sh:targetNode (and seed initial focus)
        for n in sg.objects(node_shape, SH.targetNode):
            if isinstance(n, URIRef):
                N.add(n); F_init.add(n)

        # within each node shape, look at each sh:property bnode (or IRI) it points to
        for prop_node in sg.objects(node_shape, SH.property):
            # collect all IRI-valued sh:path(s) in this property shape
            ps: Set[URIRef] = set()
            for p in sg.objects(prop_node, SH.path):
                if isinstance(p, URIRef):
                    P.add(p)
                    ps.add(p)
            # if the property shape delegates to a node shape via sh:node,
            # remember these p’s in P_node (we’ll later focus on their objects)
            if next(sg.objects(prop_node, SH.node), None) is not None:
                for p in ps:
                    P_node.add(p)

    # fallback sweep: capture any stray sh:path stated elsewhere (some serializers differ)
    for _s, _p, p in sg.triples((None, SH.path, None)):
        if isinstance(p, URIRef):
            P.add(p)

    return C, P, N, F_init, P_node


# ------------------------------ schema summaries ------------------------------

def _build_schema_summaries(vg: Graph, C: Set[URIRef], P: Set[URIRef]) -> Dict[str, Dict]:
    """
    Precompute light-weight schema views scoped to (C,P):
      - equivalent classes/properties,
      - subclass/subproperty closures (downward),
      - property families for P (eq + subprops),
      - lifted domain/range (to superclasses, and along equivalents).
    Produces small dicts we reuse while building G′ and deriving focus.
    """

    # --- equivalence of classes ---
    cls_eq: Dict[URIRef, Set[URIRef]] = {}
    for c1, _, c2 in vg.triples((None, OWL.equivalentClass, None)):
        if isinstance(c1, URIRef) and isinstance(c2, URIRef):
            cls_eq.setdefault(c1, set()).update({c1, c2})
            cls_eq.setdefault(c2, set()).update({c1, c2})

    # --- equivalence of properties (includes owl:sameAs on properties, a few datasets use it) ---
    prop_eq: Dict[URIRef, Set[URIRef]] = {}
    for p1, _, p2 in vg.triples((None, OWL.equivalentProperty, None)):
        if isinstance(p1, URIRef) and isinstance(p2, URIRef):
            prop_eq.setdefault(p1, set()).update({p1, p2})
            prop_eq.setdefault(p2, set()).update({p1, p2})
    for p1, _, p2 in vg.triples((None, OWL.sameAs, None)):
        if isinstance(p1, URIRef) and isinstance(p2, URIRef):
            prop_eq.setdefault(p1, set()).update({p1, p2})
            prop_eq.setdefault(p2, set()).update({p1, p2})

    # --- raw subclass and subproperty edges (child -> parents) ---
    sub_of: Dict[URIRef, Set[URIRef]] = {}
    for c_sub, _, c_sup in vg.triples((None, RDFS.subClassOf, None)):
        if isinstance(c_sub, URIRef) and isinstance(c_sup, URIRef):
            sub_of.setdefault(c_sub, set()).add(c_sup)

    subprop_of: Dict[URIRef, Set[URIRef]] = {}
    for p_sub, _, p_sup in vg.triples((None, RDFS.subPropertyOf, None)):
        if isinstance(p_sub, URIRef) and isinstance(p_sup, URIRef):
            subprop_of.setdefault(p_sub, set()).add(p_sup)

    # --- helper builds "parents index" and a bounded downward closure for the supers we care about ---
    def downward(children_of: Dict[URIRef, Set[URIRef]]) -> Dict[URIRef, Set[URIRef]]:
        # invert child->parents into parents->children
        parents: Dict[URIRef, Set[URIRef]] = {}
        for child, pars in children_of.items():
            for par in pars:
                parents.setdefault(par, set()).add(child)

        # do a BFS-based downward closure only starting from a requested super set
        def close_down(start_supers: Set[URIRef]) -> Dict[URIRef, Set[URIRef]]:
            out: Dict[URIRef, Set[URIRef]] = {s: set() for s in start_supers}
            from collections import deque
            for s in list(start_supers):
                q = deque(parents.get(s, set()))
                seen = set()
                while q:
                    x = q.popleft()
                    if x in seen:
                        continue
                    seen.add(x)
                    out[s].add(x)
                    for y in parents.get(x, set()):
                        if y not in seen:
                            q.append(y)
            return out
        return parents, close_down

    # parents index + bounded closures for classes & properties
    cls_parents, cls_close_down = downward(sub_of)
    prop_parents, prop_close_down = downward(subprop_of)

    # classes: we only need downward maps for supers that appear as supers (plus targeted classes)
    subcls: Dict[URIRef, Set[URIRef]] = cls_close_down(set(cls_parents.keys()) | set(C))

    # --- property family: for each p in P, collect eq(p) and all its subproperties (transitively) ---
    def prop_family_of(p: URIRef) -> Set[URIRef]:
        fam = set(prop_eq.get(p, set()) or {p})  # start with p and its equivalents
        fam.add(p)
        # collect "parents" we might need to descend from
        supers = set()
        for u in fam:
            supers |= prop_parents.get(u, set())
        # close downward from p, its eqs and the supers we saw (covers subprops of any eq)
        down = prop_close_down({p} | fam | supers)
        out = set()
        for s, subs in down.items():
            if s == p or s in fam:
                out |= subs
        fam |= out
        # include equivalents of those subs too (closure under eqprop)
        for sp in list(fam):
            fam |= prop_eq.get(sp, set()) or {sp}
        return fam

    # precompute for every targeted path p
    prop_family: Dict[URIRef, Set[URIRef]] = {p: prop_family_of(p) for p in P}

    # --- lifted domain/range for each targeted p (include superclasses & class eq) ---
    def superclasses_of(t: URIRef) -> Set[URIRef]:
        supers: Set[URIRef] = set()
        q = deque([t])
        seen = set()
        while q:
            x = q.popleft()
            for par in sub_of.get(x, set()):
                if par not in seen:
                    seen.add(par); supers.add(par); q.append(par)
        return supers

    dom_all: Dict[URIRef, Set[URIRef]] = {}
    rng_all: Dict[URIRef, Set[URIRef]] = {}
    for p in P:
        ds: Set[URIRef] = set()
        rs: Set[URIRef] = set()
        # gather domains/ranges from any member of the family(p)
        for q in prop_family[p]:
            for c in vg.objects(q, RDFS.domain):
                if isinstance(c, URIRef):
                    ds.add(c); ds |= cls_eq.get(c, set())   # include equivalent classes
            for c in vg.objects(q, RDFS.range):
                if isinstance(c, URIRef):
                    rs.add(c); rs |= cls_eq.get(c, set())
        # lift to superclasses so rdfs2/rdfs3 behave the same under subproperties
        ds_up = set(ds)
        for c in list(ds): ds_up |= superclasses_of(c)
        rs_up = set(rs)
        for c in list(rs): rs_up |= superclasses_of(c)
        dom_all[p] = ds_up
        rng_all[p] = rs_up

    # Package all summaries
    return {
        "cls_eq": cls_eq,            # class -> eq class set
        "subcls": subcls,            # super -> transitive subs (bounded)
        "prop_eq": prop_eq,          # property -> eq property set
        "subprop": None,             # kept for compatibility; not needed downstream
        "dom_all": dom_all,          # p -> lifted domains
        "rng_all": rng_all,          # p -> lifted ranges
        "prop_family": prop_family   # p -> family(p) = eq(p) ∪ subprops(eq(p))
    }

# ------------------------------ focus derivation ------------------------------

def _derive_focus(
    vg,
    C: Set[URIRef],
    P: Set[URIRef],
    N: Set[URIRef],
    F0: Set[URIRef],
    schema: Dict[str, Dict],
    P_node: Set[URIRef],
) -> Tuple[Set[URIRef], Dict]:

    is_pred  = make_is_predicate(vg)

    # Cheap guard to avoid adding schema IRIs as focus: a "class IRI" typically appears as rdf:type object
    def is_class(u: URIRef) -> bool:
        return next(vg.triples((None, RDF.type, u)), None) is not None

    # expand target classes using schema summaries (equivalents + downward subclasses)
    subcls = schema.get("subcls", {})
    cls_eq = schema.get("cls_eq", {})
    Cplus: Set[URIRef] = set()
    for c in C:
        Cplus.add(c)
        Cplus |= cls_eq.get(c, set())
        for sub in subcls.get(c, set()):
            Cplus.add(sub)
            Cplus |= cls_eq.get(sub, set())

    # seed focus with explicit target nodes + any other seeds given by harvesting
    F: Set[URIRef] = set(N) | set(F0)

    # add instances of all classes in Cplus (subjects with rdf:type ∈ Cplus)
    for t in Cplus:
        for x, _, _ in vg.triples((None, RDF.type, t)):
            if isinstance(x, URIRef) and not is_pred(x):  # skip property IRIs
                F.add(x)

    # add OBJECTS for properties that delegate via sh:node (and their property families)
    fam = schema.get("prop_family", {})
    for p in P_node:
        for q in fam.get(p, {p}):
            for _s, o in vg.subject_objects(q):           # faster than triples((None,q,None))
                if isinstance(o, URIRef) and (not is_pred(o)) and (not is_class(o)):
                    F.add(o)

    # return F + small debug payload
    return F, {"Cplus": len(Cplus), "seed_N": len(N), "seed_F0": len(F0)}


# ------------------------------ sameAs components (touching focus) ------------------------------

def _union_find_sameas(
    vg: Graph,                   
    F: Set[URIRef],
    N: Optional[Set[URIRef]] = None
) -> Tuple[Dict[URIRef, Set[URIRef]], Dict[URIRef, URIRef]]:
    """
    Build owl:sameAs components that TOUCH the focus set F.
    Returns:
      merge_map : { rep : set(subsumed) }  chosen representative -> other IRIs in its component
      rep_of    : { x  : rep }             node -> its representative
    Rep selection:
      (1) prefer sh:targetNode members if present, else
      (2) prefer focus nodes, else
      (3) lexicographically smallest IRI in the component.
    """
    N = N or set()                                  # normalize None → empty set
    is_pred = make_is_predicate(vg)                 # fast predicate test

    # Build undirected adjacency over owl:sameAs between IRIs that are not used as predicates
    adj: Dict[URIRef, Set[URIRef]] = {}
    for a, _, b in vg.triples((None, OWL.sameAs, None)):
        if not (isinstance(a, URIRef) and isinstance(b, URIRef)):
            continue
        if is_pred(a) or is_pred(b):
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a) 
    if not adj:
        return {}, {}

    # BFS components only starting from focus nodes (ignore components disjoint from F)
    visited: Set[URIRef] = set()
    components: Set[frozenset[URIRef]] = set()
    for seed in F:
        if seed in visited or seed not in adj:
            continue
        comp: Set[URIRef] = set()
        q: deque[URIRef] = deque([seed])
        visited.add(seed)
        comp.add(seed)
        while q:
            x = q.popleft()
            for y in adj.get(x, set()):
                if y not in visited:
                    visited.add(y)
                    comp.add(y)
                    q.append(y)
        components.add(frozenset(comp))

    # Rep picker implements the stated policy
    def pick_rep(comp_set: Set[URIRef]) -> URIRef:
        cand = sorted(u for u in comp_set if u in N)
        if cand:
            return cand[0]
        cand = sorted(u for u in comp_set if u in F)
        if cand:
            return cand[0]
        return sorted(comp_set)[0]

    merge_map: Dict[URIRef, Set[URIRef]] = {}
    rep_of: Dict[URIRef, URIRef] = {}
    for comp in components:
        rep = pick_rep(set(comp))
        others = set(comp) - {rep}
        merge_map[rep] = others
        for x in comp:
            rep_of[x] = rep

    return merge_map, rep_of


# ------------------------------ shapes rewriting (targetNode only) ------------------------------

def _rewrite_target_nodes(sg: Graph, merge_map: Dict[URIRef, Set[URIRef]]) -> Graph:
    """
    Return a new shapes graph S′ where sh:targetNode IRIs subsumed by sameAs
    are rewritten to their chosen representative.
    """
    # Build reverse map: subsumed -> representative
    subsumed_to_rep: Dict[URIRef, URIRef] = {}
    for rep, subs in merge_map.items():
        for s in subs:
            subsumed_to_rep[s] = rep

    # New shapes graph with same namespace manager (readability)
    S_prime = Graph()
    S_prime.namespace_manager = sg.namespace_manager

    # Copy all triples, rewrite only sh:targetNode objects when they were subsumed
    for s, p, o in sg.triples((None, None, None)):
        if p == SH.targetNode and isinstance(o, URIRef):
            rep = subsumed_to_rep.get(o)
            S_prime.add((s, p, rep if rep is not None else o))
        else:
            S_prime.add((s, p, o))
    return S_prime


# ------------------------------ advanced schema flags (scoped to P) ------------------------------

def _collect_advanced_schema(vg: Graph, P: Set[URIRef], fam_map: Dict[URIRef, Set[URIRef]]):
    """
    Build small lookups restricted to the shape paths:
      inv_of[p]            : set of inverse properties for any q in family(p)
      is_transitive[p]     : True if p or any q in family(p) is owl:TransitiveProperty
      is_functional[p]     : True if p or any q in family(p) is owl:FunctionalProperty
      is_inv_functional[p] : True if p or any q in family(p) is owl:InverseFunctionalProperty
      disjoint_props       : {frozenset({u,v})} for owl:propertyDisjointWith
      disjoint_classes     : {frozenset({C,D})} for owl:disjointWith
      complement_pairs     : {(C,D)} for owl:complementOf (both directions gathered)
    """
    inv_of: Dict[URIRef, Set[URIRef]] = {}
    is_transitive: Dict[URIRef, bool] = {p: False for p in P}
    is_functional: Dict[URIRef, bool] = {p: False for p in P}
    is_inv_functional: Dict[URIRef, bool] = {p: False for p in P}
    disjoint_props: Set[frozenset] = set()
    disjoint_classes: Set[frozenset] = set()
    complement_pairs: Set[Tuple[URIRef, URIRef]] = set()

    # Read property characteristics on each family member
    for p in P:
        fam = fam_map.get(p, {p})
        for q in fam:
            if (q, RDF.type, OWL.TransitiveProperty) in vg:
                is_transitive[p] = True
            if (q, RDF.type, OWL.FunctionalProperty) in vg:
                is_functional[p] = True
            if (q, RDF.type, OWL.InverseFunctionalProperty) in vg:
                is_inv_functional[p] = True

        # owl:inverseOf — check both object and subject sides
        for q in fam:
            for ip in vg.objects(q, OWL.inverseOf):
                if isinstance(ip, URIRef):
                    inv_of.setdefault(p, set()).add(ip)
            for ip in vg.subjects(OWL.inverseOf, q):
                if isinstance(ip, URIRef):
                    inv_of.setdefault(p, set()).add(ip)

    # Disjoint properties among the union of all families
    all_qs: Set[URIRef] = set()
    for p in P:
        all_qs |= fam_map.get(p, {p})
    for a in all_qs:
        for b in vg.objects(a, OWL.propertyDisjointWith):
            if isinstance(b, URIRef):
                disjoint_props.add(frozenset({a, b}))
        for b in vg.subjects(OWL.propertyDisjointWith, a):
            if isinstance(b, URIRef):
                disjoint_props.add(frozenset({a, b}))

    # Disjoint/complement classes (global; checked only against types present in G′)
    for C, D in vg.subject_objects(OWL.disjointWith):
        if isinstance(C, URIRef) and isinstance(D, URIRef):
            disjoint_classes.add(frozenset({C, D}))
    for C, D in vg.subject_objects(OWL.complementOf):
        if isinstance(C, URIRef) and isinstance(D, URIRef):
            complement_pairs.add((C, D))
    for D, C in vg.subject_objects(OWL.complementOf):
        if isinstance(C, URIRef) and isinstance(D, URIRef):
            complement_pairs.add((C, D))

    # Turn on feature flags only if the dataset actually needs them (keeps G′ smaller by default)
    if any(is_transitive.values()): ENABLE_TRP = True
    if any(is_functional.values()) or any(is_inv_functional.values()): ENABLE_FP_IFP = True
    if any(inv_of.values()): ENABLE_INV = True

    return inv_of, is_transitive, is_functional, is_inv_functional, disjoint_props, disjoint_classes, complement_pairs


# ------------------------------ build G′ ------------------------------

def _build_Gprime(
    vg: Graph,                              # data graph
    F: Set[URIRef],                         # focus nodes
    C: Set[URIRef],                         # targeted classes (not directly used here)
    P: Set[URIRef],                         # targeted paths
    schema: Dict[str, Dict],                # schema summaries
    rep_of: Dict[URIRef, URIRef],           # node -> representative
    merge_map: Dict[URIRef, Set[URIRef]]    # rep -> subsumed aliases
) -> Graph:
    """
    Construct the compact validation graph G′:
      • normalize families to the exact sh:path (prp-eqp*, prp-spo1 / rdfs7),
      • inject localized domain/range (rdfs2/rdfs3 lifted via scm-dom*/rng*),
      • include existing rdf:type for focus nodes + equivalences + superclasses (cax-eqc1/2, rdfs9),
      • add symmetric flips for symmetric paths (prp-symp),
      • ensure non-reflexive sameAs edges remain observable where relevant.
    """
    Gp = Graph() 
    Gp.namespace_manager = vg.namespace_manager 

    # pull summaries
    fam_map   = schema.get("prop_family", {})
    dom_all   = schema.get("dom_all", {})
    rng_all   = schema.get("rng_all", {})
    subcls    = schema.get("subcls", {})
    cls_eq    = schema.get("cls_eq", {})

    # collect advanced schema (flags may toggle on based on dataset)
    inv_of, is_transitive, is_functional, is_inv_functional, disjoint_props, disjoint_classes, complement_pairs = \
        _collect_advanced_schema(vg, P, fam_map)

    is_pred = make_is_predicate(vg)

    # Precompute which targeted paths behave as symmetric (either p or some q∈family(p) is declared symmetric)
    p_is_symmetric: Dict[URIRef, bool] = {}
    for p in P:
        symmetric = False
        if (p, RDF.type, OWL.SymmetricProperty) in vg:
            symmetric = True
        for q in fam_map.get(p, {p}):
            if (q, RDF.type, OWL.SymmetricProperty) in vg:
                symmetric = True
                break
        p_is_symmetric[p] = symmetric

    # canonicalize a term to its representative (only for non-property IRIs)
    def canon(x):
        if isinstance(x, URIRef) and not is_pred(x):
            return rep_of.get(x, x)
        return x

    # helper for adding rdf:type safely
    def add_type(node: URIRef, T: URIRef):
        if isinstance(node, URIRef):
            Gp.add((node, RDF.type, T))

    # Build reverse map: subclass → {superclasses}  (we only have 'super -> subs' in summaries)
    reverse_supers: Dict[URIRef, Set[URIRef]] = {}
    for sup, subs in subcls.items():
        for sub in subs:
            reverse_supers.setdefault(sub, set()).add(sup)

    # (1) Types of focus nodes and their liftings
    for x in F:
        for _s, _p, t in vg.triples((x, RDF.type, None)):
            if isinstance(t, URIRef) and not is_pred(t):
                xr = canon(x) 
                add_type(xr, t)                      
                for eqc in cls_eq.get(t, set()):          
                    add_type(xr, eqc)
                for sup in reverse_supers.get(t, set()): 
                    add_type(xr, sup)

    # (2) Normalize property families: map any q ∈ family(p) to canonical p, collect edges touching F
    canonical_of: Dict[URIRef, URIRef] = {}
    for p in P:
        for q in fam_map.get(p, {p}):
            canonical_of[q] = p

    edges_by_p: Dict[URIRef, Set[Tuple[URIRef, URIRef]]] = {p: set() for p in P}

    for q, p in canonical_of.items():
 
        for s, o in vg.subject_objects(q):
            s_is_focus = isinstance(s, URIRef) and s in F   # keep graph small: only edges touching F
            o_is_focus = isinstance(o, URIRef) and o in F
            if not (s_is_focus or o_is_focus):
                continue

            s_c = canon(s)                                 
            o_c = canon(o)

            if p == OWL.sameAs and s_c == o_c:             
                continue

            edges_by_p[p].add((s_c, o_c))                   

    # (2b) Local FP/IFP consolidation so SHACL sees post-merge cardinalities
    def _consolidate_fp_ifp_for_p(p: URIRef,
                                  pairs: Set[Tuple[URIRef, URIRef]],
                                  is_fp: bool, is_ifp: bool,
                                  rep_hint: Dict[URIRef, URIRef]) -> Set[Tuple[URIRef, URIRef]]:
        if is_fp:
            idx: Dict[URIRef, Set[URIRef]] = {}
            for s, o in pairs:
                idx.setdefault(s, set()).add(o)
            new_pairs: Set[Tuple[URIRef, URIRef]] = set()
            for s, objs in idx.items():
                if len(objs) <= 1:
                    new_pairs.add((s, next(iter(objs))))
                else:
                    rep = None
                    for cand in sorted(objs, key=str):
                        if rep_hint.get(cand, cand) == cand:
                            rep = cand; break
                    if rep is None:
                        rep = sorted(objs, key=str)[0]
                    new_pairs.add((s, rep))
            pairs = new_pairs

        # Inverse-functional: if an object o has multiple subjects s,
        # fold them to a single representative subject (again prefer canonical when available).
        if is_ifp:
            idx: Dict[URIRef, Set[URIRef]] = {}
            for s, o in pairs:
                idx.setdefault(o, set()).add(s)
            new_pairs: Set[Tuple[URIRef, URIRef]] = set()
            for o, subjs in idx.items():
                if len(subjs) <= 1:
                    new_pairs.add((next(iter(subjs)), o))
                else:
                    rep = None
                    for cand in sorted(subjs, key=str):
                        if rep_hint.get(cand, cand) == cand:
                            rep = cand; break
                    if rep is None:
                        rep = sorted(subjs, key=str)[0]
                    new_pairs.add((rep, o))
            pairs = new_pairs

        return pairs

    # apply consolidation per canonical path if that path is functional / inverse-functional
    for p in P:
        pairs = edges_by_p.get(p, set())
        if not pairs:
            continue
        pairs = _consolidate_fp_ifp_for_p(
            p,
            pairs,
            is_functional.get(p, False),
            is_inv_functional.get(p, False),
            rep_hint=rep_of
        )
        edges_by_p[p] = pairs

    # (2c) Materialize consolidated edges into G′, add localized domain/range, add symmetric flips
    for p in P:
        pairs = edges_by_p.get(p, set())
        if not pairs:
            continue

        p_dom = dom_all.get(p, set())
        p_rng = rng_all.get(p, set())
        p_sym = p_is_symmetric.get(p, False)

        flips_to_add = set()

        for (s_c, o_c) in pairs.copy():
            Gp.add((s_c, p, o_c))

            for Cx in p_dom:
                add_type(s_c, Cx)
            if isinstance(o_c, URIRef):
                for Cy in p_rng:
                    add_type(o_c, Cy)

            if p_sym and isinstance(o_c, URIRef):
                Gp.add((o_c, p, s_c))
                flips_to_add.add((o_c, s_c))
                for Cx in p_dom:
                    add_type(o_c, Cx)
                for Cy in p_rng:
                    add_type(s_c, Cy)

        if flips_to_add:
            edges_by_p[p].update(flips_to_add)


    # (3) keep observable non-reflexive sameAs edges for components touching F, if shapes target sameAs
    if OWL.sameAs in P:
        for rep, subs in merge_map.items():
            if rep in F or any(u in F for u in subs):
                for alias in subs:
                    Gp.add((rep, OWL.sameAs, alias))

    # (4) owl:inverseOf (optional)
    if ENABLE_INV:
        for p in P:
            invs = inv_of.get(p, set())
            if not invs:
                continue
            for (s_c, o_c) in edges_by_p.get(p, set()):
                for ip in invs:
                    if isinstance(o_c, URIRef):
                        Gp.add((o_c, ip, s_c))

    # (5) transitive properties (optional)
    if ENABLE_TRP:
        for p in P:
            if not is_transitive.get(p, False):
                continue
            adj: Dict[URIRef, Set[URIRef]] = {}
            for (s_c, o_c) in edges_by_p.get(p, set()):
                if isinstance(s_c, URIRef) and isinstance(o_c, URIRef):
                    adj.setdefault(s_c, set()).add(o_c)
            for src in list(adj.keys()):
                visited: Set[URIRef] = set([src])
                frontier: list[URIRef] = list(adj.get(src, set()))
                while frontier:
                    nxt: list[URIRef] = []
                    for mid in frontier:
                        for dst in adj.get(mid, set()):
                            if dst not in visited:
                                Gp.add((src, p, dst))
                                edges_by_p.setdefault(p, set()).add((src, dst))
                                visited.add(dst)
                                nxt.append(dst)
                    frontier = nxt

    # (7) optional metrics for thesis/debug (don’t affect G′ content)
    if ENABLE_PDW_DETECT or ENABLE_CAX_DW_DETECT or ENABLE_CLS_COM_DETECT:
        edges_by_pred: Dict[URIRef, Set[Tuple[URIRef, URIRef]]] = {}
        for s, p, o in Gp.triples((None, None, None)):
            if isinstance(p, URIRef) and isinstance(s, URIRef) and isinstance(o, URIRef):
                edges_by_pred.setdefault(p, set()).add((s, o))

        pdw_count = 0
        if ENABLE_PDW_DETECT and disjoint_props:
            for uv in disjoint_props:
                u, v = tuple(uv)
                U = edges_by_pred.get(u, set())
                V = edges_by_pred.get(v, set())
                if U and V:
                    pdw_count += len(U & V)

        cax_dw_count = 0
        cls_com_count = 0
        if ENABLE_CAX_DW_DETECT or ENABLE_CLS_COM_DETECT:
            types_of: Dict[URIRef, Set[URIRef]] = {}
            for x, _, T in Gp.triples((None, RDF.type, None)):
                if isinstance(x, URIRef) and isinstance(T, URIRef):
                    types_of.setdefault(x, set()).add(T)

            if ENABLE_CAX_DW_DETECT:
                for x, Ts in types_of.items():
                    for CD in disjoint_classes:
                        C, D = tuple(CD)
                        if C in Ts and D in Ts:
                            cax_dw_count += 1

            if ENABLE_CLS_COM_DETECT:
                for x, Ts in types_of.items():
                    for (C, D) in complement_pairs:
                        if C in Ts and D in Ts:
                            cls_com_count += 1

        if pdw_count or cax_dw_count or cls_com_count:
            print(f"[G′-metrics] prp-pdw: {pdw_count}  cax-dw: {cax_dw_count}  cls-com: {cls_com_count}")

    return Gp


# ------------------------------ top-level: merged_graph_class ------------------------------

def merged_graph_class(
    data_graph,
    shacl_graph: Optional[Graph] = None,
    data_graph_format: Optional[str] = None,
    shacl_graph_format: Optional[str] = None,
):
    """
    Class-level Re-SHACL pipeline:
      1) load graphs,
      2) harvest shapes targets (C, P, N, F0, P_node),
      3) build schema summaries (families, domains/ranges, eqs, subcls),
      4) derive focus F,
      5) sameAs components touching F (merge_map, rep_of),
      6) rewrite shapes S → S′ (targetNode canonicalization),
      7) build G′ (normalized, localized, optional advanced rules).
    Returns: (G′, same_nodes, S′, timings)
    """
    # load input graphs via your helper
    shapes, named_graphs, shape_graph = load_graph(
        data_graph, shacl_graph, data_graph_format, shacl_graph_format
    )
    vg: Graph = named_graphs[0] 
    sg: Graph = shape_graph.graph 

    # timings (profiling the pipeline—useful for experiments)
    import time
    t0 = time.time()
    C, P, N, F0, P_node = _harvest_targets(sg);                      t1 = time.time()
    schema = _build_schema_summaries(vg, C, P);                      t2 = time.time()
    F, _support = _derive_focus(vg, C, P, N, F0, schema, P_node);    t3 = time.time()
    merge_map, rep_of = _union_find_sameas(vg, F, N);                t4 = time.time()
    S_prime = _rewrite_target_nodes(sg, merge_map);                   t5 = time.time()
    G_prime = _build_Gprime(vg, F, C, P, schema, rep_of, merge_map); t6 = time.time()

    # same_nodes: expose sameAs components keyed by representative, ensuring all focus keys exist
    same_nodes: Dict[URIRef, Set[URIRef]] = {}
    for rep, subs in merge_map.items():
        same_nodes[rep] = set(subs)
    subsumed_all = set().union(*merge_map.values()) if merge_map else set()
    for x in F:
        if x not in same_nodes and x not in subsumed_all:
            same_nodes[x] = set()

    timings = {
        "harvest":     t1 - t0,
        "schema":      t2 - t1,
        "focus":       t3 - t2,
        "sameAs":      t4 - t3,
        "rewrite":     t5 - t4,
        "buildGprime": t6 - t5,
        "total_pre_validation": t6 - t0,
    }

    return G_prime, same_nodes, S_prime, timings

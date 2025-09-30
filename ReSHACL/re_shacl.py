from pyshacl import validate

from .errors import FusionRuntimeError
from pyshacl.pytypes import GraphLike
import rdflib

from rdflib.namespace import OWL, RDF, RDFS, SH
from rdflib import Graph
from pyshacl.shapes_graph import ShapesGraph

from typing import TYPE_CHECKING, Dict, Iterator, List, Optional, Set, Tuple, Union

from pyshacl.monkey import rdflib_bool_patch, rdflib_bool_unpatch
from pyshacl.rdfutil import (
    load_from_source,
)
from pyshacl.consts import (
    SH_path,
    SH_node,
    RDFS_subClassOf,
    SH_targetNode,
)
SH_class = SH["class"]

from rdflib.namespace import Namespace
RDFS_PFX = 'http://www.w3.org/2000/01/rdf-schema#'
RDFS = Namespace(RDFS_PFX)
RDFS_subPropertyOf = RDFS.subPropertyOf


if TYPE_CHECKING:
    from pyshacl.shapes_graph import ShapesGraph


def load_graph(data_graph: Union[GraphLike, str, bytes],
    shacl_graph: Optional[Union[GraphLike, str, bytes]] = None,
    data_graph_format: Optional[str] = None,
    shacl_graph_format: Optional[str] = None,
    ):
    
    loaded_dg = load_from_source(data_graph, rdf_format=data_graph_format, multigraph=True, do_owl_imports=False)
    if not isinstance(loaded_dg, rdflib.Graph):
        raise RuntimeError("data_graph must be a rdflib Graph object")

    if shacl_graph is not None:
        rdflib_bool_patch()
        loaded_sg = load_from_source(
            shacl_graph, rdf_format=shacl_graph_format, multigraph=False, do_owl_imports=False)
        rdflib_bool_unpatch()
    else:
        loaded_sg = None
        
    assert isinstance(loaded_sg, rdflib.Graph), "shacl_graph must be a rdflib Graph object"
    shape_graph = ShapesGraph(loaded_sg, None)  # type: ShapesGraph
    
    shapes = shape_graph.shapes  # This property getter triggers shapes harvest.
       
    the_target_graph = loaded_dg
    if isinstance(the_target_graph, (rdflib.Dataset, rdflib.ConjunctiveGraph)):
        named_graphs = [
            rdflib.Graph(the_target_graph.store, i, namespace_manager=the_target_graph.namespace_manager)
            if not isinstance(i, rdflib.Graph)
            else i
            for i in the_target_graph.store.contexts(None)
        ]
    else:
        named_graphs = [the_target_graph]
        
    return shapes, named_graphs, shape_graph


def check_symmetricProperty(g, p): # RULE prp-symp
    if (p, RDF.type, OWL.SymmetricProperty) in g:
        for x, y in g.subject_objects(p):
            g.add((y, p, x))
        #g.remove((p, RDF.type, OWL.SymmetricProperty))
    

def check_asymmetricProperty(g, p): # prp-asyp
    if (p, RDF.type, OWL.AsymmetricProperty) in g:
        for x, y in g.subject_objects(p):
            if (y, p, x) in g:
                raise FusionRuntimeError(
                    "Erroneous usage of asymmetric property %s on %s and %s"
                    % (p, x, y)
                )
   
def check_transitiveProperty(g, p):    
    if (p, RDF.type, OWL.TransitiveProperty) in g: 
        for s, o in g.subject_objects(p):
            trans = g.transitive_objects(o, p)
            for oo in trans:          
                g.add((s, p, oo))  
                
def check_propertyDisjointWith(g, focus_property): # prp-pdw
    for p in g.objects(focus_property, OWL.propertyDisjointWith):
        for x, y in g.subject_objects(focus_property):
            if (x, p, y) in g:
                raise FusionRuntimeError(
                    "Erroneous usage of disjoint properties %s and %s on %s and %s"
                    % (focus_property, p, x, y)
                )

    for p in g.subjects(OWL.propertyDisjointWith, focus_property):
        for x, y in g.subject_objects(p):
            if (x, focus_property, y) in g:
                raise FusionRuntimeError(
                    "Erroneous usage of disjoint properties %s and %s on %s and %s"
                    % (p, focus_property, x, y)
                )
    
    
    
def check_inverseOf(g, focus_property): 
    for p1 in g.subjects(OWL.inverseOf, focus_property): 
        for x, y in g.subject_objects(p1):
            g.add((y, focus_property, x))
        for xx, yy in g.subject_objects(focus_property):
            g.add((yy, p1, xx))
    for p2 in g.objects(focus_property, OWL.inverseOf): 
        for x, y in g.subject_objects(p2):
            g.add((y, focus_property, x))
        for xx, yy in g.subject_objects(focus_property):
            g.add((yy, p2, xx))
        
    
def check_domain_range(g, p, target_nodes, same_nodes, target_classes):
    for o in g.objects(p, RDFS.domain): # RULE prp-dom  
        for x, y in g.subject_objects(p):
            g.add((x, RDF.type, o))
            if (o in target_classes) and (not x in target_nodes):
                target_nodes.add(x)
                same_set = set()
                same_nodes.update({x: same_set})


    for o in g.objects(p, RDFS.range): # RULE prp-rng
        for x, y in g.subject_objects(p):
            g.add((y, RDF.type, o))
            if (o in target_classes) and (not y in target_nodes):
                target_nodes.add(y)
                same_set = set()
                same_nodes.update({y: same_set})
                    
    return target_nodes


def target_range(g, target_nodes, same_nodes, target_classes):
    for c in target_classes:
        #range
        for pp in g.subjects( RDFS.range, c):
            
            ep_lis = { s for s in g.transitive_subjects(OWL.equivalentProperty, pp)}
                        
            ep2 = {s for s in g.transitive_objects(pp, OWL.equivalentProperty)}
            
            ep_lis = ep_lis.union(ep2)
            for ep in ep_lis:
              
                g.add((ep, RDFS_subPropertyOf, pp))
            
            
            sp = g.transitive_subjects(RDFS_subPropertyOf, pp)
            for subp in sp:
                ep1 = { s for s in g.transitive_subjects(OWL.equivalentProperty, subp)}
               
                            
                ep3 = { s for s in g.transitive_objects(subp, OWL.equivalentProperty)}
                
                ep1 = ep1.union(ep3)
                for ep in ep1:
                   
                    for ss, oo in g.subject_objects(ep):
                        g.add((oo, RDF.type, c))
                        if not oo in target_nodes:
                            target_nodes.add(oo)
                            same_set = set()
                            same_nodes.update({oo: same_set})
                
    
                for ss, oo in g.subject_objects(subp):
                    g.add((oo, RDF.type, c))
                    if not oo in target_nodes:
                        target_nodes.add(oo)
                        same_set = set()
                        same_nodes.update({oo: same_set})
            
            for s1, oo in g.subject_objects(pp):
                g.add((oo, RDF.type, c))
                if not oo in target_nodes:
                    target_nodes.add(oo)
                    same_set = set()
                    same_nodes.update({oo: same_set})        
    
def target_domain_range(g, target_nodes, same_nodes, target_classes):
    for c in target_classes:
        #range
        for pp in g.subjects( RDFS.range, c):
            
            ep_lis = { s for s in g.transitive_subjects(OWL.equivalentProperty, pp)}
                        
            ep2 = {s for s in g.transitive_objects(pp, OWL.equivalentProperty)}
            
            ep_lis = ep_lis.union(ep2)
            for ep in ep_lis:
              
                for ss, oo in g.subject_objects(ep):
                    g.add((oo, RDF.type, c))
                    if not oo in target_nodes:
                        target_nodes.add(oo)
                        same_set = set()
                        same_nodes.update({oo: same_set})
            
            
            sp = g.transitive_subjects(RDFS_subPropertyOf, pp)
            for subp in sp:
                ep1 = { s for s in g.transitive_subjects(OWL.equivalentProperty, subp)}
               
                            
                ep3 = { s for s in g.transitive_objects(subp, OWL.equivalentProperty)}
                
                ep1 = ep1.union(ep3)
                for ep in ep1:
                    
                    for ss, oo in g.subject_objects(ep):
                        g.add((oo, RDF.type, c))
                        if not oo in target_nodes:
                            target_nodes.add(oo)
                            same_set = set()
                            same_nodes.update({oo: same_set})
                
            
                for ss, oo in g.subject_objects(subp):
                    g.add((oo, RDF.type, c))
                    if not oo in target_nodes:
                        target_nodes.add(oo)
                        same_set = set()
                        same_nodes.update({oo: same_set})
            
            for s1, oo in g.subject_objects(pp):
                g.add((oo, RDF.type, c))
                if not oo in target_nodes:
                    target_nodes.add(oo)
                    same_set = set()
                    same_nodes.update({oo: same_set})
            
             
        #domain
        for p in g.subjects(RDFS.domain, c):
            
            ep1 = {s for s in g.transitive_subjects(OWL.equivalentProperty, p)}
                        
            ep2 = { s for s in g.transitive_objects(p, OWL.equivalentProperty)}
            ep2=ep2.union(ep1)
            for ep in ep2:
                g.add((ep, RDFS_subPropertyOf, p))
            
            sp = g.transitive_subjects(RDFS_subPropertyOf, p)
            for subp in sp:
                ep3 = { s for s in g.transitive_subjects(OWL.equivalentProperty, subp)}
                            
                ep4 = { s for s in g.transitive_objects(subp, OWL.equivalentProperty)}
                ep3=ep3.union(ep4)
                for ep in ep3:
                  
                    for ss, o in g.subject_objects(ep):
                        g.add((ss, RDF.type, c))
                        if not ss in target_nodes:
                            target_nodes.add(ss)
                            same_set = set()
                            same_nodes.update({ss: same_set})
               
                for ss, o in g.subject_objects(subp):
                    g.add((ss, RDF.type, c))
                    if not ss in target_nodes:
                        target_nodes.add(ss)
                        same_set = set()
                        same_nodes.update({ss: same_set})
            
            for s, o in g.subject_objects(p):
                if not (s, RDF.type, c) in g:
                    g.add((s, RDF.type, c))
                if not s in target_nodes:
                    target_nodes.add(s)
                    same_set = set()
                    same_nodes.update({s: same_set})
        

def check_com_dw(g, class_list):
    for target_class in class_list:
        # RULE cls-com
        for c2 in g.objects(target_class, OWL.complementOf):
            for x in g.subjects(RDF.type, target_class):
                if (x, RDF.type, c2) in g:
                    raise FusionRuntimeError(
                        "Violation of complementarity for classes %s and %s on element %s (or an identical individual with it)"
                        % (target_class, c2, x)
                    )
                    
        for c1 in g.subjects(OWL.complementOf, target_class):
            for x in g.subjects(RDF.type, c1):
                if (x, RDF.type, target_class) in g:
                    raise FusionRuntimeError(
                        "Violation of complementarity for classes %s and %s on element %s (or an identical individual with it)"
                        % (c1, target_class, x)
                    )
    
        # RULE cax-dw 
        for c2 in g.objects(target_class, OWL.disjointWith):
            for x in g.subjects(RDF.type, target_class):
                if (x, RDF.type, c2) in g:
                    raise FusionRuntimeError(
                        "Disjoint classes %s and %s have a common individual %s (or an identical individual with it)"
                        % (target_class, c2, x)
                    )
                    
        for c1 in g.subjects(OWL.disjointWith, target_class):
            for x in g.subjects(RDF.type, c1):
                if (x, RDF.type, target_class) in g:
                    raise FusionRuntimeError(
                        "Disjoint classes %s and %s have a common individual %s (or an identical individual with it)"
                        % (c1, target_class, x)
                    )  
    

def check_eq_diff_erro(g, s, o):
    if (s, OWL.differentFrom, o) in g or (
        o,
        OWL.differentFrom,
        s,
    ) in g:
        raise FusionRuntimeError(
                "'sameAs' and 'differentFrom' cannot be used on the same subject-object pair: (%s, %s)"
                    % (s, o)
            )
             
def check_irreflexiveProperty(g,p): # RULE prp-irp
    if (p, RDF.type, OWL.IrreflexiveProperty) in g:
        for x, y in g.subject_objects(p):
            if x == y:
                raise FusionRuntimeError(
                        "Irreflexive property used on %s with %s" % (x, p)
                    ) 


def check_FunctionalProperty(g, focus_property):
    # prp-fp
    if (focus_property, RDF.type, OWL.FunctionalProperty) in g: 
        for x, y1 in g.subject_objects(focus_property):
            for y2 in g.objects(x, focus_property):
                if y1 != y2:
                    #if y1 in found_node_targets or y2 in found_node_targets
                    g.add((y1, OWL.sameAs, y2))
      
    
def check_InverseFunctionalProperty(g, focus_property):
    # prp-ifp
    if (focus_property, RDF.type, OWL.InverseFunctionalProperty) in g:
        for x1, y in g.subject_objects(focus_property):
            for x2 in g.subjects(focus_property, y):
                if x1 != x2:
                    #if x1 in found_node_targets or x2 in found_node_targets
                    g.add((x1, OWL.sameAs, x2))
 

def all_samePath_merged(g, path_value):
    for p in path_value:
        m1 = [o for o in g.objects(p, OWL.sameAs)]
        m2 = [s for s in g.subjects(OWL.sameAs, p)]          
        m3 = [oo for oo in g.objects(p, OWL.equivalentProperty)]
        m4 = [ss for ss in g.subjects(OWL.equivalentProperty, p)] 
        m5 = [s for s in g.subjects(RDFS.subPropertyOf, p)]
        if len(m1)!= 0 or len(m2)!= 0 or len(m3)!= 0 or len(m4) != 0 or len(m5) != 0:
            return False 
    return True

def all_property_merged(g, property):
    m1 = [o for o in g.objects(property, OWL.sameAs)]
    if len(m1)!= 0:
        return False
    m2 = [s for s in g.subjects(OWL.sameAs, property)] 
    if len(m2) != 0:
        return False             
    m3 = [oo for oo in g.objects(property, OWL.equivalentProperty)]
    if len(m3)!= 0:
        return False
    m4 = [ss for ss in g.subjects(OWL.equivalentProperty, property)] 
    if len(m4) != 0:
        return False 
    return True 
  
def all_focus_merged(g, focus, target_nodes):

    m1 = [o for o in g.objects(focus, OWL.sameAs)] # focus node = o exists
    if len(m1)!= 0:
        return False
    m2 = [s for s in g.subjects(OWL.sameAs, focus)] # s = focus exists
    for e in m2:
        if e not in target_nodes:
            return False            
    return True
        
def all_subProperties_merged(g, p):
    m1 = [s for s in g.subjects(RDFS.subPropertyOf, p)]
    if len(m1)!= 0:
        return False
    return True

def all_targetClasses_merged(g, target_classes):
    for c in target_classes:
        m1 = [s for s in g.subjects(OWL.equivalentClass, c)]
        m2 = [s for s in g.objects(c, OWL.equivalentClass)]
        m3 = [s for s in g.subjects(OWL.sameAs, c)]
        m4 = [s for s in g.objects(c, OWL.sameAs)]
        if len(m1)!= 0 or len(m2)!= 0 or len(m3)!= 0 or len(m4)!= 0:
            return False
    return True

def sameClasses_merged(g, target_class):
    m1 = [s for s in g.subjects(OWL.equivalentClass, target_class)]
    m2 = [s for s in g.objects(target_class, OWL.equivalentClass)]
    m3 = [s for s in g.subjects(OWL.sameAs, target_class)]
    m4 = [s for s in g.objects(target_class, OWL.sameAs)]
    if len(m1)!= 0 or len(m2)!= 0 or len(m3)!= 0 or len(m4)!= 0:
        return False
    return True

def merge_target_classes(g, found_node_targets, same_nodes, target_classes):  #TODO: subClass use cases
    eq_targetClass = set()
    eq_targetNodes = set()
    for c in target_classes:
        while not sameClasses_merged(g, c):
            #print("Merge Classes")
            for c1 in g.subjects(OWL.equivalentClass, c): # c1 == c
                eq_targetClass.add(c1)
                for s in g.subjects(RDF.type, c1):
                    eq_targetNodes.add(s)
                    g.add((s, RDF.type, c))
                for ss in g.subjects(RDF.type, c):
                    g.add((ss, RDF.type, c1))
                g.remove((c1, OWL.equivalentClass, c))
                g.add((c1, RDFS.subClassOf, c))
                g.add((c, RDFS.subClassOf, c1))
            for c2 in g.objects(c, OWL.equivalentClass): # c == c2
                eq_targetClass.add(c2)
                for s in g.subjects(RDF.type, c2):
                    eq_targetNodes.add(s)
                    g.add((s, RDF.type, c))
                for ss in g.subjects(RDF.type, c):
                    g.add((ss, RDF.type, c2))
                g.remove((c, OWL.equivalentClass, c2))
                g.add((c2, RDFS.subClassOf, c))
                g.add((c, RDFS.subClassOf, c2))
            for c1 in g.subjects(OWL.sameAs, c): # c1 == c 
                eq_targetClass.add(c1)
                for s in g.subjects(RDF.type, c1):
                    eq_targetNodes.add(s)
                    g.add((s, RDF.type, c))
                for ss in g.subjects(RDF.type, c):
                    g.add((ss, RDF.type, c1))
                g.remove((c1, OWL.sameAs, c))
                g.add((c1, RDFS.subClassOf, c))
                g.add((c, RDFS.subClassOf, c1))
            for c2 in g.objects(c, OWL.sameAs): # c == c2
                eq_targetClass.add(c2)
                for s in g.subjects(RDF.type, c2):
                    eq_targetNodes.add(s)
                    g.add((s, RDF.type, c))
                for ss in g.subjects(RDF.type, c):
                    g.add((ss, RDF.type, c2))
                g.remove((c, OWL.equivalentClass, c2))
                g.add((c2, RDFS.subClassOf, c))
                g.add((c, RDFS.subClassOf, c2))

    
    for new_node in eq_targetNodes:
        if new_node not in found_node_targets:
            same_set = set()
            same_nodes.update({new_node: same_set})
        
    found_node_targets.update(eq_targetNodes)
    target_classes.update(eq_targetClass)   
    
            
        
def merge_same_property(g, properties, found_node_targets, same_nodes, target_classes, shapes, target_property, shacl_graph):
    for focus_property in properties:

        while not all_subProperties_merged(g, focus_property):
            #print("Merge subProperties")
            for sub_p in g.subjects(RDFS.subPropertyOf, focus_property):   
                if (focus_property, RDFS.subPropertyOf, sub_p) in g: #scm-eqp2
                    g.add((focus_property, OWL.sameAs, sub_p))
                else:
                    for p3 in g.subjects(RDFS.subPropertyOf, sub_p): # RULE scm-spo
                        if focus_property != p3:
                            g.add((p3, RDFS.subPropertyOf, focus_property))
                            
                    for c in g.objects(focus_property,RDFS.domain): #scm-dom2
                        g.add((sub_p, RDFS.domain, c))
                        
                    for c1 in g.objects(focus_property,RDFS.range): #scm-rng2
                        g.add((sub_p, RDFS.range, c1))
                        
                    for x, y in g.subject_objects(sub_p): # prp-spo1
                        g.add((x, focus_property, y))
                
                g.remove((sub_p, RDFS.subPropertyOf, focus_property)) #可能后退一格
            
        
        while not all_property_merged(g, focus_property):
            #print(focus_property)
            g.remove((focus_property, OWL.sameAs, focus_property))
            
            for p1 in g.subjects(OWL.equivalentProperty, focus_property):
                g.remove((p1, OWL.equivalentProperty, focus_property))
                g.add((focus_property, OWL.sameAs, p1))
            for p2 in g.objects(focus_property, OWL.equivalentProperty):
                g.remove((focus_property, OWL.equivalentProperty, p2))
                g.add((focus_property, OWL.sameAs, p2))
            
            for same_prop in g.subjects(OWL.sameAs, focus_property):                 
                g.remove((same_prop, OWL.sameAs, focus_property))
                g.add((focus_property, OWL.sameAs, same_prop))
                
            for same_property in g.objects(focus_property, OWL.sameAs):
                #check_irreflexiveProperty(g, same_property)
                #check_asymmetricProperty(g, same_property)
                g.remove((same_property, OWL.sameAs, same_property))
                
                if same_property != focus_property:
              
                    for p, o in g.predicate_objects(same_property):
                        g.remove((same_property, p, o))
                        g.add((focus_property, p, o))
                    for s, p in g.subject_predicates(same_property):
                        g.remove((s, p, same_property))
                        g.add((s, p, focus_property))
                    for s, o in g.subject_objects(same_property):
                        g.add((s, focus_property, o))
                        g.remove((s, same_property, o))
                    
                    if same_property in properties:    
                        # properties.remove(same_property) 
                        for s in shapes:  # Shapes graph re-writing
                            for blin in target_property:
                                # if same_property in s.sg.graph.objects(blin, SH_path):
                                #     s.sg.graph.remove((blin, SH_path, same_property))
                                #     s.sg.graph.add((blin, SH_path, focus_property))
                                if same_property in shacl_graph.objects(blin, SH_path):
                                    shacl_graph.remove((blin, SH_path, same_property))
                                    shacl_graph.add((blin, SH_path, focus_property))
                    
                g.remove((focus_property, OWL.sameAs, same_property))
            
                
        #check_propertyDisjointWith(g, focus_property)
        check_symmetricProperty(g, focus_property)
        check_transitiveProperty(g, focus_property)
        check_inverseOf(g, focus_property)
        check_domain_range(g, focus_property, found_node_targets, same_nodes, target_classes)
        #check_com_dw(g, target_classes)
        check_FunctionalProperty(g, focus_property)
        check_InverseFunctionalProperty(g, focus_property)
  



def merge_same_focus(g, same_nodes, focus,  target_nodes, shapes, shacl_graph):
    #eq-sym
    for s in g.subjects(OWL.sameAs, focus): # s = focus node
        #check_eq_diff_erro(g, s, focus)
        if s != focus:
            g.remove((s, OWL.sameAs, focus))
            g.add((focus, OWL.sameAs, s)) # s = focus --> focus = s
                 
    
    for o in g.objects(focus, OWL.sameAs):   #focus node = o
        
        #check_eq_diff_erro(g, focus, o)
        same_set = same_nodes[focus]
        same_set.add(o)
        if o != focus :
            if o in same_nodes:
                for i in same_nodes[o]:
                    same_set.add(i)
                del same_nodes[o]
            
            # for "eq-trans", "eq-rep-s", "eq-rep-o"
            for pp, oo in g.predicate_objects(o): # o pp oo --> focus pp oo
                g.remove((o, pp, oo))  
                g.add((focus, pp, oo))
            
            for ss, pp in g.subject_predicates(o): # ss pp o --> ss pp fucus
                g.remove((ss, pp, o))
                g.add((ss, pp, focus))
                    
            if o in target_nodes :  
                for s in shapes:  # Shapes graph re-writing
                    if o in shacl_graph.objects(s.node, SH_targetNode):
                        shacl_graph.remove((s.node, SH_targetNode, o))
                        shacl_graph.add((s.node, SH_targetNode, focus))
            g.remove((focus, OWL.sameAs, o))
            g.remove((focus, OWL.sameAs, focus))
        else:
            g.remove((focus, OWL.sameAs, focus))
            
            
            
def merged_graph(
    data_graph: Union[GraphLike, str, bytes],
    shacl_graph: Optional[Union[GraphLike, str, bytes]] = None,
    data_graph_format: Optional[str] = None,
    shacl_graph_format: Optional[str] = None,
    ):
    
    shapes, named_graphs, shape_graph = load_graph( data_graph, shacl_graph, data_graph_format,shacl_graph_format)    

    shape_g = shape_graph.graph
    
    # print("shape_graph:",type(shape_graph))
    # print("shape_graph.graph:",type(shape_graph.graph))
    # print("shape_g:",type(shape_g))
    
    vg = named_graphs[0] 
    found_node_targets = set()
    target_classes = set()
    path_value = set()
    found_target_classes = set()
    global_path = set()
    same_nodes = dict()
    
    shape_linked_target = set()
    sh_class = set()
    
    target_nodes=set()
    for s in shapes:
        targets = s.target_nodes()
        target_nodes.update(targets)
        
        focus = s.focus_nodes(vg)
        found_node_targets.update(focus)
        target_classes.update(s.target_classes())
        target_classes.update(s.implicit_class_targets())
            
        target_property=set(s.property_shapes())   
        
        if len(set(s.target_classes()))==0:       
            for blin in target_property:
                global_path.update(s.sg.graph.objects(blin, SH_path))
                
        for kind_property in target_property:
            if len(set(s.sg.graph.objects(kind_property, SH_node)))!= 0 :
            
                shape_linked_target.update(s.sg.graph.objects(kind_property, SH_path))
                
            if len(set(s.sg.graph.objects(kind_property, SH_class)))!= 0 :
                sh_class.update(s.sg.graph.objects(kind_property, SH_class))
        
        target_classes.update(sh_class)        
                    
        for blin in target_property:
            path_value.update(s.sg.graph.objects(blin, SH_path))

   
    fa_p = set()    
    for tp in path_value:
        vp = vg.transitive_objects(tp, RDFS_subPropertyOf)
        for propertis in vp :
            if propertis == tp:
                continue
            fa_p.add(propertis)
    path_value.update(fa_p)
    
    for tc in target_classes:
        subc = vg.transitive_subjects(RDFS_subClassOf, tc)
        for subclass in subc:
            if subclass == tc:
                continue
            found_target_classes.add(subclass)
    target_classes.update(found_target_classes)
    
    
    for path_ahead in shape_linked_target:
        for x in vg.objects(None, path_ahead):
            found_node_targets.add(x)
    
    for f in found_node_targets:
        same_set = set()
        same_nodes.update({f: same_set})

    target_domain_range(vg, found_node_targets, same_nodes, target_classes)
    
    for focus_node in found_node_targets:    
  
        while not all_focus_merged(vg, focus_node, found_node_targets):
       
            merge_same_focus(vg, same_nodes, focus_node, target_nodes, shapes, shape_g)  
            #check_com_dw(vg, target_classes)
                
    while (not all_targetClasses_merged(vg, target_classes)) or (not all_samePath_merged(vg, path_value)):

        merge_target_classes(vg, found_node_targets, same_nodes, target_classes)
        target_range(vg, found_node_targets, same_nodes, target_classes)
        
        # merge same properties 
        merge_same_property(vg, path_value, found_node_targets, same_nodes, target_classes, shapes, target_property, shape_g)
        
        # merge same nodes
        for focus_node in found_node_targets:    
      
            while not all_focus_merged(vg, focus_node, found_node_targets):
          
                merge_same_focus(vg, same_nodes, focus_node, target_nodes, shapes, shape_g)  
                #check_com_dw(vg, target_classes)

               
        for path_ahead in shape_linked_target:
            for x in vg.objects(None, path_ahead):
                if x not in found_node_targets:
                    found_node_targets.add(x)
                    same_set = set()
                    same_nodes.update({x: same_set})
                                   
    for node in found_node_targets:
        for p,o in vg.predicate_objects(node):
            subp = vg.transitive_objects(p,RDFS_subPropertyOf)
            for subpropertyOf in iter(subp):
                if subpropertyOf == p:
                    continue
                else:
                    vg.add((node,subpropertyOf,o))
            
    # Add all original triples with property owl:sameAs
    for k in same_nodes:
        for se in same_nodes[k]:
            vg.add((k, OWL.sameAs, se))
            
    # output_shapes = Graph()    # Load the rewrited shapes graph 
    # print("shapes: "+str(type(shapes)))
    # print("shapes_length: "+str(len(shapes)))
    # for s in shapes:
    #     print("s: "+str(type(s)))
    #     output_shapes = output_shapes + s.sg.graph
    
    # print("shape_g:",type(shape_g))
        
    return vg, same_nodes, shape_g # output_shapes
         
def merged_graph_with_metrics(data_graph: GraphLike, shacl_graph: GraphLike, data_graph_format: str, shacl_graph_format: str, collect_metrics: bool=True):
    """Run the original entity-level Re-SHACL pipeline and attach a tiny metrics dict to the resulting G′.
    This does *not* instrument internal phases; it gives wall-clock totals + sizes so we can A/B vs class engine.
    Returns (G′, same_nodes, S′) identical to merged_graph.
    """
    import time
    t0 = time.time()
    Gp, same_dic, Sp = merged_graph(data_graph, shacl_graph, data_graph_format, shacl_graph_format)
    t_total = time.time() - t0
    if collect_metrics:
        met = {
            "harvest": {},              # unknown here
            "schema":  {},              # unknown here
            "merging": {
                "same_components": len(same_dic),
                "subsumed": sum(len(v) for v in same_dic.values())
            },
            "gprime":  {
                "Gprime_triples": len(Gp)
            },
            "timing":  {
                "t_total": t_total
            }
        }
        try:
            Gp._reshacl_metrics = met
        except Exception:
            pass
    return Gp, same_dic, Sp
  
            
# Returns the intermediate graph resulting from the reasoning and its size
def inter_graph(
    data_graph: Union[GraphLike, str, bytes],
    shacl_graph: Optional[Union[GraphLike, str, bytes]] = None,
    data_graph_format: Optional[str] = None,
    shacl_graph_format: Optional[str] = None,
    ):
    
    import owlrl

    from pyshacl.inference import CustomRDFSOWLRLSemantics, CustomRDFSSemantics
        
    shapes, named_graphs, shape_graph = load_graph(data_graph, shacl_graph, data_graph_format,shacl_graph_format)  
    
    inferencer = owlrl.DeductiveClosure(CustomRDFSOWLRLSemantics)
    
    g = named_graphs[0]
    
    inferencer.expand(g)
    
    result = len(g)
    
    return result, g

def inter_graph_rdfs(
    data_graph: Union[GraphLike, str, bytes],
    shacl_graph: Optional[Union[GraphLike, str, bytes]] = None,
    data_graph_format: Optional[str] = None,
    shacl_graph_format: Optional[str] = None,
    ):
    
    import owlrl

    from pyshacl.inference import CustomRDFSOWLRLSemantics, CustomRDFSSemantics
        
    shapes, named_graphs, shape_graph = load_graph(data_graph, shacl_graph, data_graph_format,shacl_graph_format)  
    
    inferencer = owlrl.DeductiveClosure(CustomRDFSSemantics)
    
    g = named_graphs[0]
    
    inferencer.expand(g)
    
    result = len(g)
    
    return result, g 

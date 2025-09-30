from importlib import reload
from rdflib import Graph, Namespace, URIRef
from pyshacl import validate
import time
import sys
from prettytable import PrettyTable
import numpy as np
from ReSHACL.re_shacl import merged_graph_with_metrics
from ReSHACL.re_shacl_class import merged_graph_class
from metrics_utils import compute_metrics_dict, save_metrics, summarize_metrics_line
from rdflib.namespace import SH, RDF
import os
import logging

DBO = Namespace("http://dbpedia.org/ontology/")
sys.path.insert(0, sys.path[0] + "/../")

if sys.version[0] == '2':
    reload(sys)
    sys.setdefaultencoding("utf-8")

def collect_common_metrics(G_used: Graph, ShapesG: Graph, same_dic: dict):
    P = set()
    for s in ShapesG.subjects(RDF.type, SH.NodeShape):
        for ps in ShapesG.objects(s, SH.property):
            for p in ShapesG.objects(ps, SH.path):
                if isinstance(p, URIRef):
                    P.add(p)
    edges_by_path = { str(p): sum(1 for _ in G_used.triples((None, p, None))) for p in P }
    comps = len(same_dic)
    subsumed = sum(len(v) for v in same_dic.values())
    target_classes = set(ShapesG.objects(None, SH.targetClass))
    focus_proxy = set()
    for x, _, t in G_used.triples((None, RDF.type, None)):
        if t in target_classes and isinstance(x, URIRef):
            focus_proxy.add(x)
    for p in P:
        for s, o in G_used.subject_objects(p):
            if isinstance(s, URIRef): focus_proxy.add(s)
            if isinstance(o, URIRef): focus_proxy.add(o)
    return {
        "harvest": {"C": len(target_classes), "P": len(P)},
        "focus": {"F": len(focus_proxy)},
        "graph": {"triples": len(G_used)},
        "sameAs": {"components": comps, "total_subsumed": subsumed},
    }


def check_directory_exists_otherwise_create(directory):
    if not os.path.isdir(directory):
        folder_names = directory.split("/")
        folder_name = ""
        for name in folder_names:
            folder_name += name + "/"
            if not os.path.isdir(folder_name):
                os.mkdir(folder_name)
                print(f"Created folder: {folder_name}")


def run_pyshacl(dataset_name, g, sg, inference_method):
    if inference_method == 'both':
        method = 'pyshacl-owl'
    elif inference_method == 'rdfs':
        method = 'pyshacl-rdfs'
    else:
        method = 'pyshacl'

    table = PrettyTable(['Method', 'Average validation time (s)', 'Standard deviation', 'Conform', '#Violation'])

    result_query = """
    SELECT ?v
    WHERE {
        ?s sh:result ?v
    }"""

    inter_time = []
    for n1 in range(0, 3):
        t1 = time.time()
        conform, v_g, v_t = validate(g, shacl_graph=sg, inference=inference_method)
        t2 = time.time()

        inter_time.append(t2 - t1)

    mean_time = np.mean(inter_time)
    std = np.std(inter_time)

    result = v_g.query(result_query)

    print(f'[{method}]=============================')

    print(' Average validation time: ', mean_time, 's')
    print(' Standard deviation: ', std, 's')
    print(' #Violation: ', len(result))

    check_directory_exists_otherwise_create(f"Outputs/{dataset_name}/violationGraph/")
    v_g.serialize(destination=f"Outputs/{dataset_name}/violationGraph/{method}_results.ttl")

    check_directory_exists_otherwise_create(f"Outputs/{dataset_name}/validationReports/")
    file = open(f"Outputs/{dataset_name}/validationReports/{method}_results.txt", "w")
    file.write(v_t)
    file.close()

    table.add_row([method, mean_time, std, conform, len(result)])

    check_directory_exists_otherwise_create(f"Outputs/{dataset_name}/")
    file_table = open(f"Outputs/{dataset_name}/RunTimeResults.txt", "a+")
    file_table.write(str(table))
    file_table.close()

    print(table)

def run_reshacl_class(dataset_name, g, sg, inference_method):
    method = 'class-reshacl'
    table = PrettyTable(['Method',
                         'Avg build time (s)',
                         'Avg validate time (s)',
                         'Std (build)',
                         'Conform',
                         '#Violation'])

    result_query = """SELECT ?v WHERE { ?s sh:result ?v }"""

    build_times, validate_times = [], []
    last_conform = False
    last_violation_count = 0

    for run_idx in range(1, 4):
        t0 = time.time()
        fused_graph, same_dic, shapes, timings = merged_graph_class(
            g, shacl_graph=sg, data_graph_format='turtle', shacl_graph_format='turtle'
        )
        t1 = time.time()
        shapes.bind("dbo", DBO)

        metrics = compute_metrics_dict(method, timings, fused_graph, shapes, same_dic)
        print(summarize_metrics_line(metrics))
        save_metrics(dataset_name, method, run_idx, metrics)
        build_times.append(t1-t0)

    t_val0 = time.time()
    conform, v_g, v_t = validate(fused_graph, shacl_graph=shapes, inference='none')
    t_val1 = time.time()
    result = v_g.query(result_query)
    last_conform = conform
    last_violation_count = len(result)

    mean_build = float(np.mean(build_times))
    std_build = float(np.std(build_times))
    mean_val = float(t_val1 - t_val0)

    print(f'[{method}]=============================')
    print(' Avg build time (pre-validation): ', mean_build, 's')
    print(' Avg validation time:             ', mean_val, 's')
    print(' Std build/validate:              ', std_build, 's')
    print(' #Violation:                      ', last_violation_count)
    print(timings)

    check_directory_exists_otherwise_create(f"Outputs/{dataset_name}/violationGraph/")
    v_g.serialize(destination=f"Outputs/{dataset_name}/violationGraph/{method}_results.ttl")

    check_directory_exists_otherwise_create(f"Outputs/{dataset_name}/validationReports/")
    with open(f"Outputs/{dataset_name}/validationReports/{method}_results.txt", "w") as f:
        f.write(v_t)

    table.add_row([method, mean_build, mean_val, std_build, last_conform, last_violation_count])

    check_directory_exists_otherwise_create(f"Outputs/{dataset_name}/")
    with open(f"Outputs/{dataset_name}/RunTimeResults.txt", "a+") as file_table:
        file_table.write(str(table))

    print(table)

def run_reshacl(dataset_name, g, sg, inference_method):
    method = "ReSHACL"
    table = PrettyTable(['Method',
                         'Avg build time (s)',
                         'Avg validate time (s)',
                         'Std (build)',
                         'Conform',
                         '#Violation'])

    result_query = """SELECT ?v WHERE { ?s sh:result ?v }"""

    build_times, validate_times = [], []
    last_conform = False
    last_violation_count = 0

    for run_idx in range(1, 4):
        t_fuse0 = time.time()
        fused_graph, same_dic, shapes = merged_graph_with_metrics(
            g, shacl_graph=sg, data_graph_format='turtle', shacl_graph_format='turtle'
        )
        t_fuse1 = time.time()
        fuse_time = t_fuse1 - t_fuse0
        shapes.bind("dbo", DBO)

        timings = {
            "fusion_total": fuse_time,
            "total_pre_validation": fuse_time
        }

        metrics = compute_metrics_dict(method, timings, fused_graph, shapes, same_dic)
        print(summarize_metrics_line(metrics))
        save_metrics(dataset_name, method, run_idx, metrics)
        build_times.append(fuse_time)

    t_val0 = time.time()
    conform, v_g, v_t = validate(fused_graph, shacl_graph=shapes, inference=inference_method)
    t_val1 = time.time()

    result = v_g.query(result_query)
    last_conform = conform
    last_violation_count = len(result)

    mean_build = float(np.mean(build_times))
    std_build = float(np.std(build_times))
    mean_val = float(t_val1 - t_val0)

    print(f'[{method}]=============================')
    print(' Avg build time (pre-validation): ', mean_build, 's')
    print(' Avg validation time:             ', mean_val, 's')
    print(' Std build/validate:              ', std_build, 's')
    print(' #Violation:                      ', last_violation_count)

    check_directory_exists_otherwise_create(f"Outputs/{dataset_name}/violationGraph/")
    v_g.serialize(destination=f"Outputs/{dataset_name}/violationGraph/re-shacl_results.ttl")

    check_directory_exists_otherwise_create(f"Outputs/{dataset_name}/validationReports/")
    with open(f"Outputs/{dataset_name}/validationReports/re-shacl_results.txt", "w") as f:
        f.write(v_t)

    table.add_row([method, mean_build, mean_val, std_build, last_conform, last_violation_count])

    check_directory_exists_otherwise_create(f"Outputs/{dataset_name}/")
    with open(f"Outputs/{dataset_name}/RunTimeResults.txt", "a+") as file_table:
        file_table.write(str(table))

    print(table)



def run_experiment(dataset_name, dataset_uri, shapes_graph_uri, method='pyshacl', ontology=''):
    g = Graph()
    # Loading the data graph
    print("***** Loading the data graph *****")
    logging.getLogger('rdflib').setLevel(logging.ERROR)
    g.parse(dataset_uri)

    if ontology != '':
        # Importing Ontology into the data graph
        print("***** Loading the ontology *****")
        g.parse(ontology, format="xml")

    sg = Graph()
    # Loading the shapes graph
    print("***** Loading the shapes graph *****")
    sg.parse(shapes_graph_uri)
    sg.bind("dbo", DBO)

    # Preheating with 10 rounds
    # i = 0
    # for i in range(5):
    #     conform, v_g, v_t = validate(g, shacl_graph=sg, inference='none')

    print(f"***** START VALIDATION ON [{dataset_name}] *****")

    if method == 'pyshacl':
        run_pyshacl(dataset_name, g, sg, 'none')
    elif method == "pyshacl-rdfs":
        run_pyshacl(dataset_name, g, sg, 'rdfs')
    elif method == "pyshacl-owl":
        run_pyshacl(dataset_name, g, sg, 'both')
    elif method == 'reshacl':
        run_reshacl(dataset_name, g, sg, 'none')
    elif method == 'class-reshacl':
        run_reshacl_class(dataset_name, g, sg, 'none')


if __name__ == "__main__":
#kg.xl
    run_experiment(dataset_name="test_data_size",
                dataset_uri="source/Datasets/kgxl_data.ttl",
                shapes_graph_uri="source/ShapesGraphs/kgxl_shapes.ttl",
                method='class-reshacl')
    run_experiment(dataset_name="test_data_s",
            dataset_uri="source/Datasets/kgxl_data.ttl",
            shapes_graph_uri="source/ShapesGraphs/kgxl_shapes.ttl",
            method='reshacl')
#lubm skg
    # run_experiment(dataset_name="lubm-skg-11",
    #                 dataset_uri="source/Datasets/lubm-skg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-skg-11",
    #                 dataset_uri="source/Datasets/lubm-skg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-skg-12",
    #                 dataset_uri="source/Datasets/lubm-skg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-skg-12",
    #                 dataset_uri="source/Datasets/lubm-skg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-skg-13",
    #                 dataset_uri="source/Datasets/lubm-skg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-skg-13",
    #                 dataset_uri="source/Datasets/lubm-skg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='reshacl')
    
    # run_experiment(dataset_name="lubm-skg-21",
    #                 dataset_uri="source/Datasets/lubm-skg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-skg-21",
    #                 dataset_uri="source/Datasets/lubm-skg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-skg-22",
    #                 dataset_uri="source/Datasets/lubm-skg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-skg-22",
    #                 dataset_uri="source/Datasets/lubm-skg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-skg-23",
    #                 dataset_uri="source/Datasets/lubm-skg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-skg-23",
    #                 dataset_uri="source/Datasets/lubm-skg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='reshacl')
        
    # run_experiment(dataset_name="lubm-skg-31",
    #                 dataset_uri="source/Datasets/lubm-skg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-skg-31",
    #                 dataset_uri="source/Datasets/lubm-skg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-skg-32",
    #                 dataset_uri="source/Datasets/lubm-skg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-skg-32",
    #                 dataset_uri="source/Datasets/lubm-skg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-skg-33",
    #                 dataset_uri="source/Datasets/lubm-skg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-skg-33",
    #                 dataset_uri="source/Datasets/lubm-skg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='reshacl')
    
#endeLite all
    # run_experiment("EnDe-Lite50",
    #                "source/Datasets/EnDe-Lite50(without_Ontology).ttl",
    #                "source/ShapesGraphs/Shape_30.ttl",
    #                method='reshacl',
    #                ontology="source/dbpedia_ontology.owl")
    # run_experiment("EnDe-Lite50",
    #                "source/Datasets/EnDe-Lite50(without_Ontology).ttl",
    #                "source/ShapesGraphs/Shape_30.ttl",
    #                method='class-reshacl',
    #                ontology="source/dbpedia_ontology.owl")
    # run_experiment("EnDe-Lite100",
    #                "source/Datasets/EnDe-Lite100(without_Ontology).ttl",
    #                "source/ShapesGraphs/Shape_30.ttl",
    #                method='reshacl',
    #                ontology="source/dbpedia_ontology.owl")
    # run_experiment("EnDe-Lite100",
    #                "source/Datasets/EnDe-Lite100(without_Ontology).ttl",
    #                "source/ShapesGraphs/Shape_30.ttl",
    #                method='class-reshacl',
    #                ontology="source/dbpedia_ontology.owl")
    # run_experiment("EnDe-Lite1000",
    #                "source/Datasets/EnDe-Lite1000(without_Ontology).ttl",
    #                "source/ShapesGraphs/Shape_30.ttl",
    #                method='reshacl',
    #                ontology="source/dbpedia_ontology.owl")
    # run_experiment("EnDe-Lite1000",
    #                "source/Datasets/EnDe-Lite1000(without_Ontology).ttl",
    #                "source/ShapesGraphs/Shape_30.ttl",
    #                method='class-reshacl',
    #                ontology="source/dbpedia_ontology.owl")
    
#lubm_mkg
    # run_experiment(dataset_name="lubm-mkg-11",
    #                 dataset_uri="source/Datasets/lubm-mkg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-mkg-11",
    #                 dataset_uri="source/Datasets/lubm-mkg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-mkg-12",
    #                 dataset_uri="source/Datasets/lubm-mkg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-mkg-12",
    #                 dataset_uri="source/Datasets/lubm-mkg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-mkg-13",
    #                 dataset_uri="source/Datasets/lubm-mkg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-mkg-13",
    #                 dataset_uri="source/Datasets/lubm-mkg-1.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='reshacl')
    
    # run_experiment(dataset_name="lubm-mkg-21",
    #                 dataset_uri="source/Datasets/lubm-mkg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-mkg-21",
    #                 dataset_uri="source/Datasets/lubm-mkg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-mkg-22",
    #                 dataset_uri="source/Datasets/lubm-mkg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-mkg-22",
    #                 dataset_uri="source/Datasets/lubm-mkg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-mkg-23",
    #                 dataset_uri="source/Datasets/lubm-mkg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-mkg-23",
    #                 dataset_uri="source/Datasets/lubm-mkg-2.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='reshacl')

    # run_experiment(dataset_name="lubm-mkg-31",
    #                 dataset_uri="source/Datasets/lubm-mkg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-mkg-31",
    #                 dataset_uri="source/Datasets/lubm-mkg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema1.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-mkg-32",
    #                 dataset_uri="source/Datasets/lubm-mkg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-mkg-32",
    #                 dataset_uri="source/Datasets/lubm-mkg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema2.ttl",
    #                 method='reshacl')
    # run_experiment(dataset_name="lubm-mkg-33",
    #                 dataset_uri="source/Datasets/lubm-mkg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='class-reshacl')
    # run_experiment(dataset_name="lubm-mkg-33",
    #                 dataset_uri="source/Datasets/lubm-mkg-3.ttl",
    #                 shapes_graph_uri="source/ShapesGraphs/lubm/schema3.ttl",
    #                 method='reshacl')

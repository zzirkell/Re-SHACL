# KG-XL synthetic generator (data + shapes)
# Generates a DBpedia-like graph with many predicates and localized targets.

from rdflib import Graph, Namespace, URIRef, Literal, BNode
from rdflib.namespace import RDF, RDFS, OWL, XSD, FOAF, DC

SCALE = "L"  # "S" (small), "M" (medium), or "L" (large)

if SCALE == "S":
    NUM_PERSON = 100_000
    NUM_ORG    = 5_000
    NUM_PROJ   = 10_000
    NUM_PAPER  = 20_000
    AVG_MEMBER_OF_PER_PERSON = 1.2
    AVG_WORKS_AT_PER_PERSON  = 1.0
    SAMEAS_CLUSTER_RATE      = 0.06    # fraction of entities that get aliases
    AVG_SAMEAS_CLUSTER_SIZE  = 2.5
    NOISE_EXTRA_PROPS        = 300     # non-targeted predicates
elif SCALE == "M":
    NUM_PERSON = 400_000
    NUM_ORG    = 20_000
    NUM_PROJ   = 40_000
    NUM_PAPER  = 120_000
    AVG_MEMBER_OF_PER_PERSON = 1.3
    AVG_WORKS_AT_PER_PERSON  = 1.0
    SAMEAS_CLUSTER_RATE      = 0.08
    AVG_SAMEAS_CLUSTER_SIZE  = 2.8
    NOISE_EXTRA_PROPS        = 800
else:  # "L"
    NUM_PERSON = 1_200_000
    NUM_ORG    = 60_000
    NUM_PROJ   = 120_000
    NUM_PAPER  = 400_000
    AVG_MEMBER_OF_PER_PERSON = 1.4
    AVG_WORKS_AT_PER_PERSON  = 1.0
    SAMEAS_CLUSTER_RATE      = 0.1
    AVG_SAMEAS_CLUSTER_SIZE  = 3.0
    NOISE_EXTRA_PROPS        = 2000

#shapes should target only a small subset of predicates (≈ 20–30)
TARGETED_PCT = 0.03

EX  = Namespace("http://ex.org/")
SCM = Namespace("http://ex.org/schema/")
SH  = Namespace("http://www.w3.org/ns/shacl#")


# DATA GRAPH

g = Graph()
g.bind("ex", EX)
g.bind("scm", SCM)
g.bind("owl", OWL)
g.bind("rdfs", RDFS)
g.bind("rdf", RDF)
g.bind("foaf", FOAF)
g.bind("dc", DC)

Person = SCM.Person
Org    = SCM.Organization
Project= SCM.Project
Paper  = SCM.Paper

g.add((Person, RDF.type, RDFS.Class))
g.add((Org, RDF.type, RDFS.Class))
g.add((Project, RDF.type, RDFS.Class))
g.add((Paper, RDF.type, RDFS.Class))

#property families (targeted subset will include some of these)
memberOf      = SCM.memberOf
isMemberOf    = SCM.isMemberOf   # equiv to memberOf (or subproperty)
member        = SCM.member       # subproperty
worksAt       = SCM.worksAt
employs       = SCM.employs      # inverseOf worksAt
friendOf      = SCM.friendOf     # symmetric
broader       = SCM.broader      # transitive (for org/category-like)
hasEmail      = SCM.hasEmail     # functional
hasSSN        = SCM.hasSSN       # inverse-functional (toy)
authorOf      = SCM.authorOf
aboutTopic    = SCM.aboutTopic

#non-targeted noise predicates
noise_preds = [SCM.term(f"noiseProp{i}") for i in range(NOISE_EXTRA_PROPS)]

#predicates typing / families / schema
for p in [memberOf, isMemberOf, member, worksAt, employs, friendOf, broader, hasEmail, hasSSN, authorOf, aboutTopic]:
    g.add((p, RDF.type, RDF.Property))

g.add((isMemberOf, OWL.equivalentProperty, memberOf))
g.add((member, RDFS.subPropertyOf, memberOf))
g.add((employs, OWL.inverseOf, worksAt))
g.add((friendOf, RDF.type, OWL.SymmetricProperty))
g.add((broader, RDF.type, OWL.TransitiveProperty))
g.add((memberOf, RDFS.domain, Person))
g.add((memberOf, RDFS.range,  Org))
g.add((worksAt, RDFS.domain,  Person))
g.add((worksAt, RDFS.range,   Org))
g.add((hasEmail, RDFS.domain, Person))
g.add((hasEmail, RDFS.range,  XSD.string))
g.add((hasSSN, RDFS.domain,   Person))
g.add((hasSSN, RDFS.range,    XSD.string))
g.add((authorOf, RDFS.domain, Person))
g.add((authorOf, RDFS.range,  Paper))
g.add((aboutTopic, RDFS.domain, Paper))
g.add((aboutTopic, RDFS.range,  SCM.Topic))

#make many other predicates to mimic DBpedia variety
for p in noise_preds:
    g.add((p, RDF.type, RDF.Property))

#instances!
def iri(kind, i): return EX[f"{kind}{i}"]

for i in range(NUM_PERSON):
    g.add((iri("person", i), RDF.type, Person))
for i in range(NUM_ORG):
    g.add((iri("org", i), RDF.type, Org))
for i in range(NUM_PROJ):
    g.add((iri("proj", i), RDF.type, Project))
for i in range(NUM_PAPER):
    g.add((iri("paper", i), RDF.type, Paper))


import random
random.seed(42)

#helper samplers
def sample_org():
    return iri("org", random.randrange(NUM_ORG))
def sample_person():
    return iri("person", random.randrange(NUM_PERSON))
def sample_paper():
    return iri("paper", random.randrange(NUM_PAPER))
#memberOf / worksAt (targeted)
for i in range(NUM_PERSON):
    s = iri("person", i)
    for _ in range(max(1, int(random.expovariate(1.0/AVG_WORKS_AT_PER_PERSON)))):
        g.add((s, worksAt, sample_org()))
    for _ in range(max(1, int(random.expovariate(1.0/AVG_MEMBER_OF_PER_PERSON)))):
        if random.random() < 0.2:
            g.add((s, member, sample_org()))
        elif random.random() < 0.1:
            g.add((s, isMemberOf, sample_org()))
        else:
            g.add((s, memberOf, sample_org()))
    # hasEmail (functional) — 90% 
    if random.random() < 0.9:
        g.add((s, hasEmail, Literal(f"user{i}@example.org")))
    # hasSSN (inverse-functional) — 70% with some duplicates to induce merges
    if random.random() < 0.7:
        val = f"SSN-{int(i/50)}" if random.random() < 0.1 else f"SSN-{i}"
        g.add((s, hasSSN, Literal(val)))

for i in range(NUM_PERSON):
    s = iri("person", i)
    if random.random() < 0.3:
        p = sample_paper()
        g.add((s, authorOf, p))
        # aboutTopic is targeted but not too dense
        if random.random() < 0.7:
            g.add((p, aboutTopic, SCM.TopicAI))

for _ in range(NUM_PERSON // 3):
    a = sample_person()
    b = sample_person()
    if a != b:
        g.add((a, friendOf, b))

for i in range(0, NUM_ORG, 50):
    if i+1 < NUM_ORG:
        g.add((iri("org", i+1), broader, iri("org", i)))
    if i+2 < NUM_ORG and random.random() < 0.5:
        g.add((iri("org", i+2), broader, iri("org", i+1)))

#add lots of non-targeted “noise” facts so P << all predicates
for i in range(NUM_PERSON):
    s = iri("person", i)
    for _ in range(random.randint(0, 2)):
        p = random.choice(noise_preds)
        if random.random() < 0.5:
            g.add((s, p, Literal(f"val-{i}-{_}")))
        else:
            g.add((s, p, sample_org() if random.random() < 0.5 else sample_paper()))

#create owl:sameAs clusters!!
def make_sameas_clusters(kind, N):
    ids = list(range(N))
    random.shuffle(ids)
    taken = set()
    for i in ids:
        if i in taken: continue
        if random.random() < SAMEAS_CLUSTER_RATE:
            size = max(2, int(random.expovariate(1.0/(AVG_SAMEAS_CLUSTER_SIZE-1)))+1)
            members = [i]
            for _ in range(size-1):
                j = random.randrange(N)
                if j not in taken and j not in members:
                    members.append(j)
            if len(members) >= 2:
                for a, b in zip(members, members[1:]):
                    g.add((iri(kind, a), OWL.sameAs, iri(kind, b)))
                    g.add((iri(kind, b), OWL.sameAs, iri(kind, a)))
                # optional reflexives can be left out; your class pipeline handles minCount if needed
                for m in members:
                    taken.add(m)
            else:
                taken.add(i)

make_sameas_clusters("person", NUM_PERSON)
make_sameas_clusters("org", NUM_ORG)

#out
g.serialize("kgxl_data.ttl", format="turtle")
print("Wrote kgxl_data.ttl with ~", len(g), "triples")

# SHAPES GRAPH
sg = Graph()
sg.bind("sh", SH)
sg.bind("scm", SCM)
sg.bind("ex", EX)
sg.bind("foaf", FOAF)
sg.bind("owl", OWL)
sg.bind("rdfs", RDFS)

def prop_shape(path_iri, minc=None, maxc=None, clazz=None, node_ref=None, closed=None):
    ps = BNode()
    sg.add((ps, RDF.type, SH.PropertyShape))
    sg.add((ps, SH.path, path_iri))
    if minc is not None:
        sg.add((ps, SH.minCount, Literal(minc, datatype=XSD.integer)))
    if maxc is not None:
        sg.add((ps, SH.maxCount, Literal(maxc, datatype=XSD.integer)))
    if clazz is not None:
        sg.add((ps, SH["class"], clazz))
    if node_ref is not None:
        sg.add((ps, SH.node, node_ref))
    if closed is not None:
        pass
    return ps

#node shapes
PersonShape = EX.PersonShape
OrgShape    = EX.OrgShape
ProjectShape= EX.ProjectShape
PaperShape  = EX.PaperShape

for shn in [PersonShape, OrgShape, ProjectShape, PaperShape]:
    sg.add((shn, RDF.type, SH.NodeShape))

#targets
sg.add((PersonShape, SH.targetClass, Person))
sg.add((OrgShape,    SH.targetClass, Org))
sg.add((ProjectShape,SH.targetClass, Project))
sg.add((PaperShape,  SH.targetClass, Paper))

#PersonShape properties (focus: memberOf -> OrgShape delegation!)
ps1 = prop_shape(memberOf, minc=1, node_ref=OrgShape)           # delegation
ps2 = prop_shape(worksAt,  maxc=1, clazz=Org)                   # maxCount + class
ps3 = prop_shape(hasEmail, minc=1)                               # functional-ish
ps4 = prop_shape(friendOf, minc=1)                               # symmetric path
ps5 = prop_shape(authorOf, minc=0)                               # optional
sg.add((PersonShape, SH.property, ps1))
sg.add((PersonShape, SH.property, ps2))
sg.add((PersonShape, SH.property, ps3))
sg.add((PersonShape, SH.property, ps4))
sg.add((PersonShape, SH.property, ps5))

#OrgShape (closed to stress typing; allow known paths)
sg.add((OrgShape, SH.closed, Literal(True)))
allowlist = [member, memberOf, employs, broader, RDF.type]
for p in allowlist:
    sg.add((OrgShape, SH.ignoredProperties, p))

#OrgShape properties
os1 = prop_shape(member, minc=0)        # mirror membership (some data will use 'member' subproperty)
os2 = prop_shape(employs, minc=0)
os3 = prop_shape(broader, minc=0)
sg.add((OrgShape, SH.property, os1))
sg.add((OrgShape, SH.property, os2))
sg.add((OrgShape, SH.property, os3))

#PaperShape
pps1 = prop_shape(aboutTopic, minc=0)
sg.add((PaperShape, SH.property, pps1))

# OPTIONAL: If you want to force reflexive sameAs for singletons under class pipeline,
# uncomment the following property shape. Keep it off by default to avoid global reflexives.
# ps_same = prop_shape(OWL.sameAs, minc=1)
# sg.add((PersonShape, SH.property, ps_same))

# out2
sg.serialize("kgxl_shapes.ttl", format="turtle")
print("Wrote kgxl_shapes.ttl with", len(sg), "triples")

Medical information is spread across many different sources.

Drug labels describe approved uses. Safety databases contain adverse event reports. ClinicalTrials.gov provides information about ongoing research. Medical vocabularies define diseases, conditions, and their relationships.

The challenge is not simply finding this information. It is connecting it.

For example:

Which diseases is a particular drug used to treat?
What adverse reactions have been reported?
Which drugs share similar medical indications?
What clinical trials are investigating related conditions?
Can we bring all this information together to answer a question?
To explore these challenges, I built MedGraph, a medical intelligence platform using Neo4j, Python, LangGraph, and GraphRAG.

The objective is simple: connect fragmented medical information into a knowledge graph and make it accessible through a grounded AI assistant.

The graph database, ETL pipelines, embeddings, and web application run locally.

1. The architecture
MedGraph brings together medical vocabularies, drug information, safety reports, clinical trials, and AI-powered search.

The current graph contains:

273,530 nodes
302,252 relationships
14 vector indexes
273,211 embeddings
The architecture has seven main components:

Medical vocabulary: ATC and MeSH.
Graph-assisted search: expanding medical terms through relationships.
Drug safety: connecting drugs with adverse event reports.
Clinical trial intelligence: linking trials with medical conditions.
Graph analytics: identifying relationships between similar drugs.
GraphRAG and AI assistant: combining semantic search with graph traversal.
Ontology and schema: defining how information is structured and connected.
Press enter or click to view image in full size

MedGraph architecture: bringing medical data, knowledge graphs, vector search, and AI together.
2. Building the medical vocabulary: ATC and MeSH
A knowledge graph needs a common language to connect information from different sources.

MedGraph uses two important medical classification systems.

ATC: Understanding drug classification
The Anatomical Therapeutic Chemical (ATC) classification system organizes drugs into five levels.

For example, Tolvaptan has the ATC code C03XA01.

Its classification looks like this:

C — Cardiovascular system
C03 — Diuretics
C03X — Other diuretics
C03XA — Vasopressin antagonists
C03XA01 — Tolvaptan
Instead of storing this as one long code, we represent every level as a separate node in Neo4j.

This allows us to navigate from a drug to its broader therapeutic classification.


Representing drug classification as a connected hierarchy in Neo4j.
Press enter or click to view image in full size

Decoding a drug’s ATC code through graph traversal.
MeSH: Understanding medical terminology
Medical professionals and researchers do not always use the same words to describe a disease.

For example, one document may mention Type 2 Diabetes, while another uses Diabetes Mellitus.

MeSH (Medical Subject Headings), maintained by the US National Library of Medicine, provides a structured vocabulary for medical concepts.

Unlike ATC, MeSH is not simply a tree. A medical concept can belong to multiple broader categories.

For example, Diabetes Mellitus is connected to both:

Nutritional and Metabolic Diseases
Endocrine System Diseases
We imported the 2026 MeSH vocabulary, including:

31,110 descriptors
235,902 entry terms
42,519 hierarchy relationships
The result is a multi-parent graph representing medical concepts and their relationships.

Press enter or click to view image in full size

MeSH is a multi-parent hierarchy, making knowledge graphs useful for representing medical terminology.
3. Graph-assisted medical search
Traditional keyword search looks for matching words.

But medical information often uses different terminology to describe the same condition.

Consider a search for Diabetes Mellitus.

A document might contain:

Type 1 Diabetes
Type 2 Diabetes
Diabetic Retinopathy
Diabetic Foot
Diabetes Complications
A basic keyword search may miss many relevant documents.

MedGraph solves this by using the MeSH hierarchy.

The process is:

The user enters a medical term.
The system finds the corresponding MeSH concept.
Neo4j traverses its hierarchy.
Related concepts and synonyms are collected.
The expanded terms are used to search medical documents.
For Diabetes Mellitus, the graph expands the query to 281 related terms.

In our sample of 250 openFDA documents, this increased the number of matching documents from 6 to 10.

That represents a 67% improvement in recall.

The important idea is that the graph understands relationships between medical terms instead of relying only on exact word matches.

Press enter or click to view image in full size

Expanding a medical search using concept relationships and synonyms.
4. Connecting drug safety and clinical trials
Medical terminology is only the foundation. The next step is connecting it with real-world medical information.

MedGraph integrates three additional datasets.

Drug safety: openFDA FAERS
The FDA Adverse Event Reporting System (FAERS) contains reports of suspected adverse events associated with drugs.

We represent the information through relationships:

Drug → Adverse Event Report → Reaction → MeSH Concept

The current dataset contains:

247 adverse event reports
464 distinct reaction terms
233 reactions connected to MeSH descriptors
This allows us to ask questions such as:

What reactions have been reported for a particular drug?
Which reactions are associated with drugs used for a specific condition?
Which reported reactions can be connected to standardized medical concepts?
These are reported associations, not proof that a drug caused a reaction.

Press enter or click to view image in full size

Connecting drug safety reports with standardized medical terminology.
Press enter or click to view image in full size

Exploring drug safety information through connected graph relationships.
Clinical trial intelligence: ClinicalTrials.gov

Drug labels tell us about approved uses. Clinical trials provide information about ongoing and completed research.

We integrated ClinicalTrials.gov using its public API.

The current graph contains:

719 clinical trials
541 sponsors
1,262 condition relationships
Because clinical trial conditions are linked to MeSH concepts, we can connect clinical research with existing drug and disease information.

This creates a useful relationship between what is already approved and what is currently being investigated.

Press enter or click to view image in full size

Connecting clinical trials, research sponsors, and medical conditions.
Press enter or click to view image in full size

Exploring the clinical research pipeline through connected data.
Drug identity: RxNorm and ICD-10-CM
Drug names can vary between manufacturers, medical records, and regulatory documents.

For example, a user may search using a brand name, while a medical dataset stores the generic ingredient.

We use RxNorm and RxNav to connect brand names with their corresponding drug ingredients.

The current dataset includes 2,315 brand and ingredient aliases.

We also use the NLM Clinical Tables API to connect 72 active disease concepts with ICD-10-CM codes.

This creates a common identity layer across different medical datasets.

Press enter or click to view image in full size

Resolving different drug names into a common identity.
5. Finding similar drugs using graph analytics
Can we identify drugs that have similar medical indications and adverse reaction profiles?

To explore this, we implemented a similarity calculation directly in Cypher.

Two drugs are compared using:

Shared medical indications.
Shared reported adverse reactions.
The similarity score is calculated as:

Similarity Score = (Shared Indications × 2) + Shared Reactions

We generated 6,878 similarity relationships across the graph.

For example, Glipizide can be connected to other drugs that share medical indications.

This is an exploratory similarity measure, not a clinical recommendation or proof that drugs are interchangeable.

An interesting aspect of this implementation is that we performed the calculations directly in Cypher without installing the Neo4j Graph Data Science plugin.

Press enter or click to view image in full size

Finding relationships between drugs using shared medical indications and safety information.
6. Combining vector search and knowledge graphs: GraphRAG
Knowledge graphs are good at representing structured relationships.

Vector search is useful for finding information based on meaning rather than exact keywords.

MedGraph combines both approaches.

We generate embeddings using the local nomic-embed-text model and store them in Neo4j vector indexes.

The current implementation includes:

14 vector indexes
273,211 embeddings
Embeddings for drugs, aliases, documents, reactions, medical descriptors, trials, sponsors, and ATC levels.
Consider a user searching for a phrase such as “high blood sugar condition”.

Vector search can identify the closest medical concept.

Once that concept is found, the knowledge graph can retrieve:

Related diseases
Associated drugs
Safety reports
Clinical trials
Relevant medical documents
The result combines semantic understanding with structured relationships.

This is the foundation of the GraphRAG implementation.

Press enter or click to view image in full size

Combining vector similarity with knowledge graph traversal.
7. Building a grounded AI assistant with LangGraph
The next step is making the knowledge graph accessible through natural language.

MedGraph uses a LangGraph ReAct agent with a pluggable language model backend.

The assistant has nine tools:

disease_overview – retrieves diseases, associated drugs, and reactions.
mesh_expand – expands medical terms through the MeSH hierarchy.
search_documents – searches medical documents.
drug_safety – retrieves adverse event information.
trials_pipeline – explores clinical trials.
decode_atc – explains drug classification.
similar_drugs – finds drugs with shared characteristics.
semantic_search – performs vector-based search.
graph_qa – answers questions using generated Cypher queries.
The assistant can use local language models through Ollama or a local OpenCode API backend.

An important design decision was to avoid allowing the language model to invent database facts.

We encountered problems with smaller models generating invalid query arguments and inventing clinical trial identifiers.

We addressed this through three changes:

Using models with proper tool-calling support.
Creating deterministic tools for common multi-step questions.
Enforcing grounding rules so the assistant only reports information returned by the graph.
If the graph does not contain the answer, the assistant should say so.

The objective is not simply to produce a fluent response. It is to produce an answer that can be traced back to the underlying data.

Press enter or click to view image in full size

A natural-language interface over the medical knowledge graph.
8. The ontology: Defining the graph structure
As the graph grows, maintaining consistency becomes increasingly important.

For example:

Can a reaction be directly connected to a drug?
Should a drug alias be treated as a separate drug?
Which relationships connect drugs with medical conditions?
What properties uniquely identify each node?
To manage this, MedGraph uses a Turtle ontology file:

ontology/medical_graph.ttl

The file defines:

16 node classes.
19 relationship types.
Unique key properties.
Relationship domains and ranges.
It acts as a shared structural contract for the entire application.

The ETL pipelines, API, dashboard, and AI assistant use the same definitions.

We also built a validation mechanism using RDFLib and automated tests.

These tests check that:

Every node label is declared.
Every relationship type is declared.
Relationship domains and ranges reference valid classes.
Database constraints match the declared key properties.
For example, if a developer accidentally writes INDICATION_FOR instead of INDICATED_FOR, the test catches the undeclared relationship before it reaches the database.

The ontology is deliberately lightweight. It is a development-time schema contract, not a full OWL reasoning engine.

It does not currently enforce cardinality, disjointness, or runtime relationship validation.

This keeps the model readable and the application predictable.

Press enter or click to view image in full size

The ontology provides a shared contract for the Neo4j database, ETL pipelines, API, and assistant.
9. A complete example: Understanding Glucotrol
Let’s see how all these components work together.

Suppose a user asks:

“What does MedGraph know about Glucotrol?”

Glucotrol is a brand name. The corresponding generic drug is Glipizide.

Instead of querying several independent databases, we can follow the relationships in the knowledge graph.

Step 1: Resolve the drug identity
Glucotrol → Glipizide

The alias relationship connects the brand name with the canonical drug record.

Step 2: Decode the ATC classification
Glipizide has the ATC code A10BB07.

Its classification is:

A — Alimentary tract and metabolism
A10 — Drugs used in diabetes
A10B — Blood glucose-lowering drugs, excluding insulins
A10BB — Sulfonylureas
A10BB07 — Glipizide
Press enter or click to view image in full size

Following the complete ATC classification path for Glipizide.
Step 3: Find medical indications
The graph contains 11 indication relationships for Glipizide.

Two of these are disease concepts:

Diabetes Mellitus
Diabetes Mellitus, Type 2
The remaining entries include other extracted label terms, illustrating why medical data extraction requires further quality checks.

Press enter or click to view image in full size

Exploring the medical indications associated with Glipizide.
Step 4: Explore the safety information
The graph contains:

7 adverse event reports
4 serious reports
0 reported deaths
0 reported hospitalizations
7 distinct reaction terms
These are observations from the current sample, not a measure of the drug’s overall safety.

Press enter or click to view image in full size

Exploring the reported adverse event information for Glipizide.
Press enter or click to view image in full size

Tracing drug safety information through connected graph relationships.
Step 5: Find similar drugs
The graph identifies 105 drugs connected to Glipizide through similarity relationships.

These relationships are based on shared indications and reported reactions.

The system can rank the candidates and show the underlying similarity information.

Press enter or click to view image in full size

Exploring drug similarity through graph analytics.
Step 6: Explore the complete path in Neo4j
The knowledge graph brings all these relationships together.

A single query can follow the brand alias, identify the drug, decode its ATC classification, and retrieve associated indications, safety reports, and similar drugs.

Press enter or click to view image in full size

A connected view of the drug identity and classification relationships.
Step 7: Ask the AI assistant
The same information can be explored conversationally.

The assistant uses the graph tools to answer questions about Glipizide’s safety profile and similar drugs.

Press enter or click to view image in full size

Retrieving a drug safety profile through natural-language interaction.
Press enter or click to view image in full size

Finding similar drugs through a conversational interface.
The assistant can also handle more complex questions.

For example, it can compare the safety profiles of Glipizide and Glimepiride, or identify drugs that overlap between a similarity search and a particular disease overview.

Press enter or click to view image in full size

Combining multiple graph tools to answer comparative questions.
Press enter or click to view image in full size

Combining multiple graph queries to answer a more complex question.
10. What the implementation demonstrates
MedGraph demonstrates several useful capabilities:

Semantic search: Medical terminology can be expanded using concept hierarchies and synonyms.

Data integration: Different public medical datasets can be connected through common identifiers and vocabularies.

Graph analytics: Drug similarity can be explored through shared indications and reported reactions.

GraphRAG: Vector search can identify relevant concepts, while graph traversal retrieves structured information.

Agentic AI: LangGraph provides a tool-based interface for exploring the medical knowledge graph.

Ontology-driven development: A shared ontology keeps the database, ETL pipelines, API, and assistant aligned.

Most importantly, these capabilities operate on the same underlying graph 


Data sources
The project uses publicly available data from:

WHO Collaborating Centre for Drug Statistics Methodology — ATC/DDD Index
US National Library of Medicine — MeSH 2026
openFDA — Drug labels and FAERS
ClinicalTrials.gov
RxNorm / RxNav
NLM Clinical Tables — ICD-10-CM
Final thoughts
Medical data becomes much more useful when we can connect it.

A drug is not just a name. It has classifications, indications, reported reactions, aliases, and relationships with other drugs and medical conditions.

A knowledge graph provides the structure to connect these pieces of information.

Vector search helps us find relevant concepts. LangGraph provides an interface for interacting with them. An ontology defines how the information is organized.

Together, these technologies create a foundation for building AI systems that do more than retrieve documents.

They can explore relationships, follow evidence, and provide answers grounded in connected knowledge.
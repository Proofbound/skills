"""Quarto-project mechanics owned by ProofBound.

This module is deliberately EMPTY of side effects and imports. It is the whole
reason the package exists: importing the indexing engine out of
``cc_template_api`` dragged in 1,672 modules (~1.0 s of FastAPI, supabase,
reportlab, jinja2, settings and middleware) that no indexing code references,
all inherited from parent ``__init__`` files that build the FastAPI app.

Import submodules directly::

    from proofbound_quarto.indexing import markers

Nothing may be re-exported here, and nothing may be imported here.
"""

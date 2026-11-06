"""Logic that runs both on the ProofBound server and on a customer's machine.

The server installs this package as a path dependency; the distributable skills
vendor it into ``scripts/_vendor/`` (see ``scripts/vendor-skill-runtime.sh`` and
``scripts/package-skill.sh``). So every module here must:

- import only the standard library (``voice`` may also import numpy), and
- never import from ``proofbound_shared`` or a service package.
"""

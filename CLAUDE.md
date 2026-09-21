# Claude instructions

@AGENTS.md

The imported file points to the canonical agent instructions and records the
Alexandria-specific repository conventions.

Keep project-specific decisions in this repository. Do not copy canonical
instructions into it. Alexandria follows the Foundry package conventions:
Python code lives under `src/pi/alexandria`, uses the PEP 420 namespace, and
uses absolute imports. The runtime currently remains on Python 3.12 because its
pinned image and dependencies are the verified deployment baseline; a Python
upgrade is a separate compatibility change.

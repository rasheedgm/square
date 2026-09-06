"""Square project-setup tool -- spin up a show end to end.

    python -m tools.project_setup        # Qt GUI

The smallest tool that exercises the pipeline spine: it drives
`square_core.services.projects` (create the Kitsu project + NAS root + folder
skeleton + `project_config.json`) and `square_core.services.breakdown`
(sequences, shots, and the task grid). It writes nothing itself -- every
outcome is a core service call.

See `docs/pipeline_architecture.md` sections 11-12.
"""

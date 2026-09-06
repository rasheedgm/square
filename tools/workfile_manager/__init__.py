"""Square workfile / version manager -- the DCC-agnostic tool #4.

    python -m tools.workfile_manager        # Qt GUI

Pick a task, see its saved workfile versions and its published outputs, start
the next version (seeded from the current one or a template), open it in the
DCC, and publish a render. Every action is one `square_core.services.work`
call; the tool holds no pipeline state of its own.
"""

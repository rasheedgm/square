"""Studio folder-structure conventions.

`SHOT_FOLDER_STRUCTURE` is the default 2D/3D tree created under every shot (and
copied into a new project's `ProjectConfig` by `projects.create`, so a project
carries its own frozen copy). Studios override it per project.
"""

from __future__ import annotations

SHOT_FOLDER_STRUCTURE = [
    # 2D
    "2D/comp/dailes", "2D/comp/elements/DMP", "2D/comp/elements/precomp",
    "2D/comp/feedback", "2D/comp/ref", "2D/comp/render/exr", "2D/comp/render/mov",
    "2D/comp/workfiles/mocha", "2D/comp/workfiles/nuke", "2D/comp/workfiles/sfx",
    "2D/dmp/dailes", "2D/dmp/elements/DMP", "2D/dmp/elements/precomp",
    "2D/dmp/feedback", "2D/dmp/ref", "2D/dmp/render/exr", "2D/dmp/render/mov",
    "2D/dmp/workfiles/mocha", "2D/dmp/workfiles/nuke", "2D/dmp/workfiles/sfx",
    "2D/prep/dailes", "2D/prep/elements/DMP", "2D/prep/elements/precomp",
    "2D/prep/feedback", "2D/prep/ref", "2D/prep/render/exr", "2D/prep/render/mov",
    "2D/prep/workfiles/mocha", "2D/prep/workfiles/nuke", "2D/prep/workfiles/sfx",
    "2D/roto/dailes", "2D/roto/elements/DMP", "2D/roto/elements/precomp",
    "2D/roto/feedback", "2D/roto/ref", "2D/roto/render/exr", "2D/roto/render/mov",
    "2D/roto/workfiles/mocha", "2D/roto/workfiles/nuke", "2D/roto/workfiles/sfx",
    "2D/temp",
    # 3D
    "3D/animation/reference", "3D/animation/render", "3D/animation/temp",
    "3D/animation/workfiles",
    "3D/env/reference", "3D/env/render", "3D/env/temp", "3D/env/workfiles",
    "3D/fx/reference", "3D/fx/render", "3D/fx/temp", "3D/fx/workfiles",
    "3D/grooming/reference", "3D/grooming/render", "3D/grooming/temp",
    "3D/grooming/workfiles",
    "3D/lighting/reference", "3D/lighting/render", "3D/lighting/temp",
    "3D/lighting/workfiles",
    "3D/matchmove/camera", "3D/matchmove/dailies", "3D/matchmove/geo",
    "3D/matchmove/LD", "3D/matchmove/workfiles",
    "3D/rotoanim/reference", "3D/rotoanim/render", "3D/rotoanim/temp",
    "3D/rotoanim/workfiles",
    # incoming
    "input",
]

# Control maps

A preprocessing stage: the person's raw photo or clip -> the edges (`canny-*`) or depth (`depth-*`) a control input reads. Inputs marked `prepared` in a recipe (Wan Fun Control's `control_video`, union ControlNets' `control_map`, …) take THIS stage's output (`stage:<name>.image|video`), never the raw file. Depth keeps layout and motion and frees surface detail (restyle); Canny keeps exact outlines. Pose needs a custom pack (not here).

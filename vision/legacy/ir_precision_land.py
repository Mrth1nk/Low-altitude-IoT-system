#!/usr/bin/env python3
"""Unified IR tracking and precision-landing entry point.

One process owns the camera and MAVLink port. It tracks the IR beacon in GUIDED
mode, then switches to LANDING_TARGET messages only in LAND/QLAND. LOITER stays
passive so takeoff and hover are not pulled around by companion commands.
"""

import os
import sys

from precision_land_v4 import Config, PrecisionLanding, print_usage


def main() -> int:
    if "-h" in sys.argv or "--help" in sys.argv:
        print_usage()
        return 0

    os.environ.setdefault("CAM_ORIENTATION", "FORWARD")
    os.environ.setdefault("CONTROL_MODE", "GUIDED_VELOCITY")
    os.environ.setdefault("PRECISION_LAND_MODES", "LAND,QLAND")
    os.environ.setdefault("ACTIVE_MODES", "GUIDED,LAND,QLAND")
    os.environ.setdefault("SEND_HZ", "15")
    os.environ.setdefault("VEL_MAX", "0.45")
    os.environ.setdefault("VEL_KP_X", "0.35")
    os.environ.setdefault("VEL_KP_Y", "0.35")

    cfg = Config()
    if "--no-mavlink" in sys.argv or os.environ.get("NO_MAVLINK", "0") == "1":
        cfg.NO_MAVLINK = True
    if "--stream" in sys.argv:
        cfg.STREAM_ENABLED = True

    return PrecisionLanding(cfg).run()


if __name__ == "__main__":
    sys.exit(main())

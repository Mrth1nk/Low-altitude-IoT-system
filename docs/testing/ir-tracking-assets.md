# IR Tracking Test Assets

Task 7 found no recorded camera frames in the repository. The detector tests
therefore use deterministic 640x480 grayscale synthetic frames containing a
single saturated circular spot, plus blank and sub-area noise frames.

These assets exercise the successful `precision_land_v4.py` behavior baseline:
brightness thresholding, contour area, circularity, centroid and confidence.
They are generated in the tests rather than stored as binary files so the exact
geometry remains reviewable and reproducible.

## Backends

- The local macOS system Python 3.9 has no OpenCV. Local discovery therefore
  runs the explicit NumPy detector fallback; the OpenCV-backend assertion is
  reported as skipped.
- The isolated ELF test copy in `/tmp` used its installed OpenCV 4.10.0 and
  NumPy 2.2.1. All detector, geometry, mode-gate, optical-state and controller
  tests ran there through the real OpenCV contour path. Production files and
  services were not changed.

## Control Convergence

The guided-tracker regression starts at zero correction on first acquisition,
then applies the configured acceleration limit on each timestamped update. It
also covers low-pass filtering, deadband, maximum horizontal speed, stale-frame
rejection and immediate correction suppression on target loss.

# Rune Protype Cleanup Notes

Date: 2026-07-02

## Duplicate Scan

Exact SHA-256 duplicate scan under `protype/` found:

- Image files scanned: 5,785
- Exact duplicate groups: 997
- Extra duplicate files: 3,663
- Exact duplicate bytes that could be reclaimed by keeping one file per hash: about 1.06 GB

Most duplicates are generated crops/debug images repeated across phase output directories, not new source samples.

## Keep

Keep these until the rune automation has been stable for a while:

- `protype/RuneInstance` - original 100 positive rune screenshots.
- `protype/RuneNegative` - no-rune negative screenshots used for rejection checks.
- `protype/RuneEvaluate` - independent live verification samples, including `Rune`, `Special`, and `NoRune`.
- `protype/RuneTrainOutput` - contains the reviewed crop CSV history, including `crop_review_results_4.csv`.
- `protype/RuneTrainPhase5Output` - final local training output and summary. The final model has also been copied into `assets/Rune/rune_template_model.npz` for git/build use.

These kept directories total about 887 MB locally.

## Delete Candidates

These are generated experiments/debug outputs and can be deleted locally after confirming the current model remains accepted:

- `protype/RuneDebugOutput`
- `protype/RuneEvaluateDebug`
- `protype/RuneLegacyDebug100Output`
- `protype/RuneLocatorExperiment`
- `protype/RuneRuntimeModelDebugOutput`
- `protype/RuneTrainLocatorFixedOutput`
- `protype/RuneTrainPhase5BaselineOutput`
- `protype/RuneTrainReview2AlignedOutput`
- `protype/RuneTrainReview2Output`
- `protype/RuneTrainReview2RelocatedOutput`
- `protype/RuneTrainReview3HistoricalAlignedOutput`
- `protype/RuneTrainReviewedOutput`

These delete candidates total about 2.19 GB locally.

## Git Policy

Do not commit the whole `protype/` tree. It is ignored because it contains large local samples, generated crops, and debug outputs.

Commit only the compact runtime artifact:

- `assets/Rune/rune_template_model.npz`

This keeps the trained template model available to source builds and PyInstaller bundles without uploading the full experimental dataset.

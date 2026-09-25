# Independent analysis archive

The ledger writes its truthful venue status before optional analysis work.
Analysis never changes trading signals or risk thresholds.

## Deployment modes (choose one)

- Gateway: set R20_ANALYSIS_STANDALONE=1 for both backend and gateway.
  Restart at an operationally safe time. The gateway runs the archive every
  300 seconds with a 180-second timeout and its existing retry policy.
- Linux systemd: run scripts/sync_analysis_archive.py from the repository
  virtualenv with WorkingDirectory set to the repository. Use a oneshot
  service, TimeoutStartSec=180, Restart=on-failure, RestartSec=60, and a timer
  with OnUnitInactiveSec=300. After a successful service run, create
  data/analysis_archive.standalone to disable inline ledger archive work.
  Do not also enable the gateway archive job.

The independent script uses a nonblocking cross-process lock next to the
analysis database. It fetches current positions before reconciliation and
does not submit orders. Capture-disabled installations skip the job.

## Production deployment on 2026-09-25

The /home/r20 deployment uses r20-analysis-archive.service and
r20-analysis-archive.timer. Check service logs with journalctl and timer
health with systemctl. The service records stage timings under
archive.sync and emits an analysis_archive summary. A successful process
exit does not prove every exchange history source is complete: also inspect
analysis_sync source errors and pagination coverage.

Code backups are stored alongside replaced files with .bak timestamps.
For rollback, stop the timer and wait for/stop only the archive service,
then remove data/analysis_archive.standalone and unset
R20_ANALYSIS_STANDALONE to restore inline archive behavior.
Never fabricate ledger timestamps or clear risk state as part of rollback.

## Validation

Keep account isolation, event chronological ordering, idempotent event IDs,
latest exit evidence and all position extrema unchanged. Regression tests
cover selective reads and bounded ID batches. Event filtering deliberately
leaves full event history available to audit/export consumers.

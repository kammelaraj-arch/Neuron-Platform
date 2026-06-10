# Neuron Master nightly backup

Snapshots the SQLite DB (via `sqlite3 .backup` for crash-safe
point-in-time consistency) plus the rest of `master_platform/data/`
(audit logs, alexa state, bootstrap files) into one `tar.gz` per
night.

## Install (on the VPS)

```bash
sudo bash /opt/neuron-platform/scripts/backup/install.sh
```

That:

1. Drops `neuron-backup.sh` to `/usr/local/bin/`.
2. Installs the systemd unit + nightly timer (`03:17 UTC` ± 10 min).
3. Seeds `/etc/default/neuron-backup` with tunables (compose file path,
   retention count, optional off-site URL).
4. Enables + starts the timer.

## What's in each archive

Each `neuron-YYYYMMDD-HHMMSSZ.tgz` contains:

- `neuron.db` — point-in-time SQLite snapshot of the master DB.
- `data.tgz` — everything else under `master_platform/data/` (logs,
  alexa state, bootstrap files, build artefacts, …).

## Retention

`NEURON_BACKUP_KEEP` defaults to 14 (≈ 2 weeks of nightlies). Older
archives are pruned on each run.

## Off-site

Set `NEURON_BACKUP_S3_URL` in `/etc/default/neuron-backup` to ship
each archive to S3 / Vultr Object Storage / Backblaze / DigitalOcean
Spaces. Either `aws` (preferred, set credentials in `~/.aws/credentials`)
or `rclone` (configure a remote that matches the URL prefix) must be
on the path.

Example for Vultr Object Storage via rclone:

```bash
sudo rclone config         # add a remote called 'vultr-objstor'
echo 'NEURON_BACKUP_S3_URL=vultr-objstor:neuron-backups' | sudo tee -a /etc/default/neuron-backup
```

## Restore

```bash
sudo bash /opt/neuron-platform/scripts/backup/restore.sh \
    /opt/neuron-backups/neuron-20260616-031700Z.tgz
```

Stops `neuron-master`, swaps in the snapshot, restarts. The pre-restore
volume snapshot is preserved as `data/pre-restore-<ts>.tgz` inside
the volume so a botched restore is reversible.

## Manual run

```bash
sudo systemctl start neuron-backup.service
sudo journalctl -u neuron-backup -e -n 40
```

## Cost

A nightly run of this on a tiny home install is ~5–20 MB compressed,
~10 s wall time. Free if you keep it on the same host; ~£0.10/month
on Vultr Object Storage with 14-day retention for the off-site copy.

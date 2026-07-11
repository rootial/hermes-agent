import json

import pytest

from cron.jobs import (
    FIRST_RUN_PENDING_STATUS,
    create_job,
    load_jobs,
    resume_job,
    save_jobs,
)
from hermes_cli.cron import cron_list


@pytest.fixture
def tmp_cron_dir(tmp_path, monkeypatch):
    cron_dir = tmp_path / "cron"
    monkeypatch.setattr("cron.jobs.CRON_DIR", cron_dir)
    monkeypatch.setattr("cron.jobs.JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", cron_dir / "output")
    return tmp_path


def test_create_job_starts_pending(tmp_cron_dir):
    job = create_job(prompt="Pending job", schedule="every 1h")

    assert job["last_run_at"] is None
    assert job["last_status"] == FIRST_RUN_PENDING_STATUS


def test_load_jobs_migrates_enabled_never_run_job(tmp_cron_dir):
    job = create_job(prompt="Pending migrate", schedule="every 1h")
    job["last_status"] = None
    save_jobs([job])

    loaded = load_jobs()

    assert loaded[0]["last_status"] == FIRST_RUN_PENDING_STATUS
    stored = json.loads(
        tmp_cron_dir.joinpath("cron", "jobs.json").read_text(encoding="utf-8")
    )
    assert stored["jobs"][0]["last_status"] == FIRST_RUN_PENDING_STATUS


def test_paused_never_run_job_keeps_empty_status(tmp_cron_dir):
    job = create_job(prompt="Paused job", schedule="every 1h")
    job.update(enabled=False, state="paused", last_status=None)
    save_jobs([job])

    assert load_jobs()[0]["last_status"] is None


def test_resume_never_run_job_restores_pending_status(tmp_cron_dir):
    job = create_job(prompt="Resume job", schedule="every 1h")
    job.update(enabled=False, state="paused", last_status=None)
    save_jobs([job])

    resumed = resume_job(job["id"])

    assert resumed["last_status"] == FIRST_RUN_PENDING_STATUS


def test_cron_list_shows_pending_first_run(tmp_cron_dir, capsys):
    create_job(prompt="Pending job", schedule="every 1h")

    cron_list()

    assert "pending first run" in capsys.readouterr().out.lower()

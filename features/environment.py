"""behave environment hooks for angrymiao."""

import logging
import shutil
import tempfile
import uuid
from pathlib import Path

from behave.model import Scenario, Status, Step
from behave.runner import Context

# logger for environment hooks
logger = logging.getLogger("features.steps")


def before_all(context: Context) -> None:
    """configures logging and creates a test artifact directory."""
    # step definitions logging
    log_level = context.config.userdata.get("log_level", "INFO")
    numeric_level = getattr(logging, log_level.upper(), None)
    if numeric_level is None:
        valid = "DEBUG, INFO, WARNING, ERROR, CRITICAL"
        raise ValueError(f"invalid log_level '{log_level}'. valid levels: {valid}")
    logger.setLevel(numeric_level)

    # creates a temporary directory for test artifacts
    context.test_run_dir = Path(tempfile.mkdtemp(prefix="angrymiao_test_"))
    logger.info("test run directory: %s", context.test_run_dir)


def after_all(context: Context) -> None:
    """cleans up test artifacts and flushes logging."""
    # cleans up test run directory
    if hasattr(context, "test_run_dir") and context.test_run_dir.exists():
        try:
            shutil.rmtree(context.test_run_dir)
        except OSError:
            logger.warning(
                "could not remove test run directory: %s", context.test_run_dir
            )

    # ensures all logging handlers flush before exit
    for handler in logging.root.handlers:
        handler.flush()


def before_scenario(context: Context, scenario: Scenario) -> None:
    """creates scenario-specific artifact directory and unique token."""
    # scenario-specific directories
    scenario_name = scenario.name.replace(" ", "_").replace("/", "_")
    context.scenario_dir = context.test_run_dir / scenario_name
    context.scenario_dir.mkdir(parents=True, exist_ok=True)

    # generates scenario token (unique id for this scenario)
    context.scenario_token = f"{scenario_name}_{uuid.uuid4()}"


def after_scenario(context: Context, scenario: Scenario) -> None:
    """preserves artifact directory on failure for debugging."""
    # if scenario failed, preserve the directory for debugging
    if scenario.status == Status.failed:
        logger.info("scenario failed. artifacts preserved at: %s", context.scenario_dir)


def before_step(context: Context, step: Step) -> None:  # noqa: ARG001
    """called before each step."""


def after_step(context: Context, step: Step) -> None:  # noqa: ARG001
    """called after each step."""

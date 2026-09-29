"""sample step definitions for angrymiao."""

import subprocess
import sys

from behave import given, then, when
from behave.runner import Context


@given("the application is available")
def available_application(context: Context) -> None:  # noqa: ARG001
    """verifies the application module can be imported."""
    import angrymiao  # noqa: F401


@when("I run the application")
def run_application(context: Context) -> None:
    """runs the application as a subprocess."""
    result = subprocess.run(
        [sys.executable, "-m", "angrymiao"],
        capture_output=True,
        text=True,
    )
    context.exit_code = result.returncode
    context.stdout = result.stdout
    context.stderr = result.stderr


@then("the exit code is {code:d}")
def assert_exit_code(context: Context, code: int) -> None:
    """verifies the exit code matches the expected value."""
    assert context.exit_code == code, (
        f"expected exit code {code}, got {context.exit_code}\n"
        f"stdout: {context.stdout}\nstderr: {context.stderr}"
    )

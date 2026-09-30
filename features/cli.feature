Feature: Command-line help
  The command is usable without attached hardware.

  Scenario: Help exits successfully
    Given the application is available
    When I run the application help command
    Then the exit code is 0

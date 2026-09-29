Feature: Sample
  A sample feature to verify the behave setup works.

  Scenario: Application runs successfully
    Given the application is available
    When I run the application
    Then the exit code is 0

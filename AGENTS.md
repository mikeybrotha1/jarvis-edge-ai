# Jarvis Edge AI Engineering Instructions

Jarvis Edge AI is a Raspberry Pi 5 edge-AI platform using Python, PostgreSQL,
Docker, an event-driven service architecture, Azure Kinect hardware, and a
Hailo AI accelerator.

## Working Rules

1. Work only on the explicitly requested task.
2. Never commit automatically.
3. Never push branches automatically.
4. Never modify unrelated files.
5. Always inspect the existing implementation before proposing changes.
6. Always present a plan before editing files.
7. Preserve Raspberry Pi, Docker, PostgreSQL, Azure Kinect, and Hailo compatibility.
8. Prefer small, typed, testable Python modules.
9. Avoid unnecessary dependencies.
10. Preserve existing behaviour unless the task explicitly requires a change.
11. Add or update tests for every behavioural change.
12. Run relevant tests after making changes.
13. Report every modified file.
14. Report unresolved risks and assumptions.
15. Stop and ask before making an architectural decision outside the task.
16. Do not conceal test failures.
17. Do not replace hardware-aware behaviour with mocks in production code.
18. Do not perform broad refactors during narrowly scoped tasks.
19. Keep each Git commit focused on one capability.
20. Do not commit until the human reviews the diff and test output.

## Productization Priorities

Every change should make Jarvis easier to:

- configure
- install
- deploy
- operate
- monitor
- extend
- integrate
- test

## Current Stage

Stage 3: Productization

Initial priorities:

1. Typed configuration foundation
2. REST API
3. Health and readiness monitoring
4. Plugin architecture
5. Installation and deployment workflow
6. Web dashboard
7. Authentication and enterprise connectors

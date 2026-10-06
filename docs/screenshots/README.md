# Screenshots

Place the following screenshot files in this directory to make the README images render on GitHub.

| Filename | Description |
|---|---|
| `01_fresh_session.png` | Fresh session greeting — 0 / 9 fields |
| `02_name_entered.png` | Single field extracted — name provided, status = provided |
| `03_multi_field.png` | Multiple fields extracted in one message |
| `04_conflict_detected.png` | Conflict banner shown for contradictory answers |
| `05_direct_edit.png` | Direct field edit via the Fields table |
| `06_draft_tab.png` | Draft document tab with formatted output |
| `07_json_tab.png` | JSON tab with syntax-highlighted structured data |
| `08_session_complete.png` | All 9 fields filled — progress bar at 9 / 9 |

Capture each case by running the app locally (`uvicorn app.main:app --reload --port 8000`) and saving browser screenshots with the filenames above.

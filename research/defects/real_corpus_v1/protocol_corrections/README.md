# Retrieval Protocol Correction Record

The initial Search execution exposed two API constraints before human coding:

1. the original merged-PR query exceeded GitHub's Boolean-operator limit and
   returned HTTP 422; and
2. a corrected LangGraph core-fix PR query reached the 1,000-result Search cap.

`initial_search_report.json` preserves the exact first-run statuses and counts.
The correction split the long query and made complete pinned first-parent Git
history the authoritative candidate frame. Search remains supplementary. No
candidate had been human-coded, no partition had been revealed, and no Operator
had been derived when this correction was made.

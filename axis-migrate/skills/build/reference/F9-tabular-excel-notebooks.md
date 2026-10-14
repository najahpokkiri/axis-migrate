# F9 · Tables, Excel, notebooks and manual steps

Open when: the step reads or writes xlsx, csv or another table; the legacy is a notebook; notes describe a manual step.
Table layout (sheet, header row, data rows, total rows, units cell) is a parameter, never a guess.

Enforced elsewhere: `compare.py` reads both sides with one reader configuration (XLSX cached values, the declared
sheet, header, data and total rows) and reports `SCHEMA_MISMATCH`, `ROW_COUNT_MISMATCH`, `KEY_SET_MISSING` and
`ATTRIBUTE_VALUE_MISMATCH`; scanner S08 blocks fill literals that do not come from a parameter.

Format: `ID · rule · fails if ignored · evidence · eval · form` (P prose, S scanner, C compare, R runtime ctx).

- **TB-1** · Excel cached values are the oracle: read formulas (`data_only=False`) and cached values (`data_only=True`) separately; a workbook last saved by a library has no cached values (None) · fails if ignored: wrong numbers, or None read as missing · evidence: ev:b46251ae13 · eval: E-xl, lux · form: P+C
- **TB-2** · Declare the sheet, header row, data range, units cell, label trimming and the rows below the data (totals, averages) in params.yaml with a `file#cell` source, and the same layout in the dataset's `compare` block; for csv declare separator, decimal mark and encoding (a BOM needs `utf-8-sig`) · fails if ignored: header read as data; units lost; first column name corrupted · evidence: ev:b46251ae13, ev:bc4f49cce0, ev:7f3f749166 · eval: E-xl, lux · form: P+C
- **TB-3** · Read code columns as strings: `keep_default_na=False` ("NA" can be a code), strip a trailing ".0", keep leading zeros; NaN keys never match · fails if ignored: codes mangled; lookups miss · evidence: ev:a2678f9a2b · eval: E-xl · form: P
- **TB-4** · Pivots drop absent classes: reindex to the full class list; nulls in a categorical input fall into the `else` branch: map them explicitly and assert · fails if ignored: KeyError, or rows in the wrong branch · evidence: ev:fa7acadbfb, ev:848508546d · eval: T-branch · form: P
- **TB-5** · One canonical label table plus aliases; class lists come from the data's distinct values; match whole tokens, never substrings · fails if ignored: bins dropped or misassigned · evidence: ev:65207e211c, ev:3d27a39bc4, ev:03f1dd1cc6 · eval: E-xl · form: P
- **TB-6** · Intensive vs extensive: a mean of shares is not a share of totals; broadcast only ratios; `to_numeric(errors="coerce")` followed by a fill changes "n/a" into a value · fails if ignored: totals multiplied; missing becomes 0 · evidence: ev:0011be43ca, ev:f6e7cb4770 · eval: lux · form: P+S+C (S08)
- **TB-7** · A described filter is not the applied one, and zero rows is a finding: check predicate literals against the column's distinct values and the NULL rate per source · fails if ignored: wrong population; false "missing" · evidence: ev:ea30b2a1cf, ev:91f4ae584c, ev:635aba4fb4, ev:c7ecdb0fb5 · eval: E-sj · form: P
- **TB-8** · A manual step (hand edit, a template value pasted into config, a file renamed between scripts) is a graph step (`kind: manual`) with a declared artefact; the user decides port-as-code or bridge (SKILL.md §5) · fails if ignored: a hidden loop; an output nobody can reproduce · evidence: ev:cbbe243c95, ev:d777f463c9 · eval: N-manual, lux · form: P
- **TB-9** · Notebooks: non-monotonic execution counts mean saved outputs are untrusted; port code that re-executes top to bottom; choose the authoritative twin (`.ipynb`, `.py`, checkpoint) by content and ask when they diverge · fails if ignored: porting a state that never ran · evidence: ev:4211adb613, ev:2092fcc006 · eval: E-nb · form: P
